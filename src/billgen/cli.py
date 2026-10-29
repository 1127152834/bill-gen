"""``billgen`` 命令行。

billgen list                       # 13 个模板一览
billgen render boa-personal -o out.pdf
billgen render --all --outdir _build
billgen synth render boa-personal --from result.json -o out.pdf
billgen parity record              # 记录基线
billgen parity verify              # 与基线比对（不一致则退出码 1）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import paths, registry
from .spec import RenderOptions

__all__ = ["main"]


def _cmd_list(args: argparse.Namespace) -> int:
    width = max(len(s.id) for s in registry.SPECS)
    print(f"{'id':<{width}}  {'kind':<12} {'adapter':<9} 名称")
    for spec in registry.SPECS:
        print(f"{spec.id:<{width}}  {spec.kind:<12} {spec.adapter:<9} {spec.display_name}")
    return 0


def _opts(args: argparse.Namespace, base: RenderOptions | None = None) -> RenderOptions:
    """以模板自己的默认选项为基底，只覆盖命令行显式给出的项。

    不这么做的话，富国的 ``fonts="declared"`` 会被全局默认值悄悄改成别的策略，
    产物从 95KB 变成 291KB（字体未子集化）——正是「重构不能改版式」要防的坑。
    """
    from dataclasses import replace

    base = base or RenderOptions()
    fonts = getattr(args, "fonts", None)
    return replace(
        base,
        watermark=not args.no_watermark,
        encrypt=False if args.no_encrypt else base.encrypt,
        page_dpi=args.dpi,
        font_policy=fonts or base.font_policy,
    )


def _render_one(spec_id: str, args: argparse.Namespace, out: Path) -> dict:
    spec = registry.get_spec(spec_id)
    renderer = registry.get_renderer(spec_id)
    if args.data:
        data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    else:
        data = renderer.default_data()
    result = renderer.render(data, _opts(args, spec.default_options))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(result.pdf)
    return {
        "template": spec_id,
        "pages": result.pages,
        "bytes": len(result.pdf),
        "elapsed_ms": result.elapsed_ms,
        "output": str(out),
    }


def _cmd_render(args: argparse.Namespace) -> int:
    if args.all:
        outdir = Path(args.outdir or "_build")
        rows = []
        for spec in registry.SPECS:
            if spec.kind == "static-web":
                continue
            try:
                rows.append(_render_one(spec.id, args, outdir / f"{spec.id}.pdf"))
            except Exception as exc:  # noqa: BLE001
                print(f"[FAIL] {spec.id}: {exc}", file=sys.stderr)
                return 1
        for row in rows:
            print(
                f"{row['template']:<18} {row['pages']}p {row['bytes']:>9}B "
                f"{row['elapsed_ms']:>5}ms -> {row['output']}"
            )
        return 0
    if not args.template:
        print("需要 --template 或 --all", file=sys.stderr)
        return 2
    out = Path(args.output or f"{args.template}.pdf")
    row = _render_one(args.template, args, out)
    print(json.dumps(row, ensure_ascii=False))
    return 0


def _cmd_synth_render(args: argparse.Namespace) -> int:
    """把 flow 的生成结果直接渲染成某个模板的 PDF。"""
    from dataclasses import replace

    from .synth import from_game_result, render

    spec = registry.get_spec(args.template)
    payload = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
    ledger = from_game_result(
        payload,
        account=args.account,
        cycle=args.cycle,
        bank_name=args.bank_name,
        watermark=not args.no_watermark,
        template=args.template,
    )
    opts = replace(
        spec.default_options,
        watermark=not args.no_watermark,
        encrypt=False if args.no_encrypt else spec.default_options.encrypt,
        page_dpi=args.dpi,
    )
    result = render(
        args.template, ledger, opts, brand=not args.no_brand, docinfo=not args.no_docinfo
    )
    out = Path(args.output or f"{args.template}.pdf")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(result.pdf)
    print(
        json.dumps(
            {
                "template": args.template,
                "account": ledger.account.kind,
                "period": [ledger.period.start.isoformat(), ledger.period.end.isoformat()],
                "rows": len(ledger.rows),
                "brand": not args.no_brand,
                "docinfo": not args.no_docinfo,
                "pages": result.pages,
                "bytes": len(result.pdf),
                "elapsed_ms": result.elapsed_ms,
                "output": str(out),
            },
            ensure_ascii=False,
        )
    )
    return 0


def _cmd_parity(args: argparse.Namespace) -> int:
    from .parity.baseline import compare_file, record

    baseline = Path(args.baseline or (Path(__file__).parent / "parity" / "baseline.json"))
    if args.action == "record":
        target = Path(args.out or baseline)
        payload = record(Path(args.repo or paths.ROOT))
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"基线已写入 {target}（{len(payload)} 条）")
        return 0
    return 0 if compare_file(baseline, Path(args.repo or paths.ROOT)) else 1


def _cmd_serve(args: argparse.Namespace) -> int:
    from .server.http import serve

    return serve(
        args.host,
        args.port,
        hunter=not args.no_hunter,
        hunter_port=args.hunter_port,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="billgen", description="合成账单模板生成器")
    parser.add_argument("--version", action="version", version=f"billgen {_version()}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出全部模板").set_defaults(func=_cmd_list)

    render = sub.add_parser("render", help="渲染一个或全部模板")
    render.add_argument("template", nargs="?", help="模板 id，例如 boa-personal")
    render.add_argument("--all", action="store_true", help="渲染全部可渲染模板")
    render.add_argument("--outdir", help="配合 --all 的输出目录")
    render.add_argument("-o", "--output", help="输出 PDF 路径")
    render.add_argument("--data", help="数据 JSON 路径（缺省用模板示例数据）")
    render.add_argument("--no-watermark", action="store_true")
    render.add_argument("--no-encrypt", action="store_true")
    render.add_argument("--dpi", type=int, default=110)
    render.add_argument(
        "--fonts", choices=["declared", "subset", "embed-all"],
        help="字体策略：declared=照原稿声明不嵌入（默认，最像真件但依赖本机字体）；"
             "subset=嵌入并子集化（文件小）；embed-all=整字体嵌入（最稳，文件大）",
    )
    render.set_defaults(func=_cmd_render)

    synth = sub.add_parser("synth", help="由生成结果（flow）渲染账单 PDF")
    synth_sub = synth.add_subparsers(dest="synth_cmd", required=True)
    synth_render = synth_sub.add_parser("render", help="账本 → 指定模板 PDF")
    synth_render.add_argument("template", help="模板 id，例如 boa-personal")
    synth_render.add_argument(
        "--from", dest="from_file", required=True, help="flow 的 GenerationResult JSON 路径"
    )
    synth_render.add_argument(
        "--account",
        choices=["checking", "business_checking", "credit"],
        help="选择账户（缺省取结果里的第一个）",
    )
    synth_render.add_argument("--cycle", type=int, default=-1, help="结算期下标，-1 表示最后一期")
    synth_render.add_argument("--bank-name", dest="bank_name", help="覆盖银行显示名")
    synth_render.add_argument("-o", "--output", help="输出 PDF 路径")
    synth_render.add_argument("--no-watermark", action="store_true")
    synth_render.add_argument(
        "--no-brand", action="store_true", help="保留模板原有的真实行标（默认替换成账本自己的行标）"
    )
    synth_render.add_argument(
        "--no-docinfo",
        action="store_true",
        help="保留模板自带的 Producer/时间戳（默认改成真实账单引擎的痕迹）",
    )
    synth_render.add_argument("--no-encrypt", action="store_true")
    synth_render.add_argument("--dpi", type=int, default=110)
    synth_render.add_argument(
        "--fonts", choices=["declared", "subset", "embed-all"],
        help="字体策略，见 billgen render --help",
    )
    synth_render.set_defaults(func=_cmd_synth_render)

    parity = sub.add_parser("parity", help="基线记录与比对")
    parity.add_argument("action", choices=["record", "verify"])
    parity.add_argument("--baseline", help="基线文件（默认包内 parity/baseline.json）")
    parity.add_argument("--out", help="record 的输出路径")
    parity.add_argument("--repo", help="被探测仓库根（默认当前源码树）")
    parity.set_defaults(func=_cmd_parity)

    mcp = sub.add_parser("mcp", help="启动 MCP 服务（stdio），供 LangGraph / Agent 调渲染")
    mcp.add_argument("--out", help="产物目录（默认 $BILLGEN_MCP_OUT 或系统临时目录）")
    mcp.add_argument("--transport", default="stdio", choices=["stdio", "sse", "streamable-http"])
    mcp.set_defaults(func=_cmd_mcp)

    serve = sub.add_parser("serve", help="启动单进程 Web 编辑器与统一 API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8800)
    serve.add_argument("--no-hunter", action="store_true", help="不拉起账单猎手（只跑渲染工作台）")
    serve.add_argument("--hunter-port", type=int, default=8797, help="猎手子进程监听的本机端口")
    serve.set_defaults(func=_cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


def _cmd_mcp(args) -> int:
    from .mcp.server import main as mcp_main

    argv = ["--transport", args.transport]
    if args.out:
        argv += ["--out", args.out]
    return mcp_main(argv)


def _version() -> str:
    from . import __version__

    return __version__


if __name__ == "__main__":
    raise SystemExit(main())
