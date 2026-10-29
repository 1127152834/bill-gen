#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""US Bank 个户对账单模板生成器（SAMPLE 用途）
架构（大通同款）：字节级静态克隆 + 内容流单元定点重写；同数据回放零操作=1:1。
- 字体=Type0(Identity-H)+全量字体程序：char→GID 走程序 cmap（fontTools），
  ToUnicode/W 随用随补——可编辑字符集=Arial 家族全字母数字。
- 流水：p1 摘要后 Deposits→Other Withdrawals→Balance Summary 串行，
  页底 709.3 截断→续页 188.5 起；尾空白页；"Page X of Y" 按账单页计数。
"""
import io, json, math, os, re, sys
from decimal import Decimal

import fitz
from fontTools.ttLib import TTFont

from runtime_paths import font_path
import chstream
import barcode_us as BC2D
import imb_us as IMB

BASE = os.path.dirname(os.path.abspath(__file__))
PAGE_H = 792.0
TEMPLATES = {"usbiz": os.path.join(BASE, "assets", "usbiz_static.pdf")}
_DOC = fitz.open(TEMPLATES["usbiz"])

# ============ 版面常量（US公户原稿逐线实测；行距/列位与个户同源） ============
PITCH = 10.4
HEAD_TO_COLHDR = 10.5          # 518.5→529.0
COLHDR_TO_ROW = 10.4           # 529.0→539.4
TOTAL_AFTER_LAST = 17.2        # 560.2→577.4
SEC_GAP = 18.3                 # 577.4→595.7
SUMMARY_END_GAP = 17.2         # 483.0→500.2
FIRST_SEC_GAP = 18.3           # 500.2→518.5
NOTE_AFTER_LAST = 16.3
P1_BOTTOM = 723.0
CONT_FIRST = 188.5             # 续页锚点（无原稿实证，沿个户模型）
CONT_TITLE = 167.6
BOTTOM = 723.0
BAL_PAIRS = 3
EPS = 0.15

COL = {"date": 20.1, "day": 43.9, "type": 56.2, "detail": 207.4,
       "sub_left": 70.6, "sub_right": 221.8, "ref": 409.0,
       "dollar": 495.4, "amt_r": 589.0}
BAL_COLS = [(20.1, 174.8), (207.4, 362.0), (394.5, 549.2)]
BAL_CONT = (20.1, 174.8)

# ============ 字体机制 ============
def parse_cmap(txt):
    """ToUnicode CMap 解析：bfchar(<c><u>) + bfrange(<lo><hi><u>/<u,u,...>)。
    成对正则会把 bfrange 误读成 bfchar——CID 映射全错（实测坑）。"""
    out = {}

    def uni_chars(hexs):
        chars = []
        for i in range(0, len(hexs) - len(hexs) % 4, 4):
            v = int(hexs[i:i + 4], 16)
            if 0 < v < 0x110000:
                chars.append(chr(v))
        return chars or ["?"]

    for block in re.findall(r"beginbfchar(.*?)endbfchar", txt, re.S):
        for c, u in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            chs = uni_chars(u)
            out[int(c, 16)] = chs[0] if len(chs) == 1 else "".join(chs)
    for block in re.findall(r"beginbfrange(.*?)endbfrange", txt, re.S):
        for lo, hi, dst in re.findall(
                r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>",
                block):
            lo_i, hi_i = int(lo, 16), int(hi, 16)
            base = uni_chars(dst)[0]
            for c in range(lo_i, min(hi_i, lo_i + 65535) + 1):
                out[c] = chr(min(0x10FFFF, ord(base) + (c - lo_i)))
        for lo, hi, arr in re.findall(
                r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]", block,
                re.S):
            lo_i = int(lo, 16)
            dsts = re.findall(r"<([0-9A-Fa-f]+)>", arr)
            for k, dh in enumerate(dsts):
                chs = uni_chars(dh)
                out[lo_i + k] = chs[0] if len(chs) == 1 else "".join(chs)
    return out


class UsFont:
    """Type0 全量字体程序：char→GID(CID)、per-1000 宽度、ToUnicode/W 增量补写。"""

    def __init__(self, doc, font_xref):
        self.xref = font_xref
        self.cmap, self.widths = {}, {}
        self.new_cids = set()
        obj = doc.xref_object(font_xref)
        m = re.search(r"/DescendantFonts \[ (\d+) 0 R", obj)
        self.df = int(m.group(1)) if m else None
        mu = re.search(r"/ToUnicode (\d+) 0 R", obj)
        self.tou = int(mu.group(1)) if mu else None
        self.cid2ch = {}
        if self.tou:
            self.cid2ch = parse_cmap(doc.xref_stream(self.tou).decode(
                "latin1"))
        buf = doc.extract_font(font_xref)[-1]
        self.tt = TTFont(io.BytesIO(buf), fontNumber=0, lazy=True)
        upm = self.tt["head"].unitsPerEm
        best = self.tt.getBestCmap() or {}
        hmtx = self.tt["hmtx"]
        for u, gname in best.items():
            try:
                gid = self.tt.getGlyphID(gname)
            except Exception:
                continue
            self.cmap[chr(u)] = gid
            self.widths[gid] = round(hmtx[gname][0] * 1000.0 / upm)
        self.mapped_cids = set(self.cid2ch)

    def cid(self, ch):
        g = self.cmap.get(ch)
        if g is not None and g not in self.mapped_cids:
            self.new_cids.add(g)
        return g

    def adv(self, ch):
        return self.widths.get(self.cmap.get(ch), 500)

    def commit(self, doc):
        """收尾：新用字符补 ToUnicode bfchar + DescendantFonts /W。"""
        if not self.new_cids or self.tou is None or self.df is None:
            return
        inv = {}
        for ch, g in self.cmap.items():
            inv.setdefault(g, ch)
        add = sorted(g for g in self.new_cids if g in inv)
        if not add:
            return
        cmap = doc.xref_stream(self.tou).decode("latin1")
        block = "".join(f"<{g:04X}><{ord(inv[g]):04X}>\n" for g in add)
        cnt = re.search(r"(\d+) beginbfchar", cmap)
        if cnt:
            cmap = cmap.replace(f"{cnt.group(1)} beginbfchar",
                                f"{int(cnt.group(1)) + len(add)} beginbfchar", 1)
            cmap = cmap.replace("endbfchar", block + "endbfchar", 1)
        else:
            # 原 CMap 只有 bfrange（无 bfchar 块）——新建一块插在 endcmap 前
            cmap = cmap.replace(
                "endcmap",
                f"{len(add)} beginbfchar\n{block}endbfchar\nendcmap", 1)
        doc.update_stream(self.tou, cmap.encode("latin1"), compress=False)
        warr = doc.xref_object(self.df)
        mm = re.search(r"(/W \[)(.*?)(\]\s*>>)", warr, re.S)
        if mm:
            seg = " ".join(f"{g} [{self.widths.get(g, 500)}]" for g in add)
            doc.update_object(self.df, warr[:mm.start(2)] + mm.group(2).rstrip()
                              + " " + seg + " " + warr[mm.end(2):])


FONTS = {}
for _pi in range(len(_DOC)):
    for _f in _DOC[_pi].get_fonts(full=True):
        if _f[2] == "Type0":
            FONTS[(_pi, _f[4])] = UsFont(_DOC, _f[0])

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
     "Nov", "Dec"], 1)}

ROW_FONT, ROW_SIZE = "F6", 9.0
BOLD_FONT = "F5"
ITAL_FONT = "F2"


def _font_pg(pg_i):
    """字体/度量源页：账单页（含克隆续页）→模板 p1；披露页→模板 p2。
    公户模板仅 2 页且克隆续页物理索引可≥2——不能沿个户的"≥2 续页"规则。"""
    if (pg_i, "F6") in FONTS:
        return pg_i
    if (0, "F6") in FONTS:
        return 0
    return 1


def cid_bytes(pg_i, fname, text):
    uf = FONTS.get((_font_pg(pg_i), fname))
    if uf is None:
        return None
    out = bytearray()
    for ch in text:
        g = uf.cid(ch)
        if g is None:
            return None
        out += bytes((g >> 8, g & 0xFF))
    return bytes(out)


def text_w(pg_i, fname, text, size):
    uf = FONTS.get((_font_pg(pg_i), fname))
    if uf is None:
        return len(text) * size * 0.5
    return sum(uf.adv(ch) for ch in text) * size / 1000.0


def esc(b):
    out = bytearray()
    for c in b:
        if c in (0x28, 0x29, 0x5c):
            out += b"\\" + bytes([c])
        elif c == 0x0d:
            out += b"\\r"
        elif c == 0x0a:
            out += b"\\n"
        elif c == 0x08:
            out += b"\\b"
        elif c == 0x0c:
            out += b"\\f"
        elif c == 0x09:
            out += b"\\t"
        elif c < 0x20 or c > 0x7e:
            out += f"\\{c:03o}".encode()
        else:
            out.append(c)
    return bytes(out)


def make_units(pg_i, fname, text, size, x, bl):
    b = cid_bytes(pg_i, fname, text)
    if b is None:
        return b""
    return (f"/{fname} {size:g} Tf\n0 0 0 rg\n1 0 0 1 {x:.2f} "
            f"{PAGE_H - bl:.2f} Tm\n").encode() + b"(" + esc(b) + b")Tj\n"


# ============ LAYOUTS（导入时提取：文本/坐标/资源名/字号） ============
def _build_layouts(doc):
    pages = []
    for pi in range(len(doc)):
        base2res = {f[3].split("+")[-1]: f[4]
                   for f in doc[pi].get_fonts(full=True)}
        spans = []
        for b in doc[pi].get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for s in l["spans"]:
                    if not s["text"].strip():
                        continue
                    spans.append({"text": s["text"],
                                  "x": round(s["origin"][0], 2),
                                  "bl": round(s["origin"][1], 2),
                                  "x1": round(s["bbox"][2], 2),
                                  "font": base2res.get(s["font"], s["font"]),
                                  "size": round(s["size"], 2)})
        pages.append({"spans": spans})
    return pages


LAYOUTS = {"usbiz": _build_layouts(_DOC)}


# ============ 内容流页（大通 Page 同款） ============
class Page:
    def __init__(self, doc, page, tag="usbiz"):
        self.doc, self.page, self.tag = doc, page, tag
        self.code2ch = {}
        xrefs = page.get_contents()
        self.stream_xrefs = list(xrefs)
        self.data = b"".join(doc.xref_stream(x) for x in xrefs)
        self.segs = chstream.parse(self.data)
        for seg in self.segs:
            if seg.kind != "btseg":
                continue
            for u in seg.units:
                pre = b""
                if u.font and not chstream.TF_RE.search(u.raw):
                    pre += f"/{u.font} {u.size} Tf\n".encode()
                if u.rgb != (0, 0, 0) and not chstream.RG_RE.search(u.raw):
                    pre += (f"{u.rgb[0]:.2f} {u.rgb[1]:.2f} {u.rgb[2]:.2f}"
                            " rg\n").encode()
                if pre:
                    u.raw = pre + u.raw
        self.units = chstream.all_units(self.segs)
        self.lspans = LAYOUTS[tag][min(page.number,
                                       len(LAYOUTS[tag]) - 1)]["spans"]
        self._loc = [(si, ui) for si, s in enumerate(self.segs)
                     if s.kind == "btseg" for ui, u in enumerate(s.units)
                     if u.text is not None and u.font]
        # 零修改直通：dirty=False 时 commit 不重写 Contents（保原稿字节——
        # 解析期给 unit 补的 Tf/rg 注释只在真有修改时才落盘）
        self.dirty = False

    def _decode(self, font, raw):
        """CID 双字节 → 文本（ToUnicode 优先，程序 cmap 反查兜底）。
        克隆续页复用模板页 2 的字体对象。"""
        uf = FONTS.get((_font_pg(self.page.number), font))
        out = []
        for i in range(0, len(raw) - 1, 2):
            cid = (raw[i] << 8) | raw[i + 1]
            if uf is None:
                out.append("?")
                continue
            ch = uf.cid2ch.get(cid)
            if ch is None:
                for c, g in uf.cmap.items():
                    if g == cid:
                        ch = c
                        break
            out.append(ch or "?")
        return "".join(out)

    def spans_units(self, span):
        want = span["text"]
        x1 = span.get("x1", span["x"] + len(want) * 5)
        out, consumed = [], ""
        cand = [k for k, u in enumerate(self.units)
                if u.x is not None and abs(u.bl - span["bl"]) < 1.5
                and span["x"] - 1.8 <= u.x <= x1 + 1.0]
        cand.sort(key=lambda k: self.units[k].x)
        for k in cand:
            if consumed == want:
                break
            dec = self._decode(self.units[k].font, self.units[k].text)
            if not dec:
                continue
            for cd in (dec, " " + dec.lstrip(), dec.rstrip(), dec.strip(),
                       " " + dec.strip()):
                if want.startswith(consumed + cd):
                    consumed += cd
                    out.append(self._loc[k])
                    break
        if consumed.replace(" ", "") != want.replace(" ", ""):
            return []
        return out

    def append_text(self, raw_units):
        self.dirty = True
        op = b"q\nBT\n" + raw_units + b"ET\nQ\n"
        for seg in reversed(self.segs):
            if seg.kind == "raw":
                seg.raw = seg.raw + op
                return
        self.segs.append(chstream.RawSeg(op))

    def delete_units(self, locs):
        self.dirty = True
        for si, ui in sorted(set(locs), key=lambda t: -t[1]):
            self.segs[si].units[ui].raw = b""

    def insert_before(self, loc, raw_bytes):
        self.dirty = True
        si, ui = loc
        self.segs[si].units[ui].raw = raw_bytes + self.segs[si].units[ui].raw

    def replace_span(self, span, new_text, align=None, right=None):
        if new_text.rstrip() == span["text"].rstrip():
            return True
        locs = self.spans_units(span)
        if not locs:
            return False
        x = span["x"]
        if align == "r" and right is not None:
            x = right - text_w(self.page.number, span["font"], new_text,
                               span["size"])
        raw = make_units(self.page.number, span["font"], new_text,
                         span["size"], x, span["bl"])
        if not raw:
            return False
        self.delete_units(locs)
        self.insert_before(locs[0], raw)
        return True

    def _re_locs(self, lo, hi):
        """带内矩形填充线（x y w h re f）定位。"""
        out = []
        for si, seg in enumerate(self.segs):
            if seg.kind != "raw":
                continue
            for m in re.finditer(rb"([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+) re\s*f", seg.raw):
                x, y, w, h = (float(m.group(i)) for i in (1, 2, 3, 4))
                top, bot = PAGE_H - (y + h), PAGE_H - y
                if top < hi and bot >= lo:
                    out.append((si, m.span()))
        return out

    def delete_res(self, lo, hi):
        self.dirty = True
        hits = {}
        for si, (a, b) in self._re_locs(lo, hi):
            hits.setdefault(si, []).append((a, b))
        for si, sps in hits.items():
            seg = self.segs[si]
            for a, b in sorted(set(sps), key=lambda t: -t[0]):
                seg.raw = seg.raw[:a] + seg.raw[b:]

    def add_line(self, bl, x, w, h=0.2):
        """横线（矩形填充；bl=线顶基线，两原稿线型实证）。"""
        self.dirty = True
        y = PAGE_H - bl - h
        op = f"q {x:g} {y:.2f} {w:g} {h:g} re f Q ".encode()
        for seg in reversed(self.segs):
            if seg.kind == "raw":
                seg.raw += op
                return
        self.segs.append(chstream.RawSeg(op))

    def add_vbar(self, top_bl, x, h=20.7, w=0.55):
        self.dirty = True
        y = PAGE_H - top_bl - h
        op = f"q {x:g} {y:.2f} {w:g} {h:g} re f Q ".encode()
        for seg in reversed(self.segs):
            if seg.kind == "raw":
                seg.raw += op
                return
        self.segs.append(chstream.RawSeg(op))

    def commit(self):
        if not self.dirty:
            return                      # 零修改——Contents 原字节直通
        data = chstream.serialize(self.segs)
        if self.stream_xrefs:
            self.doc.update_stream(self.stream_xrefs[0], data)
            for x in self.stream_xrefs[1:]:
                self.doc.update_stream(x, b"")
        elif data:
            # 新建页无 Contents——创建内容流对象
            x = self.doc.get_new_xref()
            self.doc.update_object(x, "<< >>")
            self.doc.update_stream(x, data)
            self.doc.xref_set_key(self.page.xref, "Contents", f"{x} 0 R")


def find(L, pi, pred):
    for s in L[pi]["spans"]:
        if pred(s):
            return s
    return None


# ============ 帮助函数 ============
def _cents(v):
    try:
        s = str(v).replace(",", "").replace("$", "").replace(" ", "").strip()
        if s.endswith("-"):
            s = "-" + s[:-1]
        return int(Decimal(s) * 100)
    except Exception:
        return 0


def _fmt_amt(cents, dollar=False):
    q = abs(cents)
    s = f"{q // 100:,}.{q % 100:02d}" + ("-" if cents < 0 else "")
    return ("$" if dollar else "") + s


def _usd_span(v):
    """金额拆 (has_dollar, value)：负数尾 '-'，$ 仅显式。"""
    c = _cents(v)
    return c


def _month_key(ds):
    MONTHS = {m: i for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
         "Oct", "Nov", "Dec"], 1)}
    p = re.findall(r"[A-Za-z]+|\d+", str(ds or ""))
    if not p:
        return ""
    mo = MONTHS.get(p[0][:3].title(), 0)
    day = int(p[1]) if len(p) > 1 else 0
    return f"{mo:02d}{day:02d}"


# ============ 自动计算 ============
def us_calc(d):
    begin = _cents(d.get("begin_balance", 0))
    dep = sum(_cents(r["amount"]) for r in d.get("deposits", []))
    wd = sum(_cents(r["amount"]) for r in d.get("withdrawals", []))
    end = begin + dep - wd
    d["summary"] = {
        "begin": _fmt_amt(begin), "deposits": _fmt_amt(dep),
        "withdrawals": _fmt_amt(-wd), "ending": _fmt_amt(end)}
    # 每日余额（按日期合并；提款为负）
    rows = []
    for r in d.get("deposits", []):
        rows.append((r.get("date", ""), _cents(r["amount"])))
    for r in d.get("withdrawals", []):
        rows.append((r.get("date", ""), -_cents(r["amount"])))
    rows.sort(key=lambda t: _month_key(t[0]))
    bal, daily, last = begin, [], None
    for dt, c in rows:
        bal += c
        k = _month_key(dt)
        if k != last:
            daily.append([dt, _fmt_amt(bal)])
            last = k
        else:
            daily[-1][1] = _fmt_amt(bal)
    d["daily"] = daily
    # 天数（含首尾）：Sep 8..Sep 30 = 23 实证
    import re as _re
    def _md(s_):
        m = _re.search(r"([A-Za-z]{3})\w*\s+(\d{1,2}),\s*(\d{4})", str(s_))
        return (int(m.group(3)), _MONTHS[m.group(1)[:3].title()],
                int(m.group(2))) if m else None
    a = _md(d["period"]["start"])
    b_ = _md(d["period"]["end"])
    try:
        from datetime import date
        d["days"] = str((date(*b_) - date(*a)).days + 1)
    except Exception:
        d["days"] = "1"


# ============ 水印 ============
def add_watermark(doc, page):
    t = "S A M P L E"
    size = 100
    w = text_w(page.number, ROW_FONT, t, size)
    cx, cy = 306.0, 396.0
    c45, s45 = math.cos(math.radians(45)), math.sin(math.radians(45))
    x = cx - w / 2 * c45
    y = cy + w / 2 * s45
    hex_t = "<" + "".join(f"{ord(c):04X}" for c in t) + ">"
    ops = (f"q 0.78 0.78 0.78 rg BT /W {size} Tf "
           f"1 0 0 1 {x:.2f} {y:.2f} Tm {c45:.4f} {s45:.4f} {-s45:.4f} "
           f"{c45:.4f} 0 0 Tm {hex_t} Tj ET Q").encode()
    if not hasattr(doc, "_wm_font_xref"):
        doc._wm_font_xref = doc.get_new_xref()
        doc.update_object(doc._wm_font_xref,
                          "<< /Type /Font /Subtype /Type1 "
                          "/BaseFont /Helvetica-Bold >>")
    rk = doc.xref_get_key(page.xref, "Resources/Font")
    ent = f"/W {doc._wm_font_xref} 0 R"
    if rk[0] == "xref":
        fo = int(rk[1].split()[0])
        old = doc.xref_object(fo).rstrip()
        if "/W " not in old:
            doc.update_object(fo, old[:-2] + f" {ent}>>")
    elif rk[0] == "null":
        # 静态披露页 /Font 为 null（文本在 XObject 内）——新建 Font 字典
        doc.xref_set_key(page.xref, "Resources/Font", f"<<{ent}>>")
    else:
        old = rk[1].rstrip()
        if "/W " not in old:
            doc.xref_set_key(page.xref, "Resources/Font",
                             old[:-2] + f" {ent}>>")
    cts = page.get_contents()
    if cts:
        doc.update_stream(cts[0], doc.xref_stream(cts[0]) + ops)
    else:
        # 页内容全在 XObject（无页级 contents）——新建 Contents 流画水印
        nx = doc.get_new_xref()
        doc.update_object(nx, "<< >>")
        doc.update_stream(nx, ops, compress=False)
        doc.xref_set_key(page.xref, "Contents", f"{nx} 0 R")


# ============ 流水引擎（快照-擦除-发射，大通验证架构） ============
SUMMARY_TITLE_BL = 441.2      # Account Summary
SUM_HDR_ITEMS_BL = 451.9      # "# Items"（右栏计数列头）
SUMMARY_FIRST = 462.2
SUM_DOLLAR_X, SUM_VAL_R = 192.9, 292.9
SUM_CNT_X, SUM_CNT_R = 161.6, 166.6          # # Items 计数列
SUM_DAYS_LABEL_X, SUM_DAYS_VAL_R = 315.4, 589.0  # 天数行（右侧独立栏）
SUM_ENDING_X = 45.7

# 模板带（页, lo, hi）——快照/擦除用（公户实测 y）
BANDS = {
    "summary_rows": (0, 443.0, 504.0),
    "summary_hdr": (0, 449.0, 456.0),
    "dep_head": (0, 516.5, 531.5),
    "dep_rows": (0, 537.0, 553.0),
    "dep_total": (0, 575.0, 581.0),
    "wd_head": (0, 593.0, 608.5),
    "wd_rows": (0, 614.0, 652.0),
    "wd_total": (0, 662.5, 668.5),
    "bal_head": (0, 681.0, 697.0),
    "bal_rows": (0, 702.0, 708.5),
    # 注：无 cont_bal_head 快照——公户模板 p1 的 165-181.5 带是页头
    # "To Contact" 块（个户模板该带才是 p3 续表头）；续表头一律合成发射
    "note": (0, 718.5, 724.5),
}

SECTIONS = [
    ("deposits", "Other Deposits ", "Total Other Deposits"),
    ("withdrawals", "Other Withdrawals ", "Total Other Withdrawals"),
]


def _band_locs(pg, lo, hi):
    return [(si, ui) for si, ui in pg._loc
            if lo <= pg.segs[si].units[ui].bl < hi]


def _snap(pg, band):
    pgi, lo, hi = band
    return {"units": [pg.segs[si].units[ui].raw for si, ui in _band_locs(pg, lo, hi)],
            "src": pgi}


def _erase(pg, lo, hi):
    pg.delete_units(_band_locs(pg, lo, hi))


def _emit(snap, pages, dst_pg, dy):
    """快照单元发射（dy=bl 增量）。跨页/同页统一走快照。"""
    if not snap["units"]:
        return
    blob = []
    for raw in snap["units"]:
        u = chstream.Unit(raw)
        if u.pdf_y is not None:
            u.set_y_pdf(u.pdf_y - dy)
        blob.append(u.raw)
    pages[dst_pg].append_text(b"".join(blob))


def _date_units(pg_i, date, bl, x=None, day_x=None, font=ROW_FONT,
                size=ROW_SIZE):
    """日期发射：单数字日=月+日两 span（日右移 23.8）；否则整体一 span。"""
    x = COL["date"] if x is None else x
    day_x = (x + 23.8) if day_x is None else day_x
    m = re.match(r"^([A-Za-z]{3})\s+(\d{1,2})$", str(date).strip())
    if m and len(m.group(2)) == 1:
        return (make_units(pg_i, font, m.group(1), size, x, bl)
                + make_units(pg_i, font, m.group(2), size, day_x, bl))
    return make_units(pg_i, font, str(date), size, x, bl)


def _sim(rows, y0, bottom):
    """单页行放置：返回 [(y_main, lines=[(x,text)...], amount, row)]。"""
    placed, y = [], y0
    for r in rows:
        lines = []
        main = [(COL["date"], r.get("date", "")), (COL["type"], r.get("type", "")),
                (COL["detail"], r.get("detail", ""))]
        if r.get("ref"):
            main.append((COL["ref"], r["ref"]))
        lines.append(("main", main, r))
        for sub in r.get("extra", []):
            parts = str(sub).split("|", 1)
            lines.append(("sub", [(COL["sub_left"], parts[0].strip())]
                          + ([(COL["sub_right"], parts[1].strip())]
                             if len(parts) > 1 and parts[1].strip() else []), r))
        last_bl = y + PITCH * (len(lines) - 1)
        if last_bl > bottom + 1e-6:
            break
        placed.append((y, lines, r))
        y = last_bl + PITCH
    return placed, rows[len(placed):]


def flow_engine(pages, d):
    """规划+落位：返回账单页数 n（含 p1）。物理续页=pages[2:]，空白页由 build_pdf 处理。"""
    # ---- 规划 ----
    dep_rows = [dict(r) for r in d.get("deposits", [])]
    wd_rows = [dict(r) for r in d.get("withdrawals", [])]
    has_dep, has_wd = bool(dep_rows), bool(wd_rows)
    summary_rows = [("Beginning Balance on {start}", d["summary"]["begin"],
                     True, 20.1, None)]
    if has_dep:
        summary_rows.append(("Other Deposits", d["summary"]["deposits"], False,
                             20.1, len(dep_rows)))
    if has_wd:
        summary_rows.append(("Other Withdrawals", d["summary"]["withdrawals"],
                             False, 20.1, len(wd_rows)))
    # 基线用模板精确值（462.2/472.6/483.0——公户原稿实测）
    TPL_SUM_BL = [462.2, 472.6, 483.0]
    sum_lines = []
    for i, (lbl, val, dollar, x, cnt) in enumerate(summary_rows):
        sum_lines.append((TPL_SUM_BL[i], lbl, val, dollar, x, cnt))
    ending_y = TPL_SUM_BL[len(summary_rows) - 1] + SUMMARY_END_GAP
    sum_lines.append((ending_y, "Ending Balance on  {end}",
                      d["summary"]["ending"], True, SUM_ENDING_X, None))
    first_y = ending_y + FIRST_SEC_GAP

    plans = [{"pg": 0, "items": []}]
    cur_bottom = P1_BOTTOM
    cur_y = first_y
    page_no = 1

    def new_cont_page():
        nonlocal cur_y, cur_bottom, page_no
        page_no += 1
        cur_y = None
        cur_bottom = BOTTOM
        plans.append({"pg": page_no, "items": []})   # 账单第 N 页=物理索引 N（披露页占 1）

    sec_defs = [("deposits", "Other Deposits ", "Total Other Deposits", dep_rows),
                ("withdrawals", "Other Withdrawals ", "Total Other Withdrawals", wd_rows)]
    for key, title, total_lbl, rows in sec_defs:
        if not rows:
            continue
        remaining = list(rows)
        first_frag = True
        last_bl_y = None
        while remaining:
            if cur_y is None:
                row_y = CONT_FIRST if not first_frag else CONT_TITLE
                head_y = CONT_TITLE if first_frag else None
            else:
                head_y = cur_y
                row_y = head_y + HEAD_TO_COLHDR + COLHDR_TO_ROW
            if first_frag and head_y is not None and row_y + PITCH > cur_bottom + 1e-6:
                new_cont_page()              # 头+行放不下 → 换页重试
                continue
            placed, remaining = _sim(remaining, row_y, cur_bottom)
            if not placed:
                new_cont_page()
                continue
            if first_frag:
                plans[-1]["items"].append({"kind": "sec_head", "title": title,
                                           "y": head_y, "first": True})
                first_frag = False
            else:
                # 段中途续页："(continued)" 头（Balance Summary (continued) 同款实证）
                plans[-1]["items"].append({"kind": "sec_cont_head",
                                           "title": title, "y": CONT_TITLE,
                                           "key": key})
            first_row = not any(it.get("kind") == "sec_rows"
                                and it.get("key") == key
                                for p in plans for it in p["items"])
            plans[-1]["items"].append({"kind": "sec_rows", "placed": placed,
                                       "key": key, "first_row": first_row})
            last_bl_y = placed[-1][0] + PITCH * (len(placed[-1][1]) - 1)
            cur_y = last_bl_y + PITCH
        # 全部行放完 → 合计（同页末行+17.3；放不下→次页顶）
        total_y = last_bl_y + TOTAL_AFTER_LAST
        if total_y > cur_bottom + 1e-6:
            new_cont_page()
            plans[-1]["items"].append({"kind": "sec_cont_head",
                                       "title": title, "y": CONT_TITLE,
                                       "key": key})
            total_y = CONT_FIRST
        plans[-1]["items"].append({"kind": "sec_total", "label": total_lbl,
                                   "y": total_y})
        cur_y = total_y + SEC_GAP
    # ---- 余额表 ----
    daily = d.get("daily", [])
    cont_mode = False
    started = False
    while True:
        if cur_y is None:
            title_y = CONT_TITLE
            row_y = title_y + HEAD_TO_COLHDR + COLHDR_TO_ROW
        else:
            title_y = cur_y
            row_y = title_y + HEAD_TO_COLHDR + COLHDR_TO_ROW
        per = 1 if cont_mode else BAL_PAIRS
        cap_pairs = max(0, int((cur_bottom - row_y) // PITCH) + 1) * per
        if len(daily) <= cap_pairs and daily:
            plans[-1]["items"].append({"kind": "bal_head", "y": title_y,
                                       "cont": cont_mode})
            plans[-1]["items"].append({"kind": "bal_rows", "y": row_y,
                                       "cells": daily, "cont": cont_mode})
            note_y = row_y + PITCH * (max(1, -(-len(daily) // per)) - 1) \
                + NOTE_AFTER_LAST
            if note_y > cur_bottom + 1e-6:
                new_cont_page()
                plans[-1]["items"].append({"kind": "note", "y": CONT_FIRST})
            else:
                plans[-1]["items"].append({"kind": "note", "y": note_y})
            break
        if not daily:
            break
        # 放得下多少发多少，剩余续页
        rows_fit = int((cur_bottom - row_y) // PITCH) + 1
        take = rows_fit * per
        chunk, daily = daily[:take], daily[take:]
        if chunk:
            plans[-1]["items"].append({"kind": "bal_head", "y": title_y,
                                       "cont": cont_mode})
            plans[-1]["items"].append({"kind": "bal_rows", "y": row_y,
                                       "cells": chunk, "cont": cont_mode})
            started = True
        elif not started:
            pass                            # 尚未起表：次页整表新起（非 continued）
        new_cont_page()
        if started:
            cont_mode = True
    n_stmt = len(plans)
    return plans, n_stmt, sum_lines, ending_y


def _tpl_lines(lay, lo, hi):
    """模板带内逐基线视觉文本（span 按 x 连接、空白折叠）——等值比较用。"""
    by = {}
    for sp in LAYOUTS["usbiz"][lay]["spans"]:
        if lo <= sp["bl"] < hi:
            by.setdefault(round(sp["bl"], 1), []).append((sp["x"], sp["text"]))
    out = []
    for bl, v in sorted(by.items()):
        t = _norm(" ".join(x for _, x in sorted(v)))
        out.append((_rb(bl), t))
    return out


def _norm(t):
    return re.sub(r"\s+", " ", str(t)).strip()


def _rb(bl):
    return round(bl, 1)


def _lines_eq(a, b):
    """等值比较（±0.15pt 容差——原稿生成器自身有 0.1 级浮点抖动）。"""
    if len(a) != len(b):
        return False
    return all(wa[1] == wb[1] and abs(wa[0] - wb[0]) <= 0.15
               for wa, wb in zip(a, b))


def _plan_lines(plans, d, sum_lines):
    """计划可视文本（与模板同口径：按基线合并 span、空白折叠、0.5pt 桶）。"""
    out = {}
    p1 = [(_rb(SUM_HDR_ITEMS_BL), "# Items"),
          (_rb(461.0), "Number of Days in Statement Period "
           + str(d.get("days", "1")))]
    for y, lbl, val, dollar, x, cnt in sum_lines:
        lbl = lbl.format(start=d["period"]["start"].split(",")[0],
                         end=d["period"]["end"])
        txt = " ".join(t for t in ([lbl, str(cnt) if cnt is not None else "",
                                    "$" if dollar else "", val]) if t)
        p1.append((_rb(y), _norm(txt)))
    out[0] = p1
    COLHDR = "Date Description of Transaction Ref Number Amount"
    for plan in plans:
        pg = plan["pg"]
        lines = out.setdefault(pg, [])
        for it in plan["items"]:
            if it["kind"] == "sec_head":
                lines.append((_rb(it["y"]), _norm(it["title"])))
                lines.append((_rb(it["y"] + HEAD_TO_COLHDR), COLHDR))
            elif it["kind"] == "sec_cont_head":
                lines.append((_rb(it["y"]),
                              _norm(it["title"].rstrip() + " (continued)")))
                lines.append((_rb(it["y"] + HEAD_TO_COLHDR), COLHDR))
            elif it["kind"] == "sec_rows":
                neg = it.get("key") == "withdrawals"
                first = it.get("first_row")
                for ri, (y, ls, r) in enumerate(it["placed"]):
                    for li, (kind, cells, rr) in enumerate(ls):
                        bl = _rb(y + PITCH * li)
                        if kind == "main":
                            amt = _fmt_amt(-_cents(rr["amount"]) if neg
                                           else _cents(rr["amount"]))
                            parts = [rr.get("date", ""), rr.get("type", ""),
                                     rr.get("detail", "")]
                            if rr.get("ref"):
                                parts.append(rr["ref"])
                            if first and ri == 0:
                                parts += ["$", amt]
                            else:
                                parts.append(amt)
                            lines.append((bl, _norm(" ".join(
                                str(p) for p in parts if p))))
                        else:
                            lines.append((bl, _norm(" ".join(
                                str(x) for _, x in cells))))
            elif it["kind"] == "sec_total":
                key = "deposits" if "Deposits" in it["label"] else "withdrawals"
                tot = sum(_cents(r["amount"]) for r in d.get(key, []))
                if key == "withdrawals":
                    tot = -tot
                lines.append((_rb(it["y"]),
                              _norm(f"{it['label']} $ {_fmt_amt(tot)}")))
            elif it["kind"] == "bal_head":
                lines.append((_rb(it["y"]),
                              "Balance Summary (continued)" if it["cont"]
                              else "Balance Summary"))
                lines.append((_rb(it["y"] + HEAD_TO_COLHDR),
                              "Date Ending Balance" if it["cont"] else
                              "Date Ending Balance Date Ending Balance"
                              " Date Ending Balance"))
            elif it["kind"] == "bal_rows":
                cols = [BAL_CONT] if it["cont"] else BAL_COLS
                per = len(cols)
                byrow = {}
                for i, (dt, val) in enumerate(it["cells"]):
                    row, _ = divmod(i, per)
                    byrow.setdefault(row, []).append(f"{dt} {val}")
                for row, parts in sorted(byrow.items()):
                    lines.append((_rb(it["y"] + PITCH * row),
                                  _norm(" ".join(parts))))
            elif it["kind"] == "note":
                lines.append((_rb(it["y"]),
                              "Balances only appear for days reflecting change."))
    return out


def _emit_summary(pages, d, sum_lines):
    """公户摘要：左栏（Beginning/Other Deposits/Other Withdrawals + #Items 计数列）
    + 右栏天数行 + Ending（缩进 45.7 粗体）。模板带已含天数/#Items 原行（先擦后发）。"""
    pages[0].delete_units(_band_locs(pages[0], 449.0, 504.0))
    pages[0].delete_res(449.0, 504.0)
    blob = b""
    # "# Items" 计数列表头（模板 451.9 实证）
    blob += make_units(0, BOLD_FONT, "# Items", ROW_SIZE, 148.6, SUM_HDR_ITEMS_BL)
    for y, lbl, val, dollar, x, cnt in sum_lines:
        lbl = lbl.format(start=d["period"]["start"].split(",")[0],
                         end=d["period"]["end"])
        bfont = BOLD_FONT if "Ending" in lbl else ROW_FONT
        blob += make_units(0, bfont, lbl, ROW_SIZE, x, y)
        if cnt is not None:
            blob += make_units(0, ROW_FONT, str(cnt), ROW_SIZE, SUM_CNT_X, y)
        if dollar:
            blob += make_units(0, bfont, "$", ROW_SIZE, SUM_DOLLAR_X, y)
        vx = SUM_VAL_R - text_w(0, bfont, val, ROW_SIZE)
        blob += make_units(0, bfont, val, ROW_SIZE, vx, y)
    # 右栏天数行（固定 461.0——与 Beginning 行并排，模板实测）
    blob += make_units(0, ROW_FONT, "Number of Days in Statement Period",
                       ROW_SIZE, SUM_DAYS_LABEL_X, 461.0)
    dv = d.get("days", "1")
    blob += make_units(0, ROW_FONT, dv, ROW_SIZE,
                       SUM_DAYS_VAL_R - text_w(0, ROW_FONT, dv, ROW_SIZE), 461.0)
    if blob:
        pages[0].append_text(blob)
    # 线：类别区与 Ending 之间（模板 488.4 = ending-11.8 实证）
    pages[0].add_line(ending_of(sum_lines) - 11.8, 192.9, 100.8)


