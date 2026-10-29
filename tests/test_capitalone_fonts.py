"""C1 font-program optimization preserves the historical PDF contract."""
from copy import deepcopy
from unittest.mock import patch

import fitz
import pytest

from billgen.themes.capitalone import c1_generate as c1


def _case(name):
    data = deepcopy(c1.DATA)
    if name != "default":
        data.update(customer_name="张三 山田 한글",
                    address_lines=["张三 山田 한글", "北京市朝阳区", "東京都 日本語", "서울 한국어"])
        data["transactions"][0].update(desc="中文 日本語 한국어", memo="備考 测试 메모")
    if name == "multipage":
        data["transactions"] = [dict(data["transactions"][0]) for _ in range(65)]
    return data


def _pdf(data):
    with c1.build(data=data) as doc:
        return doc.tobytes(garbage=4, deflate=True)


def _fonts(doc):
    return [[f[1:] for f in page.get_fonts()] for page in doc]


@pytest.mark.parametrize("name", ["default", "cjk", "multipage"])
def test_subset_preserves_text_glyphs_pixels_fonts_and_metadata(name):
    if not c1.FONT_CJK_BASE:
        pytest.skip("CJK fallback font is absent")
    data = _case(name)
    original_data = deepcopy(data)
    with patch.object(c1, "_subset_cjk_font"):
        golden = _pdf(data)
    optimized = _pdf(data)
    assert data == original_data
    with fitz.open(stream=golden) as before, fitz.open(stream=optimized) as after:
        assert len(before) == len(after)
        assert before.metadata == after.metadata
        assert before.get_xml_metadata() == after.get_xml_metadata()
        assert _fonts(before) == _fonts(after)
        for a, b in zip(before, after, strict=True):
            assert a.get_text("rawdict") == b.get_text("rawdict")
            assert a.get_texttrace() == b.get_texttrace()
            for scale in (1, 2):
                matrix = fitz.Matrix(scale, scale)
                assert a.get_pixmap(matrix=matrix).samples == b.get_pixmap(matrix=matrix).samples
            # Nothing in the drawing instructions is rewritten by font reduction.
            assert [before.xref_stream(x) for x in a.get_contents()] == [
                after.xref_stream(x) for x in b.get_contents()]
        full_fonts = {f[0] for p in before for f in p.get_fonts() if f[4] == "cjk"}
        small_fonts = {f[0] for p in after for f in p.get_fonts() if f[4] == "cjk"}
        full_size = sum(len(before.extract_font(x)[-1]) for x in full_fonts)
        small_size = sum(len(after.extract_font(x)[-1]) for x in small_fonts)
        if not full_fonts:
            # 这条用例不含 CJK：字体按需插入，两张 PDF 都不该带 CJK 字体，
            # 也就没有可缩的字体程序（早期是每页无条件嵌 CJK，才会恒有收益）。
            assert small_fonts == set()
            assert len(optimized) == len(golden)
        else:
            assert small_size < full_size
            assert len(optimized) < len(golden)


def test_subset_failure_keeps_original_document():
    if not c1.FONT_CJK_BASE:
        pytest.skip("CJK fallback font is absent")
    data = _case("cjk")
    with patch.object(c1, "_subset_cjk_font"):
        golden = _pdf(data)
    with (
        patch("fontTools.subset.Subsetter.subset", side_effect=ValueError("subset fixture")),
        pytest.warns(RuntimeWarning, match="fallback font kept in full"),
    ):
        result = _pdf(data)
    with fitz.open(stream=golden) as a, fitz.open(stream=result) as b:
        assert [p.get_text("rawdict") for p in a] == [p.get_text("rawdict") for p in b]
        assert _fonts(a) == _fonts(b)
        assert [p.get_pixmap().samples for p in a] == [p.get_pixmap().samples for p in b]
