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
        assert len(outer) == 3  # also exercises the notice page's full header
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
_BASELINES = {'demo': (2,
          ['2d2617fe4a76d51f72ab2f98725e283cf8490c095ef3143618ebaa299068f17b',
           'e9a146819c2af32186cfbc89189ab063ab0a8bf9da1b5b5b750d4e2ff76e6a63'],
          ['14eeeaa4e47ae3c0440af4d439dac22ea2fcf3f2e956011572172112bfd57768',
           '7f19ad6260e868056fd10259d29c7dee4661e1257f941ac927562f2fad2f61db'],
          'f743daf225319addd27fbd7ab6e00a1214800ba70000b94b53d1c51bc3ba5cff'),
 'original': (2,
              ['2d2617fe4a76d51f72ab2f98725e283cf8490c095ef3143618ebaa299068f17b',
               'e9a146819c2af32186cfbc89189ab063ab0a8bf9da1b5b5b750d4e2ff76e6a63'],
              ['14eeeaa4e47ae3c0440af4d439dac22ea2fcf3f2e956011572172112bfd57768',
               '7f19ad6260e868056fd10259d29c7dee4661e1257f941ac927562f2fad2f61db'],
              'f743daf225319addd27fbd7ab6e00a1214800ba70000b94b53d1c51bc3ba5cff'),
 'jun': (2,
         ['2d2617fe4a76d51f72ab2f98725e283cf8490c095ef3143618ebaa299068f17b',
          'e9a146819c2af32186cfbc89189ab063ab0a8bf9da1b5b5b750d4e2ff76e6a63'],
         ['14eeeaa4e47ae3c0440af4d439dac22ea2fcf3f2e956011572172112bfd57768',
          '7f19ad6260e868056fd10259d29c7dee4661e1257f941ac927562f2fad2f61db'],
         'f743daf225319addd27fbd7ab6e00a1214800ba70000b94b53d1c51bc3ba5cff'),
 'empty': (2,
           ['3cb04d770fc6c200981aec0b3601175a8c75a011e48f64afa329144539147a51',
            'e9a146819c2af32186cfbc89189ab063ab0a8bf9da1b5b5b750d4e2ff76e6a63'],
           ['6486921a572a8182678014a1169ebac79c31b26590386fb75fa8a441b248e4d3',
            '7f19ad6260e868056fd10259d29c7dee4661e1257f941ac927562f2fad2f61db'],
           '513ed77d28c0774cd82c825f43ce0a098de525cbf51ed26ba666e71461e3c93b'),
 'multipage': (4,
               ['f77ee9bf199a7e9ce8a23f03e97f87a3f8ee18051c47447bf029f7cfd36a77f0',
                '5f8a628dc59f98b766b55869a14f7ada80f5e732f205e165851f6a6850929bde',
                '6c2f3f44c3f61eb94871fad71e0db0b5c9ac7dca576ed0bd8af6e419c7273df5',
                '018d32bf347d9ca353cec14e280507b2d7cdd0a1cb624f474f976ad9c4ca9f93'],
               ['9062d217f697a60cf2139a3361f8ed36a5920add84e3ab9ec5f6e725e8db9e02',
                '46d9a8fc5bc7073f89b8f5d177d84941ad7ee84e58f7d859c622fea34bcfd53c',
                '4b5f67b13e1c36999282056c8e97aa5f66c20ab277cdcb46b3215b4133d6722a',
                '1fe0d465c7b0590ebf8de4330436c7677fa7acf5cad029473c5b1c952d5002e0'],
               'ef893b2e4510e66bbd56a198509373997e6890b6e00a6f03ae00f984c49acc0d'),
 'custom': (2,
            ['40965e86fcb68276cdefb5dfd3734e03e91de514542cdc61157a27f0b9746615',
             '184b8f6786a90f68d9f4d7b480adf15e46911a41d0a5e43fa5309d0092f6ea7f'],
            ['70bf90c1a6975dc1f2cac296657c83b34476a19d0c72ea96b94cad3ec7ac311c',
             '3ae35377ed53baddec67763973263e0f3a62d3665ec0e1bb07054f2962915eeb'],
            '280cdb033c8b2d91983be123cd44810e41301b88798f8c112f908dc7fbca3e6a')}


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
        assert hashlib.sha256(payload).hexdigest() == pdf_hash
