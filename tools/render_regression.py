#!/usr/bin/env python3
"""Fixed, source-verified PDF fingerprints; never updates historical parity baselines.

  python tools/render_regression.py check
  python tools/render_regression.py capture --chase-original DIR --usbank-original DIR

Capture compares every generated page against untouched P4 source before adding an
explicit environment profile. Check is strict: unknown renderer/donor-font profiles
fail, rather than silently accepting a self-comparison. No PDFs or extracted text
are retained. Metadata, encryption randomness and PDF object numbers are excluded.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import platform
import shutil
import struct
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402
import PIL  # noqa: E402
from PIL import features  # noqa: E402

FIXTURE = ROOT / "tests/fixtures/render_regression_v1.json"
THEMES = {
    "chase-personal": "chase.generate_personal",
    "chase-business": "chase.generate_business",
    "usbank-personal": "usbank.generate_us",
    "usbank-business": "usbank.generate_biz",
}
CASES = ("default", "empty", "multipage", "replay")


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def environment() -> dict:
    from runtime_paths import font_path

    return {
        "pymupdf": fitz.VersionBind,
        "mupdf": fitz.VersionFitz,
        "pillow": PIL.__version__,
        "freetype": features.version("freetype2"),
        "donor_fonts": {style: sha(Path(font_path(style)).read_bytes())
                        for style in ("Regular", "Bold", "Italic", "BoldItalic")},
    }


def profile_key(env: dict) -> str:
    return sha(json.dumps(env, sort_keys=True).encode())[:20]


def case_data(theme: str, case: str) -> dict:
    family, variant = theme.split("-")
    module = importlib.import_module(f"billgen.themes.{THEMES[theme]}")
    base = Path(module.__file__).parent
    if family == "chase":
        name = "replay" if case == "replay" else "data"
        source = base / f"editor_{variant}/{name}_{variant}.json"
    else:
        tag = "us" if variant == "personal" else "biz"
        source = base / (f"replay_{tag}.json" if case == "replay"
                         else f"editor_{tag}/data_{tag}.json")
    if not source.exists() and case != "replay":
        import billgen
        example = theme.replace("usbank-", "us-") + ".json"
        candidates = (ROOT / "examples" / example,
                      Path(billgen.__file__).parent / "examples" / example)
        source = next((path for path in candidates if path.is_file()), source)
    data = json.loads(source.read_text())
    if family == "chase" and case in ("empty", "multipage"):
        for key in ("transactions", "deposits", "checks", "atm_withdrawals",
                    "electronic_withdrawals", "other_withdrawals", "fees", "daily_balance"):
            if key in data:
                data[key] = []
        if case == "multipage":
            data["transactions" if variant == "personal" else "deposits"] = [
                {"date": f"01/{i % 28 + 1:02}", "desc": f"SAMPLE ROW {i}\nCONTINUATION",
                 "amount": "1.00", "type": "deposit"} for i in range(70)]
    elif family == "usbank":
        if case == "empty":
            data["deposits"], data["withdrawals"] = [], []
        elif case == "multipage":
            row = data["deposits"][0]
            data["deposits"] = [dict(
                row, date=f"Jan {i % 28 + 1}", detail=f"SAMPLE TRANSACTION {i:03}",
                extra=["SAMPLE FIRST EXTRA|SECOND COLUMN", "SAMPLE SECOND EXTRA"],
                amount=f"{i + 1}.25") for i in range(70)]
    return data


def case_ids():
    return [(theme, case, watermark) for theme in THEMES for case in CASES
            for watermark in (False, True)]


def case_key(theme, case, watermark):
    return f"{theme}/{case}/watermark-{'on' if watermark else 'off'}"


def fingerprint(path: Path) -> dict:
    pages = []
    with fitz.open(path) as doc:
        for page in doc:
            # Length-framing distinguishes [b'ab', b'c'] from [b'a', b'bc'].
            streams = [doc.xref_stream(x) for x in page.get_contents()]
            content = b"".join(struct.pack(">Q", len(s)) + s for s in streams)
            pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), colorspace=fitz.csRGB,
                                  alpha=False, annots=True)
            pages.append({"text": sha(page.get_text().encode("utf-8")),
                          "content": sha(content), "pixels": sha(pix.samples),
                          "size": [pix.width, pix.height, pix.n],
                          "rect": list(page.rect)})
    return {"page_count": len(pages), "pages": pages}


def original_module(theme: str, snapshots: dict):
    family = theme.split("-")[0]
    current = importlib.import_module(f"billgen.themes.{THEMES[theme]}")
    source = snapshots[family] / Path(current.__file__).name
    module = types.ModuleType(f"_regression_original_{theme.replace('-', '_')}")
    module.__file__ = current.__file__  # Keep the original asset resolution contract.
    before = sys.path[:]
    # Legacy families reuse bare names (notably chstream); do not let the Chase
    # original leak its implementation into a subsequently loaded USBank original.
    names = {p.stem for p in Path(current.__file__).parent.glob("*.py")}
    saved = {name: sys.modules.pop(name) for name in names if name in sys.modules}
    try:
        sys.path.insert(0, str(Path(current.__file__).parent))
        exec(compile(source.read_text(), module.__file__, "exec"), module.__dict__)
    finally:
        sys.path[:] = before
        for name in names:
            sys.modules.pop(name, None)
        sys.modules.update(saved)
    return module, sha(source.read_bytes())


def isolate_chase_cache(module):
    """Always rebuild Type3 glyphs with this profile's font, never a stale Mac cache."""
    if hasattr(module, "_regression_cache"):
        return Path(module._regression_cache.name)
    module._regression_cache = tempfile.TemporaryDirectory(prefix="billgen-chase-fonts-")
    base = Path(module._regression_cache.name)
    source = Path(module.__file__).parent
    shutil.copytree(source / "assets", base / "assets",
                    ignore=shutil.ignore_patterns("*prepared*"))
    shutil.copyfile(source / "extend_fonts.py", base / "extend_fonts.py")
    return base


