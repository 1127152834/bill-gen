"""流水行的「号」：形状要对，且必须可复算。

真账单里这些号的长度与前缀都有惯例（见 docs/statement-rows.md），
测试锁住形状，避免以后改适配器时又把 `BG250701001` 这种内部串塞回去。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from billgen.synth import from_game_result, refs
from billgen.synth.adapters import binding_for

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "synth_result.json"


def _ledger():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return from_game_result(payload, account="checking", cycle=0)


def test_ach_trace_is_fifteen_digits_starting_with_routing() -> None:
    trace = refs.ach_trace("735248661", "seed")
    assert re.fullmatch(r"\d{15}", trace)
    assert trace.startswith("73524866")


def test_fedwire_imad_is_twenty_two_chars_starting_with_mmdd() -> None:
    imad = refs.fedwire_imad("0701", "seed")
    assert len(imad) == 22
    assert imad.startswith("0701")


def test_zelle_conf_is_nine_lowercase_alnum() -> None:
    assert re.fullmatch(r"[a-z0-9]{9}", refs.zelle_conf("seed"))


def test_sofi_txid_matches_batch_and_sequence_shape() -> None:
    assert re.fullmatch(r"\d{3}-\d{8}", refs.sofi_txid("seed"))


def test_internal_and_card_numbers_have_bank_shape() -> None:
    assert re.fullmatch(r"\d{14}", refs.internal_id("seed"))
    assert re.fullmatch(r"\d{6}", refs.card_auth("seed"))


def test_references_are_deterministic() -> None:
    for func in (refs.ach_trace, refs.sofi_txid, refs.internal_id, refs.zelle_conf):
        first = func("735248661", "seed") if func is refs.ach_trace else func("seed")
        second = func("735248661", "seed") if func is refs.ach_trace else func("seed")
        assert first == second


def test_ledger_assigns_a_reference_to_every_row() -> None:
    ledger = _ledger()
    assert ledger.rows
    for row in ledger.rows:
        assert row.reference, f"{row.date} {row.description} 缺号"
    # ACH 行必须是 15 位 trace，且前 8 位等于账户路由号
    for row in ledger.rows:
        if row.type == "ACH":
            assert row.reference.startswith(ledger.account.routing[:8])
            assert len(row.reference) == 15


def test_amex_prints_trace_not_internal_prefix() -> None:
    ledger = _ledger()
    binding = binding_for("amex")
    data = binding.build(ledger, {})
    ids = [tx["id"] for tx in data["transactions"]]
    assert ids and all(value.startswith("ID ") for value in ids)
    assert not any("BG" in value for value in ids)
    ach = [tx for tx in data["transactions"] if len(tx["id"].split()[1]) == 15]
    assert ach, "至少一行应印 15 位 ACH trace"


def test_sofi_uses_batch_sequence_transaction_id() -> None:
    ledger = _ledger()
    data = binding_for("sofi").build(ledger, {})
    for tx in data["transactions"]:
        assert re.fullmatch(r"\d{3}-\d{8}", tx["txid"])


def test_capitalone_fills_category_column() -> None:
    """CATEGORY 列填商户品类（真账单如此），不能填 Credit/Debit（那是金额方向）。"""
    ledger = _ledger()
    data = binding_for("capitalone").build(ledger, {})
    categories = {tx["category"] for tx in data["transactions"]}
    assert categories <= {"Groceries", "Dining", "Gas/Automotive", "Transportation", "Merchandise",
                           "Health Care", "Bills & Utilities", "Entertainment", "Travel",
                           "Insurance", "Cash", "Fees", "Interest", "Transfers", "Income", "Other"}
    assert categories & {"Groceries", "Income", "Bills & Utilities"}


def test_same_ledger_reads_differently_per_bank() -> None:
    """同一笔卡消费在 Chase / Wells Fargo / Capital One 上必须是三种行文。"""
    ledger = _ledger()
    chase = [r["desc"] for r in binding_for("chase-personal").build(ledger, {})["transactions"]]
    wf = [r["desc"] for r in binding_for("wellsfargo").build(ledger, {})["transactions"]]
    c1 = [r["desc"] for r in binding_for("capitalone").build(ledger, {})["transactions"]]
    assert any(d.startswith("Card Purchase ") and " Card " in d for d in chase)
    assert any(d.startswith("Card Purchase ") and " TX" in d for d in wf)
    assert any(" TX" in d and not d.startswith("Card Purchase") for d in c1), c1


def test_sofi_type_column_is_specific() -> None:
    """SoFi 的类型列是 Direct Payment / Card Purchase / Interest Earned，不是笼统的 Withdrawal。"""
    ledger = _ledger()
    data = binding_for("sofi").build(ledger, {})
    types = {tx["type"] for tx in data["transactions"]}
    assert types <= {"Deposit", "Withdrawal", "Direct Payment", "Card Purchase",
                     "Interest Earned", "Transfer", "Check", "Fee"}
    assert "Card Purchase" in types


def test_usbank_puts_trace_in_continuation_lines() -> None:
    ledger = _ledger()
    data = binding_for("usbank-personal").build(ledger, {})
    rows = data["deposits"] + data["withdrawals"]
    ach = [row for row in rows if row["type"] == "ACH"]
    assert ach and all(row["extra"] and row["extra"][0].startswith("REF=") for row in ach)
    fees = [row for row in rows if row["type"] == "Fee"]
    assert fees and all(row.get("ref") for row in fees)


def test_chase_personal_uses_description_sub_column() -> None:
    ledger = _ledger()
    data = binding_for("chase-personal").build(ledger, {})
    descs = [tx["desc"] for tx in data["transactions"]]
    assert any(desc.startswith("ACH Debit") or desc.startswith("ACH Credit") for desc in descs)
    for desc in descs:
        assert len(desc) <= 96, desc


def test_boa_writes_structured_ach_descriptors() -> None:
    ledger = _ledger()
    data = binding_for("boa-personal").build(ledger, {})
    rows = data["deposits"] + data["withdrawals"]
    ach = [row for row in rows if row["desc"].startswith("DES:")]
    assert ach, "ACH 行应是 DES:/ID:/INDN:/CO ID: 结构"
    for row in ach:
        assert "INDN:" in row["desc"] and "CO ID:" in row["desc"]


def test_binding_rejects_credit_accounts() -> None:
    assert binding_for("boa-personal").accepts("credit") is False
