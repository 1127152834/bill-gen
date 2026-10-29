"""Shared mechanics and optional pre-refactor, source-snapshot golden comparisons.

Set CHASE_ORIGINAL_SOURCE_DIR to a temporary directory containing untouched
original generate_personal.py / generate_business.py. No historical baseline is
recorded or updated by these tests.
"""
import copy
import importlib
import inspect
import json
import os
import sys
import types
from pathlib import Path

import fitz
import pytest

from billgen.themes.chase import common

CHASE = Path(common.__file__).parent
TAGS = ("personal", "business")


def generator(tag):
    return importlib.import_module(f"billgen.themes.chase.generate_{tag}")


@pytest.mark.parametrize("tag", TAGS)
def test_shared_bindings_keep_variant_state_explicit(tag):
    module = generator(tag)
    assert str(inspect.signature(module.build_pdf)) == "(d, out_path, watermark=True)"
    assert module.encode.__self__ is module._FONT_CODEC
    assert module._FONT_CODEC.ch2code is module.CH2CODE
    assert module._FONT_CODEC.code2ch is module.CODE2CH
    assert module._FONT_CODEC.widths is module.WIDTHS
    assert module._FONT_CODEC.tables is module.TABLES
    for name in ("_cents", "_fmt", "find", "sub_text", "_col_xs",
                 "_flat_row_count", "_pdf_esc", "_now_pdf_date",
                 "_normalize_type3_glyphs"):
        assert getattr(module, name) is getattr(common, name)
    for name in ("chstream", "EF", "BC"):
        assert getattr(module, name).__name__.startswith("billgen.themes.chase.")


def test_font_codecs_are_isolated_and_observe_live_updates():
    p, b = (generator(tag) for tag in TAGS)
    assert p._FONT_CODEC is not b._FONT_CODEC
    assert p.CH2CODE is not b.CH2CODE
    tables = {"test:F11": {"type": "Type3"}}
    codes = {"test": {"F11": {"A": 1}}}
    widths = {"test": {"F11": [0, 5]}}
    codec = common.FontCodec(tables, codes, widths, {}, {})
    assert codec.encode("test", "F11", "A?") == b"\x01?"
    codes["test"]["F11"]["A"] = 2
    widths["test"]["F11"] = [0, 5, 9]
    assert codec.encode("test", "F11", "A") == b"\x02"
    assert codec.char_w("test", "F11", "A", 2) == 18


@pytest.mark.parametrize("tag", TAGS)
def test_prepared_cache_hit_restores_all_font_maps(tag, tmp_path):
    module = generator(tag)
    assets = tmp_path / "assets"
    assets.mkdir()
    pdf = assets / f"chase_static_{tag}_prepared.pdf"
    with fitz.open() as doc:
        doc.new_page()
        doc.save(pdf)
    (assets / f"prepared_{tag}.json").write_text(json.dumps({"sig": "test"}))
    (assets / f"font_tables_{tag}_prepared.json").write_text(json.dumps({
        "ch2code": {"F11": {"X": 7}}, "code2ch": {"F11": {"7": "X"}},
        "widths": {"F11": [0] * 7 + [13]},
    }))
    codec = common.FontCodec({f"{tag}:F11": {"type": "Type3"}}, {}, {}, {}, {})

    def unexpected(*args):
        pytest.fail("cache hit should not rebuild fonts")

    with common.open_prepared(tag, str(tmp_path), {}, codec,
                              lambda _: "test", unexpected, unexpected) as doc:
        assert len(doc) == 1
    assert codec.encode(tag, "F11", "X") == b"\x07"
    assert codec.code2ch[tag]["F11"] == {7: "X"}
    assert codec.char_w(tag, "F11", "X", 2) == 26
    assert module._FONT_CODEC is not codec


@pytest.mark.parametrize("tag", TAGS)
def test_prepared_cache_miss_calls_variant_callbacks(tag, tmp_path, monkeypatch):
    monkeypatch.setenv("BILL_GEN_DATA_DIR", str(tmp_path / "writable"))
    assets = tmp_path / "assets"
    assets.mkdir()
    source = tmp_path / "source.pdf"
    with fitz.open() as doc:
        doc.new_page()
        doc.save(source)
    codec = common.FontCodec({}, {}, {}, {}, {})
    calls = []

    def extend(doc, selected):
        assert selected == tag
        calls.append("extend")
        codec.ch2code[tag] = {"F11": {"X": 7}}
        codec.code2ch[tag] = {"F11": {7: "X"}}
        codec.widths[tag] = {"F11": [0] * 7 + [13]}

    def digits(doc):
        calls.append("digits")

    with common.open_prepared(tag, str(tmp_path), {tag: str(source)}, codec,
                              lambda _: "new", extend, digits) as doc:
        assert len(doc) == 1
    assert calls == ["extend", "digits"]
    bundles = list((tmp_path / "writable" / "cache" / "chase" / tag).glob("*.json"))
    assert len(bundles) == 1
    restored = json.loads(bundles[0].read_text())
    assert restored["fonts"]["code2ch"]["F11"] == {"7": "X"}
    assert list(assets.iterdir()) == []
    # A corrupt writable bundle is a miss and is rebuilt, not an asset write.
    bundles[0].write_text("{broken")
    with common.open_prepared(tag, str(tmp_path), {tag: str(source)}, codec,
                              lambda _: "new", extend, digits) as doc:
        assert len(doc) == 1
    assert calls == ["extend", "digits", "extend", "digits"]
    assert list(assets.iterdir()) == []

    def unexpected(*args):
        pytest.fail("valid writable bundle should hit")

    fresh = common.FontCodec({}, {}, {}, {}, {})
    with common.open_prepared(tag, str(tmp_path), {}, fresh,
                              lambda _: "new", unexpected, unexpected) as doc:
        assert len(doc) == 1
    assert fresh.code2ch[tag]["F11"] == {7: "X"}


