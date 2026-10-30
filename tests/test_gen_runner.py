"""`billgen.gen.runner` 的离线测试：产物布局、摘要字段、缺少配置时的失败路径。

真实生成要打真模型，那部分不进单测；这里只测「生成结果 → 磁盘产物 → 给页面的摘要」
这条确定性链路，以及配置缺失时的报错是否是人话。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from billgen.gen import runner as R
from billgen.gen.pipeline import GenResult, MonthOutcome
from billgen.jobs import JobQueue


def _outcome(month: str, rows: int = 3, ok: bool = True) -> MonthOutcome:
    return MonthOutcome(
        month=month,
        data={"template": "chase-personal", "period": {"label": month}, "transactions": []},
        ok=ok,
        report={"month": month, "rows": rows, "opening": 100.0, "closing": 200.0},
        warnings=[] if ok else ["合计对不上"],
    )


def _result(*outcomes: MonthOutcome) -> GenResult:
    return GenResult(
        template="chase-personal",
        months=list(outcomes),
        warnings=["audit: 一条提示"],
        audit={"findings": []},
        usage={"llmCalls": 3, "tavilyQueries": 5, "tokens": {"prompt": 10, "completion": 20}},
    )


def test_row_count_accepts_int_and_list():
    assert R._row_count({"rows": 7}) == 7
    assert R._row_count({"rows": [1, 2, 3]}) == 3
    assert R._row_count({}) == 0
    assert R._row_count({"rows": None}) == 0


def test_month_files_writes_one_json_per_month(tmp_path: Path):
    files = R._month_files(_result(_outcome("2026-07"), _outcome("2026-08")), tmp_path)
    names = [f["name"] for f in files]
    assert names == ["chase-personal-2026-07.json", "chase-personal-2026-08.json"]
    assert json.loads((tmp_path / names[0]).read_text())["period"]["label"] == "2026-07"
    assert all(f["ok"] for f in files)
    assert [f["rows"] for f in files] == [3, 3]


def test_month_files_keeps_failed_month_visible(tmp_path: Path):
    files = R._month_files(_result(_outcome("2026-07"), _outcome("2026-08", ok=False)), tmp_path)
    assert files[1]["ok"] is False
    assert files[1]["warnings"] == ["合计对不上"]


def test_batch_report_marks_partial_when_some_month_fails():
    report = R._batch_report(_result(_outcome("2026-07"), _outcome("2026-08", ok=False)))
    assert report["ok"] is False
    assert report["partial"] is True
    assert report["failed_months"] == ["2026-08"]
    assert report["warnings"] == ["audit: 一条提示"]


def test_batch_report_all_good_is_not_partial():
    report = R._batch_report(_result(_outcome("2026-07")))
    assert (report["ok"], report["partial"], report["failed_months"]) == (True, False, [])


def test_manifest_records_sha256_and_rows(tmp_path: Path):
    outcome = _outcome("2026-07", rows=9)
    files = R._month_files(_result(outcome), tmp_path)
    manifest = R._manifest(_result(outcome), files, tmp_path)
    entry = manifest["files"][0]
    assert entry["name"] == "chase-personal-2026-07.json"
    assert entry["rows"] == 9
    assert len(entry["sha256"]) == 64
    assert entry["bytes"] > 0
    assert manifest["months"] == ["2026-07"]


def test_runner_reports_missing_profile_as_human_message(tmp_path: Path):
    """档案不存在时，错误是「档案不存在」，不是一堆栈。"""
    queue = JobQueue(tmp_path / "jobs", R.make_runner(profiles=tmp_path / "profiles.json"), max_workers=1)
    try:
        job = queue.submit("missing-profile", "chase-personal", ["2026-09"])
        done = queue.wait(job.id, timeout=30)
    finally:
        queue.shutdown(wait=True)
    assert done.status == "failed"
    assert "不存在" in (done.error or "")


def test_runner_fails_with_human_message_when_config_missing(tmp_path: Path, monkeypatch):
    """档案齐了但没模型配置时，失败在「缺少模型配置」，而不是抛一堆栈给页面。"""
    from billgen.profiles import ProfileStore, ensure_accounts, new_person

    store_file = tmp_path / "profiles.json"
    store = ProfileStore(store_file)
    store.load()
    person = new_person(
        name="MEI WANG",
        street="2104 S 1st St",
        city="Austin",
        state="TX",
        zip_code="78704",
        display_name="王梅",
        bank="chase-personal",
    )
    ensure_accounts(person)
    store.upsert(person)

    monkeypatch.setenv("BILLGEN_LLM_CONFIG", str(tmp_path / "nope.json"))
    for name in ("BILLGEN_LLM_BASE_URL", "BILLGEN_LLM_API_KEY", "BILLGEN_LLM_MODEL", "TAVILY_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    queue = JobQueue(tmp_path / "jobs", R.make_runner(profiles=store_file), max_workers=1)
    try:
        job = queue.submit(person.id, "chase-personal", ["2026-09"])
        done = queue.wait(job.id, timeout=30)
    finally:
        queue.shutdown(wait=True)
    assert done.status == "failed"
    assert "模型配置" in (done.error or "")


def test_store_path_prefers_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BILLGEN_PROFILES", str(tmp_path / "x.json"))
    assert R.store_path() == tmp_path / "x.json"
    monkeypatch.delenv("BILLGEN_PROFILES")
    monkeypatch.setenv("BILL_GEN_DATA_DIR", str(tmp_path / "data"))
    assert R.store_path() == tmp_path / "data" / "billgen" / "profiles.json"


@pytest.mark.parametrize("months", [["2026-09"], ["2026-08", "2026-09"]])
def test_month_file_names_follow_template_and_month(tmp_path: Path, months: list[str]):
    outcomes = [_outcome(m) for m in months]
    files = R._month_files(_result(*outcomes), tmp_path)
    assert [f["month"] for f in files] == months
    for item in files:
        assert (tmp_path / item["name"]).is_file()


def test_flatten_profile_moves_payload_fields_to_top_level():
    """档案里的地址在 payload 里；不摊平，账单行址会退回模型瞎猜的城市。"""
    record = {
        "id": "abc123",
        "kind": "person",
        "bank": "mercury",
        "display_name": "李衡",
        "payload": {
            "name": "HENG LI",
            "address": {"street": "1119 Freer Avenue", "city": "Temple City", "state": "CA", "zip": "91780"},
        },
        "accounts": {"checking": {"number": "1234567890"}},
    }
    flat = R._flatten_profile(record)
    assert flat["name"] == "HENG LI"
    assert flat["address"]["city"] == "Temple City"
    assert flat["profile_id"] == "abc123"
    assert flat["accounts"]["checking"]["number"] == "1234567890"
    assert flat["display_name"] == "李衡"


def test_flatten_profile_keeps_business_identity():
    record = {
        "id": "biz1",
        "kind": "business",
        "bank": "chase-business",
        "payload": {
            "legalName": "Lumen Creative LLC",
            "entityType": "LLC",
            "address": {"street": "2104 S 1st St", "city": "Austin", "state": "TX", "zip": "78704"},
        },
    }
    flat = R._flatten_profile(record)
    assert flat["legalName"] == "Lumen Creative LLC"
    assert flat["entityType"] == "LLC"
