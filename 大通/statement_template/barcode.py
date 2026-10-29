#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竖向条码生成器 — 原稿规则完全破解版（2026-10-25 由 7 组原稿样本实证）：

结构（自顶向下 109 元素，条/空交替，首元素为条）：
  顶部护线 4 元素 = 宽窄窄宽 "1001"
  10 个数据窗口 × 10 元素 = 交叉 2/5（I2of5）数字对：
      窗口 w(1..10) 的条位编码 num[2*(10-w)+1]、空位编码 num[2*(10-w)]
      —— 即自底向上按 (d1,d0)(d3,d2)…(d19,d18) 读出原号
  底部护线 5 元素 = "00001"（反转后=标准 I2of5 终止 10000）
每半窗口 5 元素恰好 2 宽；码表（自顶向下切片）为自定义 2/5 码
（=标准 I2of5 码字逐位反转，'8' 由该对称性推出，7 样本 9/9 位验证）。
几何：262×910、条区 240px+右 22px 实心黑带、窄 6px/宽 12px、首末宽条 11px。
"""
from PIL import Image

BAR_W, BAND = 240, 22
NARROW, WIDE = 6, 12
EDGE_WIDE = 11          # 首末条（原稿为 11px）

# 自定义 2/5 码表（自顶向下条/空切片；= 标准 I2of5 反转）
ALPHA = {"0": "01100", "1": "10001", "2": "10010", "3": "00011", "4": "10100",
         "5": "00101", "6": "00110", "7": "11000", "8": "01001", "9": "01010"}
GUARD_TOP, GUARD_BOTTOM = "1001", "00001"


def _norm(num):
    d = "".join(c for c in str(num) if c.isdigit())
    if len(d) > 20:
        d = d[-20:]
    return d.rjust(20, "0")


def encode_seq(num):
    """跟踪号 → 109 元素宽窄位串（与原稿位级一致，任意号可解）"""
    num = _norm(num)
    bits = GUARD_TOP
    for w in range(1, 11):
        bar_d = num[2 * (10 - w) + 1]
        space_d = num[2 * (10 - w)]
        bits += "".join(b + s for b, s in zip(ALPHA[bar_d], ALPHA[space_d]))
    return bits + GUARD_BOTTOM


def decode_seq(bits):
    """109 位串 → 20 位号（自检/验收用）"""
    inv = {v: k for k, v in ALPHA.items()}
    out = [""] * 20
    for w in range(1, 11):
        win = bits[4 + (w - 1) * 10: 14 + (w - 1) * 10]
        out[2 * (10 - w) + 1] = inv[win[0::2]]
        out[2 * (10 - w)] = inv[win[1::2]]
    return "".join(out)


def generate_barcode(data, width=262, height=910):
    seq = encode_seq(data)
    widths = []
    for i, bit in enumerate(seq):
        w = WIDE if bit == "1" else NARROW
        if i in (0, len(seq) - 1) and w == WIDE:
            w = EDGE_WIDE
        widths.append(w)
    total = sum(widths)
    top = (height - total) // 2
    img = Image.new("1", (width, height), 1)
    px = img.load()
    pos, bar = top, True
    for wpx in widths:
        if bar:
            for y in range(pos, min(pos + wpx, height)):
                for x in range(BAR_W):
                    px[x, y] = 0
        pos += wpx
        bar = not bar
    for y in range(height):
        for x in range(width - BAND, width):
            px[x, y] = 0
    return img
