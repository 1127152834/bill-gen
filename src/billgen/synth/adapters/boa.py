"""美国银行（BOA）个人/商业账单：存款 / 扣款 / 费用三张分表 + 每日余额。"""
from __future__ import annotations

import hashlib
from typing import Any

from .. import refs
from ..ledger import LedgerDocument, LedgerRow
from ..money import (
    cents_to_currency,
    cents_to_grouped,
    cents_to_plain,
    digits,
    fmt_long,
    fmt_slash_short,
)
from ._common import (
    bank_block,
    clip,
    customer_lines,
    deep,
    holder_upper,
    metadata,
    split_rows,
    zelle_party,
)

__all__ = ["build"]

DESC_LIMIT = 40


def _descriptor(ledger: LedgerDocument, row: LedgerRow) -> str:
    """BOA 的 ACH 行是结构化描述符，不是一个商户名。

    真账单样例：``DES:CASHREWARD ID:LI INDN:JPMORGAN CHASE  CO ID:XXXXX12345 PPD``、
    ``Zelle payment to JOHN SMITH Conf# w9wh533i7``、``CHECK 1234``。
    描述列宽 374.7pt，引擎会按宽度自动折行，所以这里写全，让它自然折行。
    """
    key = f"{ledger.seed}:{row.date.isoformat()}:{row.amount_cents}:{row.description}"
    text = clip(row.description, DESC_LIMIT)
    holder = ledger.holder.name.upper()
    orig = "WEB" if row.direction == "debit" else "PPD"
    if row.type == "ACH":
        # NACHA 的 DES 字段是短码：22 字符以内，且不切断单词（真账单不会出现 "PAYROLL DIRE"）
        des = text[:22]
        if len(text) > 22 and " " in des:
            des = des.rsplit(" ", 1)[0]
        des = des.strip() or text[:22].strip()
        holder_field = holder[:22].rstrip()
        # 真账单的 CO ID 是 ``XXXXX12345``：5 个掩码位 + 5 位公司号
        co = digits(f"coid:{key}", 5)
        return f"DES:{des} ID:{row.reference} INDN:{holder_field} CO ID:XXXXX{co} {orig}"
    if row.type == "ZELLE":
        who = zelle_party(row, holder)
        return f"Zelle payment to {who} Conf# {refs.zelle_conf(key)}"
    if row.type == "POS":
        return f"{text} {row.date:%m/%d} Card {ledger.account.last4}"
    if row.type == "CHECK":
        return f"CHECK {row.check_no or ''} {text}".strip()
    return text


def _row(ledger: LedgerDocument, row: LedgerRow, limit: int = DESC_LIMIT) -> dict[str, str]:
    return {
        "date": fmt_slash_short(row.date),
        "desc": _descriptor(ledger, row),
        "amount": cents_to_plain(row.signed_cents),
    }


def _promo(ledger: LedgerDocument) -> dict[str, str]:
    """促销/法务段落里的商品名与评奖来源：全部由场景银行自己派生。"""
    name = ledger.bank.name
    digest = hashlib.sha256(f"promo:{ledger.seed}|{name}".encode()).hexdigest()
    abbr = "".join(name.upper().split())[:12]
    return {
        "overdraft_product": f"{name} Balance Guard\u2122",
        "business_checking": f"{name} Business Checking",
        "business_checking_short": f"{name} Business Checking",
        "award_source": "Small Business Review",
        "award_domain": ledger.bank.domain,
        "safebalance_product": ledger.account.product,
        "form_code": f"{abbr}-{int(digest[:2], 16) % 90 + 10}-{abbr}-0560.B",
        "brokerage_brand": name,
    }


def build(ledger: LedgerDocument, defaults: dict[str, Any]) -> dict[str, Any]:
    d = deep(defaults)
    parts = split_rows(ledger)

    deposits = [_row(ledger, r) for r in parts["deposits"]]
    withdrawals = [_row(ledger, r) for r in parts["checks"] + parts["atm"] + parts["electronic"] + parts["other"]]
    withdrawals.sort(key=lambda r: r["date"])
    fees = [_row(ledger, r) for r in parts["fees"]]

    deposits_cents = sum(r.amount_cents for r in parts["deposits"])
    withdrawals_cents = -sum(
        r.amount_cents for r in parts["checks"] + parts["atm"] + parts["electronic"] + parts["other"]
    )
    fees_cents = -sum(r.amount_cents for r in parts["fees"])

    d.update(
        {
            "auto": True,
            "watermark": ledger.watermark,
            "bank": bank_block(ledger),
            "customer_lines": customer_lines(ledger),
            "owner_names": [holder_upper(ledger)],
            "owner_line": holder_upper(ledger),
            "strip_name": holder_upper(ledger),
            "account_number": ledger.account.grouped,
            "product": ledger.account.product,
            "period": {
                "label": ledger.period.label_long,
                "begin": fmt_long(ledger.period.start),
                "end": fmt_long(ledger.period.end),
            },
            "begin_balance": cents_to_plain(ledger.opening_cents),
            "deposits": deposits,
            "withdrawals": withdrawals,
            "fees": fees,
            "summary": {
                "begin": cents_to_currency(ledger.opening_cents),
                "deposits": cents_to_grouped(deposits_cents),
                "withdrawals": cents_to_grouped(withdrawals_cents),
                "checks": "0.00",
                "fees": cents_to_grouped(fees_cents),
                "ending": cents_to_currency(ledger.closing_cents),
            },
            "totals": {
                "deposits": cents_to_currency(deposits_cents),
                "withdrawals": cents_to_currency(withdrawals_cents),
                "checks": "$0.00",
                "fees": cents_to_currency(fees_cents),
            },
            "stats": {
                "items_prev": "0",
                "days": str(ledger.period.days),
                "avg_ledger": cents_to_currency(ledger.average_daily_cents()),
            },
            "metadata": metadata(ledger),
            "promo": _promo(ledger),
        }
    )
    return d

def count_rows(data: dict[str, Any]) -> int:
    """账单实际承载的交易条数（用于「一行都不许丢」的自检）。"""
    return sum(len(data.get(key) or ()) for key in ("deposits", "withdrawals", "fees"))
