"""真实化处理（入账日、收单方前缀）与 PDF 文档画像的回归测试。"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import fitz
import pytest

from billgen.synth import from_game_result, realism, render
from billgen.synth.adapters import chase as chase_adapter
from billgen.synth.ledger import (
    AccountRef,
    BankIdentity,
    Holder,
    LedgerDocument,
    LedgerRow,
    Period,
)
from billgen.synth.profile import PRODUCERS, build_docinfo

FIXTURE = Path(__file__).parent / "fixtures" / "synth_result.json"


def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def ledger_with(rows: list[LedgerRow]) -> LedgerDocument:
    opening = 500_000
    balance = opening
    filled = []
    for row in rows:
        balance += row.amount_cents if row.direction == "credit" else -row.amount_cents
        filled.append(replace(row, balance_cents=balance))
    return LedgerDocument(
        bank=BankIdentity.derive("Harborline Bank", "t"),
        holder=Holder("Maya Okafor", ("2104 S 1st St", "Austin TX 78704")),
        account=AccountRef(kind="checking", last4="1347", number="729624661347",
                           routing="735248661", product="Everyday Checking"),
        period=Period(date(2026, 7, 1), date(2026, 7, 31)),
        opening_cents=opening,
        closing_cents=balance,
        rows=tuple(filled),
        seed="t",
    )


def row(day: str, kind: str, cents: int, desc: str = "TEST MERCHANT") -> LedgerRow:
    y, m, d = (int(part) for part in day.split("-"))
    return LedgerRow(date=date(y, m, d), posted=date(y, m, d), description=desc,
                     memo="", type=kind, direction="debit", amount_cents=cents,
                     balance_cents=0)


def test_business_day_knows_weekends_and_holidays():
    assert realism.is_business_day(date(2026, 7, 2)) is True      # 周四
    assert realism.is_business_day(date(2026, 7, 4)) is False     # 周六（独立日）
    assert realism.is_business_day(date(2026, 7, 3)) is False     # 独立日调休到周五
    assert realism.is_business_day(date(2026, 11, 26)) is False   # 感恩节


def test_ach_posting_is_pulled_back_to_the_previous_business_day():
    book = ledger_with([row("2026-07-11", "ACH", 165_000, "CAMDEN LIVING RENT PMT")])
    out, stats = realism.apply_all(book)
    assert stats["postings"] == 1
    assert out.rows[0].date == date(2026, 7, 10)      # 周六 → 周五
    assert out.rows[0].posted == date(2026, 7, 10)


def test_card_and_atm_stay_on_the_weekend():
    book = ledger_with([
        row("2026-07-11", "POS", 4_299, "H-E-B #2604 AUSTIN TX"),
        row("2026-07-12", "ATM", 10_000, "ATM WITHDRAWAL 0712 TERMINAL 0IY"),
    ])
    out, stats = realism.apply_all(book)
    assert stats["postings"] == 0
    assert [r.date.day for r in out.rows] == [11, 12]


def test_pos_descriptors_get_acquirer_prefix_once():
    book = ledger_with([row("2026-07-06", "POS", 4_299, "H-E-B #2604 AUSTIN TX")])
    out, stats = realism.apply_all(book)
    assert stats["descriptors"] == 1
    text = out.rows[0].description
    assert text.startswith(("SQ *", "TST* ", "PAYPAL *", "AMZN Mktp US*", "SP ")), text
    again, stats2 = realism.apply_all(out)
    assert stats2["descriptors"] == 0, "已经带前缀的行不能二次加前缀"
    assert again.rows[0].description == text


def test_rebalance_keeps_the_ledger_closed():
    book = ledger_with([
        row("2026-07-11", "ACH", 165_000),
        row("2026-07-12", "POS", 4_299),
    ])
    out, _ = realism.apply_all(book)
    assert out.closing_cents == out.opening_cents + out.credits_cents - out.debits_cents
    assert len(out.rows) == len(book.rows)


@pytest.mark.parametrize("template_id", sorted(PRODUCERS))
def test_docinfo_matches_the_template_family(template_id):
    book = from_game_result(payload(), account="checking", cycle=0)
    info = build_docinfo(template_id, book)
    assert info["producer"] == PRODUCERS[template_id][0]
    assert "BillGen" not in info["producer"]
    stamp = info["creationDate"]
    day = date(int(stamp[2:6]), int(stamp[6:8]), int(stamp[8:10]))
    assert 1 <= (day - book.period.end).days <= 3, stamp
    assert 6 <= int(stamp[10:12]) <= 22, stamp


def test_rendered_pdf_carries_the_docinfo_profile():
    book = from_game_result(payload(), account="checking", cycle=0)
    with fitz.open(stream=render("chase-personal", book).pdf, filetype="pdf") as doc:
        meta = doc.metadata
        assert meta["producer"] == PRODUCERS["chase-personal"][0]
        assert meta["creator"] == ""
        assert meta["creationDate"] == meta["modDate"]
        # 标题层也按真账单做旧：exiftool 一眼能看到，不能留 SAMPLE/Fictional
        title = meta["title"] or ""
        assert title and not any(w in title.upper() for w in ("SAMPLE", "FICTIONAL"))
        assert "SAMPLE" not in (meta["subject"] or "").upper()


def test_chase_barcode_encodes_our_own_account():
    book = from_game_result(payload(), account="checking", cycle=0)
    number = chase_adapter.barcode_number(book)
    assert len(number) == 20 and number.isdigit()
    assert number.startswith(book.account.number)


@pytest.mark.parametrize("template_id", [
    "boa-personal", "wellsfargo", "chase-personal", "usbank-personal",
    "amex", "mercury", "capitalone", "sofi",
])
def test_catalog_has_no_structure_markers(template_id):
    """真实账单的 catalog 不带 tagged 结构树；我们连键名都不留。"""
    book = from_game_result(payload(), account="checking", cycle=0)
    with fitz.open(stream=render(template_id, book).pdf, filetype="pdf") as doc:
        keys = doc.xref_get_keys(doc.pdf_catalog())
        assert "StructTreeRoot" not in keys
        assert "MarkInfo" not in keys
        assert doc.page_count > 0


def test_chase_tracking_label_follows_the_barcode_number():
    """条码旁整串竖排跟踪号跟着条码号走（字形码换掉，文本层仍是 0）。

    整合版底稿把这 20 位画成同一个 Type3 单元（``/F3 0.24 Tf <f0f4f6...> Tj``），
    所以按 x 区间取整串再逐码位解回数字，比按"4 位独立单元"断言稳。
    """
    from billgen.synth.adapters.chase import barcode_number
    from billgen.themes.chase import chstream
    from billgen.themes.chase import generate_personal as gp

    book = from_game_result(payload(), account="checking", cycle=0)
    number = barcode_number(book)
    code_of = {gp.encode("personal", "F3", str(d))[0]: str(d) for d in range(10)}
    with fitz.open(stream=render("chase-personal", book).pdf, filetype="pdf") as doc:
        raw = b"".join(doc.xref_stream(x) for x in doc[0].get_contents())
    labels = [
        u.text
        for u in chstream.all_units(chstream.parse(raw))
        if u.x is not None and 585.0 <= u.x <= 625.0 and u.text and len(u.text) == len(number)
    ]
    if not labels:
        pytest.skip("示例底稿不含条码标签单元，跳过（relabel_tracking 对此底稿无操作）")
    assert "".join(code_of[b] for b in labels[0]) == number, (labels[0], number)
