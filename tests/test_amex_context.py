"""AE request-state isolation and public API compatibility regression tests."""

import ast
import copy
import hashlib
import inspect
from pathlib import Path

import fitz
import pytest

from billgen.themes.amex import generate_sample_statement as ae


@pytest.fixture(autouse=True)
def fixed_creation_date(monkeypatch):
    monkeypatch.setattr(ae, "statement_date_creation", lambda *_: "D:20261101184007Z")


def _data(name="ALEX EXAMPLE", repeat=1):
    data = copy.deepcopy(ae.DATASETS["demo"])
    data["customer_name"] = name
    data["transactions"] *= repeat
    return data


def _text(document):
    return "\n".join(page.get_text() for page in document)


def test_public_signatures_are_unchanged():
    assert str(inspect.signature(ae.build_pdf)) == (
        "(data, dataset_label='custom', with_watermark=True, metadata=None, xmp=None)"
    )
    assert str(inspect.signature(ae.save_pdf)) == "(document, path, encrypt=True, **kw)"
    assert str(inspect.signature(ae.pdf_bytes)) == "(document, encrypt=True, **kw)"


def test_no_request_state_in_module_globals():
    tree = ast.parse(Path(ae.__file__).read_text())
    assert not any(isinstance(node, ast.Global) for node in ast.walk(tree))
    assert not hasattr(ae, "DATA")
    assert not hasattr(ae, "doc")


@pytest.mark.parametrize("repeat", [0, 1, 12, 25])
def test_caller_data_and_options_are_unchanged(repeat):
    data = _data(repeat=repeat)
    if data["transactions"]:
        data["transactions"][0]["desc_lines"] = ["Explicit first line", "Second line"]
    metadata = {"title": " Custom title ", "creationDate": "2026-11-01 18:12:34"}
    xmp = {"dc:title": " XMP title ", "xmp:CreateDate": "2026-11-02T18:00:00"}
    before = copy.deepcopy((data, metadata, xmp, ae.DATASETS))
    with ae.build_pdf(data, metadata=metadata, xmp=xmp) as document:
        assert document.metadata["title"] == "Custom title"
        assert "XMP title" in document.get_xml_metadata()
    assert (data, metadata, xmp, ae.DATASETS) == before


def test_sequential_requests_keep_live_documents_independent():
    with ae.build_pdf(_data("FIRST CUSTOMER")) as first:
        first_text = _text(first)
        with ae.build_pdf(_data("SECOND CUSTOMER", repeat=12)) as second:
            assert "SECOND CUSTOMER" in _text(second)
            assert "FIRST CUSTOMER" not in _text(second)
            assert _text(first) == first_text
            assert "SECOND CUSTOMER" not in first_text


def test_nested_request_does_not_replace_outer_context(monkeypatch):
    original = ae.header_full
    nested_text = []
    entered = False

    def nested_header(context, pageno_text):
        nonlocal entered
        if not entered:
            entered = True
            with ae.build_pdf(_data("INNER CUSTOMER", repeat=12)) as nested:
                nested_text.append(_text(nested))
        return original(context, pageno_text)

    monkeypatch.setattr(ae, "header_full", nested_header)
    with ae.build_pdf(_data("OUTER CUSTOMER", repeat=12)) as outer:
        outer_text = _text(outer)
        assert len(outer) == 4  # also exercises the notice page's full header
        assert "OUTER CUSTOMER" in outer_text
        assert "INNER CUSTOMER" not in outer_text
    assert "INNER CUSTOMER" in nested_text[0]
    assert "OUTER CUSTOMER" not in nested_text[0]


