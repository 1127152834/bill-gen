#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""US Bank 个户对账单模板生成器
架构（大通同款）：字节级静态克隆 + 内容流单元定点重写；同数据回放零操作=1:1。
- 字体=Type0(Identity-H)+全量字体程序：char→GID 走程序 cmap（fontTools），
  ToUnicode/W 随用随补——可编辑字符集=Arial 家族全字母数字。
- 流水：p1 摘要后 Deposits→Other Withdrawals→Balance Summary 串行，
  页底 709.3 截断→续页 188.5 起；尾空白页；"Page X of Y" 按账单页计数。
"""
import json, math, os, re, sys

import fitz

from billgen.themes.usbank import chstream
from billgen.themes.usbank import barcode_us as BC2D
from billgen.themes.usbank import imb_us as IMB

from billgen.themes.usbank.shared import (
    parse_cmap,
    patch_outlines,
    UsFont,
    esc,
    _build_layouts,
    find,
    _cents,
    _fmt_amt,
    _usd_span,
    _month_key,
    _band_locs,
    _snap,
    _erase,
    _emit,
    _norm,
    ending_of,
    _zip_of,
    _street_of,
    _now_pdf_date,
    _iso_from_pdf_date,
    write_metadata as _write_metadata,
)

BASE = os.path.dirname(os.path.abspath(__file__))
PAGE_H = 792.0
TEMPLATES = {"us": os.path.join(BASE, "assets", "us_static_personal.pdf")}
_DOC = fitz.open(TEMPLATES["us"])

# ============ 版面常量（两原稿逐线实测） ============
PITCH = 10.4
HEAD_TO_COLHDR = 10.6
COLHDR_TO_ROW = 10.3
TOTAL_AFTER_LAST = 17.3
SEC_GAP = 18.2
SUMMARY_END_GAP = 17.3
FIRST_SEC_GAP = 18.2
NOTE_AFTER_LAST = 16.3
P1_BOTTOM = 709.3
CONT_FIRST = 188.5
CONT_TITLE = 167.6
BOTTOM = 709.3
BAL_PAIRS = 3
EPS = 0.15

COL = {"date": 20.1, "day": 43.9, "type": 56.2, "detail": 207.4,
       "sub_left": 70.6, "sub_right": 221.8, "ref": 409.0,
       "dollar": 495.4, "amt_r": 589.0}
BAL_COLS = [(20.1, 174.8), (207.4, 362.0), (394.5, 549.2)]
BAL_CONT = (20.1, 174.8)

# ============ 字体机制（状态由本生成器独立持有） ============
FONTS = {}
for _pi in range(len(_DOC)):
    for _f in _DOC[_pi].get_fonts(full=True):
        if _f[2] == "Type0":
            FONTS[(_pi, _f[4])] = UsFont(_DOC, _f[0])

ROW_FONT, ROW_SIZE = "F6", 9.0
BOLD_FONT = "F5"
ITAL_FONT = "F2"


def _font_pg(pg_i):
    """字体/度量源页：p1=0，续页与克隆页=2（静态页/空白页不编辑）。"""
    if (pg_i, "F6") in FONTS:
        return pg_i
    return 2 if pg_i >= 2 else 0


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


def make_units(pg_i, fname, text, size, x, bl):
    b = cid_bytes(pg_i, fname, text)
    if b is None:
        return b""
    return (f"/{fname} {size:g} Tf\n0 0 0 rg\n1 0 0 1 {x:.2f} "
            f"{PAGE_H - bl:.2f} Tm\n").encode() + b"(" + esc(b) + b")Tj\n"


# ============ LAYOUTS（导入时提取：文本/坐标/资源名/字号） ============
LAYOUTS = {"us": _build_layouts(_DOC)}


# ============ 内容流页（大通 Page 同款） ============
class Page:
    def __init__(self, doc, page, tag="us"):
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

    def spans_units(self, span, allow_overflow=False):
        want = span["text"]
        x1 = span.get("x1", span["x"] + len(want) * 5)
        out, consumed = [], ""
        cand = [k for k, u in enumerate(self.units)
                if u.x is not None and abs(u.bl - span["bl"]) < 1.5
                and span["x"] - 1.8 <= u.x <= x1 + 1.0]
        cand.sort(key=lambda k: self.units[k].x)
        cand_ext = [] if not allow_overflow else [
            k for k, u in enumerate(self.units)
            if u.x is not None and u.x >= span["x"] - 1.8
            and abs(u.bl - span["bl"]) < 1.5]
        cand_ext.sort(key=lambda k: self.units[k].x)
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
            # 布局里的 x1 偶尔比实际绘制文本短（如持卡人姓名两行），此时放宽
            # 右边界重扫同基线单元，按前缀继续匹配，避免整行替换被静默跳过。
            out, consumed = [], ""
            for k in cand_ext:
                if consumed == want:
                    break
                dec = self._decode(self.units[k].font, self.units[k].text)
                if not dec:
                    continue
                for cand_dec in (dec, " " + dec.lstrip(), dec.rstrip(), dec.strip(),
                                 " " + dec.strip()):
                    if want.startswith(consumed + cand_dec):
                        consumed += cand_dec
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
        locs = self.spans_units(span, allow_overflow=True)
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


# ============ 自动计算 ============
def us_calc(d):
    begin = _cents(d.get("begin_balance", 0))
    dep = sum(_cents(r["amount"]) for r in d.get("deposits", []))
    wd = sum(_cents(r["amount"]) for r in d.get("withdrawals", []))
    end = begin + dep - wd
    d["summary"] = {
        "begin": _fmt_amt(begin),
        "deposits": _fmt_amt(dep),
        "withdrawals": _fmt_amt(-wd),
        "ending": _fmt_amt(end)}
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


# ============ 水印 ============
def _wm_units(pg_i, fname, text, size, x, y, c45, s45, gray=0.78):
    """水印用页面自带的嵌入字体画（CID 编码），不新建 base-14 Helvetica。

    参考件的字体全是嵌入子集，凭空多一个非嵌入 Helvetica-Bold 会被 `pdffonts`
    一眼看出来。返回 None 表示这一页没有可用字体（调用方退回旧路径）。
    """
    body = cid_bytes(pg_i, fname, text)
    if body is None:
        return None
    hexstr = "<" + body.hex().upper() + ">"
    return (f"q {gray:g} {gray:g} {gray:g} rg BT /{fname} {size} Tf "
            f"1 0 0 1 {x:.2f} {y:.2f} Tm {c45:.4f} {s45:.4f} {-s45:.4f} "
            f"{c45:.4f} 0 0 Tm {hexstr} Tj ET Q").encode()


def _wm_register_helv(doc, page):
    """退回路径：把 base-14 Helvetica-Bold 注册进页面字体资源。"""
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


def add_watermark(doc, page):
    t = "S A M P L E"
    size = 100
    w = text_w(page.number, ROW_FONT, t, size)
    cx, cy = 306.0, 396.0
    c45, s45 = math.cos(math.radians(45)), math.sin(math.radians(45))
    x = cx - w / 2 * c45
    y = cy + w / 2 * s45
    ops = _wm_units(page.number, ROW_FONT, t, size, x, y, c45, s45)
    if ops is None:
        # 这一页没有任何可用的嵌入字体（例如纯图片页）——退回 base-14
        hex_t = "<" + "".join(f"{ord(c):04X}" for c in t) + ">"
        ops = (f"q 0.78 0.78 0.78 rg BT /W {size} Tf "
               f"1 0 0 1 {x:.2f} {y:.2f} Tm {c45:.4f} {s45:.4f} {-s45:.4f} "
               f"{c45:.4f} 0 0 Tm {hex_t} Tj ET Q").encode()
        _wm_register_helv(doc, page)
    cts = page.get_contents()
    if cts:
        # 前置：水印在内容之下，不压金额
        doc.update_stream(cts[0], ops + b"\n" + doc.xref_stream(cts[0]))
    else:
        # 页内容全在 XObject（无页级 contents）——新建 Contents 流画水印
        nx = doc.get_new_xref()
        doc.update_object(nx, "<< >>")
        doc.update_stream(nx, ops, compress=False)
        doc.xref_set_key(page.xref, "Contents", f"{nx} 0 R")


# ============ 流水引擎（快照-擦除-发射，大通验证架构） ============
SUMMARY_TITLE_BL = 450.2
SUMMARY_FIRST = 460.9
SUM_DOLLAR_X, SUM_VAL_R = 192.9, 292.9
SUM_ENDING_X = 50.8

# 模板带（页, lo, hi）——快照/擦除用
BANDS = {
    "summary_rows": (0, 458.0, 502.0),
    "dep_head": (0, 515.0, 530.5),
    "dep_rows": (0, 536.0, 552.0),
    "dep_total": (0, 563.0, 568.5),
    "wd_head": (0, 581.5, 596.5),
    "wd_rows": (0, 602.0, 652.0),
    "wd_total": (0, 661.0, 666.5),
    "bal_head": (0, 679.5, 695.5),
    "bal_rows": (0, 700.0, 706.5),
    "cont_bal_head": (2, 165.0, 181.5),
    "cont_bal_rows": (2, 186.0, 192.5),
    "note": (2, 202.0, 208.0),
}

SECTIONS = [
    ("deposits", "Deposits / Credits ", "Total Deposits / Credits"),
    ("withdrawals", "Other Withdrawals ", "Total Other Withdrawals"),
]


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
    summary_rows = [("Beginning Balance on {start}", d["summary"]["begin"], True, 20.1)]
    if has_dep:
        summary_rows.append(("Deposits / Credits", d["summary"]["deposits"], False, 20.1))
    if has_wd:
        summary_rows.append(("Other Withdrawals", d["summary"]["withdrawals"], False, 20.1))
    # 基线用模板精确值（460.9/471.2/481.6——原稿间距 10.3/10.4 不均，
    # 计算值会偏 0.1pt 破坏回放像素）
    TPL_SUM_BL = [460.9, 471.2, 481.6]
    sum_lines = []
    for i, (lbl, val, dollar, x) in enumerate(summary_rows):
        sum_lines.append((TPL_SUM_BL[i], lbl, val, dollar, x))
    ending_y = TPL_SUM_BL[len(summary_rows) - 1] + SUMMARY_END_GAP
    sum_lines.append((ending_y,
                      "Ending Balance on  {end}", d["summary"]["ending"], True,
                      SUM_ENDING_X))
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
        plans.append({"pg": 2 + (page_no - 2), "items": []})

    sec_defs = [("deposits", "Deposits / Credits ", "Total Deposits / Credits", dep_rows),
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
    for sp in LAYOUTS["us"][lay]["spans"]:
        if lo <= sp["bl"] < hi:
            by.setdefault(round(sp["bl"], 1), []).append((sp["x"], sp["text"]))
    out = []
    for bl, v in sorted(by.items()):
        t = _norm(" ".join(x for _, x in sorted(v)))
        out.append((_rb(bl), t))
    return out


def _rb(bl):
    return round(bl * 2) / 2.0


def _plan_lines(plans, d, sum_lines):
    """计划可视文本（与模板同口径：按基线合并 span、空白折叠、0.5pt 桶）。"""
    out = {}
    p1 = []
    for y, lbl, val, dollar, x in sum_lines:
        lbl = lbl.format(start=d["period"]["start"].split(",")[0],
                         end=d["period"]["end"])
        p1.append((_rb(y), _norm(lbl + " " + (("$ " + val) if dollar
                                              else val))))
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
    _erase(pages[0], *BANDS["summary_rows"][1:])
    # `Account Summary` 这行小标题在参考件里是有的（Arial-BoldMT 10pt，基线 450.25，
    # x 20.15），我们擦掉摘要区之后一直没补回来 —— 页面上就少一行标题，
    # 公户版是画的（bl 441.2），个人版以前漏了。
    blob = make_units(0, BOLD_FONT, "Account Summary", 10.0, 20.1, SUMMARY_TITLE_BL)
    for y, lbl, val, dollar, x in sum_lines:
        lbl = lbl.format(start=d["period"]["start"].split(",")[0],
                         end=d["period"]["end"])
        bfont = BOLD_FONT if "Ending" in lbl else ROW_FONT
        blob += make_units(0, bfont, lbl, ROW_SIZE, x, y)
        if dollar:
            blob += make_units(0, bfont, "$", ROW_SIZE, SUM_DOLLAR_X, y)
        vx = SUM_VAL_R - text_w(0, bfont, val, ROW_SIZE)
        blob += make_units(0, bfont, val, ROW_SIZE, vx, y)
    if blob:
        pages[0].append_text(blob)
    # 摘要分隔线：类别区与 Ending 之间（模板 487.2 = ending-11.7 实证）
    pages[0].add_line(ending_of(sum_lines) - 11.7, 192.9, 100.8)


def _emit_page_items(pages, plan, d, snaps):
    """非等值页：按计划发射段头/行/合计/余额表/注 + 全部线型（实证模型）。"""
    pg_i = plan["pg"]
    fp = 0 if pg_i == 0 else 2
    for it in plan["items"]:
        if it["kind"] == "sec_head":
            is_dep = it["title"].startswith("Deposits")
            band = snaps["dep_head"] if is_dep else snaps["wd_head"]
            tpl_y = 517.1 if is_dep else 583.9
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
            band = snaps["cont_bal_head"] if it["cont"] else snaps["bal_head"]
            tpl_y = 167.6 if it["cont"] else 681.8
            _emit(band, pages, pg_i, it["y"] - tpl_y)
            colhdr_y = it["y"] + HEAD_TO_COLHDR
            if it["cont"]:
                pages[pg_i].add_line(colhdr_y + 1.9, 20.15, 165.6)
            else:
                for x in (20.15, 207.3, 394.6):
                    pages[pg_i].add_line(colhdr_y + 2.1, x, 165.6)
            for x in (196.25, 383.45):
                pages[pg_i].add_vbar(colhdr_y - 8.3, x)
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
            _emit(snaps["note"], pages, pg_i, it["y"] - 204.9)
            pages[pg_i].add_line(it["y"] + 5.4, 20.15, 576.0)


def _materialize(doc, pages, plans, d, sum_lines):
    snaps = {k: _snap(pages[b[0]], b) for k, b in BANDS.items()}
    # 整页等值 → 零触碰（回放字节保真；否则擦模板带+重排放回）
    new_lines = _plan_lines(plans, d, sum_lines)
    tpl_p1 = _tpl_lines(0, 458.0, 712.0)
    want_p1 = [(b, t) for b, t in new_lines.get(0, []) if 458.0 <= b < 712.0]
    if want_p1 == tpl_p1:
        pass
    else:
        # p1 非等值：摘要+全部段整区擦（文本+矢量线）→ 重发
        pages[0].delete_units(_band_locs(pages[0], 440.0, 712.0))
        pages[0].delete_res(440.0, 712.0)
        _emit_summary(pages, d, sum_lines)
        _emit_page_items(pages, plans[0], d, snaps)
    # 续页：第 2+ 账单页
    tpl_cont = _tpl_lines(2, 160.0, 215.0)
    for plan in plans[1:]:
        pg_i = plan["pg"]
        want = [(b, t) for b, t in new_lines.get(pg_i, [])
                if 160.0 <= b < 215.0]
        if want == tpl_cont and pg_i == 2:
            continue                       # 模板续页原样（回放）
        pages[pg_i].delete_units(_band_locs(pages[pg_i], 160.0, 215.0))
        pages[pg_i].delete_res(160.0, 215.0)
        _emit_page_items(pages, plan, d, snaps)


def _stamp_page_no(pg, k, total):
    """续页/披露页/尾页在原稿里没有页码 span，按首页样式（x=550、bl=126.9、9pt）补打。

    真实 Uni-Statement 每页都在同一位置印 "Page k of total"，只有首页有可替换
    span 时其余页会漏印——这是最容易被一眼看穿的版式硬伤。
    """
    doc = pg.doc
    try:                                  # 披露页/尾页资源里没有 F6，先从首页借一个
        kind, val = doc.xref_get_key(doc.load_page(0).xref, "Resources/Font/F6")
        if kind == "xref":
            doc.xref_set_key(pg.page.xref, "Resources/Font/F6", val)
    except Exception:
        pass
    raw = make_units(pg.page.number, "F6", f"Page {k} of {total}",
                     9.0, 550.0, 126.9)
    if raw:
        pg.append_text(raw)


def _replace_fields(pages, d, n_stmt):
    """字段替换（同文跳过保回放字节）：账号/期间/客户/页码，含克隆续页。"""
    L = LAYOUTS["us"]
    # 页码只数「账单页」：参考件 `us_static_personal.pdf` 共 4 页，账单页 2 张，
    # 印的是 `Page 1 of 2` / `Page 2 of 2`；披露页与尾空白页没有页码。
    total = n_stmt
    dashed = str(d.get("account_display", "")).strip()
    spaced = dashed.replace("-", " ")
    account_pattern = r"\d[ -]\d{3}[ -]\d{4}[ -]\d{4}"
    for pg in pages:
        pi = pg.page.number
        if pi == 1:
            continue                          # 静态披露页：原稿无页码
        lay = 2 if (pi >= 2 and pi != len(pages) - 1) else pi
        if pi == len(pages) - 1 and pi >= 2:
            continue                          # 尾空白页：原稿无页码
        lsp = L[lay]["spans"]
        # 账号（两种格式）+ 期间两日期 + 客户三行 + 页码
        for s in lsp:
            t = s["text"].strip()
            if re.search(account_pattern, t):
                pg.replace_span(s, re.sub(account_pattern,
                    lambda match: dashed if "-" in match.group(0) else spaced,
                    s["text"]))
            elif t in ("Aug 7, 2026", "Sep 4, 2026") and s["bl"] < 130:
                pg.replace_span(s, d["period"]["start" if t.startswith("Aug")
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
                er = next((x for x in lsp if "ER" in x["text"]
                           and 110 < x["bl"] < 120), None)
                if er is not None:
                    core = f"{IMB.imb_tracking(serial)[-15:]} ER"
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
        # 页码：账单页序号（首页=1，续页=物理索引）+ 账单页总数
        k = 1 if pi == 0 else pi
        stamped = False
        for s in lsp:
            if re.fullmatch(r"Page \d of \d", s["text"].strip()):
                pg.replace_span(s, f"Page {k} of {total}")
                stamped = True
                break
        if not stamped:
            _stamp_page_no(pg, k, total)


# ===== 元数据（缺省=原稿值，时间为实时；显式填写优先）=====
META_DEFAULTS = {'title': 'U.S. Bank Online Statement', 'creator': 'Ricoh Americas Corporation, AFP2PDF v3.2.1', 'producer': 'Ricoh Americas Corporation, AFP2PDF v3.2.1', 'subject': '', 'x_producer': 'Ricoh Americas Corporation, AFP2PDF v3.2.1', 'x_creator': 'Ricoh Americas Corporation, AFP2PDF v3.2.1', 'x_title': 'U.S. Bank Online Statement', 'x_desc': '', 'xmptk': 'Ricoh Americas Corporation, AFP2PDF v3.2.1'}


def write_metadata(doc, md):
    return _write_metadata(doc, md, META_DEFAULTS, _now_pdf_date)


def build_pdf(d, out_path, watermark=True):
    d = json.loads(json.dumps(d))
    if d.get("template") != "us":
        raise ValueError(f"本模块仅处理 us，收到 {d.get('template')}")
    if d.get("auto", True):
        us_calc(d)
    plans, n_stmt, sum_lines, _ = flow_engine(None, d)
    doc = fitz.open(TEMPLATES["us"])
    # 物理续页克隆（账单第 3+ 页），空白页保持在最后
    extra = n_stmt - 2
    if extra > 0:
        for i in range(extra):
            doc.fullcopy_page(2, 3 + i)
    n_phys = 2 + max(1, n_stmt - 1) + 1        # p1+静态+续页们+空白
    if n_stmt == 1:
        doc.delete_page(2)                     # 无续页：删模板续页（空白页收尾）
    pages = []
    for p in doc:
        lay = min(p.number, 3) if p.number <= 3 else 2
        if p.number >= 3:
            lay = 2 if p.number < n_phys - 1 else 3
        pages.append(Page(doc, p, "us"))
        pages[-1].lspans = LAYOUTS["us"][lay]

    _materialize(doc, pages, plans, d, sum_lines)
    _replace_fields(pages, d, n_stmt)

    for pg in pages:
        pg.commit()
    # 字体补写（ToUnicode/W）
    committed = set()
    for (pi, fname), uf in FONTS.items():
        if pi in (0, 2) and uf.tou not in committed and uf.new_cids:
            uf.commit(doc)
            committed.add(uf.tou)
    # 空壳字形补写：底稿子集只收过原稿用到的字，`Jan` 的 'J' 有码位无轮廓，
    # 替换期间后文本层正常但像素空白（实测 `an 05`）。这里按系统 Arial 补轮廓。
    patch_outlines(doc)
    # 多余物理页删除
    for pi in range(len(doc) - 1, n_phys - 1, -1):
        doc.delete_page(pi)
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