def ending_of(sum_lines):
    return sum_lines[-1][0]




def _del_zebra(pg, lo=140.0, hi=730.0):
    """擦除交易区斑马纹（0.952 灰 path f*，五段拼接）。"""
    pat = re.compile(
        rb"0\.95[\d]* 0\.95[\d]* 0\.95[\d]* rg\n"
        rb"([\d.]+ [\d.]+ m\n(?:[\d.]+ [\d.]+ l\n){4}h\nf\*)")
    for si, seg in enumerate(pg.segs):
        if seg.kind != "raw":
            continue
        out, last = [], 0
        for m in pat.finditer(seg.raw):
            ys = [float(v) for v in re.findall(rb"[\d.]+", m.group(1))]
            top = 792 - max(ys)
            bot = 792 - min(ys)
            if top < hi and bot >= lo:
                out.append(seg.raw[last:m.start()])
                last = m.end()
        if out:
            seg.raw = b"".join(out) + seg.raw[last:]


def _del_gray(pg, lo=140.0, hi=730.0):
    """擦除克隆页 0.90196 静态灰块（p1 横幅底/摘要区高亮；fill+同色描边成对）。
    注意：块内含 Q Q 弹栈算子，整段删除会改变 q/Q 栈深度导致 MuPDF 中止解释
    整页（实测 0 字符提取）——须等长空白替换绘制部分、保留 Q Q 与线型算子。"""
    path = rb"[\d.]+ [\d.]+ m\n(?:[\d.]+ [\d.]+ l\n){4}h\n"
    pat = re.compile(
        rb"(0\.9019\d* 0\.9019\d* 0\.9019\d* rg\n" + path + rb"f\*\n)"
        rb"(Q\nQ\n0 w\n1 J\n)"
        rb"(0\.9019\d* 0\.9019\d* 0\.9019\d* RG\n" + path + rb"S\n)")
    for seg in pg.segs:
        if seg.kind != "raw":
            continue
        out, last = [], 0
        for m in pat.finditer(seg.raw):
            ys = [float(v) for v in re.findall(rb"[\d.]+", m.group(1))]
            top = 792 - max(v for v in ys if v < 613)
            bot = 792 - min(v for v in ys if v < 613)
            if top < hi and bot >= lo:
                keep = (b" " * len(m.group(1)) + m.group(2)
                        + b" " * len(m.group(3)))
                out.append(seg.raw[last:m.start()])
                out.append(keep)
                last = m.end()
        if out:
            out.append(seg.raw[last:])
            seg.raw = b"".join(out)


