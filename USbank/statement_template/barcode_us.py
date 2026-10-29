#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""US Bank 条码编码器（DataMatrix ECC200 22×22 + IDAutomation Universal 2D 字串）

两原稿样本位级完全复现（2026-10-26 逆向实证）：
- 载荷 = YYYYMMDD(期间结束日) + "0422" + 12 位账号
- 编码 = DataMatrix ECC200 22×22（数据区 20×20，30 数据码字 + 20 RS 校验，
  GF(256) poly 0x12D、first_root=1）；码字流 = Base256 模式（锁定 231、
  长度码字与数据按 255 态随机化 (149·p mod 255)+1），尾部 129+253 态填充
- 符号方向：放置矩阵恒等映射到 [1:21,1:21]；左边列/底行实线，顶行偶位交替、
  右列奇行交替（角 0,0/21,0/21,21 暗）
- 字体字串 = 6 行 × 22 字符；每字符一列 × 4 模块行（MSB=顶），字母密码：
  1111→A 1110→B 1101→C 1100→D 1011→E 1010→F 1001→G 1000→H
  0111→I 0110→J 0101→K 0100→L 0011→M 0010→N 0001→O 0000→P
  （pystrich 放置表 + RS 库复用；纯 Python 依赖）
"""
import re

from pystrich.datamatrix.placement import DataMatrixPlacer
from pystrich.reedsolomon import GF256_0x12D, reed_solomon_encode

BITS2CH = {}
for _ch, _bits in (("A", "1111"), ("B", "1110"), ("C", "1101"), ("D", "1100"),
                   ("E", "1011"), ("F", "1010"), ("G", "1001"), ("H", "1000"),
                   ("I", "0111"), ("J", "0110"), ("K", "0101"), ("L", "0100"),
                   ("M", "0011"), ("N", "0010"), ("O", "0001"), ("P", "0000")):
    BITS2CH[_bits] = _ch

DATA_WORDS = 30          # 22×22 数据码字容量
EC_WORDS = 20
MAX_PAYLOAD = DATA_WORDS - 2        # Base256: 锁定 + 长度

_PLACER = DataMatrixPlacer()
_PLAN = _PLACER._build_plan(20, 20)


def _r255(p):
    return ((149 * p) % 255) + 1


def _r253(p):
    return ((149 * p) % 253) + 1


def base256_codewords(payload: bytes):
    cws = [231, (len(payload) + _r255(2)) % 256]
    for i, b in enumerate(payload):
        cws.append((b + _r255(3 + i)) % 256)
    n = len(cws)
    padsize = DATA_WORDS - n
    if padsize:
        cws.append(129)
        for i in range(1, padsize):
            v = 129 + _r253(n + i + 1)
            cws.append(v if v <= 254 else v - 254)
    return cws


def symbol_22(payload: bytes):
    """载荷 → 22×22 符号位矩阵（1=墨）。"""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"载荷 {len(payload)} 字节超出 22×22 容量 "
                         f"({MAX_PAYLOAD})")
    data = base256_codewords(payload)
    ec = reed_solomon_encode(data, GF256_0x12D, EC_WORDS, first_root=1)
    all_cw = data + list(ec)
    m = [[0] * 20 for _ in range(20)]
    _PLACER.place(all_cw, m)
    sym = [[0] * 22 for _ in range(22)]
    for r in range(20):
        for c in range(20):
            sym[r + 1][c + 1] = m[r][c]
    for y in range(22):
        sym[y][0] = 1
        sym[21][y] = 1
        sym[0][y] = 1 if y % 2 == 0 else 0
        if 1 <= y <= 20:
            sym[y][21] = 1 if y % 2 == 1 else 0
    sym[0][0] = 1
    sym[21][0] = 1
    sym[21][21] = 1
    return sym


def encode_us_2d(payload: str):
    """载荷 → IDAutomation 字体六行字串（每行 22 字符）。"""
    sym = symbol_22(payload.encode("ascii"))
    lines = []
    for band in range(6):
        chars = []
        for x in range(22):
            bits = "".join(
                "1" if (band * 4 + mr < 22 and sym[band * 4 + mr][x]) else "0"
                for mr in range(4))
            chars.append(BITS2CH[bits])
        lines.append("".join(chars))
    return lines


_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
     "Nov", "Dec"], 1)}


def us_bank_payload(period_end: str, account_display: str) -> str:
    """期间结束日 + 账号 → 条码载荷（两原稿实证：YYYYMMDD+0422+12 位账号）。"""
    m = re.search(r"([A-Za-z]{3})\w*\s+(\d{1,2}),\s*(\d{4})", str(period_end))
    if not m:
        raise ValueError(f"期间结束日无法解析: {period_end!r}")
    mon = _MONTHS.get(m.group(1)[:3].title())
    if mon is None:
        raise ValueError(f"未知月份: {m.group(1)!r}")
    ymd = f"{m.group(3)}{mon:02d}{int(m.group(2)):02d}"
    acct = re.sub(r"\D", "", str(account_display))[-12:].zfill(12)
    return f"{ymd}0422{acct}"
