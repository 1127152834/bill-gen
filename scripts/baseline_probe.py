#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账单模板仓库 — 13 入口渲染基线探针（重构前的行为锚点 / P0 交付物）。

用途
----
在动手重构之前，把「每个模板入口当前能产出什么」变成机器可判定的基线：
页数、页面尺寸、字节数、文本层 SHA-256、嵌入字体名、耗时。
重构每一个阶段结束时用 --compare 复跑，只要基线不变，就等于「功能保持一致」。

为什么每个模板要独立子进程
--------------------------
各模板的引擎模块同名（`generate`、`chstream`、`barcode`），且都以
`sys.path.insert(0, BASE) + import generate` 的方式加载；同一进程内第二次
import 会命中 sys.modules 缓存，拿到上一个模板的模块。这正是原仓库 run.py
用 8 个子进程换取隔离的根本原因。探针沿用子进程，并在重构后改为显式模块
加载（importlib + 唯一模块名），届时 --parallel 可改为同进程执行。

用法
----
    python3 baseline_probe.py --repo bill-gen --python .venv/bin/python \
        --out baseline.json
    python3 baseline_probe.py --repo bill-gen --python .venv/bin/python \
        --baseline baseline.json --compare
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# id -> (展示名, 实现范式, 引擎目录, 数据文件, 入口函数)
#   范式 from-scratch : 从零绘制（坐标 + 布局 JSON）
#   范式 replay       : 底稿重放（打开 prepared PDF，按 span 表原位替换）
#   范式 static-web   : 纯前端 pdf-lib 导出，无后端
TEMPLATES = {
    "wf": {
        "name": "Wells Fargo",
        "kind": "from-scratch",
        "dir": "富国/statement_template",
        "data": "富国/statement_template/editor/data.json",
        "entry": "build_pdf",
        "module": "generate",
    },
    "ae": {
        "name": "American Express",
        "kind": "from-scratch",
        "dir": "AE",
        "data": None,                      # DATASETS['demo'] 内置
        "entry": "build_pdf",
        "module": "generate_sample_statement",
    },
    "merc": {
        "name": "Mercury",
        "kind": "from-scratch",
        "dir": "水星C1sofi/mercury-statement-template",
        "data": None,                      # 模块级 DATA 全局
        "entry": "build",
        "module": "generate_statement",
    },
    "c1": {
        "name": "Capital One",
        "kind": "from-scratch",
        "dir": "水星C1sofi/c1-statement-template",
        "data": None,
        "entry": "build",
        "module": "c1_generate",
    },
    "sofi": {
        "name": "SoFi",
        "kind": "from-scratch",
        "dir": "水星C1sofi/sofi-statement-template",
        "data": None,
        "entry": "build",
        "module": "sofi_generate",
    },
    "boa-personal": {
        "name": "BOA Personal",
        "kind": "from-scratch",
        "dir": "BOA/statement_template",
        "data": "BOA/statement_template/editor/data_personal.json",
        "entry": "build_pdf",
        "module": "generate",
    },
    "boa-business": {
        "name": "BOA Business",
        "kind": "from-scratch",
        "dir": "BOA/statement_template",
        "data": "BOA/statement_template/editor/data_business.json",
        "entry": "build_pdf",
        "module": "generate",
    },
    "chase-personal": {
        "name": "Chase Personal",
        "kind": "replay",
        "dir": "大通/statement_template",
        "data": "大通/statement_template/editor_personal/data_personal.json",
        "entry": "build_pdf",
        "module": "generate_personal",
    },
    "chase-business": {
        "name": "Chase Business",
        "kind": "replay",
        "dir": "大通/statement_template",
        "data": "大通/statement_template/editor_business/data_business.json",
        "entry": "build_pdf",
        "module": "generate_business",
    },
    "us-personal": {
        "name": "US Bank Personal",
        "kind": "replay",
        "dir": "USbank/statement_template",
        "data": "USbank/statement_template/editor_us/data_us.json",
        "entry": "build_pdf",
        "module": "generate_us",
    },
    "us-business": {
        "name": "US Bank Business",
        "kind": "replay",
        "dir": "USbank/statement_template",
        "data": "USbank/statement_template/editor_biz/data_biz.json",
        "entry": "build_pdf",
        "module": "generate_biz",
    },
    "alliant": {
        "name": "Alliant",
        "kind": "static-web",
        "dir": "alliant",
        "data": None,
        "entry": None,
        "module": None,
    },
    "ally": {
        "name": "Ally Bank",
        "kind": "static-web",
        "dir": "ally bank",
        "data": None,
        "entry": None,
        "module": None,
    },
}