def _add_banner_bg(pg):
    """续页横幅底：0.90196 灰底(643.7-657.5, fill+同色描边)+上缘黑线(657.75 h1.5)
    —— 个户 p3 原稿指令逐字复刻。prepend 保证在横幅文字之下。"""
    pth = ("20.15 643.7 m\n596.15 643.7 l\n596.15 657.5 l\n20.15 657.5 l\n"
           "20.15 643.7 l\nh\n")
    op = ("q\n0.90196 0.90196 0.90196 rg\n" + pth + "f*\n"
          "0 w\n1 J\n0.90196 0.90196 0.90196 RG\n" + pth + "S\n"
          "Q\n"
          "q\n0 0 0 rg\n20.15 657.75 576 1.5 re\nf\nQ\n").encode()
    for seg in pg.segs:
        if seg.kind == "raw":
            seg.raw = op + seg.raw
            return
    pg.segs.append(chstream.RawSeg(op))



def _add_zebra(pg, bl2, bl3, x_segs=((20.1, 36.0), (56.1, 151.2), (207.3, 201.6),
                                     (408.9, 86.5), (495.4, 100.8))):
    """斑马纹：第 2 行基线-8 到第 3 行基线+2.3，五段拼接（原稿 0.95294 实证）。"""
    top = bl2 - 8.0
    bot = bl3 + 2.3
    y0 = PAGE_H - bot
    h = bot - top
    ops = []
    for x, w in x_segs:
        ops.append(
            f"q 0.95294 0.95294 0.95294 rg\n"
            f"{x:g} {y0:.2f} m {x + w:g} {y0:.2f} l {x + w:g} {y0 + h:.2f} l "
            f"{x:g} {y0 + h:.2f} l h f* Q\n")
    op = "".join(ops).encode()
    # 插页首（首个 raw 段前部）：斑马底色必须在全部行文本之下
    for seg in pg.segs:
        if seg.kind == "raw":
            seg.raw = op + seg.raw
            return
    pg.segs.append(chstream.RawSeg(op))


