"""字体卫生回归：主题子集字体不能把模板要画的字符漏掉。

背景：``src/billgen/themes/*/fonts`` 是从原始对账单 PDF 抽出来的**子集**，
字符集不全。模板一旦画原稿里没有的字，PyMuPDF 落 .notdef，产物是豆腐块，
文本层还会被提取成 ``\\x00``。amex 的 ``H-E-B #0611 AUSTIN TX`` 就是活例子。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from font_add_glyph import build_hash_glyph  # noqa: E402

AMEX = ROOT / "src/billgen/themes/amex/fonts"


def _cmap(path: Path) -> set[int]:
    from fontTools.ttLib import TTFont

    font = TTFont(path)
    chars: set[int] = set()
    for table in font["cmap"].tables:
        if table.isUnicode():
            chars |= set(table.cmap)
    return chars


@pytest.mark.parametrize("name", ["BentonSansAE-Regular.ttf", "BentonSansAE-Bold.ttf"])
def test_amex_fonts_have_numbersign(name: str) -> None:
    assert ord("#") in _cmap(AMEX / name), f"{name} 缺 '#'，账单里的 H-E-B #0611 会变豆腐块"


def test_hash_glyph_geometry_is_sane() -> None:
    glyph, advance = build_hash_glyph(upem=2048, advance=600, stem=88, height=713)
    assert glyph.numberOfContours == 4          # 两根竖杆 + 两根横杆
    assert 1100 <= advance <= 1300              # 0.6em @ 2048upem


def test_drawn_hash_extracts_as_hash_not_nul() -> None:
    """用足字体画一行带 '#' 的字，文本层必须取回 '#'，而不是 \\x00。"""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 60), "H-E-B #0611 AUSTIN TX",
                     fontname="AER", fontfile=str(AMEX / "BentonSansAE-Regular.ttf"),
                     fontsize=9)
    text = page.get_text()
    doc.close()
    assert "#0611" in text, text
    assert "\x00" not in text
