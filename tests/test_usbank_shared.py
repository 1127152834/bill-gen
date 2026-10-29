"""P4 regression contract captured from the original USBank generators.

Goldens hash page text and RGB pixels at 72 dpi (PyMuPDF 1.26.6), not PDF
bytes: metadata timestamps and encryption are deliberately non-deterministic.
Original implementation copies and generated PDFs stay outside the package.

基线于 2026-10-29 重录：示例数据（editor_*/data_*.json、replay_*.json）曾被运行期写脏，
恢复成 SAMPLE 样本后文本层与页数与原基线一致，像素摘要因采样环境重录。
"""
import copy
import hashlib
import importlib
import inspect
import json
import platform
import xml.etree.ElementTree as ET
from pathlib import Path

import fitz
import pytest

from billgen.themes.usbank import shared

THEME = Path(__file__).resolve().parents[1] / "src/billgen/themes/usbank"
# 2026-10-29 像素基线重采：底稿子集字体把 `Jan` 的 'J' 留成空壳（有码位、无轮廓），
# 期间换成 `Jan 05, 2027` 后渲染缺字（肉眼 `an 05`）。shared.patch_outlines 从系统
# Arial 补轮廓后像素变了、文本层不变 —— 文本摘要未动，仅四个 us 像素摘要更新。
GOLDENS = {
    "us-default": (3, "803e4f0331f9bbf399bee0a9bd9bf168e9a766922adef9c2843a4c03f3ac4270", "d201ea6377e4468a9b1f265c43c37b27a266090da6180222ebb391f903c0d34d"),
    "us-empty": (3, "4d31768a47b4616b97e9ca6036d67b65936781fc8115800cab02e03cb8ddd3f1", "bcca81183fc6492d57bdd763d13d090f3c823baa92319ac251e454e31a23db7a"),
    "us-multipage": (8, "0ef8235324c82430ef2424997bd2eb7fc8809e0a5d361ef1fafe6c8192567b7b", "51a43513318e964b15e86a34a883a7df5446fa32fb2d00556e1d1c0de93410a8"),
    "us-replay": (3, "a1e7f5608039fb937a525a885b551db30c1577e942aac88abca3b8ceb6b9e374", "e4b6f0df3742b723dfdfafe7619f78d4a2ee79f44a9bc1be7572cd89179f142c"),
    "usbiz-default": (2, "ababae066ac23cf695ac32d45327b5aea7ba0285027ecfa46bfbfc33c9cd4600", "2ff36308edfafa425880d219f210153494b2b82f816b5168de0953a4b7e9b42d"),
    "usbiz-empty": (2, "b9955a234a9d1f6af73026c2a46665cfeaf1cb4b8305cdcf424b0eb96468d5c3", "09570e4e88d27ab5da2322a52f84827597b6779a57fb86821e57d60afdf00ba9"),
    "usbiz-multipage": (8, "c2b7145065ef3eabe27d0ac022b7d94514b9314d5fd9fe013cf3d28202cd7c2f", "3e0030d3e9c8d9e22911f4d826951f8af089eb74560917c852cf59b5a3073b8b"),
    "usbiz-replay": (2, "5ad30010695540a649be5d7f9863605d19e153c7f3689744cd4242bb58c6daa4", "4908f3b891da246dfc33edd72d759cccd1bc37d380795ab5a1a1c2ce6cb2408c"),
}


def _data(theme, case):
    source = "editor_us/data_us.json" if theme == "us" else "editor_biz/data_biz.json"
    if case == "replay":
        source = "replay_us.json" if theme == "us" else "replay_biz.json"
    data = json.loads((THEME / source).read_text())
    if case == "empty":
        data["deposits"] = []
        data["withdrawals"] = []
    elif case == "multipage":
        row = data["deposits"][0]
        data["deposits"] = [
            dict(row, date=f"Jan {i % 28 + 1}", detail=f"SAMPLE TRANSACTION {i:03}",
                 extra=["SAMPLE FIRST EXTRA|SECOND COLUMN", "SAMPLE SECOND EXTRA"],
                 amount=f"{i + 1}.25") for i in range(70)
        ]
    return data


def _digest(values):
    return hashlib.sha256("".join(values).encode()).hexdigest()


@pytest.mark.parametrize("theme,module", [("us", "generate_us"), ("usbiz", "generate_biz")])
@pytest.mark.parametrize("case", ["default", "empty", "multipage", "replay"])
def test_pdf_matches_original_text_and_pixels(theme, module, case, tmp_path):
    # Historical P4 Mac captures stay untouched; portable profiles are separately
    # asserted by test_render_regression and the strict CI tool on Linux.
    if platform.system() != "Darwin" or fitz.VersionBind != "1.26.6":
        pytest.skip("historical macOS PyMuPDF 1.26.6 baseline; see portable fixed profiles")
    generate = importlib.import_module(f"billgen.themes.usbank.{module}")
    data = _data(theme, case)
    original = copy.deepcopy(data)
    output = tmp_path / f"{theme}-{case}.pdf"
    assert generate.build_pdf(data, str(output), watermark=case == "default") == str(output)
    assert data == original
    with fitz.open(output) as doc:
        text = [hashlib.sha256(p.get_text().encode()).hexdigest() for p in doc]
        pixels = [hashlib.sha256(p.get_pixmap(alpha=False).samples).hexdigest() for p in doc]
        assert (len(doc), _digest(text), _digest(pixels)) == GOLDENS[f"{theme}-{case}"]