def _emit_page_items(pages, plan, d, snaps):
    """非等值页：按计划发射段头/行/合计/余额表/注 + 全部线型（实证模型）。"""
    pg_i = plan["pg"]
    fp = 0 if pg_i == 0 else 2
    for it in plan["items"]:
        if it["kind"] == "sec_head":
            is_dep = "Deposits" in it["title"]
            band = snaps["dep_head"] if is_dep else snaps["wd_head"]
            tpl_y = 518.5 if is_dep else 595.7
            _emit(band, pages, pg_i, it["y"] - tpl_y)
            # 线：段上分隔线（标题-12.5）+ 列头下划线（列头+2.1）
            colhdr_y = it["y"] + HEAD_TO_COLHDR
            pages[pg_i].add_line(it["y"] - 12.5, 20.15, 576.0)
            pages[pg_i].add_line(colhdr_y + 2.1, 20.15, 576.0)
        elif it["kind"] == "sec_cont_head":
            title = it["title"].rstrip() + " (continued)"
            blob = make_units(fp, BOLD_FONT, title, 10.0, 20.1, it["y"])
            for x, txt in ((20.1, "Date"), (56.2, "Description of Transaction"),
                           (409.0, "Ref Number"), (557.9, "Amount")):
                blob += make_units(fp, ITAL_FONT, txt, 9.0, x,
                                   it["y"] + HEAD_TO_COLHDR)
            pages[pg_i].append_text(blob)
            # 线：仅列头下划线（Balance Summary (continued) 区域实证，
            # 无段上分隔线）
            pages[pg_i].add_line(it["y"] + HEAD_TO_COLHDR + 2.1, 20.15, 576.0)
        elif it["kind"] == "sec_rows":
            blob = b""
            neg = it.get("key") == "withdrawals"
            first = it.get("first_row")
            for ri, (y, lines, r) in enumerate(it["placed"]):
                amt = _fmt_amt(-_cents(r["amount"]) if neg
                               else _cents(r["amount"]))
                use_dollar = (first and ri == 0)
                for li, (kind, cells, rr) in enumerate(lines):
                    line_bl = y + PITCH * li
                    if kind == "main":
                        blob += _date_units(fp, rr.get("date", ""), line_bl)
                        for x, txt in cells:
                            if x == COL["date"] or not str(txt).strip():
                                continue
                            blob += make_units(fp, ROW_FONT, str(txt),
                                               ROW_SIZE, x, line_bl)
                        if use_dollar:
                            blob += make_units(fp, ROW_FONT, "$", ROW_SIZE,
                                               COL["dollar"], line_bl)
                        vx = COL["amt_r"] - text_w(fp, ROW_FONT, amt, ROW_SIZE)
                        blob += make_units(fp, ROW_FONT, amt, ROW_SIZE, vx,
                                           line_bl)
                    else:
                        for x, txt in cells:
                            blob += make_units(fp, ROW_FONT, str(txt),
                                               ROW_SIZE, x, line_bl)
            # 斑马纹先画（z 序在下），行文本后叠加
            if len(it["placed"]) >= 3:
                b2 = it["placed"][1][0]
                b3 = it["placed"][2][0]
                _add_zebra(pages[pg_i], b2, b3)
            if blob:
                pages[pg_i].append_text(blob)
        elif it["kind"] == "sec_total":
            key = "deposits" if "Deposits" in it["label"] else "withdrawals"
            tot = sum(_cents(r["amount"]) for r in d.get(key, []))
            if key == "withdrawals":
                tot = -tot
            val = _fmt_amt(tot)
            lx = 452.0 - text_w(fp, BOLD_FONT, it["label"], ROW_SIZE)
            blob = make_units(fp, BOLD_FONT, it["label"], ROW_SIZE, lx, it["y"])
            blob += make_units(fp, BOLD_FONT, "$", ROW_SIZE, COL["dollar"],
                               it["y"])
            vx = COL["amt_r"] - text_w(fp, BOLD_FONT, val, ROW_SIZE)
            blob += make_units(fp, BOLD_FONT, val, ROW_SIZE, vx, it["y"])
            pages[pg_i].append_text(blob)
            # 线：合计上金额段线（-11.7）+ 合计下全线（+5.7）
            pages[pg_i].add_line(it["y"] - 11.7, 495.4, 100.8)
            pages[pg_i].add_line(it["y"] + 5.7, 20.15, 576.0)
        elif it["kind"] == "bal_head":
            if it["cont"]:
                # 续表头合成（个户 p3 范式几何：标题+单列头；公户无快照源）
                blob = make_units(fp, BOLD_FONT, "Balance Summary (continued)",
                                  10.0, 20.1, it["y"])
                for x, txt in ((20.1, "Date"), (115.4, "Ending Balance")):
                    blob += make_units(fp, ITAL_FONT, txt, 9.0, x,
                                       it["y"] + HEAD_TO_COLHDR)
                pages[pg_i].append_text(blob)
                pages[pg_i].add_line(it["y"] + HEAD_TO_COLHDR + 1.9, 20.15,
                                    165.6)
            else:
                _emit(snaps["bal_head"], pages, pg_i, it["y"] - 683.2)
                colhdr_y = it["y"] + HEAD_TO_COLHDR
                for x in (20.15, 207.3, 394.6):
                    pages[pg_i].add_line(colhdr_y + 2.1, x, 165.6)
            for x in (196.25, 383.45):
                pages[pg_i].add_vbar(it["y"] + HEAD_TO_COLHDR - 8.3, x)
        elif it["kind"] == "bal_rows":
            cols = [BAL_CONT] if it["cont"] else BAL_COLS
            per = len(cols)
            blob = b""
            for i, (dt, val) in enumerate(it["cells"]):
                row, col = divmod(i, per)
                dx, vr = cols[col]
                y = it["y"] + PITCH * row
                blob += _date_units(fp, dt, y, x=dx, day_x=dx + 23.8)
                vx = vr - text_w(fp, ROW_FONT, val, ROW_SIZE)
                blob += make_units(fp, ROW_FONT, val, ROW_SIZE, vx, y)
            if blob:
                pages[pg_i].append_text(blob)
        elif it["kind"] == "note":
            _emit(snaps["note"], pages, pg_i, it["y"] - 720.5)
            pages[pg_i].add_line(it["y"] + 5.4, 20.15, 576.0)


