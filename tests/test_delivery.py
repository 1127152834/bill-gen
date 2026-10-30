"""交付包（每月 JSON + 校验报告 + 清单）的行为约定。"""
from __future__ import annotations

import io
import json
import pathlib
import zipfile

from billgen import delivery

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "synth_result.json"


def _result() -> dict:
    return json.loads(FIXTURE.read_text())


def test_package_carries_month_files_report_and_manifest():
    blob, manifest = delivery.build_package("capitalone", _result())
    pack = zipfile.ZipFile(io.BytesIO(blob))
    names = set(pack.namelist())

    assert manifest["count"] == len(manifest["months"]) >= 1
    assert {"validation-report.json", "manifest.json"} <= names
    for month in manifest["months"]:
        name = f"capitalone-{month}.json"
        assert name in names
        assert json.loads(pack.read(name))  # 每份都是能解析的 JSON
    assert manifest["template"] == "capitalone"
    assert all(item["sha256"] and item["filename"] for item in manifest["files"])


def test_report_marks_failures_without_hiding_them():
    blob, manifest = delivery.build_package("capitalone", _result())
    report = json.loads(zipfile.ZipFile(io.BytesIO(blob)).read("validation-report.json"))
    assert report["batch"]["ok"] is manifest["ok"]
    assert len(report["months"]) == manifest["count"]
    for month in report["months"]:
        assert month["ok"] is (not month["structure"] and not month["content"])


def test_requesting_a_month_outside_the_ledger_yields_no_files():
    blob, manifest = delivery.build_package("capitalone", _result(), months=["1999-01"])
    pack = zipfile.ZipFile(io.BytesIO(blob))
    assert manifest["count"] == 0
    assert manifest["ok"] is False
    assert set(pack.namelist()) == {"validation-report.json", "manifest.json"}
    report = json.loads(pack.read("validation-report.json"))
    assert report["batch"]["problems"][0]["rule"] == "no_months"


def test_merge_entry_accumulates_months_and_flags_gaps(tmp_path):
    """交付是一月一月落盘的，报告必须累积；断月要报 month_gap，别静默拼成一份。"""
    from billgen.synth import monthpack

    entry = {"month": "2027-01", "template": "capitalone", "ok": True, "rows": 3,
             "path": "/x/capitalone-2027-01.json", "bytes": 10, "sha256": "a" * 64,
             "opening": 100.0, "closing": 120.0}
    first = delivery.merge_entry(tmp_path, "capitalone", entry)
    assert first["ok"] is True and first["months"] == ["2027-01"]

    second = delivery.merge_entry(tmp_path, "capitalone", {**entry, "month": "2027-03"})
    assert second["months"] == ["2027-01", "2027-03"]
    assert second["ok"] is False
    assert second["problems"][0]["rule"] == "month_gap"

    report = json.loads((tmp_path / "validation-report.json").read_text())
    assert [m["month"] for m in report["months"]] == ["2027-01", "2027-03"]
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert len(manifest["files"]) == 2


def test_check_delivered_catches_placeholder_and_missing_rows():
    from billgen.synth import monthpack

    data = {"transactions": [{"date": "01/04", "desc": "SAMPLE TRANSACTION", "amount": "-12.00"}]}
    report = monthpack.check_delivered("capitalone", "2027-01", data)
    rules = {item["rule"] for item in report["content"]}
    assert "placeholder_text" in rules
    assert report["ok"] is False


def test_check_delivered_accepts_a_well_formed_month():
    from billgen.synth import monthpack

    data = {"transactions": [{"date": "01/04", "desc": "H-E-B #591 AUSTIN TX", "amount": "-12.00"}]}
    report = monthpack.check_delivered("capitalone", "2027-01", data)
    assert report["content"] == []
    assert report["rows"] == 1
