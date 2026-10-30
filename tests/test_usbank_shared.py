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
    # 2026-10-30 重采：页码由「物理页数」改为「账单页总数」（参考件 4 页只印 of 2），
    # 披露页与尾空白页不再补页码；水印改用页面自带的嵌入字体（CID 编码）画，
    # 不再往账单里塞一个非嵌入的 Helvetica-Bold。
    "us-default": (3, "ca9bdd585c2ffc096f8e93061ca40cedab155e2db60066479919985eb42280b3", "e59828eba9eabf5e2968bad7657f5cb510d48b5d4eb07963461e858583c7c8e4"),
    "us-empty": (3, "3349a76319fbe1ebd907aa705b68a5fc8f13232debfeaf71355143f3bb760dda", "cf10d21cfabc7505f1f084496471b7631c582185ae2c9027e4a74562194eac40"),
    "us-multipage": (8, "1e52a148153c9d8f439ee6ad1032787325a751cb1bd9abf4ac8659ddcfadbf39", "b4c2176c842e7d77eeba704cb120298d9e97d7d6c37aca0ddb4cfec735a066c6"),
    "us-replay": (3, "7faf4e493a0ba90cd77321cf2694a9ab6dc4ff89b4b3a472722587233d02d71c", "5da0b054a1917641c886b4b6361c8b8bd476bddae734e330d9eaba55459b2e6b"),
    "usbiz-default": (2, "f43bbad9f9dad3819ff9fc67ab324fe500289b77fdff6c4b75ac40ab36fc7787", "4814b5bf1e485fa94096cf68815fdcf917f206eb8e0997014c0b934355be0976"),
    "usbiz-empty": (2, "4e105449f2baac983a17aecad3c777791bc41b89c7d43d547179d4f7ff078ca0", "069795b33bb59565f605eab618a0a498bda278baaedd745897fa1e0e20a8e5fc"),
    # 2026-10-30 重采：续页客户三行改在 61.5/70.8/80 直接写（先 redaction 抹掉第 1 页那份），
    # 段头与列头不再压在第一行流水上。
    "usbiz-multipage": (8, "fc6683d43a3de4e8ae267864abc1664ebb594a6d00590efccc05d8045d63b4cc", "3ceb3e5765e12c3fbba68ed59137edfde6bfc397df472fbe0f2a82a37c805c96"),
    "usbiz-replay": (2, "67eeac9688dab5ceae64a19b48c056e8d2026930da9281eba1906bdac8617a9a", "4b703451b4bdd3d48321b316b18b01787101137c49c3aa31b2d66b8cbebe11a6"),
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