def _materialize(doc, pages, plans, d, sum_lines):
    snaps = {k: _snap(pages[b[0]], b) for k, b in BANDS.items()}
    # 整页等值 → 零触碰（回放字节保真；否则擦模板带+重排放回）
    new_lines = _plan_lines(plans, d, sum_lines)
    tpl_p1 = _tpl_lines(0, 443.0, 712.0)
    want_p1 = [(b, t) for b, t in new_lines.get(0, []) if 443.0 <= b < 712.0]
    if _lines_eq(want_p1, tpl_p1):
        pass
    else:
        # p1 非等值：摘要+全部段整区擦（文本+矢量线）→ 重发
        pages[0].delete_units(_band_locs(pages[0], 449.0, 730.0))
        pages[0].delete_res(449.0, 730.0)
        _del_zebra(pages[0])
        _emit_summary(pages, d, sum_lines)
        _emit_page_items(pages, plans[0], d, snaps)
    # 续页：克隆页（无模板续页可比）——直接擦流水区发射
    for plan in plans[1:]:
        pg_i = plan["pg"]
        pages[pg_i].delete_units(_band_locs(pages[pg_i], 155.0, 712.0))
        pages[pg_i].delete_res(155.0, 712.0)
        _emit_page_items(pages, plan, d, snaps)


