"""``billgen`` 命令行。

    billgen list                       # 13 个模板一览
    billgen render boa-personal -o out.pdf
    billgen render --all --outdir _build
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
    return replace(
        base,
        watermark=not args.no_watermark,
        encrypt=not args.no_encrypt,
        page_dpi=args.dpi,
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
            print(f"{row['template']:<18} {row['pages']}p {row['bytes']:>9}B "
                  f"{row['elapsed_ms']:>5}ms -> {row['output']}")
        return 0
    if not args.template:
        print("需要 --template 或 --all", file=sys.stderr)
        return 2
    out = Path(args.output or f"{args.template}.pdf")
    row = _render_one(args.template, args, out)
    print(json.dumps(row, ensure_ascii=False))
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
    render.set_defaults(func=_cmd_render)

    parity = sub.add_parser("parity", help="基线记录与比对")
    parity.add_argument("action", choices=["record", "verify"])
    parity.add_argument("--baseline", help="基线文件（默认包内 parity/baseline.json）")
    parity.add_argument("--out", help="record 的输出路径")
    parity.add_argument("--repo", help="被探测仓库根（默认当前源码树）")
    parity.set_defaults(func=_cmd_parity)

    args = parser.parse_args(argv)
    return args.func(args)


def _version() -> str:
    from . import __version__

    return __version__


if __name__ == "__main__":
    raise SystemExit(main())
