"""大通银行（Chase）：个人版走统一流水，商业版走五个分区表。"""
from __future__ import annotations

from typing import Any

from .. import refs
from ..ledger import LedgerDocument, LedgerRow
from ..money import cents_to_currency, cents_to_grouped, cents_to_plain, fmt_month_day, fmt_slash
from ._common import bank_block, clip, deep, metadata, split_rows

__all__ = ["build_business", "build_personal"]

DESC_LIMIT = 38
# 描述列的第二子列（真账单里 `Card Purchase` 与 `10/13 Arcteryx…` 分占两栏）
SUB_PAD = "          "
PERSONAL_TYPES = {
    "CHECK": "electronic",
    "ATM": "atm",
    "FEE": "fee",
    "POS": "atm",
}


def _period(ledger: LedgerDocument) -> dict[str, str]:
    return {
        "label": f"{ledger.period.start.strftime('%B %d, %Y')} through "
        f"{ledger.period.end.strftime('%B %d, %Y')}",
        "start": fmt_month_day(ledger.period.start),
        "end": fmt_month_day(ledger.period.end),
    }


def _base(ledger: LedgerDocument, defaults: dict[str, Any]) -> dict[str, Any]:
    d = deep(defaults)
    d.update(
        {
            "auto": True,
            "encrypt": bool(d.get("encrypt")),
            "watermark": ledger.watermark,
            "begin_balance": cents_to_plain(ledger.opening_cents),
            "customer_lines": [
                ledger.holder.name,
                *ledger.holder.street_lines[:-1],
                ledger.holder.city_state,
            ],
            "zip": ledger.holder.zip5,
            "account_display": ledger.account.display,
            "product": ledger.account.product,
            "bank": bank_block(ledger),
            "period": _period(ledger),
            "metadata": metadata(ledger),
            "micr": {
                "routing": ledger.account.routing,
                "dre": (d.get("micr") or {}).get("dre", "SAMPLE"),
                "cycle": (d.get("micr") or {}).get("cycle", "00"),
                "extra": (d.get("micr") or {}).get("extra", "SAMPLE"),
            },
            # 条码里应装「本账户自己的号」，不能留底稿原值（那是别人的账号）
            "barcode_num": barcode_number(ledger),
        }
    )
    return d


def barcode_number(ledger: LedgerDocument) -> str:
    """20 位跟踪号：账号 12 位 + 账期号 + 6 位流水序号（引擎会把第 11 位改成页码）。"""
    from ..money import digits

    cycle = int(ledger.extras.get("cycle_index", 0))
    tail = digits(f"barcode:{ledger.seed}:{ledger.period.start.isoformat()}", 6)
    return f"{ledger.account.number}{cycle + 1:02d}{tail}"


def _key(ledger: LedgerDocument, row: LedgerRow) -> str:
    return f"{ledger.seed}:{row.date.isoformat()}:{row.amount_cents}:{row.description}"


def _fit(label: str, mid: str, tail: str, budget: float = 336.0, size: float = 9.5) -> str:
    """把「标签 + 中段 + 尾部」拼进描述列，超宽就裁中段（不能压到金额列）。"""
    import fitz

    def width(text: str) -> float:
        return fitz.get_text_length(text, fontname="helv", fontsize=size)

    while mid and width(f"{label}{SUB_PAD}{mid} {tail}") > budget:
        mid = mid[:-1].rstrip()
    return f"{label}{SUB_PAD}{mid} {tail}".rstrip()


def _personal_desc(ledger: LedgerDocument, row: LedgerRow) -> str:
    """个人版描述：类型标签 + 子列里的「日期 + 商户 + 身份号」。

    真账单样例：``Card Purchase        10/13 Arcteryx Burlingame Burlingame CA Card 4499``、
    ``Zelle Payment From Apple U Trucking Inc 31179996497``、
    ``Payment Sent           10/09Pandaremit US 917-9248298 De Card 4499``。
    """
    key = _key(ledger, row)
    text = clip(row.description, DESC_LIMIT)
    stamp = f"{row.date:%m/%d}"
    if row.type == "ZELLE":
        who = text.replace("ZELLE ", "").strip() or "TRANSFER"
        return clip(f"Zelle Payment {who} {digits_zelle(ledger, key)}", DESC_LIMIT + 24)
    if row.type == "POS":
        return _fit("Card Purchase", f"{stamp} {text}", f"Card {ledger.account.last4}")
    if row.type == "ATM":
        return _fit("ATM Withdrawal", f"{stamp} {text}", refs.card_auth(key))
    if row.type == "CHECK":
        return _fit("Check Paid", f"{stamp} {text}", row.check_no or "")
    if row.type == "FEE":
        return _fit("Fee", f"{stamp} {text}", "")
    if row.type == "ACH":
        label = "ACH Credit" if row.direction == "credit" else "ACH Debit"
        return _fit(label, f"{stamp} {text}", "")
    return _fit("", f"{stamp} {text}", "")


