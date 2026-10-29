"""每一家银行的流水行「长什么样」。

同一个账本喂给 11 套版式，行文不能一个样：真账单里
``Chase`` 印 ``Card Purchase 12/11 WHOLE FOODS MKT 105 Card 1347``，
``Capital One`` 把品类填进 CATEGORY 列（``Groceries``），
``SoFi`` 的类型列写 ``Direct Payment`` 而不是笼统的 ``Withdrawal``。
这里按行（bank）给描述/类型/品类塑形，适配器只管把字段塞进版式。
"""
from __future__ import annotations

from typing import Final

from .ledger import LedgerDocument, LedgerRow

__all__ = [
    "amex_desc",
    "capitalone_category",
    "capitalone_desc",
    "mercury_desc",
    "mercury_type",
    "sofi_desc",
    "sofi_type",
    "wellsfargo_desc",
]

#: 商户名 → Capital One / SoFi 那种品类词（真账单就是这几个词）
_CATEGORY_RULES: Final[tuple[tuple[tuple[str, ...], str], ...]] = (
    (("WHOLE FOODS", "WHOLEFDS", "TRADER JOE", "KROGER", "SAFEWAY", "H-E-B", "HEB ",
      "COSTCO", "ALDI", "PUBLIX", "SPROUTS", "GROCERY"), "Groceries"),
    (("STARBUCKS", "COFFEE", "CAFE", "BAKERY", "DONUT", "JUICE", "TEA HOUSE"), "Dining"),
    (("RESTAURANT", "GRILL", "PIZZA", "TACO", "BURGER", "KITCHEN", "BBQ", "SUSHI",
      "THAI", "BISTRO", "DELI", "NOODLE", "RAMEN", "CANTINA", "DINER"), "Dining"),
    (("SHELL", "CHEVRON", "EXXON", "MOBIL", "CIRCLE K", "VALERO", "SUNOCO", "FUEL"), "Gas/Automotive"),
    (("UBER", "LYFT", "TRANSIT", "PARKING", "TOLL"), "Transportation"),
    (("AMAZON", "AMZN", "TARGET", "WALMART", "WAL-MART", "BEST BUY", "ETSY", "EBAY"), "Merchandise"),
    (("CVS", "WALGREENS", "PHARMACY", "CLINIC", "DENTAL", "MEDICAL"), "Health Care"),
    (("COMCAST", "VERIZON", "T-MOBILE", "AT&T", "UTILITY", "ENERGY", "WATER", "INTERNET"),
     "Bills & Utilities"),
    (("NETFLIX", "SPOTIFY", "STEAM", "PLAYSTATION", "CINEMA", "THEATER"), "Entertainment"),
    (("AIRBNB", "AIRLINE", "HOTEL", "MARRIOTT", "DELTA", "UNITED"), "Travel"),
    (("INSURANCE", "GEICO", "STATE FARM", "PROGRESSIVE"), "Insurance"),
)

_TYPE_CATEGORY: Final[dict[str, str]] = {
    "ATM": "Cash",
    "FEE": "Fees",
    "INTEREST": "Interest",
    "CHECK": "Other",
    "ZELLE": "Transfers",
    "TRANSFER": "Transfers",
}


def _city_state(ledger: LedgerDocument) -> str:
    """`AUSTIN TX`：真账单的卡行末尾就是城市 + 州缩写。"""
    return ledger.holder.city_state.upper()


def merchant_category(text: str, row: LedgerRow | None = None) -> str:
    """按商户名判品类；判不出来再按交易类型落到 Cash/Fees/Transfers。"""
    up = (text or "").upper()
    for keys, label in _CATEGORY_RULES:
        if any(key in up for key in keys):
            return label
    if row is not None:
        if row.type in _TYPE_CATEGORY:
            return _TYPE_CATEGORY[row.type]
        if row.direction == "credit":
            return "Income"
    return "Other"


def _is_card(row: LedgerRow) -> bool:
    return row.type in {"POS", "REFUND"}


def _mmdd(row: LedgerRow) -> str:
    return f"{row.date:%m/%d}"


def capitalone_desc(ledger: LedgerDocument, row: LedgerRow, text: str) -> str:
    """Capital One：卡行 = 商户 + 城市 + 州；ACH 保留 NACHA 原文。"""
    if _is_card(row):
        return f"{text} {_city_state(ledger)}".strip()
    return text


def capitalone_category(ledger: LedgerDocument, row: LedgerRow, text: str) -> str:
    """Capital One 的 CATEGORY 列填品类词，不是 Credit/Debit（那是金额方向）。"""
    return merchant_category(text, row)


