"""拆月交付件（每月一份模板 JSON）与三层校验的行为约定。

这些测试盯的是「别静默通过」：交付件被手改一个数字、少一行、金额写成 `1,2x4.00`、
或者两个月之间期初期末接不上，都必须当场报出来。
"""
from __future__ import annotations

import copy
import dataclasses
import json
import pathlib
from datetime import date

import pytest

from billgen.synth import from_game_result
from billgen.synth.ledger import LedgerRow, Period
from billgen.synth.monthpack import (
    MonthArtifact,
    dumps,
    json_rows,
    json_total_issues,
    month_artifacts,
    month_keys,
    split_ledger,
    validate_artifact,
    validate_batch,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "synth_result.json"


def _ledger(template: str = "capitalone"):
    payload = json.loads(FIXTURE.read_text())
    return from_game_result(payload, watermark=False, template=template)


def _three_month_ledger(template: str = "capitalone"):
    """拿现成账本当底子，把流水摊到 7/8/9 三个月，余额链按时序重算。"""
    base = _ledger(template)
    specs = [
        (date(2026, 7, 3), "KESTREL DESIGN CO PAYROLL PPD", "credit", 412_000),
        (date(2026, 7, 18), "WHOLEFDS LMR 6825 AUSTIN TX", "debit", 18_450),
        (date(2026, 8, 5), "CAMDEN LIVING RENT PMT 0801", "debit", 165_000),
        (date(2026, 8, 21), "KESTREL DESIGN CO PAYROLL PPD", "credit", 412_000),
        (date(2026, 9, 9), "AUSTIN ENERGY UTILITY ACH PMT", "debit", 22_640),
        (date(2026, 9, 25), "H-E-B #0611 AUSTIN TX", "debit", 8_734),
    ]
    balance = base.opening_cents
    rows: list[LedgerRow] = []
    for when, description, direction, amount in specs:
        balance += amount if direction == "credit" else -amount
        rows.append(
            LedgerRow(
                date=when,
                posted=when,
                description=description,
                memo="",
                type="ACH" if "PAYROLL" in description or "ACH" in description else "Card",
                direction=direction,
                amount_cents=amount,
                balance_cents=balance,
            )
        )
    return dataclasses.replace(
        base,
        period=Period(start=date(2026, 7, 1), end=date(2026, 9, 30)),
        rows=tuple(rows),
        opening_cents=base.opening_cents,
        closing_cents=balance,
    )


def test_three_months_produce_three_files_with_linked_balances():
    ledger = _three_month_ledger()
    assert month_keys(ledger) == ["2026-07", "2026-08", "2026-09"]

    artifacts = month_artifacts("capitalone", ledger)
    assert [item.month for item in artifacts] == ["2026-07", "2026-08", "2026-09"]
    assert all(item.ok for item in artifacts), [item.report() for item in artifacts if not item.ok]

    # 每个月自己闭合，且上个月期末就是下个月期初
    assert artifacts[0].opening_cents == ledger.opening_cents
    assert artifacts[-1].closing_cents == ledger.closing_cents
    for previous, current in zip(artifacts, artifacts[1:]):
        assert previous.closing_cents == current.opening_cents
    assert sum(item.rows for item in artifacts) == len(ledger.rows)
    assert validate_batch(artifacts) == {
        "ok": True,
        "months": ["2026-07", "2026-08", "2026-09"],
        "failed": [],
        "problems": [],
    }


def test_each_month_json_carries_only_that_month_rows():
    artifacts = month_artifacts("capitalone", _three_month_ledger())
    for artifact in artifacts:
        rows = json_rows(artifact.data)
        assert len(rows) == artifact.rows
        assert all("," not in row["date"] or "/" in row["date"] for row in artifact.data["transactions"])


def test_requested_months_are_respected_and_empty_months_skipped():
    ledger = _three_month_ledger()
    only_september = month_artifacts("capitalone", ledger, ["2026-09"])
    assert [item.month for item in only_september] == ["2026-09"]
    # 只要 9 月时，期初仍然是 8 月末的余额（跳过的月份收支不能凭空消失）
    august_close = ledger.opening_cents + 412_000 - 18_450 + 412_000 - 165_000
    assert only_september[0].opening_cents == august_close

    # 7/8/9 中间的 8 月没有流水时，不硬造一份空账单
    without_august = dataclasses.replace(
        ledger, rows=tuple(row for row in ledger.rows if row.date.month != 8)
    )
    assert month_keys(without_august) == ["2026-07", "2026-09"]


def test_balance_gap_and_month_gap_are_reported():
    artifacts = month_artifacts("capitalone", _three_month_ledger())
    broken = [copy.deepcopy(artifacts[0]), copy.deepcopy(artifacts[2])]
    broken[1].opening_cents += 100
    batch = validate_batch(broken)
    assert batch["ok"] is False
    assert {item["rule"] for item in batch["problems"]} == {"balance_gap", "month_gap"}


def test_failed_month_keeps_the_other_months_deliverable():
    ledger = _three_month_ledger()
    artifacts = month_artifacts("capitalone", ledger)
    artifacts[1].structure.append({"rule": "missing_key", "path": "x", "detail": "坏了"})
    batch = validate_batch(artifacts)
    assert batch["ok"] is False
    assert batch["failed"] == ["2026-08"]
    assert batch["problems"] == []


def test_tampered_json_is_caught_by_json_side_arithmetic():
    ledger = _three_month_ledger()
    artifact = month_artifacts("capitalone", ledger)[0]
    _, sub = split_ledger(ledger)[0]

    changed = copy.deepcopy(artifact)
    changed.structure.clear()
    changed.content.clear()
    changed.data["transactions"][0]["amount"] = -999.99
    validate_artifact(changed, sub)
    assert "json_total_mismatch" in {item["rule"] for item in changed.content}

    dropped = copy.deepcopy(artifact)
    dropped.structure.clear()
    dropped.content.clear()
    dropped.data["transactions"].pop()
    validate_artifact(dropped, sub)
    rules = {item["rule"] for item in dropped.content}
    assert {"json_row_count_mismatch", "json_total_mismatch"} <= rules


def test_broken_money_and_date_in_rows_are_reported():
    ledger = _three_month_ledger()
    artifact = month_artifacts("capitalone", ledger)[0]
    _, sub = split_ledger(ledger)[0]

    broken = copy.deepcopy(artifact)
    broken.structure.clear()
    broken.content.clear()
    broken.data["transactions"][0]["amount"] = "12,3x4.00"
    validate_artifact(broken, sub)
    assert "row_type_mismatch" in {item["rule"] for item in broken.structure}

    undated = copy.deepcopy(artifact)
    undated.structure.clear()
    undated.content.clear()
    undated.data["transactions"][0]["date"] = "13/45/2026"
    validate_artifact(undated, sub)
    assert "date_unparseable" in {item["rule"] for item in undated.structure}


def test_placeholder_text_in_json_is_rejected():
    ledger = _three_month_ledger()
    artifact = month_artifacts("capitalone", ledger)[0]
    _, sub = split_ledger(ledger)[0]

    broken = copy.deepcopy(artifact)
    broken.structure.clear()
    broken.content.clear()
    broken.data["transactions"][1]["desc"] = "SAMPLE TRANSACTION"
    validate_artifact(broken, sub)
    assert "placeholder_text" in {item["rule"] for item in broken.content}


def test_month_build_failure_is_reported_not_raised(monkeypatch):
    """适配器抛异常时，那个月记成 build_failed，其他月份照样交付。"""
    from billgen.synth import monthpack

    real = monthpack.build_data

    def sometimes_fail(template_id, ledger):
        if ledger.period.start.month == 8:
            raise ValueError("模拟适配器炸了")
        return real(template_id, ledger)

    monkeypatch.setattr(monthpack, "build_data", sometimes_fail)
    artifacts = month_artifacts("capitalone", _three_month_ledger())
    by_month = {item.month: item for item in artifacts}
    assert by_month["2026-08"].ok is False
    assert by_month["2026-08"].content[0]["rule"] == "build_failed"
    assert "模拟适配器炸了" in by_month["2026-08"].content[0]["detail"]
    assert by_month["2026-07"].ok and by_month["2026-09"].ok
    assert validate_batch(artifacts)["failed"] == ["2026-08"]


def test_dumps_is_pretty_json_and_keeps_chinese():
    artifact = MonthArtifact(
        month="2026-09",
        template="capitalone",
        data={"note": "中文备注", "amount": "1,234.56"},
        opening_cents=0,
        closing_cents=123456,
        rows=1,
    )
    text = dumps(artifact)
    assert "中文备注" in text and "\n  " in text
    assert json.loads(text)["amount"] == "1,234.56"


def test_json_total_issues_ignores_data_without_rows():
    empty = MonthArtifact(month="2026-09", template="capitalone", data={"transactions": []},
                          opening_cents=0, closing_cents=0, rows=0)
    assert json_total_issues(empty) == [
        {"rule": "no_transactions_found", "path": "", "detail": "交付件里找不到流水行，模板导入后会是空账单"}
    ]


def test_delivered_data_never_keeps_sample_watermark():
    """交付件是成品：template JSON 里若带 watermark=True，导入出片会被印成样张。"""
    from billgen.synth import monthpack as mp

    data = {"watermark": True, "transactions": []}
    mp.check_delivered("mercury", "2026-08", data)
    assert data["watermark"] is False