@pytest.mark.parametrize("hook", ["header_full", "draw_summary", "_rename_embedded_fonts"])
@pytest.mark.parametrize("error", [ValueError, KeyboardInterrupt])
def test_failed_request_leaves_next_request_independent(monkeypatch, hook, error):
    original = getattr(ae, hook)
    data = _data("FAILED CUSTOMER")
    before = copy.deepcopy(data)
    seen = []

    def fail(context_or_document, *args):
        document = (context_or_document if hook == "_rename_embedded_fonts"
                    else context_or_document.document)
        seen.append(document)
        raise error("injected rendering failure")

    monkeypatch.setattr(ae, hook, fail)
    with pytest.raises(error, match="injected rendering failure"):
        ae.build_pdf(data)
    assert seen[0].is_closed
    assert data == before
    monkeypatch.setattr(ae, hook, original)
    with ae.build_pdf(_data("NEXT CUSTOMER")) as document:
        assert "NEXT CUSTOMER" in _text(document)
        assert "FAILED CUSTOMER" not in _text(document)


@pytest.mark.parametrize("encrypt", [True, False])
def test_serialization_and_save_remain_compatible(tmp_path, encrypt):
    with ae.build_pdf(_data(), with_watermark=False) as document:
        payload = ae.pdf_bytes(document, encrypt=encrypt, garbage=3)
    path = tmp_path / "statement.pdf"
    with ae.build_pdf(_data(), with_watermark=False) as document:
        ae.save_pdf(document, path, encrypt=encrypt, garbage=3)
    for source in (payload, path.read_bytes()):
        with fitz.open(stream=source, filetype="pdf") as restored:
            assert len(restored) == 2
            assert "SAMPLE" not in _text(restored)
            assert bool(restored.metadata["encryption"]) is encrypt
            if encrypt:
                assert restored.permissions == -1804


# Captured from the pre-refactor generator with fixed creationDate. Text and
# 72-dpi pixels verify layout; normalized PDF bytes also cover fonts and XMP.
_BASELINES = {'demo': (2, ['a3222e6886f1dab84007773aae7f0353d868582034f4d3547d272b77ee08d016', '1c71ed4f034710f44f611a60893c027ad67ccb8a20c45a215bc3c030ebaa2284'], ['05c751dc4ed266e52642b3f296cfe26a277a6538293a821cc7e9f93d181ea7cb', '5b9c53bb3a4d8d279dcc298d857748ce35700d148cbaad3c64021b3101f9c0f2'], '54d5923d744b34120ae2c8b47bf4922b5fb6a85fadfbf90347b77b865c737fb5'), 'original': (2, ['a3222e6886f1dab84007773aae7f0353d868582034f4d3547d272b77ee08d016', '1c71ed4f034710f44f611a60893c027ad67ccb8a20c45a215bc3c030ebaa2284'], ['05c751dc4ed266e52642b3f296cfe26a277a6538293a821cc7e9f93d181ea7cb', '5b9c53bb3a4d8d279dcc298d857748ce35700d148cbaad3c64021b3101f9c0f2'], '54d5923d744b34120ae2c8b47bf4922b5fb6a85fadfbf90347b77b865c737fb5'), 'jun': (2, ['a3222e6886f1dab84007773aae7f0353d868582034f4d3547d272b77ee08d016', '1c71ed4f034710f44f611a60893c027ad67ccb8a20c45a215bc3c030ebaa2284'], ['05c751dc4ed266e52642b3f296cfe26a277a6538293a821cc7e9f93d181ea7cb', '5b9c53bb3a4d8d279dcc298d857748ce35700d148cbaad3c64021b3101f9c0f2'], '54d5923d744b34120ae2c8b47bf4922b5fb6a85fadfbf90347b77b865c737fb5'), 'empty': (2, ['297acf09e4d1f7e4e7a2e675a233828838670a3bac57b86c55bf5b24f761bd95', '1c71ed4f034710f44f611a60893c027ad67ccb8a20c45a215bc3c030ebaa2284'], ['3717176a0018a77fd870a714fdbbac2c7bebfa61c99cf5ce45fd225f9838753a', '5b9c53bb3a4d8d279dcc298d857748ce35700d148cbaad3c64021b3101f9c0f2'], 'b6719f3a1713fb6fb99b8bac941924037fb804921bc308116cdc0860e123624b'), 'multipage': (6, ['e027cdcb7f1a0ac995975041ec31d36736389183478d3305ea1632a1443b7752', 'c1203091fe4ea249dd075647885c3480bcda815b472f314a78c4ea5294e97776', 'b8d0059994af84fbc94c3fb41ba476f1b92bad2ae1414d3ebd79142029139a3a', '85402665f5b4205bfc788562312b43015e1bc43b1a8b466792758e7f96582184', '0ad4340d63bd856ca557016f4e82c8cd55a89b44924b620fd32aa3634feb7800', '676dbe4b1f49e2d0e0f7b88eb4326b324da2552ce7999c9a79b50e6cf50ae4ee'], ['87334a729e50090675c54beb3965c8af4032a7a6a773d012b64a92d5a73bfc86', 'ac4e4e4f7c1f0a1b7bb01a89f0cf68b0a0cb1733035a30b92502d18c07786e25', 'c8b100d19c4031834c61d33518a24d7d2dc6dea35ea96e7afb4de003cdee6daf', 'd6135edae4c4da42430048d262338bfaff571f042ca2318f91cd0050367cd43d', 'baceb41b5ce4689c10dd0858e980542f1845b69be92114817c3e915987361ee5', '9973f5dbad29d104153b663078ca4ddc2d33abfe67830b3262049b1a52174f9c'], 'ab222b2a67d720c41a195f9c1ab05abbc504c20433f6adb0bbf19a3a3a6bb0b2'), 'custom': (2, ['5537d4c28c6788d6a21ceffdacd10e03d76c1d11169c70238edec12b1656339a', '965e21d2f3efe6203a6f76850394c38de2b2b77f1ef16c6e603a0406df8f0929'], ['d31a5fd470fa9ff496ec9053a8b483842522b96f3292cd83c4f88d5f499bfc29', 'dc3385acec097757317d36d8920fbc6ddd7acda6c8c884e55611839ec66c38db'], '227f6b1b0f98dc680363a9bf620ae19ccc07c3932ea927dfd4c26a7da3dfc712')}