def wellsfargo_desc(ledger: LedgerDocument, row: LedgerRow, text: str) -> str:
    """Wells Fargo：卡行 ``Card Purchase 12/11 商户 城市 州``；ATM/支票各自带前缀。"""
    if _is_card(row):
        return f"Card Purchase {_mmdd(row)} {text} {_city_state(ledger)}".strip()
    if row.type == "ATM":
        return f"ATM Withdrawal {_mmdd(row)} {text}".strip()
    if row.type == "CHECK":
        no = row.check_no or ""
        # 上游描述本身可能就是 "CHECK 1110"，别再叠一层成 "Check 1110 CHECK"
        body = text
        if body.upper().startswith("CHECK"):
            body = body[5:].strip()
            body = body[len(no) :].strip() if no and body.startswith(no) else body
        return f"Check {no} {body}".strip() if no else (body or text)
    if row.type == "ZELLE":
        # 描述本身就以 ZELLE 开头，再加一层就成了 `Zelle ZELLE PAYMENT TO …`
        body = text[5:].strip() if text.upper().startswith("ZELLE") else text
        # 真账单是「Zelle Payment To Jordan R」这种大小写，不是整行大写
        return f"Zelle {body.title()}".strip()
    return text


def amex_desc(ledger: LedgerDocument, row: LedgerRow, text: str) -> str:
    """American Express：卡行是 ``DEBIT CARD PURCHASE 商户 城市 州``（与本模板样张一致）。

    日期已经在 DATE 列里，行内不再重复 ``12/11``；卡号后四位也不进描述，
    Amex 支票账户的账单就是这么印的。
    """
    if _is_card(row):
        return f"DEBIT CARD PURCHASE {text} {_city_state(ledger)}".strip()
    if row.type == "ACH":
        # 本模板样张里的 ACH 行就是 `ACH CREDIT …` / `ACH DEBIT …`
        mark = "ACH CREDIT" if row.direction == "credit" else "ACH DEBIT"
        return f"{mark} {text}".strip()
    return text


def sofi_desc(ledger: LedgerDocument, row: LedgerRow, text: str) -> str:
    """SoFi：卡行带城市州；转账类用 ``To Savings - 1943`` 这种内部转账口吻。"""
    if _is_card(row):
        return f"{text} {_city_state(ledger)}".strip()
    if row.type == "TRANSFER" and row.direction == "debit":
        return f"To Savings - {ledger.account.last4}"
    return text


_SOFI_TYPES: Final[dict[str, str]] = {
    "POS": "Card Purchase",
    "REFUND": "Card Purchase",
    "ATM": "Withdrawal",
    "CHECK": "Check",
    "FEE": "Fee",
    "INTEREST": "Interest Earned",
    "ZELLE": "Transfer",
    "TRANSFER": "Transfer",
}


def sofi_type(row: LedgerRow) -> str:
    """SoFi 的类型列：ACH 扣款是 ``Direct Payment``，不是笼统的 Withdrawal。"""
    if row.type in _SOFI_TYPES:
        return _SOFI_TYPES[row.type]
    if row.type == "ACH":
        return "Deposit" if row.direction == "credit" else "Direct Payment"
    return "Deposit" if row.direction == "credit" else "Withdrawal"


def mercury_desc(ledger: LedgerDocument, row: LedgerRow, text: str) -> str:
    """Mercury：卡行只印商户（``AWS EMEA``/``UBER TRIP``），不塞城市与卡号。"""
    if row.type == "ATM":
        # 真账单是 `ATM WITHDRAWAL AUSTIN TX`，不是把终端号丢在描述里
        return f"ATM WITHDRAWAL {_city_state(ledger)}".strip()
    return text.upper()


_MERCURY_TYPES: Final[dict[str, str]] = {
    "POS": "Card",
    "REFUND": "Card",
    "ATM": "ATM",
    "CHECK": "Check",
    "FEE": "Fee",
    "INTEREST": "Interest",
    "ZELLE": "Zelle",
    "TRANSFER": "Transfer",
}


def mercury_type(row: LedgerRow) -> tuple[str, str, str]:
    """Mercury 的类型列（含图标名）：ACH In/Out、Card、Wire、Fee…"""
    if row.type in _MERCURY_TYPES:
        label = _MERCURY_TYPES[row.type]
        icon = {"Card": "card", "Fee": "infoCircle", "Interest": "infoCircle"}.get(label, "achOut")
        return label, ("in" if row.direction == "credit" else "out"), icon
    if row.type == "ACH":
        if row.direction == "credit" and row.amount_cents >= 500_000:
            return "Wire In", "in", "achIn"
        return ("ACH In", "in", "achIn") if row.direction == "credit" else ("ACH Out", "out", "achOut")
    return ("ACH In", "in", "achIn") if row.direction == "credit" else ("ACH Out", "out", "achOut")
