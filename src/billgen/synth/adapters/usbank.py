"""美国合众银行（US Bank）：存款 / 取款两张分表 + 日终余额。"""
from __future__ import annotations

from typing import Any

from .. import refs
from ..ledger import LedgerDocument, LedgerRow
from ..money import (
    cents_to_grouped,
    cents_to_plain,
    digits,
    fmt_month_day,
    fmt_month_day_year,
)
from ._common import bank_block, clip, customer_lines, deep, metadata, split_rows

__all__ = ["build", "build_business", "build_personal"]

DESC_LIMIT = 36
TYPE_LABEL = {
    "POS": "Purchase",
    "ATM": "ATM",
    "ACH": "ACH",
    "ZELLE": "Zelle",
    "CHECK": "Check",
    "FEE": "Fee",
    "INTEREST": "Interest",
    "TRANSFER": "Transfer",
    "PAYMENT": "Payment",
    "DEPOSIT": "Deposit",
    "REFUND": "Refund",
}


def _usb_account(number: str) -> str:
    """真样本的账号格式是 ``1 964 9173 9161``（1-3-4-4），不是掩码。"""
    digits = "".join(ch for ch in number if ch.isdigit())
    if len(digits) == 12:
        return f"{digits[0]} {digits[1:4]} {digits[4:8]} {digits[8:]}"
    return number


def _key(ledger: LedgerDocument, item: LedgerRow) -> str:
    return f"{ledger.seed}:{item.date.isoformat()}:{item.amount_cents}:{item.description}"


def imb_serial(ledger: LedgerDocument) -> str:
    """IMB 跟踪号：2 位条码 ID + 3 位服务类型 + 6 位客户号 + 9 位流水。"""
    from ..money import digits

    head = "00" + "040"                                  # 条码 ID + 服务类型
    customer = digits(f"imb:{ledger.seed}", 6)
    serial = digits(f"imb-serial:{ledger.seed}:{ledger.period.start.isoformat()}", 9)
    return f"{head}{customer}{serial}"


def _row(ledger: LedgerDocument, item: LedgerRow) -> dict[str, Any]:
    """一行 = 主行（日期/类型/描述/Ref）+ 0~2 条续行。

    真账单的续行规矩：Zelle 给「On <日期> PMT」+「ID=USB…」；
    ACH 给「REF=<15 位 trace>」+ 公司名，下一行补公司号；手续费在 Ref 列填 10 位号。
    """
    key = _key(ledger, item)
    row: dict[str, Any] = {
        "date": fmt_month_day(item.date),
        "type": TYPE_LABEL.get(item.type, "Other"),
        "detail": clip(item.description, DESC_LIMIT),
        "extra": [],
        "amount": cents_to_grouped(item.amount_cents),
    }
    if item.type == "ZELLE":
        row["extra"] = [f"On {item.date:%m/%d/%y} PMT|ID={refs.zelle_payment_id('USB', key)}"]
    elif item.type == "ACH":
        # 续行按真实账单的宽度拆开：trace 一行，类型 + 公司号一行；
        # 拼成一条会在 Ref 列里折行，把 PAYROLL 拆成两半。
        entry = "PAYROLL" if item.direction == "credit" else "PAYMENT"
        row["extra"] = [
            f"REF={item.reference}Y00",
            f"{entry} {digits(f'coid:{key}', 10)}",
        ]
    elif item.type in {"FEE", "INTEREST"}:
        row["ref"] = item.reference
    return row


def _build(ledger: LedgerDocument, defaults: dict[str, Any], template: str) -> dict[str, Any]:
    d = deep(defaults)
    parts = split_rows(ledger)
    meta = metadata(ledger)
    meta.update(
        {
            "x_producer": meta["producer"],
            "x_creator": meta["creator"],
            "x_title": meta["title"],
            "x_subject": meta["subject"],
        }
    )
    d.update(
        {
            "template": template,
            "auto": True,
            "encrypt": bool(d.get("encrypt")),
            "watermark": ledger.watermark,
            "begin_balance": cents_to_plain(ledger.opening_cents),
            "customer_lines": customer_lines(ledger),
            "account_display": _usb_account(ledger.account.number),
            "period": {
                "label": f"{ledger.period.start.strftime('%B %d, %Y')} through "
                f"{ledger.period.end.strftime('%B %d, %Y')}",
                "start": fmt_month_day_year(ledger.period.start),
                "end": fmt_month_day_year(ledger.period.end),
            },
            "metadata": meta,
            # 底稿版式的行名/行址/电话是烙死的；品牌替换要一个 bank 块才知道换成谁
            "bank": bank_block(ledger),
            # 智能邮件条码（IMB）的 20 位跟踪号：按账号与账期派生，不能留底稿原值
            "imb_serial": imb_serial(ledger),
            "deposits": [_row(ledger, r) for r in parts["deposits"]],
            "withdrawals": [
                _row(ledger, r)
                for r in parts["checks"] + parts["atm"] + parts["electronic"] + parts["other"] + parts["fees"]
            ],
            "summary": {
                "begin": cents_to_grouped(ledger.opening_cents),
                "deposits": cents_to_grouped(ledger.credits_cents),
                "withdrawals": cents_to_grouped(-ledger.debits_cents),
                "ending": cents_to_grouped(ledger.closing_cents),
            },
            "daily": [
                [fmt_month_day(day), cents_to_grouped(balance)]
                for day, balance in ledger.daily_activity()
            ],
            "days": str(ledger.period.days),
        }
    )
    return d


def build_personal(ledger: LedgerDocument, defaults: dict[str, Any]) -> dict[str, Any]:
    return _build(ledger, defaults, "us")


def build_business(ledger: LedgerDocument, defaults: dict[str, Any]) -> dict[str, Any]:
    return _build(ledger, defaults, "usbiz")


build = build_personal

def count_rows(data: dict[str, Any]) -> int:
    return len(data.get("deposits") or ()) + len(data.get("withdrawals") or ())
