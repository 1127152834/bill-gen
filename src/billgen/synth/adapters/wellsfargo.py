"""富国银行（Wells Fargo）账单：单表流水（deposit / withdrawal / balance 三列）。"""
from __future__ import annotations

from typing import Any

from ..ledger import LedgerDocument
from ..money import cents_to_grouped, cents_to_plain, fmt_month_day, fmt_two_digit
from ._common import address_lines, bank_contact, clip, deep, metadata

__all__ = ["build"]

DESC_LIMIT = 30


def _desc(ledger: LedgerDocument, row) -> str:
    """描述列：支票行把票号写进描述（真账单有独立 Check Number 列，
    这里的版式把票号并入描述，长度仍在列宽内）。"""
    text = clip(row.description, DESC_LIMIT)
    if row.type == "CHECK" and row.check_no:
        return clip(f"{text} CHECK {row.check_no}", DESC_LIMIT + 12)
    return text


def build(ledger: LedgerDocument, defaults: dict[str, Any]) -> dict[str, Any]:
    d = deep(defaults)
    bank = deep(d.get("bank") or {})
    bank.update(
        {
            "name": ledger.bank.name,
            "legal_na": ledger.bank.legal,
            "domain": ledger.bank.domain,
        }
    )
    bank.update(bank_contact(ledger))

    transactions = []
    for row in ledger.rows:
        item = {"date": fmt_two_digit(row.date), "desc": _desc(ledger, row)}
        if row.direction == "credit":
            item["deposit"] = cents_to_grouped(row.amount_cents)
        else:
            item["withdrawal"] = cents_to_grouped(row.amount_cents)
        transactions.append(item)

    deposits = sum(r.amount_cents for r in ledger.rows if r.direction == "credit")
    withdrawals = sum(r.amount_cents for r in ledger.rows if r.direction == "debit")

    d.update(
        {
            "bank": bank,
            "statement_date": fmt_month_day(ledger.period.end),
            "total_pages": str(d.get("total_pages") or ""),
            "customer": {
                "name": ledger.holder.name,
                "address_lines": address_lines(ledger),
            },
            "account": {
                "product": ledger.account.product,
                "number": ledger.account.number,
                "rtn": ledger.account.routing,
                "primary_label": "Primary account",
            },
            "summary": {
                "auto": True,
                "period_start": fmt_two_digit(ledger.period.start),
                "period_end": fmt_two_digit(ledger.period.end),
                "begin_amount": cents_to_plain(ledger.opening_cents),
                "deposits_amount": cents_to_grouped(deposits),
                "withdrawals_amount": cents_to_grouped(withdrawals),
                "ending_amount": cents_to_plain(ledger.closing_cents),
            },
            "transactions": transactions,
            "totals": {
                "deposits": cents_to_grouped(deposits),
                "withdrawals": cents_to_grouped(withdrawals),
            },
            "fees": {
                "period": fmt_month_day(ledger.period.end),
                "standard": "0.00",
                "paid": "0.00",
            },
            "metadata": metadata(ledger),
            "watermark": ledger.watermark,
        }
    )
    return d

def count_rows(data: dict[str, Any]) -> int:
    return len(data.get("transactions") or ())
