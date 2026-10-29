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
    "us-default": (3, "de98766582c5dae3d05289cf39659a2d2b4dbda7f43aaaf766ea8de323bda621", "766a0ab278ae6c3bc5aceef8dd8893d981897d0eeda74b4fd7f0c51df6e6984e"),
    "us-empty": (3, "4c9991a2b3df22b70c2ef3e87ece684415ea036abcee885ad4ff3ce7c8ebe8ae", "77ebb6527f535008bb8de020a2d48fb116fd7e9e08f4d101b1e0dc5480fc2748"),
    "us-multipage": (8, "467ac9b5a5a60ea3bde1ac3863d8ccf937474073576c4fb08b4424384cee3322", "e09000fdd3914f65dc74bf9efd9c2c63bf67f28bd4e4eb4d09011370828e247c"),
    "us-replay": (3, "3ee229942947bd30e59146409dfb5c89b8955a9efcaa8fd1a7fda8a20b406374", "153a3e2933df17c36537bcda4a9f9b539e32b149711e3bc3066986a044e47d1a"),
    "usbiz-default": (2, "40059874355e9db9aa01ba3a741952e0bbf0e2f1082ae6d54662552051eb49e0", "8ac74649d8debe521a2e936567b750392af2b7fd2b4383d3be5ed41c0a32b552"),
    "usbiz-empty": (2, "8eb58044b396509a23400df9dc90cd97bec30767dc8bbe6c3f2e52984bed65f2", "116078d9c603beb3abe19ecda2e8047acd64459e9bdfd61d2a17fa2d9798abad"),
    "usbiz-multipage": (8, "30ec590b44e57ab3bcf761be5b26e9af554ddb06b23cd0ac9377e3d50c2cea12", "76091b5e9021b702eb2efa1ae65e01a45b491562a7bd5d519da4de47f310d033"),
    "usbiz-replay": (2, "c475730b0f4e0a042192fbc2c4fe2f7b89e12cf3b8b4b8aa7c30d597f66003db", "0913ecb8c8632238da72251157639840c9cd95f03018a306d9f4e03bbec0e6bb"),
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