def render_case(theme, case, watermark, directory: Path, module=None) -> dict:
    module = module or importlib.import_module(f"billgen.themes.{THEMES[theme]}")
    base = getattr(module, "BASE", None)
    cache = isolate_chase_cache(module) if theme.startswith("chase-") else None
    data = case_data(theme, case)
    before = copy.deepcopy(data)
    output = directory / "actual.pdf"
    try:
        if cache is not None:
            module.BASE = str(cache)
        module.build_pdf(data, str(output), watermark=watermark)
    finally:
        if cache is not None:
            module.BASE = base
    if before != data:
        raise AssertionError(f"input mutated: {case_key(theme, case, watermark)}")
    result = fingerprint(output)
    result["input"] = sha(json.dumps(before, sort_keys=True, ensure_ascii=False).encode())
    return result


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def expected_profile(fixture=None) -> dict:
    fixture = fixture if fixture is not None else load_fixture()
    env = environment()
    key = profile_key(env)
    if key not in fixture["profiles"]:
        raise AssertionError(
            f"Unrecognized render environment {key}: {json.dumps(env, sort_keys=True)}. "
            "Use the pinned CI renderer/fonts or explicitly capture a reviewed P4-source profile; "
            "pixel checks have NOT been skipped.")
    return fixture["profiles"][key]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "capture", "refresh"))
    parser.add_argument("--reason", help="refresh 时记录到 fixture 的原因说明")
    parser.add_argument("--chase-original", type=Path)
    parser.add_argument("--usbank-original", type=Path)
    args = parser.parse_args()
    if args.command == "capture" and not (args.chase_original and args.usbank_original):
        parser.error("capture requires both untouched P4 source directories")
    fixture = load_fixture() if FIXTURE.exists() else {"schema": 1, "profiles": {}}
    env = environment()
    result = {"environment": env, "captured_platform": platform.system(),
              "provenance": {}, "cases": {}}
    expected = expected_profile(fixture) if args.command in ("check", "refresh") else None
    refreshing = args.command == "refresh"
    snapshots = {"chase": args.chase_original, "usbank": args.usbank_original}
    changed: list[str] = []
    originals = {}
    with tempfile.TemporaryDirectory(prefix="billgen-render-") as temp:
        for theme, case, watermark in case_ids():
            key = case_key(theme, case, watermark)
            actual = render_case(theme, case, watermark, Path(temp))
            if refreshing:
                previous = expected["cases"].get(key) if expected else None
                result["cases"][key] = actual
                if previous != actual:
                    changed.append(key)
                    print(f"REFRESH {key}")
                else:
                    print(f"PASS {key}")
                continue
            if expected is not None:
                if actual != expected["cases"][key]:
                    raise AssertionError(f"fixed fingerprint mismatch: {key}")
            else:
                if theme not in originals:
                    originals[theme], result["provenance"][theme] = original_module(theme, snapshots)
                original = render_case(theme, case, watermark, Path(temp), originals[theme])
                if actual != original:
                    raise AssertionError(f"P4 source parity mismatch: {key}")
                result["cases"][key] = actual
            print(f"PASS {key}")
    if refreshing:
        assert expected is not None, "refresh 需要已有 fixture"
        profile = expected_profile(fixture)
        profile["cases"] = result["cases"]
        profile.setdefault("refreshed", []).append({"keys": changed, "reason": args.reason or ""})
        fixture["profiles"][profile_key(env)] = profile
        FIXTURE.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n")
        print(f"已刷新 {len(changed)} 个用例；profile={profile_key(env)}")
        return 0
    if expected is None:
        fixture["profiles"][profile_key(env)] = result
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n")
    print(f"32 fixed cases verified; profile={profile_key(env)}")


if __name__ == "__main__":
    main()