def _zip_of(d):
    import re as _re
    for ln in reversed(d.get("customer_lines", [])):
        m = _re.search(r"\d{5}(?:-?\d{4})?", str(ln))
        if m:
            return m.group(0)
    return "00000"


def _street_of(d):
    """门牌号所在行：扫描客户行取首个以数字开头的行（门牌号实证在地址行）。"""
    import re as _re
    for ln in d.get("customer_lines", []):
        if _re.match(r"\s*\d", str(ln)):
            return ln
    for ln in d.get("customer_lines", []):
        if _re.search(r"\d", str(ln)):
            return ln
    return ""




def _emit_blank_page(doc, pages, pg_i):
    """尾空白页：居中 "This page intentionally left blank"（个户空白页同款）。"""
    pg = pages[pg_i]
    # 新建页无字体资源——从模板 p1 引入 F6（取其字体对象 xref 挂到本页）
    f6_xref = next((f[0] for f in doc[0].get_fonts(full=True) if f[4] == ROW_FONT),
                   None)
    if f6_xref is not None:
        rk = doc.xref_get_key(pg.page.xref, "Resources")
        if rk[0] == "xref":                # Resources 为间接对象——改写该对象
            fo = int(rk[1].split()[0])
            # 路径式写入 /Font 子典（new_page 的 Resources 是空字典，顶层拼接会挂错层）
            doc.xref_set_key(fo, f"Font/{ROW_FONT}", f"{f6_xref} 0 R")
        elif rk[0] == "null":
            doc.xref_set_key(pg.page.xref, "Resources",
                             f"<</Font<</{ROW_FONT} {f6_xref} 0 R>>>>")
    pg.append_text(make_units(0, ROW_FONT,
                              "This page intentionally left blank", ROW_SIZE,
                              221.7, 388.8))


