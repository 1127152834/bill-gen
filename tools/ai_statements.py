#!/usr/bin/env python3
"""一句话到一摞账单 PDF：驱动 flow 用真实 LLM 造账本，再用 billgen 渲染成多个模板。

用法（先起 flow：`pnpm --filter @bh/server start`，端口默认 8797）：

    .venv/bin/python tools/ai_statements.py \
        --request '{"subjectType":"personal","accounts":{"checking":{"min":8,"max":13}},"months":3}' \
        --templates boa-personal,chase-personal \
        --out out/ai-run1

    # 或者直接把请求写进文件
    .venv/bin/python tools/ai_statements.py --request-file req.json --templates all --out out/ai-run1

产物：`out/ai-run1/<模板>-c<期号>.pdf` + `result.json`（flow 的原始结果包）+ `manifest.json`。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from billgen.synth import SynthLintError, from_game_result, supported_ids  # noqa: E402

STATIC_IDS = {"alliant", "ally-bank"}
TERMINAL = {"completed", "completed_with_warnings", "failed", "interrupted"}


def _call(url: str, *, api_key: str, body: dict | None = None, timeout: float = 60.0,
          raw: bool = False) -> object:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("X-API-Key", api_key)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = resp.read()
    return payload if raw else json.loads(payload)


def submit(server: str, request: dict, *, api_key: str) -> str:
    created = _call(f"{server}/api/generations", api_key=api_key, body=request)
    if not isinstance(created, dict) or "id" not in created:
        raise SystemExit(f"提交失败：{created}")
    return str(created["id"])


def wait(server: str, job_id: str, *, api_key: str, timeout: float, poll: float) -> dict:
    started = time.monotonic()
    last = ""
    while True:
        job = _call(f"{server}/api/generations/{job_id}", api_key=api_key)
        if not isinstance(job, dict):
            raise SystemExit(f"查询失败：{job}")
        status = str(job.get("status", ""))
        if status != last:
            stage = job.get("stage") or job.get("progress") or ""
            print(f"  [{int(time.monotonic() - started):>4}s] {status} {stage}", flush=True)
            last = status
        if status in TERMINAL:
            return job
        if time.monotonic() - started > timeout:
            raise SystemExit(f"超时：{job_id} 仍是 {status}（可稍后用 --resume-id 接着等）")
        time.sleep(poll)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--request", help="flow 的 GenerationRequest JSON 字符串")
    src.add_argument("--request-file", help="同上，从文件读")
    src.add_argument("--resume-id", help="跳过生成，直接用已有的 flow 任务 id")
    ap.add_argument("--server", default="http://127.0.0.1:8797", help="flow 服务地址")
    ap.add_argument("--api-key", default="", help="flow 的 API_KEY（回环请求可省）")
    ap.add_argument("--templates", default="all", help="逗号分隔的模板 id，或 all")
    ap.add_argument("--account", default="", help="账本账户，缺省取结果里的第一个")
    ap.add_argument("--cycles", default="all", help="all 或逗号分隔的期号（-1 = 最后一期）")
    ap.add_argument("--bank-name", default="", help="覆盖银行显示名")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--timeout", type=float, default=1800.0, help="等待生成的秒数，默认 1800")
    ap.add_argument("--poll", type=float, default=5.0, help="轮询间隔秒数")
    ap.add_argument("--no-watermark", action="store_true")
    ap.add_argument("--no-brand", action="store_true")
    args = ap.parse_args(argv)

    server = args.server.rstrip("/")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.resume_id:
        job_id = args.resume_id
        print(f"复用任务 {job_id}")
    else:
        request = json.loads(args.request) if args.request else json.loads(
            Path(args.request_file).read_text(encoding="utf-8"))
        job_id = submit(server, request, api_key=args.api_key)
        print(f"已提交 {job_id}")

    job = wait(server, job_id, api_key=args.api_key, timeout=args.timeout, poll=args.poll)
    status = str(job.get("status", ""))
    if status not in {"completed", "completed_with_warnings"}:
        raise SystemExit(f"生成未完成：{status} {job.get('error') or ''}".strip())

    payload = _call(f"{server}/api/generations/{job_id}/export.json", api_key=args.api_key, raw=True)
    result_path = out_dir / "result.json"
    result_path.write_bytes(payload)
    result = json.loads(payload)
    statements = result.get("statements") or []
    print(f"结果包 {result_path}（{len(statements)} 个账户）")

    wanted = args.templates.strip()
    if wanted == "all":
        template_ids = [tid for tid in supported_ids() if tid not in STATIC_IDS]
    else:
        template_ids = [tid.strip() for tid in wanted.split(",") if tid.strip()]
    for template_id in template_ids:
        if template_id not in supported_ids():
            raise SystemExit(f"未知模板 {template_id}（可用：{', '.join(supported_ids())}）")

    manifest: list[dict[str, object]] = []
    for template_id in template_ids:
        for raw_cycle in [None] if args.cycles.strip() == "all" else [
            int(part) for part in args.cycles.split(",") if part.strip()
        ]:
            cycle = -1 if raw_cycle is None else raw_cycle
            try:
                ledger = from_game_result(
                    result,
                    account=args.account or None,
                    cycle=cycle,
                    bank_name=args.bank_name or None,
                    watermark=not args.no_watermark,
                )
            except (KeyError, SynthLintError, ValueError) as err:
                print(f"  跳过 {template_id} cycle={cycle}：{err}", file=sys.stderr)
                continue
            from billgen.synth import render  # 延迟导入，跳过分支时不付代价

            try:
                rendered = render(template_id, ledger, brand=not args.no_brand)
            except SynthLintError as err:
                print(f"  跳过 {template_id} cycle={cycle}：{err}", file=sys.stderr)
                continue
            name = f"{template_id}-c{cycle}{'' if cycle >= 0 else ''}.pdf"
            target = out_dir / name
            target.write_bytes(rendered.pdf)
            item = {
                "template": template_id,
                "cycle": cycle,
                "file": name,
                "pages": rendered.pages,
                "bytes": len(rendered.pdf),
                "sha256": __import__("hashlib").sha256(rendered.pdf).hexdigest(),
                "account": ledger.account.kind,
                "bank": ledger.bank.name,
                "period": [ledger.period.start.isoformat(), ledger.period.end.isoformat()],
                "rows": len(ledger.rows),
                "opening_cents": ledger.opening_cents,
                "closing_cents": ledger.closing_cents,
            }
            manifest.append(item)
            print(f"  {target.name:<34} {rendered.pages} 页 {len(rendered.pdf):>8} B")

    (out_dir / "manifest.json").write_text(
        json.dumps({"generation": job_id, "bank": statements[0].get("bankName") if statements else "",
                    "files": manifest}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(f"完成：{len(manifest)} 个 PDF → {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
