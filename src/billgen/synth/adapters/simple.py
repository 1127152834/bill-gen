"""三个「单表流水」模板：水星、第一资本、索菲。

它们共用同一形状（期初 + 一维交易数组 + 若干页脚字段），差异只在字段名与日期格式，
所以放在一个模块里，用三个薄函数分别映射。
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from .. import refs
from ..ledger import LedgerDocument, LedgerRow
from ..line_styles import (
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
from ._common import (
    address_lines,
    bank_block,
    clip,
    customer_lines,
    deep,
    rebrand_literals,
    statement_memo,
)

__all__ = ["build_capitalone", "build_mercury", "build_sofi"]

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
    return rebrand_literals(d, ledger, "mercury")


def build_capitalone(ledger: LedgerDocument, defaults: dict[str, Any]) -> dict[str, Any]:
    d = deep(defaults)
    d.update(
        {
            "bank": bank_block(ledger),
            "customer_name": ledger.holder.name.upper(),
            # 这一版式第 1 页的头块是「姓名 + 地址」整块画的（模板默认数据第一行就是姓名），
            # 只给地址会印出一张没有户名的账单（实测）。
            "address_lines": customer_lines(ledger),
            # 页脚左下第三格印的是**Capital One 360 的账单寄送地址**（参考件：
            # `P.O. Box 85123, Richmond, VA 23285`），不是持卡人住址；早先照持卡人地址
            # 填，等于把「我们家地址」印在银行账单页脚上。电话同理走 360 专线。
            "footer_address": "P.O. Box 85123, Richmond, VA 23285",
            "footer_phone": "1-888-464-0727",
            "headline_month": fmt_month_year(ledger.period.end),
            "period_range": f"{ledger.period.start.strftime('%b %d')} - "
            f"{ledger.period.end.strftime('%b %d, %Y')}",
            "summary_start_label": ledger.period.start.strftime("%b %d"),
            "summary_end_label": ledger.period.end.strftime("%b %d"),
            "closing_date": ledger.period.end.strftime("%m/%d/%Y"),
            # Account Summary 那一行是「产品名...卡尾4」（参考件 `360 Checking...5521`）；
            # 直接用账本里的产品名会印成 `Everyday Checking`，与 360 版式的产品线对不上。
            "account_name": f"{str(d.get('account_title') or '360 Checking').strip()}...{ledger.account.last4}",
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
    return rebrand_literals(d, ledger, "capitalone")


def build_sofi(ledger: LedgerDocument, defaults: dict[str, Any]) -> dict[str, Any]:
    d = deep(defaults)
    d.update(
        {
            "holder_name": ledger.holder.name.upper(),
            "address_lines": _addr(ledger),
            "bank": bank_block(ledger),
            # 法务段落里印着原行总部地址与客服线，品牌态要换成这组虚构值
            # 联系方式块印的是参考件原样（字母号 + 三行地址），模板默认数据最准
            "contact_phone": d.get("contact_phone") or ledger.bank.phone_service,
            # 参考件这三行是「法定行名 / 街道 / 城市州邮编」，空值退回模板默认值
            "contact_address": d.get("contact_address") or [
                ledger.bank.legal,
                ledger.bank.po_box,
                ledger.bank.city_state_zip,
            ],
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
    return rebrand_literals(d, ledger, "sofi")


def count_rows(data: dict[str, Any]) -> int:
    return len(data.get("transactions") or ())
