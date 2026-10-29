"""金额与日期的展示格式化。

两条铁律：

* 内部一律用 **整数分**（``cents``），只在写进模板数据的最后一刻才格式化；
* 模板要什么格式给什么格式，绝不回头去猜——格式化函数按「模板需要的样式」命名。
"""
from __future__ import annotations

import hashlib
from datetime import date

__all__ = [
    "aba_routing",
    "account_number",
    "cents_to_currency",
    "cents_to_float",
    "cents_to_grouped",
    "cents_to_plain",
    "cents_to_signed",
    "digits",
    "fmt_day_month",
    "fmt_long",
    "fmt_month_day",
    "fmt_month_year",
    "fmt_slash",
    "fmt_slash_short",
    "fmt_slash_year",
    "fmt_two_digit",
]

_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


# ---------------------------------------------------------------- 金额

def _split(cents: int) -> tuple[str, str, str]:
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(int(cents)), 100)
    return sign, f"{whole:,}", f"{frac:02d}"


def cents_to_plain(cents: int) -> str:
    """``500.00`` / ``-120.00``（无千分位，无货币符号）。"""
    sign, whole, frac = _split(cents)
    return f"{sign}{whole.replace(',', '')}.{frac}"


def cents_to_grouped(cents: int) -> str:
    """``1,500.00``。"""
    sign, whole, frac = _split(cents)
    return f"{sign}{whole}.{frac}"


def cents_to_signed(cents: int) -> str:
    """``-$120.00`` 风格：符号在货币符号之外。"""
    sign, whole, frac = _split(cents)
    return f"{sign}${whole}.{frac}"


def cents_to_currency(cents: int) -> str:
    """``$1,380.00``；负数写成 ``-$120.00``。"""
    return cents_to_signed(cents)


def cents_to_float(cents: int) -> float:
    """引擎里少数几处直接收浮点金额（运通/水星/第一资本/索菲）。"""
    return round(cents / 100, 2)


# ---------------------------------------------------------------- 日期

def fmt_slash_short(d: date) -> str:
    """``01/04/27``。"""
    return f"{d.month:02d}/{d.day:02d}/{d.year % 100:02d}"


def fmt_slash(d: date) -> str:
    """``01/04``。"""
    return f"{d.month:02d}/{d.day:02d}"


def fmt_slash_year(d: date) -> str:
    """``01/04/2027``。"""
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


def fmt_two_digit(d: date) -> str:
    """``1/4``。"""
    return f"{d.month}/{d.day}"


def fmt_month_day(d: date) -> str:
    """``Jan 4``。"""
    return f"{_MONTHS[d.month - 1][:3]} {d.day}"


def fmt_month_day_year(d: date) -> str:
    """``Oct 01, 2026``。"""
    return f"{_MONTHS[d.month - 1][:3]} {d.day:02d}, {d.year}"


def fmt_day_month(d: date) -> str:
    """``4 Jan``（模板需要日期在前时用）。"""
    return f"{d.day} {_MONTHS[d.month - 1][:3]}"


def fmt_long(d: date) -> str:
    """``January 1, 2027``。"""
    return f"{_MONTHS[d.month - 1]} {d.day}, {d.year}"


def fmt_month_year(d: date) -> str:
    """``October 2026``。"""
    return f"{_MONTHS[d.month - 1]} {d.year}"


# ---------------------------------------------------------------- 派生标识

def digits(seed: str, length: int) -> str:
    """由种子稳定派生 ``length`` 位数字（同种子永远同结果）。"""
    out: list[str] = []
    counter = 0
    while len(out) < length:
        digest = hashlib.sha256(f"{seed}:{counter}".encode()).digest()
        out.extend(str(b % 10) for b in digest)
        counter += 1
    return "".join(out[:length])


def aba_routing(seed: str) -> str:
    """9 位路由号，满足 ABA 3-7-1 校验位（虚构号码也要自洽）。"""
    body = [int(c) for c in digits(f"routing:{seed}", 8)]
    weights = (3, 7, 1, 3, 7, 1, 3, 7)
    total = sum(w * d for w, d in zip(weights, body, strict=True))
    check = (-total) % 10
    return "".join(str(d) for d in body) + str(check)


def account_number(seed: str, last4: str) -> str:
    """12 位展示账号，末四位固定为账户真实后四位。"""
    head = digits(f"account:{seed}", 12 - len(last4))
    return f"{head}{last4}"
