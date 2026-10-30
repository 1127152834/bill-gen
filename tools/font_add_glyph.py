#!/usr/bin/env python3
"""给主题子集字体补一个缺失的字形（当前实现 ``#``）。

背景：``src/billgen/themes/*/fonts`` 下的字体都是从原始对账单 PDF 里抽出来的
**子集**，只含原稿出现过的字符。模板一旦画原稿里没有的字符（例如
``H-E-B #0611 AUSTIN TX`` 的 ``#``），PyMuPDF 只能落 code 0/.notdef，
产物里就是一个豆腐块，文本层还会被提取成 ``\\x00``。

``#`` 是纯几何图形（两根竖杆 + 两根横杆），可以直接按字体设计单位画出来，
比从别的字族借字形更接近原稿度量，也不用额外嵌一套字体。

用法::

    python tools/font_add_glyph.py src/billgen/themes/capitalone/fonts/MyFontBold.ttf
    python tools/font_add_glyph.py --char '#' --advance 1200 --stem 180 --height 1460 FONT
    python tools/font_add_glyph.py --check FONT     # 只报告字形表里有没有，不改文件

设计单位按 ``head.unitsPerEm`` 归一：参数都按 1000/em 给，脚本自动换算。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont


def _rect(pen, x0, y0, x1, y1) -> None:
    pen.moveTo((x0, y0))
    pen.lineTo((x0, y1))
    pen.lineTo((x1, y1))
    pen.lineTo((x1, y0))
    pen.closePath()


def build_hash_glyph(upem: int, advance: int, stem: int, height: int) -> tuple[object, int]:
    """画一个 ``#``：竖杆略内收、横杆在光学中心偏上（视觉居中）。"""
    s = upem / 1000.0
    adv = round(advance * s)
    st = round(stem * s)
    h = round(height * s)
    left = round(adv * 0.13)
    right = adv - left
    top_bar = round(h * 0.62)
    bottom_bar = round(h * 0.28)
    pen = TTGlyphPen(None)
    # 两根竖杆（上下不出头，和 Benton Sans 一致）
    _rect(pen, left, 0, left + st, h)
    _rect(pen, right - st, 0, right, h)
    # 两根横杆
    _rect(pen, 0, bottom_bar, adv, bottom_bar + st)
    _rect(pen, 0, top_bar, adv, top_bar + st)
    return pen.glyph(), adv


def add_hash(path: Path, advance: int, stem: int, height: int, dry_run: bool) -> int:
    font = TTFont(path)
    upem = font["head"].unitsPerEm
    # 先把要改的表全部解压：maxp.numGlyphs 一旦变大，hmtx 会按新字形数
    # 解析旧数据而报 "not enough 'hmtx' table data"。
    glyf = font["glyf"]
    hmtx = font["hmtx"]
    order0 = list(font.getGlyphOrder())
    cmap_tables = [t for t in font["cmap"].tables if t.isUnicode()]
    have = any(0x23 in t.cmap for t in cmap_tables)
    if have:
        print(f"{path.name}: 已有 '#' 字形，跳过")
        return 0
    if "glyf" not in font:
        print(f"{path.name}: 不是 glyf 轮廓字体，无法补几何字形", file=sys.stderr)
        return 1
    glyph, adv = build_hash_glyph(upem, advance, stem, height)
    name = "numbersign"
    while name in order0:
        name += ".alt"
    order = order0 + [name]
    font.setGlyphOrder(order)
    glyf.glyphs[name] = glyph
    glyf.glyphOrder = order
    hmtx.metrics[name] = (adv, 0)
    for t in cmap_tables:
        t.cmap[0x23] = name
    font["maxp"].numGlyphs = len(order)
    if dry_run:
        print(f"{path.name}: 可补 '#'（advance={adv} upem={upem}），未写入")
        return 0
    font.save(path)
    print(f"{path.name}: 已补 '#'（advance={adv} upem={upem}，字形名 {name}）")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="给子集字体补一个缺失字形")
    ap.add_argument("fonts", nargs="+", type=Path)
    ap.add_argument("--char", default="#", choices=["#"], help="要补的字符（当前只支持 #）")
    ap.add_argument("--advance", type=int, default=600, help="字宽（/1000 em）")
    ap.add_argument("--stem", type=int, default=88, help="杆宽（/1000 em）")
    ap.add_argument("--height", type=int, default=713, help="字高（/1000 em）")
    ap.add_argument("--check", action="store_true", help="只检查，不写入")
    args = ap.parse_args(argv)
    rc = 0
    for p in args.fonts:
        rc |= add_hash(p, args.advance, args.stem, args.height, args.check)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