# 同进程渲染脚本：由父进程以子进程方式调用，保证模块隔离。
_CHILD = r"""
import importlib, json, os, sys, time, hashlib
cfg = json.loads(sys.argv[1])
repo = cfg["repo"]; out = cfg["out"]
sys.path.insert(0, repo)                      # runtime_paths 在仓库根
sys.path.insert(0, os.path.join(repo, cfg["dir"]))
os.chdir(repo)
t0 = time.perf_counter()
mod = importlib.import_module(cfg["module"])
if cfg.get("data"):
    with open(os.path.join(repo, cfg["data"]), encoding="utf-8") as fh:
        data = json.load(fh)
else:
    data = None
if hasattr(mod, "WATERMARK"):
    mod.WATERMARK = True
res = None
if cfg["entry"] == "build_pdf":
    import inspect
    if data is None:
        data = mod.DATASETS["demo"]
    # 水印参数名在 13 个入口里并不统一（watermark / with_watermark）——探针自己适配，
    # 这正是重构要把「入口签名」收敛掉的证据。
    params = inspect.signature(mod.build_pdf).parameters
    kw = {}
    if "watermark" in params:
        kw["watermark"] = True
    elif "with_watermark" in params:
        kw["with_watermark"] = True
    ret = mod.build_pdf(data, out, **kw)
    if hasattr(ret, "save"):                  # 返回 Document 的变体（AE）
        ret.save(out, garbage=3, deflate=True); ret.close()
elif cfg["entry"] == "build":
    doc = mod.build(meta=None)
    doc.save(out, garbage=3, deflate=True); doc.close()
secs = time.perf_counter() - t0
import fitz
d = fitz.open(out)
text = "\n".join(p.get_text() for p in d)
fonts = sorted({f[3] for p in d for f in p.get_fonts()})
print(json.dumps({
    "pages": len(d),
    "size": [round(d[0].rect.width, 2), round(d[0].rect.height, 2)],
    "bytes": os.path.getsize(out),
    "text_sha256_16": hashlib.sha256(text.encode()).hexdigest()[:16],
    "text_chars": len(text),
    "fonts": fonts,
    "secs_render": round(secs, 2),
}, ensure_ascii=False))
"""


def static_probe(repo: Path, cfg: dict) -> dict:
    """静态模板：确认入口、pdf-lib 依赖与页面脚本就位（无后端可渲染）。"""
    root = repo / cfg["dir"]
    files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    missing = [f for f in ("index.html",) if not (root / f).is_file()]
    return {
        "pages": None,
        "size": None,
        "bytes": sum((root / f).stat().st_size for f in files),
        "files": len(files),
        "missing_entry": missing,
        "vendor_pdf_lib": any("pdf-lib" in f for f in files),
        "vendor_fontkit": any("fontkit" in f for f in files),
    }


def run_one(repo: Path, python: str, tid: str, cfg: dict, outdir: Path) -> dict:
    if cfg["kind"] == "static-web":
        return {"id": tid, "name": cfg["name"], "kind": cfg["kind"], **static_probe(repo, cfg)}
    out = outdir / f"{tid}.pdf"
    payload = json.dumps({**cfg, "repo": str(repo), "out": str(out)})
    t0 = time.perf_counter()
    proc = subprocess.run([python, "-c", _CHILD, payload], cwd=str(repo),
                          capture_output=True, text=True)
    wall = round(time.perf_counter() - t0, 2)
    if proc.returncode != 0:
        return {"id": tid, "name": cfg["name"], "kind": cfg["kind"],
                "error": (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["failed"],
                "secs_wall": wall}
    metrics = json.loads(proc.stdout.strip().splitlines()[-1])
    warn = [ln for ln in proc.stderr.splitlines() if "extra bytes" in ln or "MuPDF error" in ln]
    return {"id": tid, "name": cfg["name"], "kind": cfg["kind"],
            "pdf": out.relative_to(repo.parent).as_posix(), **metrics,
            "secs_wall": wall, "pdf_warnings": warn, "assets": os.path.getsize(out)}


def compare(base: dict, cur: dict) -> int:
    """逐模板比对基线；返回不一致数量。"""
    diffs = 0
    keys = ("pages", "size", "text_sha256_16", "text_chars", "fonts")
    for tid, before in base.items():
        after = cur.get(tid, {})
        if "error" in before or "error" in after:
            if before.get("error") != after.get("error"):
                print(f"[FAIL] {tid}: 错误状态变化 {before.get('error')} -> {after.get('error')}")
                diffs += 1
            continue
        for key in keys:
            if before.get(key) != after.get(key):
                print(f"[DIFF] {tid}.{key}: {before.get(key)!r} -> {after.get(key)!r}")
                diffs += 1
        for key in ("bytes", "assets"):
            b, a = before.get(key), after.get(key)
            if isinstance(b, int) and isinstance(a, int) and b != a:
                print(f"[note] {tid}.{key}: {b} -> {a} ({a - b:+d})")
    print(f"\n结论：{'PASS 基线一致' if diffs == 0 else f'{diffs} 处与基线不一致'}")
    return diffs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="bill-gen", help="被探测的仓库目录")
    ap.add_argument("--python", default=sys.executable, help="带 PyMuPDF 的解释器")
    ap.add_argument("--out", default="baseline.json", help="基线输出文件")
    ap.add_argument("--baseline", help="比对用基线文件")
    ap.add_argument("--compare", action="store_true", help="与 --baseline 比对")
    ap.add_argument("--only", nargs="*", help="只跑指定模板 id")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    outdir = Path(args.out).resolve().parent / "_render"
    outdir.mkdir(parents=True, exist_ok=True)
    ids = args.only or list(TEMPLATES)

    result = {}
    for tid in ids:
        cfg = TEMPLATES[tid]
        row = run_one(repo, args.python, tid, cfg, outdir)
        result[tid] = row
        if "error" in row:
            print(f"{tid:<16} ERROR {row['error']}")
        elif row["kind"] == "static-web":
            print(f"{tid:<16} static  files={row['files']} size={row['bytes']}")
        else:
            print(f"{tid:<16} {row['pages']}p {row['size']} {row['bytes']:>9}B "
                  f"{row['secs_render']:>5.2f}s sha={row['text_sha256_16']}"
                  + (f"  ⚠ {row['pdf_warnings'][0]}" if row.get("pdf_warnings") else ""))

    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n基线写入 {args.out}")

    if args.compare:
        if not args.baseline:
            print("--compare 需要 --baseline")
            return 2
        return 1 if compare(json.loads(Path(args.baseline).read_text(encoding="utf-8")), result) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
