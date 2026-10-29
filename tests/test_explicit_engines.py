"""Explicit request contexts for the three formerly-global PDF engines."""
from __future__ import annotations

import hashlib
import importlib
import inspect
from copy import deepcopy

import pytest

ENGINES = [
    ("mercury", "generate_statement", "company", "page_summary", 46),
    ("capitalone", "c1_generate", "customer_name", "page_summary", 35),
    ("sofi", "sofi_generate", "holder_name", "page_statement", 35),
]
# 2026-10-29 重采：数据仍是原稿基线（`PARITY_DATA`），但水印改成画在正文之下
# （原稿把 0.78 灰的 SAMPLE 盖在金额上，一眼假），字符流顺序与像素因此变化。
# 原稿基线文本见各主题模块的 `PARITY_DATA`。
DEFAULT_TEXT_SHA = {
    "mercury": "c5a491bdaf89c837f684292a8e499389513b8ebab4d3fac5aa90749be029b053",
    "capitalone": "bb9220433b6f6f8e5c3a7c662f15daf01344172a8df1b6c6637c97d03868a28d",
    "sofi": "a3c1a21308e5e084bc9d6a311b89eaae64d4ce67480aeb67e84fc2754c7c4547",
}
DEFAULT_PIXELS = {
    "mercury": ['cbb2da189272fae73f152868dbad122c', '1fd7a3caa4c27890d9d15d75ad38355d'],
    "capitalone": ['c818c6ac19354c44c5fec09a05d7e9a6', '0c91d0838c7289d4965e39ed54868c86', 'debebd7608ffdf0f0963496d5ed5735a'],
    "sofi": ['9f73361c6e72dc8f6fe59ea6c53fb527', 'ec92a65a02617d9b5498cfba5956db7a'],
}


@pytest.fixture(params=ENGINES, ids=lambda item: item[0])
def engine(request):
    theme, name, holder, hook, count = request.param
    return importlib.import_module(f"billgen.themes.{theme}.{name}"), holder, hook, count


def _snapshot(mod):
    names = ("DATA", "WATERMARK", "META", "FONT_CJK", "_fallback_used")
    return {name: (getattr(mod, name), deepcopy(getattr(mod, name))) for name in names}


def _unchanged(mod, snapshot):
    for name, (identity, value) in snapshot.items():
        assert getattr(mod, name) is identity
        assert getattr(mod, name) == value


def _text(doc):
    return "\n".join(page.get_text() for page in doc)


def _request(mod, holder, count, label):
    data = deepcopy(mod.DATA)
    data[holder] = label
    # Exercise every string-valued data field, including footers, notes, fees,
    # labels, account details, contact fields and address-dependent geometry.
    for key, value in data.items():
        if isinstance(value, str):
            data[key] = label
        elif isinstance(value, list) and value and isinstance(value[0], str):
            data[key] = [label] * len(value)
    row = deepcopy(data["transactions"][0])
    row.update(desc=label, memo=label, txid=label, amount=1.25)
    data["transactions"] = [deepcopy(row) for _ in range(count)]
    return data


def test_default_signature_and_original_pdf_parity(engine):
    mod, _, _, _ = engine
    assert str(inspect.signature(mod.build)) == "(meta=None, *, data=None, watermark=None)"
    before = _snapshot(mod)
    theme = mod.__name__.split(".")[-2]
    with mod.build() as doc:
        glyphs = "".join(
            char["c"] for page in doc for block in page.get_text("rawdict")["blocks"]
            if block["type"] == 0 for line in block["lines"]
            for span in line["spans"] for char in span["chars"]
        )
        assert hashlib.sha256(glyphs.encode()).hexdigest() == DEFAULT_TEXT_SHA[theme]
        assert [page.get_pixmap().digest.hex() for page in doc] == DEFAULT_PIXELS[theme]
        raw = [page.get_text("rawdict") for page in doc]
        metadata, xmp = doc.metadata, doc.get_xml_metadata()
    with mod.build(data=deepcopy(mod.DATA), watermark=mod.WATERMARK) as doc:
        assert [page.get_text("rawdict") for page in doc] == raw
        assert doc.metadata == metadata
        assert doc.get_xml_metadata() == xmp
    with mod.build({"title": "Legacy positional metadata"}) as doc:
        assert doc.metadata["title"] == "Legacy positional metadata"
    _unchanged(mod, before)


