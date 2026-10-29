#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大通内容流解析 v2：Tm 单元制（BT..ET 内按 Tm 切文本单元），矢量/图片原样保留。
坐标系：PDF 自下而上 → 页面 bl = 792 - Tm.y（Letter）。"""
import re

BT_RE = re.compile(rb"BT(.*?)ET", re.S)
TM_RE = re.compile(rb"([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+Tm")
TF_RE = re.compile(rb"/(\w+)\s+([\d.]+)\s+Tf")
RG_RE = re.compile(rb"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg")
PAGE_H = 792.0


def _unescape(b):
    out = bytearray()
    i = 0
    while i < len(b):
        c = b[i]
        if c == 0x5c and i + 1 < len(b):
            n = b[i + 1]
            mp = {0x6e: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12, 0x28: 40, 0x29: 41, 0x5c: 92}
            if n in mp:
                out.append(mp[n]); i += 2; continue
            if 0x30 <= n <= 0x37:
                j = i + 1; od = b""
                while j < len(b) and len(od) < 3 and 0x30 <= b[j] <= 0x37:
                    od += bytes([b[j]]); j += 1
                out.append(int(od, 8)); i = j; continue
        out.append(c); i += 1
    return bytes(out)


def _esc(b):
    out = bytearray()
    for c in b:
        if c in (0x28, 0x29, 0x5c):
            out += b"\\" + bytes([c])
        else:
            out.append(c)
    return bytes(out)


class Unit:
    """一个文本单元：Tm + Tf + rg + 串"""
    kind = "unit"
    pending_pre = b""   # 解析推出的继承状态（Tf/rg 前缀）；仅单元被修改/前驱被删时才落盘

    def __init__(self, raw):
        self.raw = raw
        m = TM_RE.search(raw)
        self.x = float(m.group(5)) if m else None
        self.pdf_y = float(m.group(6)) if m else None
        m = TF_RE.search(raw)
        self.font = m.group(1).decode() if m else None
        self.size = float(m.group(2)) if m else None
        m = RG_RE.search(raw)
        self.rgb = (float(m.group(1)), float(m.group(2)), float(m.group(3))) if m else (0, 0, 0)
        # 串（首个）
        self.str_raw = None  # 完整 token bytes（含括号/尖括号）
        self.text = None
        self._find_str()

    def _find_str(self):
        b = self.raw
        i = 0
        n = len(b)
        while i < n:
            c = b[i]
            if c == 0x3c:  # <
                j = b.find(b">", i)
                if j > 0:
                    hx = re.sub(rb"\s", b"", b[i + 1:j])
                    if len(hx) % 2: hx += b"0"
                    try:
                        self.str_raw = b[i:j + 1]
                        self.text = bytes.fromhex(hx.decode())
                        self.is_hex = True
                        return
                    except Exception:
                        pass
                i = j + 1 if j > 0 else n
            elif c == 0x28:
                depth, j, buf = 1, i + 1, bytearray()
                while j < n and depth:
                    ch = b[j]
                    if ch == 0x5c:
                        buf.append(ch)
                        if j + 1 < n: buf.append(b[j + 1])
                        j += 2; continue
                    if ch == 0x28: depth += 1
                    elif ch == 0x29:
                        depth -= 1
                        if not depth: break
                    buf.append(ch); j += 1
                self.str_raw = b[i:j + 1]
                self.text = _unescape(bytes(buf))
                self.is_hex = False
                return
            else:
                i += 1

    @property
    def bl(self):
        return PAGE_H - self.pdf_y if self.pdf_y is not None else None

    def materialize_pending(self):
        """把继承状态前缀写进自身（单元将被修改/前驱被删时调用，保证自含）"""
        if self.pending_pre:
            self.raw = self.pending_pre + self.raw
            self.pending_pre = b""

    def set_text(self, new_bytes):
        """替换串内容（保持 token 风格）"""
        self.materialize_pending()
        if self.is_hex:
            tok = b"<" + new_bytes.hex().encode() + b">"
        else:
            tok = b"(" + _esc(new_bytes) + b")"
        if self.str_raw:
            self.raw = self.raw.replace(self.str_raw, tok, 1)
        else:
            self.raw += tok + b" Tj"
        self.text = new_bytes
        self.str_raw = tok

    def set_x(self, x):
        self.materialize_pending()
        m = TM_RE.search(self.raw)
        self.raw = self.raw[:m.start(5)] + f"{x:.2f}".encode() + self.raw[m.end(5):]
        self.x = x

    def set_y_pdf(self, y):
        # 单元 raw 可能打包多个 Tm（replace_span 逐字符前插），须整体平移
        self.materialize_pending()
        dy = y - (self.pdf_y if self.pdf_y is not None else y)
        raw, out, last = self.raw, [], 0
        for m in TM_RE.finditer(raw):
            out.append(raw[last:m.start(6)])
            out.append(f"{float(m.group(6)) + dy:.2f}".encode())
            last = m.end(6)
        out.append(raw[last:])
        self.raw = b"".join(out)
        self.pdf_y = y

    def serialize(self):
        return self.raw


class TextSeg:
    """BT..ET 段：由 Units 组成（Tf 状态跨单元继承）"""
    kind = "btseg"

    def __init__(self, inner):
        self.inner = inner
        self.units = []
        ms = list(TM_RE.finditer(inner))
        if not ms:
            self.units.append(Unit(inner))
            self.head = b""
        else:
            self.head = inner[:ms[0].start()]
            for i, m in enumerate(ms):
                seg = inner[m.start(): ms[i + 1].start() if i + 1 < len(ms) else len(inner)]
                self.units.append(Unit(seg))
        # 继承 Tf / rg 状态
        cf, cs, crg = None, None, (0, 0, 0)
        for u in self.units:
            if u.font is None:
                u.font, u.size = cf, cs
            else:
                cf, cs = u.font, u.size
            if u.rgb == (0, 0, 0) and not RG_RE.search(u.raw):
                u.rgb = crg
            else:
                crg = u.rgb

    def serialize(self):
        return b"BT" + self.head + b"".join(u.raw for u in self.units) + b"ET"


class RawSeg:
    kind = "raw"

    def __init__(self, data):
        self.raw = data

    def serialize(self):
        return self.raw


def parse(data):
    """内容流 → [TextSeg|RawSeg]；TextSeg.units 可编辑"""
    segs = []
    pos = 0
    for m in BT_RE.finditer(data):
        if m.start() > pos:
            segs.append(RawSeg(data[pos:m.start()]))
        segs.append(TextSeg(m.group(1)))
        pos = m.end()
    if pos < len(data):
        segs.append(RawSeg(data[pos:]))
    return segs


def all_units(segs):
    return [u for s in segs if s.kind == "btseg" for u in s.units if u.text is not None and u.font]


def serialize(segs):
    return b"".join(s.serialize() for s in segs)