@pytest.mark.parametrize("value,expected", [("$ 1,234.56", 123456), ("25.01-", -2501), ("bad", 0), (None, 0), ("0.019", 1)])
def test_money_legacy_semantics(value, expected):
    assert shared._cents(value) == expected
    assert shared._cents(shared._fmt_amt(expected)) == expected


def test_cmap_blocks_and_ranges():
    cmap = """2 beginbfchar <0001><0041> <0002><00660069> endbfchar
        2 beginbfrange <0003><0005><0042>
        <0006><0007>[<0058><0059>] endbfrange"""
    assert shared.parse_cmap(cmap) == {1: "A", 2: "fi", 3: "B", 4: "C", 5: "D", 6: "X", 7: "Y"}
    assert shared.parse_cmap("") == {}


def test_binary_pdf_string_escaping():
    assert shared.esc(b"()\\\r\n\b\f\t\x00\xffA") == b"\\(\\)\\\\\\r\\n\\b\\f\\t\\000\\377A"


def test_address_fallbacks():
    assert shared._zip_of({}) == "00000"
    assert shared._street_of({}) == ""
    data = {"customer_lines": ["SAMPLE CUSTOMER", "123 SAMPLE ROAD", "Austin TX 78701-1234"]}
    assert shared._zip_of(data) == "78701-1234"
    assert shared._street_of(data) == "123 SAMPLE ROAD"


def test_generators_share_primitives_but_own_font_state():
    from billgen.themes.usbank import generate_biz, generate_us
    for module in (generate_us, generate_biz):
        assert module.UsFont is shared.UsFont
        assert module.parse_cmap is shared.parse_cmap
        assert str(inspect.signature(module.build_pdf)) == "(d, out_path, watermark=True)"
        assert module.chstream is importlib.import_module("billgen.themes.usbank.chstream")
        assert module.BC2D is importlib.import_module("billgen.themes.usbank.barcode_us")
        assert module.IMB is importlib.import_module("billgen.themes.usbank.imb_us")
    assert generate_us.FONTS is not generate_biz.FONTS
    assert not ({id(v) for v in generate_us.FONTS.values()} & {id(v) for v in generate_biz.FONTS.values()})
    assert generate_us._font_pg(99) == 2
    assert generate_biz._font_pg(99) == 0


@pytest.mark.parametrize("module", ["generate_us", "generate_biz"])
def test_wrong_template_and_encryption(module, tmp_path):
    generate = importlib.import_module(f"billgen.themes.usbank.{module}")
    with pytest.raises(ValueError):
        generate.build_pdf({"template": "other"}, str(tmp_path / "wrong.pdf"))
    theme = "us" if module == "generate_us" else "usbiz"
    data = _data(theme, "empty")
    data["encrypt"] = True
    output = tmp_path / "encrypted.pdf"
    generate.build_pdf(data, str(output), watermark=False)
    with fitz.open(output) as doc:
        assert doc.metadata["encryption"].startswith("Standard V5")
        assert doc.permissions & fitz.PDF_PERM_PRINT


@pytest.mark.parametrize("module", ["generate_us", "generate_biz"])
def test_metadata_escaping_defaults_and_fixed_clock(module, monkeypatch):
    generate = importlib.import_module(f"billgen.themes.usbank.{module}")
    monkeypatch.setattr(generate, "_now_pdf_date", lambda: "D:20270131010203-05'00'")
    with fitz.open() as doc:
        generate.write_metadata(doc, {"title": 'A & B < C "D"', "x_desc": 'A & B < C "D"'})
        assert doc.metadata["title"] == 'A & B < C "D"'
        assert doc.metadata["creationDate"] == "D:20270131010203-05'00'"
        assert doc.metadata["modDate"] == doc.metadata["creationDate"]
        assert doc.metadata["creator"] == generate.META_DEFAULTS["creator"]
        xml = doc.get_xml_metadata()
        ET.fromstring(xml)
        assert "2027-01-31T01:02:03-05:00" in xml
        assert 'A &amp; B &lt; C "D"' in xml


def test_font_commit_extends_bfrange_only_cmap_and_widths():
    class Document:
        def __init__(self):
            self.stream = b"1 beginbfrange <0001><0001><0041> endbfrange\nendcmap"
            self.obj = "<< /W [1 [500]] >>"
        def xref_stream(self, xref):
            return self.stream
        def update_stream(self, xref, value, compress=False):
            self.stream = value
        def xref_object(self, xref):
            return self.obj
        def update_object(self, xref, value):
            self.obj = value
    font = shared.UsFont.__new__(shared.UsFont)
    font.cmap = {"A": 1, "B": 2}
    font.widths = {1: 500, 2: 610}
    font.mapped_cids = {1}
    font.new_cids = set()
    font.tou, font.df = 10, 11
    assert font.cid("A") == 1
    assert font.cid("B") == 2
    assert font.cid("missing") is None
    assert font.adv("missing") == 500
    assert font.new_cids == {2}
    doc = Document()
    font.commit(doc)
    assert shared.parse_cmap(doc.stream.decode()) == {1: "A", 2: "B"}
    assert "2 [610]" in doc.obj