def test_explicit_requests_are_reentrant_and_snapshot_inputs(engine, monkeypatch):
    mod, holder, hook, count = engine
    before = _snapshot(mod)
    a = _request(mod, holder, count, "ALPHA")
    b = _request(mod, holder, 2, "BRAVO")
    original_a, original_b = deepcopy(a), deepcopy(b)
    meta = {"title": "ALPHA", "author": "ALPHA"}
    original = getattr(mod, hook)
    nested = []
    entered = False

    def interleaved(*args, **kwargs):
        nonlocal entered
        ctx = kwargs["ctx"]
        if not entered:
            entered = True
            # A must keep its entry-time deep snapshot, including nested rows.
            a[holder] = "MUTATED"
            a["transactions"][0]["desc"] = "MUTATED"
            meta["title"] = "MUTATED"
            with mod.build({"title": "BRAVO"}, data=b, watermark=True) as doc:
                nested.append((_text(doc), doc.metadata["title"]))
            assert ctx.data[holder] == "ALPHA"
        return original(*args, **kwargs)

    monkeypatch.setattr(mod, hook, interleaved)
    with mod.build(meta, data=a, watermark=False) as doc:
        text = _text(doc)
        assert doc.page_count >= 3  # Force continuation/footer/balance helpers.
        assert "ALPHA" in text
        assert "BRAVO" not in text and "MUTATED" not in text
        assert "S A M P L E" not in text
        assert doc.metadata["title"] == "ALPHA"
        if mod.__name__.split(".")[-2] == "capitalone":
            assert "ALPHA" in doc.get_xml_metadata()
    assert "BRAVO" in nested[0][0] and "ALPHA" not in nested[0][0]
    assert "S A M P L E" in nested[0][0]
    assert nested[0][1] == "BRAVO"
    assert b == original_b
    assert a["transactions"][1:] == original_a["transactions"][1:]
    _unchanged(mod, before)


def test_failure_preserves_defaults_and_next_request(engine, monkeypatch):
    mod, holder, hook, _ = engine
    before = _snapshot(mod)
    original = getattr(mod, hook)
    seen = []

    def fail(doc, *args, **kwargs):
        seen.append(doc)
        kwargs["ctx"].data[holder] = "FAILED"
        raise ValueError("injected render failure")

    monkeypatch.setattr(mod, hook, fail)
    with pytest.raises(ValueError, match="injected render failure"):
        mod.build(data=deepcopy(mod.DATA), watermark=False)
    assert seen[0].is_closed
    _unchanged(mod, before)
    monkeypatch.setattr(mod, hook, original)
    with mod.build() as doc:
        assert "FAILED" not in _text(doc)
    _unchanged(mod, before)


def test_context_owns_cjk_and_mutable_font_caches(engine):
    mod, holder, _, _ = engine
    data = deepcopy(mod.DATA)
    first = mod._render_context(data, False)
    second = mod._render_context(data, True)
    assert first.data is not data and first.data["transactions"] is not data["transactions"]
    first.fallback_used.add("test")
    first.fitz_fonts["test"] = object()
    first.shapers["test"] = object()
    assert not second.fallback_used and not second.fitz_fonts and not second.shapers
    # Selection is request-local and does not run the legacy global selector.
    if mod.FONT_CJK_BASE and mod.__name__.split(".")[-2] != "capitalone":
        primary = mod._REG if hasattr(mod, "_REG") else mod._VAR
        supported = next((chr(cp) for cp in mod._CJK_CMAP
                          if cp > 0x3000 and cp not in mod._CMAP[primary]), None)
        if supported:
            data[holder] = supported
            assert mod._render_context(data, False).font_cjk == mod.FONT_CJK_BASE
            assert mod._render_context(mod.DATA, False).font_cjk is None
