"""synth 层：账本构建、模板适配、闭合自检、端到端渲染。"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from billgen.synth import (
    LedgerError,
    SynthLintError,
    build_data,
    fingerprint,
    from_game_result,
    is_supported,
    render,
)
from billgen.synth.adapters import count_rows
from billgen.synth.lint import issues
from billgen.synth.money import (
    cents_to_currency,
    cents_to_grouped,
    cents_to_plain,
    fmt_long,
    fmt_month_day_year,
    fmt_slash_short,
)


def _tx(day: int, desc: str, cents: int, direction: str, txn_type: str, *, posted: int | None = None) -> dict:
    stamp = f"2027-01-{day:02d}"
    return {
        "id": f"t{day:02d}{txn_type.lower()}",
        "transactionDate": stamp,
        "postedDate": f"2027-01-{posted or day:02d}",
        "description": desc,
        "memo": "",
        "type": txn_type,
        "direction": direction,
        "amountCents": cents,
    }


def _payload(account_type: str = "checking") -> dict:
    transactions = [
        _tx(4, "ACME PAYROLL DIRECT DEP", 250_000, "credit", "ACH"),
        _tx(6, "CAMDEN LIVING RENT PMT 0106", 120_000, "debit", "ACH"),
        _tx(9, "H-E-B #2604 AUSTIN TX", 4_311, "debit", "POS"),
        _tx(12, "ATM WITHDRAWAL 0112 TERMINAL", 10_000, "debit", "ATM"),
        _tx(15, "SERVICE FEE", 1_200, "debit", "FEE"),
        _tx(20, "CHECK 1042", 5_000, "debit", "CHECK"),
    ]
    credits = sum(t["amountCents"] for t in transactions if t["direction"] == "credit")
    debits = sum(t["amountCents"] for t in transactions if t["direction"] == "debit")
    opening = 100_000
    business = account_type == "business_checking"
    subject = (
        {
            "legalName": "Okafor Design Studio LLC",
            "entityType": "llc",
            "industry": "design",
            "description": "Brand design studio",
            "founded": 2019,
            "employeeCount": 2,
            "address": {"street": "2104 S 1st St", "city": "Austin", "state": "TX", "zip": "78704"},
        }
        if business
        else {
            "name": "Maya Okafor",
            "age": 34,
            "address": {"street": "2104 S 1st St", "city": "Austin", "state": "TX", "zip": "78704"},
            "occupation": "Designer",
            "household": "single",
            "hobbies": ["cycling"],
        }
    )
    return {
        "id": "gen-test",
        "seed": "unit-test",
        "createdAt": "2027-02-01T00:00:00.000Z",
        "subject": subject,
        "statements": [
            {
                "bankName": "Harborline Bank",
                "accountType": account_type,
                "accountLast4": "1347",
                "periodStart": "2027-01-01",
                "periodEnd": "2027-01-31",
                "months": [
                    {
                        "cycleStart": "2027-01-01",
                        "cycleEnd": "2027-01-31",
                        "openingBalanceCents": opening,
                        "closingBalanceCents": opening + credits - debits,
                        "totalCreditsCents": credits,
                        "totalDebitsCents": debits,
                        "transactionCount": len(transactions),
                    }
                ],
                "checking": {"opening": opening, "closing": opening + credits - debits},
                "transactions": transactions,
            }
        ],
        "audit": {"rules": [], "findings": []},
        "usage": {"llmCalls": 0, "tokens": 0, "tavilyQueries": 0},
        "warnings": [],
        "status": "completed",
    }


@pytest.fixture()
def ledger():
    return from_game_result(_payload(), account="checking")


@pytest.fixture()
def business_ledger():
    return from_game_result(_payload("business_checking"), account="business_checking")


# ---------------------------------------------------------------- money

def test_money_formats():
    assert cents_to_plain(-120_000) == "-1200.00"
    assert cents_to_grouped(1_234_567) == "12,345.67"
    assert cents_to_currency(-123_456) == "-$1,234.56"


def test_date_formats():
    from datetime import date

    day = date(2027, 1, 4)
    assert fmt_slash_short(day) == "01/04/27"
    assert fmt_long(day) == "January 4, 2027"
    assert fmt_month_day_year(day) == "Jan 04, 2027"


# ---------------------------------------------------------------- 账本

def test_ledger_builds_closed_chain(ledger):
    assert ledger.account.kind == "checking"
    assert ledger.period.start.isoformat() == "2027-01-01"
    assert len(ledger.rows) == 6
    expected = ledger.opening_cents + ledger.credits_cents - ledger.debits_cents
    assert expected == ledger.closing_cents
    assert ledger.rows[-1].balance_cents == ledger.closing_cents
    assert ledger.account.routing.isdigit() and len(ledger.account.routing) == 9


def test_ledger_assigns_sequential_check_numbers(ledger):
    checks = ledger.checks
    assert len(checks) == 1
    assert checks[0].check_no and checks[0].check_no.isdigit()


def test_ledger_rejects_unknown_account():
    with pytest.raises(LedgerError):
        from_game_result(_payload(), account="savings")


def test_ledger_keeps_holder_and_bank_identity(ledger):
    assert ledger.holder.name == "Maya Okafor"
    assert ledger.holder.zip5 == "78704"
    assert ledger.holder.city_state == "Austin TX"
    assert ledger.bank.name == "Harborline Bank"
    assert ledger.bank.legal == "Harborline Bank, N.A."


# ---------------------------------------------------------------- 适配器

PERSONAL_TEMPLATES = (
    "boa-personal", "wellsfargo", "amex", "mercury",
    "capitalone", "sofi", "chase-personal", "usbank-personal",
)
BUSINESS_TEMPLATES = ("boa-business", "chase-business", "usbank-business")


@pytest.mark.parametrize("template_id", PERSONAL_TEMPLATES)
def test_adapter_carries_every_row(template_id, ledger):
    data = build_data(template_id, ledger)
    assert count_rows(template_id, data) == len(ledger.rows)
    assert issues(ledger, data, template_id) == []


@pytest.mark.parametrize("template_id", BUSINESS_TEMPLATES)
def test_business_adapter_carries_every_row(template_id, business_ledger):
    data = build_data(template_id, business_ledger)
    assert count_rows(template_id, data) == len(business_ledger.rows)
    assert issues(business_ledger, data, template_id) == []


def test_adapter_marks_sample_provenance(ledger):
    data = build_data("boa-personal", ledger)
    assert data["watermark"] is True
    assert data["metadata"]["title"].startswith("SAMPLE")
    assert data["metadata"]["producer"] == "BillGen Sample Editor"
    assert data["bank"]["name"] == "Harborline Bank"


def test_adapter_truncates_long_descriptions_without_dropping_rows(ledger):
    long_row = replace(ledger.rows[1], description="X" * 120)
    stretched = replace(ledger, rows=(ledger.rows[0], long_row) + ledger.rows[2:])
    data = build_data("wellsfargo", stretched)
    descriptions = [item["desc"] for item in data["transactions"]]
    assert len(descriptions) == len(stretched.rows)
    assert all(len(text) <= 34 for text in descriptions)


def test_lint_flags_dropped_rows(ledger):
    data = build_data("boa-personal", ledger)
    data["withdrawals"] = data["withdrawals"][:-1]
    found = issues(ledger, data, "boa-personal")
    assert [item.code for item in found] == ["rows_dropped"]
    with pytest.raises(SynthLintError):
        from billgen.synth import verify

        verify(ledger, data, "boa-personal")


def test_lint_flags_broken_closure(ledger):
    broken = replace(ledger, closing_cents=ledger.closing_cents + 1)
    data = build_data("boa-personal", broken, verify_rows=False)
    codes = {item.code for item in issues(broken, data, "boa-personal")}
    assert "balance_not_closed" in codes


def test_lint_flags_account_mismatch(ledger):
    data = build_data("chase-personal", ledger, verify_rows=False)
    codes = {item.code for item in issues(ledger, data, "chase-business")}
    assert "account_mismatch" in codes


def test_business_template_rejects_personal_ledger(ledger):
    with pytest.raises(SynthLintError) as excinfo:
        render("boa-business", ledger)
    assert any(item.code == "account_mismatch" for item in excinfo.value.issues)


# ---------------------------------------------------------------- 渲染

@pytest.mark.parametrize("template_id", PERSONAL_TEMPLATES)
def test_render_produces_pdf(template_id, ledger):
    result = render(template_id, ledger)
    assert result.pages >= 1
    assert result.pdf.startswith(b"%PDF-")
    assert len(result.pdf) > 20_000


@pytest.mark.parametrize("template_id", BUSINESS_TEMPLATES)
def test_render_business_pdf(template_id, business_ledger):
    result = render(template_id, business_ledger)
    assert result.pages >= 1
    assert result.pdf.startswith(b"%PDF-")
    assert len(result.pdf) > 20_000


def test_render_is_deterministic(ledger):
    # 底稿字体是按需扩展的：首次渲染会把缺的字补进 prepared 文档（进程级缓存），
    # 所以先热身一次，再比对两次「热态」渲染——这才是「同数据同字节」的真正承诺。
    render("chase-personal", ledger)
    first = render("chase-personal", ledger).pdf
    second = render("chase-personal", ledger).pdf
    assert first == second


def test_fingerprint_is_stable(ledger):
    first = render("usbank-personal", ledger).pdf
    second = render("usbank-personal", ledger).pdf
    assert fingerprint(first) == fingerprint(second)


def test_static_templates_are_not_advertised():
    assert is_supported("boa-personal")
    assert not is_supported("alliant")
    assert not is_supported("ally")


# ---------------------------------------------------------------- CLI

def test_cli_synth_render(tmp_path, capsys):
    from billgen.cli import main

    payload = tmp_path / "result.json"
    payload.write_text(json.dumps(_payload()), encoding="utf-8")
    out = tmp_path / "statement.pdf"
    code = main(["synth", "render", "usbank-personal", "--from", str(payload), "-o", str(out)])
    assert code == 0
    row = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert row["rows"] == 6
    assert row["pages"] >= 1
    assert out.is_file() and out.stat().st_size > 20_000


def issues_for(payload):
    """把结果包渲染一遍，同时收集 lint 的提醒项。"""
    from billgen.synth import build_data, from_game_result, lint, render

    ledger = from_game_result(payload, account="checking", cycle=0)
    data = build_data("boa-personal", ledger)
    advisories = lint.verify(ledger, data, "boa-personal")
    return type("Report", (), {"advisories": advisories, "result": render("boa-personal", ledger)})()


def test_overdrawn_ledger_still_renders_with_advisory():
    """真实账户会透支：负数余额不该拦渲染，但要作为提醒报出来。"""
    payload = _payload()
    statement = payload["statements"][0]
    month = statement["months"][0]
    # 期初只有 200 美元，第一笔租金就把它打到负数
    month["openingBalanceCents"] = 20_000
    statement["checking"] = {"opening": 20_000, "closing": 20_000}
    month["closingBalanceCents"] = 20_000
    rows = statement["transactions"]
    rows[1]["amountCents"] = 500_000
    month["totalDebitsCents"] = sum(t["amountCents"] for t in rows if t["direction"] == "debit")
    report = issues_for(payload)
    codes = {issue.code for issue in report.advisories}
    assert "negative_balance" in codes
    assert report.result.pages >= 1
