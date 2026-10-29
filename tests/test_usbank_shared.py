"""P4 regression contract captured from the original USBank generators.

Goldens hash page text and RGB pixels at 72 dpi (PyMuPDF 1.26.6), not PDF
bytes: metadata timestamps and encryption are deliberately non-deterministic.
Original implementation copies and generated PDFs stay outside the package.
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
GOLDENS = {
    "us-default": (3, "06f63260773cd6e9b05a49ae185a5fd0323a1cd1027e5a78cdd14ea9bd888248", "06de2f707aeb98a300f82306d4d03b6a13bb38587b9c9996f5b2811b00ac133a"),
    "us-empty": (3, "84e292fbace48355495bce8304f0f3997e6573de8d881e757ae200252352d819", "a9c4352adae39a3d6fd6ad48ad133a4e9ed2bfeaf5851a89e11d27bf3d993919"),
    "us-multipage": (8, "c6db011e1d70c77c8477572a428f2d33bb9aa2c1b7be99bfff632343a0dcdc46", "6f7ffacb4e56d94899d1793512e10b7f6b4c49ed0030265ae556f6b89a2a01c5"),
    "us-replay": (3, "a45d0cb9274409dfeafecf794c60e86ac45c3c40e4edd9852dea82dbfa6607bf", "474bb398f2256d9fdee240b2f98df88b2f0ad0ffcf72ee0da434ead8f2674379"),
    "usbiz-default": (2, "46e1c32f56c15771bff999e6a23a4320986232a480a9577c6be426f2784f185d", "0649366a8865cfb3b54c0430db51bb3b211e828888a0d0021ee5009f7942121b"),
    "usbiz-empty": (2, "5490cf33a2c19493c72d6db37c46d01cdf6acde0a944f71d7082c180dbfb3777", "951629ddd4799ea88ff46bd60153f3091f1cbd9d92dd00e94cf77d668623463e"),
    "usbiz-multipage": (8, "a21ffed3c1c6a6cde7fb41a982686fea9c3aa0e4fb5354bddf983d90de01eb0d", "a6fa96004db4df6949ea5c1314fbb03e4e87ef24b597c3ec6f521b3b9a59c4c0"),
    "usbiz-replay": (2, "841f32793a77c8ae0e6f583e4036514bcc5afe3ef15f529fa662f4f379e4fb74", "40a9009746fbe30bb6b964e1923a7bf59139cc305f4f4b029a03f9963df637f1"),
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