def _font_program_xrefs(doc, font_xref):
    """Type0 字体的 (CIDFont, FontDescriptor, FontFile2) xref 链。"""
    obj = doc.xref_object(font_xref)
    df = int(re.search(r"DescendantFonts \[ (\d+) 0 R", obj).group(1))
    fdo = doc.xref_object(int(re.search(r"FontDescriptor (\d+) 0 R",
                                        doc.xref_object(df)).group(1)))
    return int(re.search(r"FontFile2 (\d+) 0 R", fdo).group(1))


_DONOR_ARIAL = {'ArialMT': font_path('Regular'), 'Arial-BoldMT': font_path('Bold'), 'Arial-ItalicMT': font_path('Italic'), 'Arial-BoldItalicMT': font_path('BoldItalic')}
_DONOR_CACHE = {}


def _patch_glyphs(doc):
    """空壳字形补写：公户子集 cmap 全但未用字形 contours=0（括号即此类——
    (CONTINUED) 渲染缺括号事故）。从系统 Arial 同变体（同 upm=2048）移植
    轮廓+hmtx；glyphOrder 同名匹配，未命中跳过。
    惰性：仅当本次构建新用了空壳字形（new_cids 命中 contours=0）才写
    FontFile2——回放场景零触碰，字体程序流保持原稿字节。"""
    from fontTools.ttLib import TTFont as _TTF
    for f in doc[0].get_fonts(full=True):
        if f[2] != "Type0" or f[4] not in ("F2", "F3", "F5", "F6"):
            continue
        base = f[3].split("+")[-1]
        path = _DONOR_ARIAL.get(base)
        if not path:
            continue
        uf = FONTS.get((0, f[4]))
        if uf is None or not uf.new_cids:
            continue
        glyf0 = uf.tt["glyf"]
        order0 = uf.tt.getGlyphOrder()
        if not any(g < len(order0)
                   and glyf0[order0[g]].numberOfContours == 0
                   for g in uf.new_cids):
            continue                          # 新用字符字形齐全——不触碰字体流
        if base not in _DONOR_CACHE:
            _DONOR_CACHE[base] = _TTF(path)
        dtt = _DONOR_CACHE[base]
        try:
            ff = _font_program_xrefs(doc, f[0])
        except AttributeError:
            continue
        tt = _TTF(io.BytesIO(doc.xref_stream(ff)))
        glyf, hmtx = tt["glyf"], tt["hmtx"]
        dg, dh = dtt["glyf"], dtt["hmtx"]
        patched = 0
        for name in set(tt.getBestCmap().values()):
            if glyf[name].numberOfContours == 0 and name in dg \
                    and dg[name].numberOfContours > 0:
                glyf[name] = dg[name]
                hmtx[name] = dh[name]
                patched += 1
        if patched:
            out = io.BytesIO()
            tt.save(out)
            doc.update_stream(ff, out.getvalue())


def _replace_fields(pages, d, n_stmt):
    """字段替换（同文跳过保回放字节）：账号/期间/客户/页码，含克隆续页。"""
    L = LAYOUTS["usbiz"]
    dashed = str(d.get("account_display", "")).strip()
    spaced = dashed.replace("-", " ")
    for pg in pages:
        pi = pg.page.number
        if pi == 1 or pi > n_stmt:
            continue                          # 披露页(索引1)/尾空白页
        lay = 0                               # 账单页们同源布局（续页为 p1 克隆）
        lsp = L[lay]["spans"]
        # 账号（两种格式）+ 期间两日期 + 客户三行 + 页码
        for s in lsp:
            t = s["text"].strip()
            if re.fullmatch(r"\d[ -]\d{3}[ -]\d{4}[ -]\d{4}", t):
                pg.replace_span(s, dashed if "-" in s["text"] else spaced)
            elif t in ("Sep 8, 2026", "Sep 30, 2026") and s["bl"] < 130:
                pg.replace_span(s, d["period"]["start" if t == "Sep 8, 2026"
                                              else "end"])
        cust_bl = [61.5, 70.8, 80.0] if lay == 2 else [144.5, 153.5, 162.5]
        for i, ln in enumerate(d.get("customer_lines", [])[:3]):
            target = None
            for s in lsp:
                if abs(s["bl"] - cust_bl[i]) < 2 and s["font"] == ROW_FONT \
                        and s["x"] < 100:
                    target = s
                    break
            if target is not None:
                pg.replace_span(target, ln)
        # 二维条码（F1 六行）+ IMB 邮政条码（F7）+ ER 文字行
        if pi == 0:
            try:
                lines = BC2D.encode_us_2d(BC2D.us_bank_payload(
                    d["period"]["end"], d.get("account_display", "")))
                f1s = sorted([x for x in lsp if x["font"] == "F1"
                              and 90 < x["bl"] < 130], key=lambda x: x["bl"])
                for span, txt in zip(f1s, lines):
                    pg.replace_span(span, txt)
            except Exception as e:
                print(f"[条码] 生成失败保留原样: {e}", file=sys.stderr)
            try:
                serial = str(d.get("imb_serial", "") or '000000000')
                routing = IMB.imb_routing(_zip_of(d), _street_of(d))
                imb_bc = IMB.encode_imb(IMB.imb_tracking(serial), routing)
                f7 = next((x for x in lsp if x["font"] == "F7"
                           and 125 < x["bl"] < 135), None)
                if f7 is not None:
                    pg.replace_span(f7, imb_bc)
                # 后缀随模板：个户 ER / 公户 EB（原稿实证）
                er = next((x for x in lsp if re.search(r"\d{15}\s+E[RB]\s*$",
                                                        x["text"].strip())
                           and 110 < x["bl"] < 120), None)
                if er is not None:
                    suf = re.search(r"(E[RB])\s*$", er["text"].strip()).group(1)
                    core = f"{IMB.imb_tracking(serial)[-15:]} {suf}"
                    # 原文=前导空格+core+尾随空格；等 rstrip 则整行跳过（保回放）
                    lead = len(er["text"]) - len(er["text"].lstrip())
                    trail = len(er["text"]) - len(er["text"].rstrip())
                    mid = len(er["text"]) - lead - trail
                    pad = max(0, lead + max(0, mid - len(core)) )
                    pg.replace_span(er, " " * lead + core
                                    + " " * max(trail, len(er["text"])
                                               - lead - len(core)))
            except Exception as e:
                print(f"[IMB] 生成失败保留原样: {e}", file=sys.stderr)
        # 页码（个户同款）：物理 p1=账单页1；披露页不编号；续页 pi≥2=账单页 pi
        k = 1 if pi == 0 else pi
        for s in lsp:
            if re.fullmatch(r"Page \d of \d", s["text"].strip()):
                pg.replace_span(s, f"Page {k} of {n_stmt}")
                break



# ===== 元数据（缺省=原稿值，时间为实时；显式填写优先）=====
META_DEFAULTS = {'title': 'SAMPLE — NOT A REAL BANK STATEMENT', 'creator': 'BillGen Sample Editor', 'producer': 'BillGen Sample Editor', 'subject': 'Fictional sample statement', 'x_producer': 'BillGen Sample Editor', 'x_creator': 'BillGen Sample Editor', 'x_title': 'SAMPLE — NOT A REAL BANK STATEMENT', 'x_desc': 'Fictional sample statement', 'xmptk': 'BillGen Sample Editor'}


