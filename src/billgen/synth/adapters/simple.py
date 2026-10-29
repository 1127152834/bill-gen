"""四个「单表流水」模板：美国运通、水星、第一资本、索菲。

它们共用同一形状（期初 + 一维交易数组 + 若干页脚字段），差异只在字段名与日期格式，
所以放在一个模块里，用四个薄函数分别映射。
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from .. import refs
from ..ledger import LedgerDocument, LedgerRow
from ..line_styles import (
    amex_desc,
    capitalone_category,
    capitalone_desc,
    mercury_desc,
    mercury_type,
    sofi_desc,
    sofi_type,
)
from ..money import (
    cents_to_float,
    fmt_month_day,
    fmt_month_day_year,
    fmt_month_year,
    fmt_slash_year,
)
from ._common import address_lines, bank_block, clip, customer_lines, deep, statement_memo

__all__ = ["build_amex", "build_capitalone", "build_mercury", "build_sofi"]

DESC_LIMIT = 44


def _key(ledger: LedgerDocument, row: LedgerRow) -> str:
    """同一行在本模板里的稳定派生键（编号要可复算，不能随机）。"""
    return f"{ledger.seed}:{row.date.isoformat()}:{row.amount_cents}:{row.description}"


def _signed(row: LedgerRow) -> float:
    return cents_to_float(row.signed_cents)


def _absolute(row: LedgerRow) -> float:
    return cents_to_float(row.amount_cents)


def _addr(ledger: LedgerDocument) -> list[str]:
    return address_lines(ledger)


def build_amex(ledger: LedgerDocument, defaults: dict[str, Any]) -> dict[str, Any]:
    d = deep(defaults)
    d.update(
        {
            "customer_name": ledger.holder.name.upper(),
            "address_lines": _addr(ledger),
            "account_ending": ledger.account.last4,
            "account_name": ledger.account.product,
            "bank": bank_block(ledger),
            # 出件日 = 期末 + 2 天：真账单是结单后 1–3 个工作日出件，
            # 和期末同一天等于「当天结单当天寄」。
            "statement_date": (ledger.period.end + timedelta(days=2)).strftime("%m/%d/%Y"),
            "period_start": ledger.period.start.strftime("%m/%d/%Y"),
            "period_end": ledger.period.end.strftime("%m/%d/%Y"),
            "days_in_period": ledger.period.days,
            "beginning_balance": cents_to_float(ledger.opening_cents),
            "transactions": [
                {
                    "date": r.date.strftime("%m/%d/%Y"),
                    "desc": clip(amex_desc(ledger, r, r.description), DESC_LIMIT + 12),
                    "amount": _signed(r),
                    "id": f"ID {r.reference}" if r.reference.isdigit() else "ID " + refs.amex_reference(_key(ledger, r)),
                    "memo": statement_memo(r),
                    "type": "Deposit" if r.direction == "credit" else "Withdrawal",
                }
                for r in ledger.rows
            ],
        }
    )
    return d


MERCURY_TYPE = {
    "POS": ("Card", "out", "card"),
    "ATM": ("ATM Withdrawal", "out", "card"),
    "CHECK": ("Check", "out", "achOut"),
    "FEE": ("Fee", "out", "achOut"),
    "ZELLE": ("Transfer Out", "out", "biArrow"),
    "TRANSFER": ("Transfer Out", "out", "biArrow"),
    "PAYMENT": ("ACH Out", "out", "achOut"),
    "ACH": ("ACH Out", "out", "achOut"),
    "DEPOSIT": ("ACH In", "in", "achIn"),
    "INTEREST": ("Interest", "in", "infoCircle"),
    "REFUND": ("ACH In", "in", "achIn"),
}


def build_mercury(ledger: LedgerDocument, defaults: dict[str, Any]) -> dict[str, Any]:
    d = deep(defaults)
    transactions = []
    for r in ledger.rows:
        label, direction, icon = mercury_type(r)
        transactions.append(
            {
                "date": fmt_month_day(r.date),
                "desc": clip(mercury_desc(ledger, r, r.description), DESC_LIMIT),
                "amount": _signed(r),
                "id": refs.internal_id(_key(ledger, r)),
                "memo": statement_memo(r),
                "type": label,
                "dir": direction,
                "icon": icon,
            }
        )
    d.update(
        {
            "company": ledger.holder.name.upper(),
            "address": _addr(ledger),
            "account_type": "Checking",
            "account_number": ledger.account.display,
            "routing_number": ledger.account.routing,
            "beginning_balance": cents_to_float(ledger.opening_cents),
            "period_label": fmt_month_year(ledger.period.end),
            "period_start": fmt_month_day_year(ledger.period.start),
            "period_end": fmt_month_day_year(ledger.period.end),
            "period_days": str(ledger.period.days),
            "transactions": transactions,
        }
    )
    return d


def build_capitalone(ledger: LedgerDocument, defaults: dict[str, Any]) -> dict[str, Any]:
    d = deep(defaults)
    d.update(
        {
            "bank": bank_block(ledger),
            "customer_name": ledger.holder.name.upper(),
            # 这一版式第 1 页的头块是「姓名 + 地址」整块画的（模板默认数据第一行就是姓名），
            # 只给地址会印出一张没有户名的账单（实测）。
            "address_lines": customer_lines(ledger),
            "footer_address": ", ".join(_addr(ledger)).upper().strip(", "),  # 页脚只放地址，不带户名
            "headline_month": fmt_month_year(ledger.period.end),
            "period_range": f"{ledger.period.start.strftime('%b %d')} - "
            f"{ledger.period.end.strftime('%b %d, %Y')}",
            "summary_start_label": ledger.period.start.strftime("%b %d"),
            "summary_end_label": ledger.period.end.strftime("%b %d"),
            "closing_date": ledger.period.end.strftime("%m/%d/%Y"),
            "account_name": ledger.account.product,
            "account_number": ledger.account.display,
            "days_in_cycle": str(ledger.period.days),
            "opening_balance": cents_to_float(ledger.opening_cents),
            "transactions": [
                {
                    "date": r.date.strftime("%m/%d"),
                    "desc": clip(capitalone_desc(ledger, r, r.description), DESC_LIMIT + 8),
                    "amount": _signed(r),
                    "id": refs.internal_id(_key(ledger, r)),
                    "memo": statement_memo(r),
                    "category": capitalone_category(ledger, r, r.description),
                    "type": "Deposit" if r.direction == "credit" else "Withdrawal",
                }
                for r in ledger.rows
            ],
        }
    )
    return d


def build_sofi(ledger: LedgerDocument, defaults: dict[str, Any]) -> dict[str, Any]:
    d = deep(defaults)
    d.update(
        {
            "holder_name": ledger.holder.name.upper(),
            "address_lines": _addr(ledger),
            "bank": bank_block(ledger),
            # 法务段落里印着原行总部地址与客服线，品牌态要换成这组虚构值
            "contact_phone": ledger.bank.phone_service,
            # 第三个槽位是空行占位（原稿三行地址），补空串避免越界
            "contact_address": [ledger.bank.po_box, ledger.bank.city_state_zip, ""],
            "account_number": ledger.account.display,
            "account_last4": ledger.account.last4,
            "account_type": ledger.account.product,
            "statement_period": f"{ledger.period.start.strftime('%m/%d/%Y')} - "
            f"{ledger.period.end.strftime('%m/%d/%Y')}",
            "beginning_balance": cents_to_float(ledger.opening_cents),
            "beginning_as_of": ledger.period.start.strftime("%m/%d/%Y"),
            "current_balance": cents_to_float(ledger.closing_cents),
            "current_as_of": ledger.period.end.strftime("%m/%d/%Y"),
            "transactions": [
                {
                    "date": fmt_slash_year(r.date),
                    "desc": clip(sofi_desc(ledger, r, r.description), DESC_LIMIT + 8),
                    "amount": _signed(r),
                    "id": refs.internal_id(_key(ledger, r)),
                    "txid": refs.sofi_txid(_key(ledger, r)),
                    "memo": statement_memo(r),
                    "type": sofi_type(r),
                }
                for r in ledger.rows
            ],
        }
    )
    return d


def count_rows(data: dict[str, Any]) -> int:
    return len(data.get("transactions") or ())
