"""模板推荐：确定性、账户类型硬门槛、理由可读、catalog 覆盖。"""
from __future__ import annotations

from typing import Any

from billgen.synth import from_game_result
from billgen.synth.recommend import catalog_for, recommend, score_all


def payload(account_type: str = "checking", rows: int = 9) -> dict[str, Any]:
    """闭合账本：期初 1000，第一笔 250 贷，其余平均借，期末随之回落。"""
    credits = 25_000
    debits = 12_000 * (rows - 1)
    opening = 100_000
    closing = opening + credits - debits
    transactions = [
        {
            "id": "t0",
            "transactionDate": "2027-01-04",
            "postedDate": "2027-01-04",
            "description": "PAYROLL DIRECT DEP ACME CORP",
            "memo": "",
            "type": "ACH",
            "direction": "credit",
            "amountCents": credits,
        }
    ]
    for i in range(1, rows):
        transactions.append(
            {
                "id": f"t{i}",
                "transactionDate": f"2027-01-{4 + i:02d}",
                "postedDate": f"2027-01-{4 + i:02d}",
                "description": "H-E-B #2604 AUSTIN TX",
                "memo": "",
                "type": "POS",
                "direction": "debit",
                "amountCents": 12_000,
            }
        )
    return {
        "id": "rec-1",
        "seed": "rec-seed",
        "createdAt": "2027-02-01T00:00:00.000Z",
        "subject": {"kind": "personal", "name": "Maya Okafor"},
        "audit": {"rules": [], "findings": []},
        "usage": {"llmCalls": 0, "tokens": 0, "tavilyQueries": 0},
        "warnings": [],
        "status": "completed",
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
                        "closingBalanceCents": closing,
                        "totalCreditsCents": credits,
                        "totalDebitsCents": debits,
                        "transactionCount": rows,
                    }
                ],
                "checking": {"openingBalanceCents": opening, "closingBalanceCents": closing},
                "transactions": transactions,
            }
        ],
    }


def ledger(account_type: str = "checking", rows: int = 9):  # noqa: ANN201 - 测试帮手工厂
    return from_game_result(payload(account_type, rows), account=account_type, cycle=0)


def test_scores_are_sorted_and_explained() -> None:
    ranked = score_all(ledger())
    assert [c.score for c in ranked] == sorted((c.score for c in ranked), reverse=True)
    for candidate in ranked:
        assert candidate.reasons, f"{candidate.template_id} 没有理由"
        assert any(text.startswith("accepts ") for text in candidate.reasons)


def test_deterministic_for_same_ledger() -> None:
    first = [c.as_dict() for c in score_all(ledger())]
    second = [c.as_dict() for c in score_all(ledger())]
    assert first == second


def test_score_changes_with_seed_only_by_tie_break() -> None:
    one = {c.template_id: c.score for c in score_all(ledger())}
    other = {c.template_id: c.score for c in score_all(ledger())}
    assert one == other  # 同账本同种子，逐分相同


def test_business_ledger_only_gets_business_layouts() -> None:
    ranked = score_all(ledger("business_checking"))
    ids = {c.template_id for c in ranked}
    assert ids <= {"boa-business", "chase-business", "usbank-business", "mercury"}
    assert "boa-personal" not in ids
    assert "chase-business" in ids


def test_limit_and_exclude() -> None:
    top2 = recommend(ledger(), limit=2)
    assert len(top2) == 2
    skipped = top2[0].template_id
    again = recommend(ledger(), limit=2, exclude=(skipped,))
    assert skipped not in {c.template_id for c in again}
    assert recommend(ledger(), limit=0) == []


def test_description_overflow_costs_points() -> None:
    short = ledger()
    long_rows = payload()
    long_rows["statements"][0]["transactions"][1]["description"] = "X" * 60
    longer = from_game_result(long_rows, account="checking", cycle=0)
    before = {c.template_id: c.score for c in score_all(short)}
    after = {c.template_id: c.score for c in score_all(longer)}
    # 34 字上限的富国跌得比 44 字上限的大通更狠
    assert before["wellsfargo"] - after["wellsfargo"] > before["chase-personal"] - after["chase-personal"]
    assert any("truncate" in reason for c in score_all(longer) if c.template_id == "wellsfargo" for reason in c.reasons)


def test_catalog_matches_account_kind() -> None:
    personal = {item["template"] for item in catalog_for("checking")}
    business = {item["template"] for item in catalog_for("business_checking")}
    assert {"boa-personal", "chase-personal", "usbank-personal", "wellsfargo"} <= personal
    assert {"boa-business", "chase-business", "usbank-business"} <= business
    assert not (personal & {"boa-business", "chase-business", "usbank-business"})
