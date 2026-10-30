"""银行侧的「号」：ACH trace、Fedwire IMAD/Trn、Zelle 确认号、卡授权号……

真账单里这些号不是随机串，长度与构成都有惯例：

* ACH trace —— 前 8 位是 ODFI 路由号，后 7 位是序号，共 15 位
  （见 moov-io/ach 与 ramp 的说明），银行常追加 `Y00` 这类批次尾缀；
* Fedwire IMAD —— 固定 22 字符、以 `MMDD` 开头，`Trn` 是报文序号；
* Zelle 确认号 —— 9~11 位小写字母数字，各行加自己的前缀（U.S. Bank 的 `USB…`）；
* 卡交易 —— 末四位、6~9 位授权号、13 位票号三种都在用。

全部函数都是纯函数：同一个输入永远得到同一个号，这样渲染字节稳定、
回归指纹可复算，也不会出现同一条交易在预览和导出里编号不一致。
"""

from __future__ import annotations

import hashlib

from .money import digits

__all__ = [
    "ach_trace",
    "card_auth",
    "check_number",
    "fedwire_imad",
    "fedwire_trn",
    "internal_id",
    "sofi_txid",
    "zelle_conf",
    "zelle_payment_id",
]

_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_DIGITS = "0123456789"


def _stream(seed: str, alphabet: str, length: int) -> str:
    """从 sha256 流里取字符（`digits()` 只给数字，这里要字母数字混合）。"""
    out: list[str] = []
    counter = 0
    while len(out) < length:
        digest = hashlib.sha256(f"{seed}:{counter}".encode()).digest()
        out.extend(alphabet[b % len(alphabet)] for b in digest)
        counter += 1
    return "".join(out[:length])


def ach_trace(routing: str, seed: str) -> str:
    """15 位 ACH trace：ODFI 路由号前 8 位 + 7 位序号。"""
    head = (routing or "00000000")[:8]
    return head + digits(f"trace:{seed}", 7)


def fedwire_imad(when: str, seed: str) -> str:
    """Fedwire IMAD：`MMDD` + 6 位混合 + 12 位序号，共 22 字符。"""
    body = _stream(f"imad:{seed}", _ALNUM, 18)
    return f"{when}{body}"


def fedwire_trn(seed: str) -> str:
    """Fedwire 报文序号：7 位数字 + 两位后缀（原稿形如 ``3448736002Es``）。"""
    return digits(f"trn:{seed}", 9) + _stream(f"trns:{seed}", _ALNUM, 2)


def zelle_conf(seed: str) -> str:
    """Zelle 确认号：9 位小写字母数字（BOA 印 ``Conf# w9wh533i7``）。"""
    return _stream(f"zelle:{seed}", "abcdefghijklmnopqrstuvwxyz0123456789", 9)


def zelle_payment_id(prefix: str, seed: str) -> str:
    """带银行前缀的 Zelle 交易号，例如 ``USBkw7lsGba2``。"""
    return prefix + _stream(f"zpid:{seed}", _ALNUM, 9)


def card_auth(seed: str) -> str:
    """卡授权号：6 位数字（原稿 ``#000758857`` 的后 6 位）。"""
    return digits(f"auth:{seed}", 6)


def internal_id(seed: str) -> str:
    """内部交易号：14 位数字（Amex 信用卡的参考号就是这种形状）。"""
    return digits(f"iid:{seed}", 14)


def sofi_txid(seed: str) -> str:
    """SoFi 的 ``Transaction ID: 133-12773001``：3 位批次 + 8 位序号。"""
    return f"{digits(f'sofi-b:{seed}', 3)}-{digits(f'sofi-s:{seed}', 8)}"


def check_number(seed: str, start: int = 1000) -> str:
    """支票号：4 位起跳（模板自带列）。"""
    return str(start + int(digits(f"check:{seed}", 4)) % 8000)