def digits_zelle(ledger: LedgerDocument, key: str) -> str:
    """Zelle 交易号：真账单是 11 位数字（``31179996497``）。"""
    from ..money import digits

    return digits(f"zelle11:{ledger.seed}:{key}", 11)


def build_personal(ledger: LedgerDocument, defaults: dict[str, Any]) -> dict[str, Any]:
    d = _base(ledger, defaults)
    transactions = []
    for row in ledger.rows:
        kind = "deposit" if row.direction == "credit" else PERSONAL_TYPES.get(row.type, "electronic")
        transactions.append(
            {
                "date": fmt_slash(row.date),
                "desc": _personal_desc(ledger, row),
                "amount": cents_to_plain(row.signed_cents),
                "type": kind,
            }
        )
    atm_rows = [r for r in ledger.rows if r.type == "ATM"]
    d.update(
        {
            "transactions": transactions,
            "deposits": [],
            "checks": [],
            "electronic_withdrawals": [],
            "other_withdrawals": [],
            "fees": [],
            "daily_balance": [],
            "show_atm": bool(atm_rows),
            "show_pf": False,
            "atm": {
                "name": ledger.holder.name.upper(),
                "card_no": ledger.account.last4,
                "withdrawals": cents_to_grouped(sum(r.amount_cents for r in atm_rows)),
                "purchases": "0.00",
                "credits": "0.00",
            },
            "msf": {
                "lowest_daily": cents_to_plain(ledger.lowest_daily_cents()),
                "payment_solutions": "0.00",
            },
        }
    )
    return d


def _business_desc(ledger: LedgerDocument, row: LedgerRow, limit: int = DESC_LIMIT) -> str:
    """商业版描述：大额转账把 Fedwire 的 IMAD / Trn 直接印在描述里。"""
    key = _key(ledger, row)
    text = clip(row.description, limit)
    if row.direction == "debit" and row.amount_cents >= 100_000:
        imad = refs.fedwire_imad(f"{row.date:%m%d}", key)
        trn = refs.fedwire_trn(key)
        return clip(f"{text} Imad: {imad} Trn: {trn}", limit + 52)
    return text


def build_business(ledger: LedgerDocument, defaults: dict[str, Any]) -> dict[str, Any]:
    d = _base(ledger, defaults)
    parts = split_rows(ledger)

    def row(item, limit: int = DESC_LIMIT) -> dict[str, str]:
        return {"date": fmt_slash(item.date), "desc": _business_desc(ledger, item, limit),
                "amount": cents_to_grouped(item.amount_cents)}

    checks = []
    for item in parts["checks"]:
        entry = row(item, limit=30)
        entry["no"] = str(item.check_no or "")
        entry["paid"] = fmt_slash(item.posted or item.date)
        checks.append(entry)

    d.update(
        {
            "deposits": [row(r) for r in parts["deposits"]],
            "checks": checks,
            "electronic_withdrawals": [row(r) for r in parts["electronic"] + parts["atm"]],
            "other_withdrawals": [row(r) for r in parts["other"]],
            "fees": [row(r) for r in parts["fees"]],
            "transactions": [],
            "daily_balance": [
                [fmt_slash(day), cents_to_grouped(balance)]
                for day, balance in ledger.daily_activity()
            ],
            "msf": {
                "lowest_daily": cents_to_plain(ledger.lowest_daily_cents()),
                "payment_solutions": "0.00",
            },
            "show_atm": False,
            "show_pf": False,
            "checks_footnote": "",
            "summary": {
                "begin": cents_to_currency(ledger.opening_cents),
                "deposits": cents_to_grouped(ledger.credits_cents),
                "checks": cents_to_grouped(-sum(r.amount_cents for r in parts["checks"])),
                "electronic_withdrawals": cents_to_grouped(
                    -sum(r.amount_cents for r in parts["electronic"] + parts["atm"])
                ),
                "other_withdrawals": cents_to_grouped(-sum(r.amount_cents for r in parts["other"])),
                "fees": cents_to_grouped(-sum(r.amount_cents for r in parts["fees"])),
                "ending": cents_to_currency(ledger.closing_cents),
            },
        }
    )
    return d

def count_personal(data: dict[str, Any]) -> int:
    return len(data.get("transactions") or ())


def count_business(data: dict[str, Any]) -> int:
    return sum(
        len(data.get(key) or ())
        for key in ("deposits", "checks", "electronic_withdrawals", "other_withdrawals", "fees")
    )


count_rows = count_personal
