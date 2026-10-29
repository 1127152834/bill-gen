"""富国银行（Wells Fargo）账单：单表流水（deposit / withdrawal / balance 三列）。"""
from __future__ import annotations

from typing import Any

from ..ledger import LedgerDocument
from ..line_styles import wellsfargo_desc
from ..money import cents_to_grouped, cents_to_plain, fmt_month_day, fmt_two_digit
from ._common import address_lines, bank_contact, clip, deep, metadata

__all__ = ["build"]

DESC_LIMIT = 30


def _desc(ledger: LedgerDocument, row) -> str:
    """描述列：支票行把票号写进描述（真账单有独立 Check Number 列，
    这里的版式把票号并入描述，长度仍在列宽内）。"""
    text = clip(row.description, DESC_LIMIT)
    return clip(wellsfargo_desc(ledger, row, text), DESC_LIMIT + 26)


def build(ledger: LedgerDocument, defaults: dict[str, Any]) -> dict[str, Any]:
    d = deep(defaults)
    bank = deep(d.get("bank") or {})
    # 账本还没定行、或定的就是模板自带那家时，一个字都不动：模板里的
    # `(1-800-869-3557)`、`N.A. (115)`、`NMLSR ID 399801` 都是真件数据，
    # 用派生值盖掉等于把真号码换成看起来对但其实是编的号（实测踩过）。
    template_name = str((d.get("bank") or {}).get("name") or "").strip()
    same_bank = ledger.bank.name.strip().lower() == template_name.lower()
    if (ledger.bank.name or ledger.bank.legal or ledger.bank.domain) and not same_bank:
        bank.update(
            {
                "name": ledger.bank.name,
                "legal_na": ledger.bank.legal,
                "domain": ledger.bank.domain,
            }
        )
        # 空值不许覆盖模板真数据：`phone_main` 派生为空时封面就只剩「Available by
        # phone …:」，号码那一行整条消失（实测）。
        bank.update({k: v for k, v in bank_contact(ledger).items() if v})
    # 富国的模板字段叫 `legal_na`，契约要 `legal`；两者必须同时存在，
    # 否则「账本没定行」时封面行名会被判成空值。
    legal = bank.get("legal") or bank.get("legal_na") or "Wells Fargo Bank, N.A."
    bank["legal"] = legal
    bank["legal_na"] = bank.get("legal_na") or legal
    bank.setdefault("name", "Wells Fargo")
    bank.setdefault("domain", "wellsfargo.com")

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
                "number": ledger.account.display,
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