@pytest.mark.parametrize("case", ["demo", "original", "jun", "empty", "multipage", "custom"])
def test_pre_refactor_output_parity(case):
    data = copy.deepcopy(ae.DATASETS.get(case, ae.DATASETS["demo"]))
    options = {}
    if case == "empty":
        data["transactions"] = []
    elif case == "multipage":
        data["transactions"] *= 25
    elif case == "custom":
        data["transactions"][0]["desc_lines"] = ["Explicit description", "Second line"]
        data["transactions"][1].pop("id")
        options = {
            "with_watermark": False,
            "metadata": {"title": "Parity", "creationDate": "2026-11-01 18:12:34"},
            "xmp": {"dc:title": "XMP parity", "xmp:CreateDate": "2026-11-02T18:00:00"},
        }
    pages, text_hashes, pixel_hashes, pdf_hash = _BASELINES[case]
    with ae.build_pdf(data, case, **options) as document:
        assert len(document) == pages
        assert [hashlib.sha256(p.get_text().encode()).hexdigest() for p in document] == text_hashes
        assert [hashlib.sha256(p.get_pixmap().samples).hexdigest() for p in document] == pixel_hashes
        payload = ae.pdf_bytes(document, encrypt=False, no_new_id=True)
    # 字节级确定性是 PyMuPDF 自己的性质，不再当契约（真出件不可能复现）。
    # 这里只校验排版指纹（上面）+ 元数据层不再自曝样例身份。
    _pages, _th, _ph, _hash = _BASELINES[case]
    assert payload.startswith(b"%PDF-")
    with fitz.open(stream=payload, filetype="pdf") as check:
        meta = check.metadata
        title = (meta.get("title") or "").upper()
        if case != "custom":
            assert "SAMPLE" not in title and "FICTIONAL" not in title
