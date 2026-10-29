"""Portable fixed P4 fingerprints, with explicit renderer/font profile selection.

Unknown local profiles are reported as skips. CI's separate strict entry point is
`python tools/render_regression.py check`, which fails on unknown profiles.
CapitalOne intentionally uses same-environment differential tests instead (CJK
font discovery varies by operating system); see test_capitalone_fonts.py.
"""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools/render_regression.py"
SPEC = importlib.util.spec_from_file_location("billgen_render_regression", SCRIPT)
regression = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(regression)


@pytest.fixture(scope="module")
def fixed_profile():
    try:
        return regression.expected_profile()
    except AssertionError as exc:
        pytest.skip(str(exc))


@pytest.mark.parametrize("theme,case,watermark", regression.case_ids())
def test_fixed_p4_page_fingerprints(theme, case, watermark, fixed_profile, tmp_path):
    key = regression.case_key(theme, case, watermark)
    actual = regression.render_case(theme, case, watermark, tmp_path)
    expected = fixed_profile["cases"][key]
    assert actual["input"] == expected["input"], (key, "synthetic input changed")
    assert actual["page_count"] == expected["page_count"], (key, "page count")
    for index, (left, right) in enumerate(zip(actual["pages"], expected["pages"], strict=True)):
        for field in ("text", "content", "pixels", "size", "rect"):
            assert left[field] == right[field], (key, index, field)


def test_unknown_profile_fails_strictly():
    with pytest.raises(AssertionError, match="pixel checks have NOT been skipped"):
        regression.expected_profile({"profiles": {}})


def test_fixed_fixture_case_coverage():
    fixture = regression.load_fixture()
    assert fixture["schema"] == 1
    expected = {regression.case_key(*case) for case in regression.case_ids()}
    assert len(expected) == 32
    for profile in fixture["profiles"].values():
        assert set(profile["cases"]) == expected
        assert set(profile["provenance"]) == set(regression.THEMES)
        for theme in regression.THEMES:
            many = profile["cases"][f"{theme}/multipage/watermark-off"]
            empty = profile["cases"][f"{theme}/empty/watermark-off"]
            # Chase personal's original engine has a fixed two-page flow; its
            # stress case fills both pages rather than creating extra pages.
            assert many["page_count"] >= 2
            assert many["pages"] != empty["pages"]
            if theme != "chase-personal":
                assert many["page_count"] > empty["page_count"]
            off = profile["cases"][f"{theme}/default/watermark-off"]
            on = profile["cases"][f"{theme}/default/watermark-on"]
            assert off["pages"][0]["pixels"] != on["pages"][0]["pixels"]
