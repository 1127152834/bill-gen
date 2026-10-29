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
# 水印层级改回「最上层、极浅灰」后（与参考件一致，见 docs/notes/e2e-round1.md），
# 字符流顺序与像素都变了，三套基线按新渲染重采。
# 2026-10-30 再采 capitalone：标语改成参考件的 "Thanks for saving with Capital One 360®"、
# 页脚换成 360 专线与 P.O. Box、Account Summary 首行改 "360 Checking...<last4>"。
DEFAULT_TEXT_SHA = {
    "mercury": "03624891786d6e2bd4dfbac2b305bdff1458b1db0304ab3d1b702bda7a6d4f2f",
    "capitalone": "d07cdc3528341c4c08497836b8896fa221bbb021d0fa2f229784c11579e0c0b0",
    "sofi": "4260995e2f8761189763277b12b3f1114e09cd2467070b4279aa688d57a70f32",
}
DEFAULT_PIXELS = {
    "mercury": ['f5ba07c7d5a6a5efedfefb58b4180c4c', '4ae7e9f5265b75ff1020257b3a002c6c'],
    # 2026-10-30 第 3 页条款换成参考件原文，第三页像素随之改变
    "capitalone": ['039826011de069ddf80cf0e19916ede6', 'bf95905cb2ce2cf46a18b025ae935a1a', 'e94c861c64e8ae4697b5504e7cf73e48'],
    "sofi": ['beddba3594841db54db4b629da43a055', 'af167418cb9d2a86127a55cbc24bb796'],
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