def _now_pdf_date():
    """实时时间 → 原稿同款 D:YYYYMMDDHHMMSS-05'00'（美东 UTC-5）格式"""
    from datetime import datetime, timedelta, timezone
    t = datetime.now(timezone(timedelta(hours=-5)))
    return f"D:{t:%Y%m%d%H%M%S}-05'00'"


def _iso_from_pdf_date(s):
    """D:YYYYMMDDHHMMSS±hh'mm' → ISO YYYY-MM-DDTHH:MM:SS±hh:mm（XMP 用）"""
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})([+-]\d{2})'(\d{2})'", s or "")
    if not m:
        return None
    y, mo, dd, h, mi, se, tz, tg = m.groups()
    return f"{y}-{mo}-{dd}T{h}:{mi}:{se}{tz}:{tg}"


def write_metadata(doc, md):
    """Info + XMP（结构复刻原稿包：属性式字段 + dc:title/description 的 Alt 容器）"""
    md = md or {}
    def g(k):
        return str(md.get(k, "")).strip() or META_DEFAULTS[k]
    creation = str(md.get("creation", "")).strip() or _now_pdf_date()
    mod = str(md.get("mod", "")).strip() or creation
    doc.set_metadata({
        "title": g("title"), "author": "", "creator": g("creator"),
        "subject": g("subject"), "keywords": "",
        "producer": g("producer"),
        "creationDate": creation, "modDate": mod,
    })
    esc = lambda s: (str(s).replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;").replace('"', "&quot;"))   # 属性值用
    txt = lambda s: (str(s).replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;"))                          # 元素文本用（裸引号合法，与原稿一致）
    iso_c, iso_m = _iso_from_pdf_date(creation), _iso_from_pdf_date(mod)
    xmp = ('<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
           f'<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="{esc(g("xmptk"))}">\n'
           '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
           '    <rdf:Description rdf:about=""\n'
           '        xmlns:dc="http://purl.org/dc/elements/1.1/"\n'
           '        xmlns:pdf="http://ns.adobe.com/pdf/1.3/"\n'
           '        xmlns:xmp="http://ns.adobe.com/xap/1.0/"\n'
           '      dc:format="application/pdf"\n'
           f'      pdf:Producer="{esc(g("x_producer"))}"\n'
           + (f'      xmp:CreateDate="{iso_c}"\n' if iso_c else "")
           + (f'      xmp:ModifyDate="{iso_m}"\n' if iso_m else "")
           + f'      xmp:CreatorTool="{esc(g("x_creator"))}">\n'
           '      <dc:description>\n        <rdf:Alt>\n'
           f'          <rdf:li xml:lang="x-default">{txt(g("x_desc"))}</rdf:li>\n'
           '        </rdf:Alt>\n      </dc:description>\n'
           '      <dc:title>\n        <rdf:Alt>\n'
           f'          <rdf:li xml:lang="x-default">{txt(g("x_title"))}</rdf:li>\n'
           '        </rdf:Alt>\n      </dc:title>\n'
           '    </rdf:Description>\n  </rdf:RDF>\n</x:xmpmeta>\n'
           + " " * 2048 + '\n<?xpacket end="w"?>')
    doc.set_xml_metadata(xmp)


def build_pdf(d, out_path, watermark=True):
    d = json.loads(json.dumps(d))
    if d.get("template") != "usbiz":
        raise ValueError(f"本模块仅处理 usbiz，收到 {d.get('template')}")
    if d.get("auto", True):
        us_calc(d)
    plans, n_stmt, sum_lines, _ = flow_engine(None, d)
    doc = fitz.open(TEMPLATES["usbiz"])
    # 个户同款物理序：账单页1 → 披露页 → 续账单页们 → 尾空白页（n_stmt≥2 时；
    # n_stmt=1 无续页=原稿 2 页原样）。续页=p1 克隆插在披露页（索引1）之后
    extra = n_stmt - 1
    for i in range(extra):
        doc.fullcopy_page(0, -1)             # 追加克隆；位于披露页后索引 2..n_stmt
    n_phys = 2 + extra + (1 if n_stmt >= 2 else 0)
    pages = []
    for p in doc:
        is_stmt = p.number == 0 or 2 <= p.number <= n_stmt
        lay = 0 if is_stmt else (1 if p.number == 1 else -1)
        pages.append(Page(doc, p, "usbiz"))
        pages[-1].lspans = LAYOUTS["usbiz"][lay] if lay >= 0 else []

    # 克隆续页重建（先于发射——后擦会删掉发射内容）：
    # 个户续页页头范式：完整页头（logo/右栏信息）+ 客户三行上移 61.5/70.8/80 +
    # 灰底横幅（左账户类型 + 右 (CONTINUED)，无 Member FDIC）+ 次行 U.S.Bank/Acct；
    # 页头无条码区（公户 p1 的 2D 码/EB 行/IMB 在顶部——续页清除）
    cust_snap = _snap(pages[0], (0, 142.0, 166.0))
    for pg_i in range(2, n_stmt + 1):
        pages[pg_i].delete_units(_band_locs(pages[pg_i], 140.0, 730.0))
        pages[pg_i].delete_res(140.0, 730.0)
        _del_zebra(pages[pg_i])
        _del_gray(pages[pg_i])
        # 顶部条码区残留清除（2D 码六行/EB 行/IMB：td 82-142 且 x<500；
        # 右栏期间信息 x>500 保留；客户快照 bl<=80 在带外不受影响）
        locs = [(si, ui) for si, ui in pages[pg_i]._loc
                if 82.0 <= pages[pg_i].segs[si].units[ui].bl < 142.0
                and (pages[pg_i].segs[si].units[ui].x or 0) < 500.0]
        pages[pg_i].delete_units(locs)
        # 银行回址（P.O. Box/Saint Paul）与 TRN 控制行属 p1 版式，
        # 个户式续页头不含——清除（仅左栏 x<100，保右栏 Statement Period 标签）
        # 回址/TRN 行清除——保右栏期间起日（x>500 的日期 span）
        for lo, hi in ((55.0, 68.0), (77.0, 83.0)):
            locs = [(si, ui) for si, ui in pages[pg_i]._loc
                    if lo <= pages[pg_i].segs[si].units[ui].bl < hi
                    and not (pages[pg_i].segs[si].units[ui].x or 0) > 500]
            pages[pg_i].delete_units(locs)
        _emit(cust_snap, pages, pg_i, 61.5 - 144.5)
        # 横幅：灰底+黑线（个户 p3 实证），文字=左账户类型+右 (CONTINUED)
        # （个户 p3 无 Member FDIC——p1 横幅右端的 Member FDIC 由 (CONTINUED) 取代）
        _add_banner_bg(pages[pg_i])
        blob = make_units(pg_i, BOLD_FONT, "BUSINESS ESSENTIALS CHECKING",
                          12.0, 20.1, 145.3)
        blob += make_units(pg_i, BOLD_FONT, "(CONTINUED)", 12.0, 516.8, 145.3)
        blob += make_units(pg_i, ROW_FONT, "U.S. Bank National Association",
                           7.0, 20.1, 154.6)
        acc_ln = "Account Number " + str(d.get("account_display", ""))
        blob += make_units(pg_i, BOLD_FONT, acc_ln, 9.0, 452.0, 156.4)
        pages[pg_i].append_text(blob)
    _materialize(doc, pages, plans, d, sum_lines)
    _replace_fields(pages, d, n_stmt)
    # 尾空白页（物理页数奇时补；个人版 This page intentionally left blank 同款）
    while doc.page_count < n_phys:
        p_new = doc.new_page(width=612, height=792)
        pages.append(Page(doc, p_new, "usbiz"))
        pages[-1].lspans = []
    if doc.page_count == n_phys and n_phys > n_stmt + 1:
        _emit_blank_page(doc, pages, n_phys - 1)
        pages[n_phys - 1].commit()

    for pg in pages:
        pg.commit()
    # 字体补写（ToUnicode/W）
    committed = set()
    for (pi, fname), uf in FONTS.items():
        if pi in (0, 2) and uf.tou not in committed and uf.new_cids:
            uf.commit(doc)
            committed.add(uf.tou)
    # 空壳字形补写（括号等未用字形——续页 (CONTINUED)/(continued) 必需）
    _patch_glyphs(doc)
    write_metadata(doc, d.get("metadata"))
    if watermark:
        for p in doc:
            add_watermark(doc, p)
    if d.get("encrypt"):
        doc.save(out_path, garbage=3, deflate=True,
                 encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="usbank_owner",
                 permissions=fitz.PDF_PERM_PRINT | fitz.PDF_PERM_ACCESSIBILITY)
    else:
        doc.save(out_path, garbage=3, deflate=True)
    doc.close()
    return out_path
