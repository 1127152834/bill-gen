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
