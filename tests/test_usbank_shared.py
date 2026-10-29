"""P4 regression contract captured from the original USBank generators.

Goldens hash page text and RGB pixels at 72 dpi (PyMuPDF 1.26.6), not PDF
bytes: metadata timestamps and encryption are deliberately non-deterministic.
Original implementation copies and generated PDFs stay outside the package.

基线于 2026-10-30 重录（页码补打 + 扫描颗粒层）——重录原因：每页都印 Page k of n，并在 pdfpost 叠极淡扫描颗粒；2026-10-29 重录：示例数据（editor_*/data_*.json、replay_*.json）曾被运行期写脏，
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
    "us-default": (3, "bbf186a0a85db104440686941ad4d10c12adfe502a06b23bc0e197f108119f8d", "1bb83dc3a801841437bb84e64a8eedb7eab70000e1ef17eca4d0c90d7c45d0dc"),
    "us-empty": (3, "482a853c21c5ca115f8406b65db27af951e2959692504661f541ef5ceb788f8e", "fa6c8f53956cf1355b2e23872d04967c7e73da6797c7d9622c64ecc773b7e09e"),
    "us-multipage": (8, "d1c29afd4d191ed7648a012a629077230978aa83f1fbe6fb6c2a15495eb99439", "4440074fb5e769feb53a8f5991007ee3e73e5625ef3ed48a881771961293a4c8"),
    "us-replay": (3, "72a036fbfd4533a81924c5a5140a73dbc60ad2cedd7af6d06603bf9485b5e792", "7cb785aeb8c727de3198a00929159d0d322ef9b611b9d41b1ab04a268a19aad4"),
    "usbiz-default": (2, "e309100a37d13b339d51385d82097609c382b2c93fc7c27e53a045166e01ef9a", "d3e83d0497ccfdf65764b9532a57e8a414d310712f436460bfa33b73367ced2b"),
    "usbiz-empty": (2, "8eb58044b396509a23400df9dc90cd97bec30767dc8bbe6c3f2e52984bed65f2", "116078d9c603beb3abe19ecda2e8047acd64459e9bdfd61d2a17fa2d9798abad"),
    "usbiz-multipage": (8, "30ec590b44e57ab3bcf761be5b26e9af554ddb06b23cd0ac9377e3d50c2cea12", "76091b5e9021b702eb2efa1ae65e01a45b491562a7bd5d519da4de47f310d033"),
    "usbiz-replay": (2, "16b7a30c6cb76757feb17f4e29a307ef4df42aace8378683246b862fe4297952", "af8775c9aea6d3fb4fbd0fe7bb5731d60ad98b103ee278cfb49e95156517a95c"),
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