def test_prepared_cache_key_tracks_actual_donor_bytes(tmp_path, monkeypatch):
    donor = tmp_path / "font.ttf"
    donor.write_bytes(b"mac-font")
    monkeypatch.setattr(common, "font_path", lambda _: str(donor))
    first = common._prepared_cache_key("unchanged-source-signature")
    donor.write_bytes(b"linux-font")
    assert common._prepared_cache_key("unchanged-source-signature") != first


@pytest.mark.parametrize("tag", TAGS)
def test_readonly_assets_and_unwritable_cache_build_in_memory(tag, tmp_path, monkeypatch):
    import builtins

    assets = tmp_path / "assets"
    assets.mkdir()
    marker = assets / f"prepared_{tag}.json"
    marker.write_text('{"sig":"stale"}')
    before = {p.name: p.read_bytes() for p in assets.iterdir()}
    source = tmp_path / "source.pdf"
    with fitz.open() as doc:
        doc.new_page()
        doc.save(source)
    real_open = builtins.open

    def readonly_open(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and Path(file).is_relative_to(assets):
            assert not any(flag in mode for flag in "wax+"), "source asset write"
        return real_open(file, mode, *args, **kwargs)

    def no_cache(*args, **kwargs):
        raise PermissionError("read-only runtime storage")

    monkeypatch.setattr(builtins, "open", readonly_open)
    monkeypatch.setattr(common, "data_path", no_cache)
    with common.open_prepared(tag, str(tmp_path), {tag: str(source)},
                              common.FontCodec({}, {}, {}, {}, {}), lambda _: "new",
                              lambda *_: None, lambda *_: None) as doc:
        assert len(doc) == 1
    assert {p.name: p.read_bytes() for p in assets.iterdir()} == before


@pytest.mark.parametrize("tag", TAGS)
def test_replay_build_is_stable_without_mutating_input(tag, tmp_path):
    module = generator(tag)
    data = _case(tag, "replay")
    untouched = copy.deepcopy(data)
    paths = [tmp_path / "first.pdf", tmp_path / "second.pdf"]
    for path in paths:
        assert module.build_pdf(data, str(path), watermark=False) == str(path)
        assert data == untouched
    with fitz.open(paths[0]) as first, fitz.open(paths[1]) as second:
        assert len(first) == len(second)
        for a, b in zip(first, second, strict=True):
            assert a.get_text() == b.get_text()
            assert [first.xref_stream(x) for x in a.get_contents()] == [
                second.xref_stream(x) for x in b.get_contents()
            ]


def _original(tag, snapshot):
    """Compile untouched original code with its original asset location."""
    module = types.ModuleType(f"_chase_original_{tag}")
    module.__file__ = str(CHASE / f"generate_{tag}.py")
    before = sys.path[:]
    try:
        exec(compile((snapshot / f"generate_{tag}.py").read_text(),
                     module.__file__, "exec"), module.__dict__)
    finally:
        sys.path[:] = before
    return module


def _case(tag, case):
    source = CHASE / f"editor_{tag}" / f"data_{tag}.json"
    if case == "replay":
        source = CHASE / f"editor_{tag}" / f"replay_{tag}.json"
    data = json.loads(source.read_text())
    if case in ("empty", "many"):
        for key in ("transactions", "deposits", "checks", "atm_withdrawals",
                    "electronic_withdrawals", "other_withdrawals", "fees", "daily_balance"):
            if key in data:
                data[key] = []
        if case == "many":
            rows = [{"date": f"01/{i % 28 + 1:02}",
                     "desc": f"ACME ROW {i}\nCONTINUATION", "amount": "1.00",
                     "type": "deposit"} for i in range(70)]
            data["transactions" if tag == "personal" else "deposits"] = rows
    return data


@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize("case", ("default", "empty", "many", "replay"))
@pytest.mark.parametrize("watermark", (True, False))
def test_original_source_text_and_pixel_golden(tag, case, watermark, tmp_path):
    snapshot = os.environ.get("CHASE_ORIGINAL_SOURCE_DIR")
    if not snapshot:
        pytest.skip("pre-refactor source snapshot supplied only during extraction validation")
    old, new = _original(tag, Path(snapshot)), generator(tag)
    assert old._prep_sig(tag) == new._prep_sig(tag)
    data = _case(tag, case)
    original_data = copy.deepcopy(data)
    paths = [tmp_path / "original.pdf", tmp_path / "refactored.pdf"]
    old.build_pdf(data, str(paths[0]), watermark=watermark)
    new.build_pdf(data, str(paths[1]), watermark=watermark)
    assert data == original_data
    with fitz.open(paths[0]) as expected, fitz.open(paths[1]) as actual:
        assert len(expected) == len(actual)
        for i, (left, right) in enumerate(zip(expected, actual, strict=True)):
            assert left.get_text() == right.get_text(), (tag, case, i, "text")
            a, b = left.get_pixmap(), right.get_pixmap()
            assert (a.width, a.height, a.n) == (b.width, b.height, b.n)
            assert a.samples == b.samples, (tag, case, i, "pixels")
            assert [expected.xref_stream(x) for x in left.get_contents()] == [
                actual.xref_stream(x) for x in right.get_contents()
            ], (tag, case, i, "content streams")
