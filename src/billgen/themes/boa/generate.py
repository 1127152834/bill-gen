#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BOA 对账单模板生成器 — 个人版（Adv Plus Banking）+ 商业版（Business Advantage）
1:1 复刻自原稿：Connections 字体（重建）、矢量细线、logo/横幅/图标、版式坐标。
银行名等文本按 data.bank 替换；水印默认开启。支票影像页不复刻（已按要求删除）。
"""
import json
import math
import os
import re
from decimal import Decimal

import fitz

BASE = os.path.dirname(os.path.abspath(__file__))
W, H = 612.0, 792.0

FONTS = {n: os.path.join(BASE, "assets", "fonts", f"{n}.otf")
         for n in ("Reg", "Med", "Bol", "Ita", "FG", "HS")}
IMGS = os.path.join(BASE, "assets", "img")

INK = (0x23 / 255, 0x1F / 255, 0x20 / 255)
GRAY = (0x40 / 255, 0x3F / 255, 0x41 / 255)
RED = (0xE4 / 255, 0x17 / 255, 0x3E / 255)
BLUE = (0x00 / 255, 0x61 / 255, 0xAB / 255)
SOFT = (0x4C / 255, 0x4D / 255, 0x4E / 255)

# 版式常量（原稿实测）
PAGE_NUM_X, PAGE_NUM_BL = 504.0, 782.64
AMT_R = 576.0
DESC_X, DESC_W = 90.8, 374.7
DATE_X = 36.0
ROW_PITCH, LINE_PITCH = 18.0, 10.8
SUM_R = 345.2
FIRST_ROW_D = {"personal": 16.02, "business": 17.6}   # 表头 y0 → 首行 y0
TX1_HDR_BL = {"personal": 132.36, "business": 132.48}  # 首流水页第一段标题基线
SEC_HDR_BL = 123.13
CONT_HDR_BL = 78.53
TX1_LIMIT, CONT_LIMIT = 590.0, 738.0

_font_cache = {}


def _font(name):
    if name not in _font_cache:
        _font_cache[name] = fitz.Font(fontfile=FONTS[name])
    return _font_cache[name]


def tlen(text, fname, size):
    return _font(fname).text_length(text, fontsize=size)


def _rgb(hexc):
    h = hexc.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


#: 底稿抽出来的字体是子集，个别字母只有编码没有轮廓（`FG.otf` 缺 F/H/J/K/N/O/Q/W/X/Z，
#: `HS.otf` 缺一半大写）。直接 insert_text 时这些字会**整字消失**（实测页脚控制串
#: 的 `SPEC: F` 印成 `SPEC:`）。这里兜一层：缺字形的字符改用 Helvetica 补画，
#: 位置按 Helvetica 字宽推进，至少字母不会凭空不见。
_MISSING_CACHE: dict[str, set[str]] = {}
_FALLBACK_ADV = 0.56


def _missing_chars(fname):
    if fname in _MISSING_CACHE:
        return _MISSING_CACHE[fname]
    from fontTools.ttLib import TTFont as _TTFont
    from fontTools.pens.recordingPen import RecordingPen as _Pen
    path = FONTS.get(fname)
    missing = set()
    if path:
        font = _TTFont(path, lazy=True)
        cmap = font.getBestCmap()
        glyphs = font.getGlyphSet()
        for code in range(32, 127):
            glyph = cmap.get(code)
            if not glyph:
                missing.add(chr(code))
                continue
            pen = _Pen()
            glyphs[glyph].draw(pen)
            if not pen.value:
                missing.add(chr(code))
    _MISSING_CACHE[fname] = missing
    return missing


def _draw_with_fallback(page, x, bl, text, fname, size, color):
    """整段绘制；缺字形的字符用 Helvetica 顶上，空格只推进不画。"""
    missing = _missing_chars(fname)
    if not text or not (missing & set(text)):
        page.insert_text(fitz.Point(x, bl), text, fontsize=size,
                         fontname=f"f{fname}", fontfile=FONTS[fname], color=color)
        return
    cx = x
    run: list[str] = []
    run_missing = False

    def flush():
        nonlocal cx, run
        if not run:
            return
        piece = "".join(run)
        if piece.strip():
            if run_missing:
                page.insert_text(fitz.Point(cx, bl), piece, fontsize=size,
                                 fontname="helv", color=color)
                cx += len(piece) * size * _FALLBACK_ADV
            else:
                page.insert_text(fitz.Point(cx, bl), piece, fontsize=size,
                                 fontname=f"f{fname}", fontfile=FONTS[fname], color=color)
                cx += tlen(piece, fname, size)
        else:
            cx += len(piece) * size * _FALLBACK_ADV
        run = []

    for ch in text:
        if ch == " ":
            run.append(ch)
            continue
        miss = ch in missing
        if run and any(c != " " for c in run) and miss != run_missing:
            flush()
        if not run:
            run_missing = miss
        elif not any(c != " " for c in run):
            run_missing = miss
        run.append(ch)
    flush()


def txt(page, x, bl, text, fname, size, color, fit_x1=None):
    color = color if isinstance(color, tuple) else _rgb(color)
    if fit_x1 is not None:
        w = tlen(text, fname, size)
        hsc = (fit_x1 - x) / w if w > 0 else 1.0
        if 0.9 < hsc < 1.1 and abs(hsc - 1.0) > 0.0005:
            page.insert_text(fitz.Point(x, bl), text, fontsize=size,
                             fontname=f"f{fname}", fontfile=FONTS[fname],
                             color=color,
                             morph=(fitz.Point(x, bl), fitz.Matrix(hsc, 1)))
            return
    _draw_with_fallback(page, x, bl, text, fname, size, color)


def txt_r(page, x1, bl, text, fname, size, color):
    txt(page, x1 - tlen(text, fname, size), bl, text, fname, size, color)


RULE_W, RULE_THIN = 0.2390640, 0.1181244
RULE_ABOVE, RULE_ABOVE_TOTAL = 4.96, 5.38


def rule(page, x0, y, x1, w=RULE_W, color=INK):
    page.draw_line(fitz.Point(x0, y), fitz.Point(x1, y), color=color, width=w)


def img(page, name, x, y, w, h):
    fn = name
    if "." not in fn:
        for ext in (".jpeg", ".png"):
            if os.path.exists(os.path.join(IMGS, fn + ext)):
                fn = fn + ext
                break
    page.insert_image(fitz.Rect(x, y, x + w, y + h), filename=os.path.join(IMGS, fn),
                      keep_proportion=False)


def add_watermark(page):
    size, f, t = 100, "Bol", "S A M P L E"
    w = tlen(t, f, size)
    cx, cy = W / 2, H / 2
    dx, dy = math.cos(math.radians(45)), -math.sin(math.radians(45))
    x = cx - w / 2 * dx + 17.7
    y = cy - w / 2 * dy + 17.65
    page.insert_text(fitz.Point(x, y), t, fontsize=size,
                     fontname="fBol", fontfile=FONTS["Bol"],
                     color=(0.78, 0.78, 0.78), fill_opacity=0.32, overlay=False,
                     morph=(fitz.Point(x, y), fitz.Matrix(45)))


# ---------------------------------------------------------------- 替换
# 促销/法务段落里出现的真实商品名与评奖来源（顺序无意义，最后统一按长度倒序）
_PROMO_KEYS = (
    ("Balance Connect™", "overdraft_product"),
    ("Business Fundamentals® Checking", "business_checking"),
    ("Business Fundamentals®", "business_checking_short"),
    ("Best Banks for Small Business", "award_name"),
    ("SmartAsset.com", "award_domain"),
    ("SmartAsset", "award_source"),
    ("SafeBalance Banking®", "safebalance_product"),
    ("SSM-03-25-0560.B", "form_code"),
    ("Merrill", "brokerage_brand"),
)


# 个人支票账单底部广告位：底稿来自商业账户套件，讲的是小微企业经营贷。
# 这里默认换成人个版的钱管家/安全工具文案（promo 显式给了就按 promo 走）。
_PERSONAL_COPY = {
    "Products and solutions to move your business forward":
        "Tools to help you stay in control of your money",
    "- Business Fundamentals\u00ae Checking - now with no monthly fee on new accounts for 12 months":
        "- Set real-time alerts for balances, deposits and large purchases",
    "- Business credit cards":
        "- Lock and unlock your debit card instantly from the app",
    "- Named among Best Banks for Small Business by SmartAsset*":
        "- Pay bills and move money with Zelle\u00ae in Online Banking",
    "* SmartAsset.com 2025":
        "* Mobile and online alerts require an active account.",
    "certain information is collected from your mobile device for business purposes. This credit":
        "certain information is collected from your mobile device for account servicing. This bank",
    "card program is issued and administered by Bank of America, N.A. Banking products":
        "program is issued and administered by Bank of America, N.A. Banking products",
}


def build_subs(d):
    b = d.get("bank", {})
    name, legal, domain = b.get("name", ""), b.get("legal", ""), b.get("domain", "")
    abbr = "".join(name.upper().split())[:12] or "BANK"
    subs = {}
    if legal:
        subs["Bank of America, N.A."] = legal
    if name:
        subs["Bank of America Corporation"] = name + " Corporation"
        subs["Bank of America"] = name
    if domain:
        subs["bankofamerica.com"] = domain
    subs["BKOFAMERICA"] = abbr
    # 版式里烙死的自家产品名与第三方评奖文案：数据给了就换，不给保持原稿（保 parity）
    promo = d.get("promo") or {}
    for original, key in _PROMO_KEYS:
        value = promo.get(key)
        if value:
            subs[original] = str(value)
    for k, v in _PERSONAL_COPY.items():
        subs.setdefault(k, v)
    return sorted(subs.items(), key=lambda kv: -len(kv[0]))


def sub_text(s, subs):
    for k, v in subs:
        if k in s:
            s = s.replace(k, v)
    return s


# ---------------------------------------------------------------- 金额
def _cents(v):
    try:
        s = str(v).replace(",", "").replace("$", "").strip()
        return int((Decimal(s) * 100).quantize(Decimal("1")))
    except Exception:
        return 0


def _fmt(cents, dollar=False):
    q = abs(cents)
    s = f"{q // 100:,}.{q % 100:02d}"
    if dollar:
        s = "$" + s
    return ("-" if cents < 0 else "") + s


def fmt_plain(v):
    return _fmt(_cents(v))


def fmt_dollar(v):
    return _fmt(_cents(v), dollar=True)


def apply_auto_calc(d):
    if not d.get("auto"):
        return
    begin = _cents(d.get("begin_balance", 0))
    dep = sum(_cents(r["amount"]) for r in d.get("deposits", []))
    wd = sum(_cents(r["amount"]) for r in d.get("withdrawals", []))
    fee = sum(_cents(r["amount"]) for r in d.get("fees", []))
    chk = 0  # 本版式没有独立的支票分表，支票行并入 withdrawals，故单独一栏只印 0
    d["summary"] = {
        "begin": _fmt(begin, dollar=True), "deposits": _fmt(dep), "withdrawals": _fmt(wd),
        "checks": "0.00", "fees": _fmt(fee),
        "ending": _fmt(begin + dep + wd + fee + chk, dollar=True),
    }
    d["totals"] = {"deposits": _fmt(dep, dollar=True), "withdrawals": _fmt(wd, dollar=True),
                   "checks": "$0.00", "fees": _fmt(fee, dollar=True)}
    d["stats_auto"] = {
        "deposits_n": len(d.get("deposits", [])),
        "withdrawals_n": len(d.get("withdrawals", [])),
        "fees_n": len(d.get("fees", [])),
    }
    # 每日余额：按 MM/DD 聚合流水
    daily = {}
    for sec in ("deposits", "withdrawals", "fees"):
        for r in d.get(sec, []):
            mm = str(r.get("date", ""))[:5]
            if not mm:
                continue
            daily[mm] = daily.get(mm, 0) + _cents(r.get("amount", 0))
    out, cur = [], begin
    for mm in sorted(daily):
        cur += daily[mm]
        out.append([mm, _fmt(cur)])
    d["daily"] = out


def wrap_desc(text, width=DESC_W, fname="Reg", size=9.0):
    """显式换行优先；超宽行自动换行"""
    out = []
    for seg in str(text).split("\n"):
        if tlen(seg, fname, size) <= width:
            out.append(seg)
            continue
        cur = ""
        for word in seg.split(" "):
            t = (cur + " " + word).strip()
            if tlen(t, fname, size) <= width or not cur:
                cur = t
            else:
                out.append(cur)
                cur = word
        if cur:
            out.append(cur)
    return out or [""]


# ---------------------------------------------------------------- 静态渲染
_TW_MAP = {}  # (page,font,color) -> [writer, page, rgb] 延迟批量写入


def _txt_static(page, s, text, subs):
    """静态 span：若文本未被替换且有逐字坐标，则逐字收集（TextWriter 批量写，亚像素级 1:1）"""
    if s.get("chars_x"):
        key = (id(page), s["font"], s["color"])
        if key not in _TW_MAP:
            _TW_MAP[key] = [fitz.TextWriter(page.rect), page, _rgb(s["color"])]
        w = _TW_MAP[key][0]
        fo = _font(s["font"])
        for ch, cx in zip(text, s["chars_x"]):
            w.append(fitz.Point(cx, s["bl"]), ch, font=fo, fontsize=s["size"])
        return
    txt(page, s["x"], s["bl"], text, s["font"], s["size"], s["color"], fit_x1=s.get("x1"))


def render_static(page, items, subs):
    for im in items.get("imgs", []):
        img(page, im["n"], im["x"], im["y"], im["w"], im["h"])
    for r in items.get("rules", []):
        rule(page, r["x0"], r["y"], r["x1"], r.get("w", 0.239))
    for rc in items.get("rects", []):
        if rc.get("fill") and rc["fill"] != "#ffffff":
            page.draw_rect(fitz.Rect(rc["x0"], rc["y0"], rc["x1"], rc["y1"]),
                           color=_rgb(rc["stroke"]) if rc.get("stroke") else None,
                           fill=_rgb(rc["fill"]), width=0.239)
    for s in items.get("spans", []):
        raw = s["text"]
        rep = sub_text(raw, subs)
        if rep == raw:
            _txt_static(page, s, raw, subs)
        else:
            txt(page, s["x"], s["bl"], rep, s["font"], s["size"], s["color"])
    for w, pg, rgb in _TW_MAP.values():
        w.write_text(pg, color=rgb)
    _TW_MAP.clear()


def header_strip(page, d, bl, rule_x1):
    name = d.get("strip_name") or d["customer_lines"][0]
    t = f"{name}   |   Account # {d['account_number']}   |   {d['period']['label']}"
    txt(page, 36.0, bl, t, "Med", 8.0, INK)
    rule(page, 36.0, bl + 4.06, rule_x1)


def page_num(page, n, total):
    txt(page, PAGE_NUM_X, PAGE_NUM_BL, f"Page {n} of {total}", "FG", 9.5, GRAY)


def blank_note(page):
    txt(page, 223.2, 407.04, "This page intentionally left blank", "Reg", 12.0, _rgb("#636466"))


# ---------------------------------------------------------------- p1
def build_page1(doc, d, L, subs):
    p = doc.new_page(width=W, height=H)
    render_static(p, L["p1"], subs)
    for i, ln in enumerate(d["customer_lines"][:4]):
        txt(p, 45.0, 165.48 + 10.78 * i, ln, "Reg", 9.0, INK)
    biz = d["template"] == "business"
    txt(p, 36.0, 320.4, f"Your {d['product']} ", "Bol", 17.0, INK)
    txt(p, 36.0, 337.68, f"for {d['period']['label']}", "Reg", 11.0, INK)
    txt(p, 378.0, 337.68, f"Account number: {d['account_number']}", "Reg", 11.0, INK)
    owner_line = d.get("owner_line") or "     ".join(
        d.get("owner_names") or [d["customer_lines"][0]])
    txt(p, 36.0, 355.92, owner_line, "Bol", 10.0, INK)
    s = d.get("summary", {})
    labels = [
        (f"Beginning balance on {d['period']['begin']}", s.get("begin", "")),
        ("Deposits and other " + ("credits" if biz else "additions"), s.get("deposits", "")),
        ("Withdrawals and other " + ("debits" if biz else "subtractions"), s.get("withdrawals", "")),
        ("Checks", s.get("checks", "")),
        ("Service fees", s.get("fees", "")),
    ]
    for i, (lab, val) in enumerate(labels):
        bl = 398.88 + 18.48 * i
        txt(p, 36.0, bl, lab, "Reg", 9.5, INK)
        txt_r(p, SUM_R, bl, val, "Reg", 9.5, INK)
    end_bl = 492.12 if biz else 492.0
    txt(p, 36.0, end_bl, f"Ending balance on {d['period']['end']}", "Bol", 10.0, BLUE)
    txt_r(p, SUM_R, end_bl, s.get("ending", ""), "Bol", 10.0, BLUE)
    if biz:
        st = d.get("stats", {})
        n_auto = d.get("stats_auto", {})
        rows = [
            f"# of deposits/credits: {st.get('deposits_n', n_auto.get('deposits_n', ''))}",
            f"# of withdrawals/debits: {st.get('withdrawals_n', n_auto.get('withdrawals_n', ''))}",
            f"# of items-previous cycle¹: {st.get('items_prev', '')}",
            f"# of days in cycle: {st.get('days', '')}",
            f"Average ledger balance: {st.get('avg_ledger', '')}",
        ]
        for i, t in enumerate(rows):
            txt(p, 363.6, 402.96 + 18.0 * i, t, "Reg", 9.0, INK)
        txt(p, 363.6, 491.76, "¹Includes checks paid, deposited items and other debits",
            "Ita", 8.0, INK)
    return p


# ---------------------------------------------------------------- 流式排版
class Flow:
    def __init__(self, doc, d, L, subs):
        self.doc, self.d, self.L, self.subs = doc, d, L, subs
        self.pages = []
        self.style = None
        self.hdr_d = 17.11   # 段标题 y0 → 表头 y0
        self.y = None            # 下一段落标题 y0
        self.bottom = TX1_LIMIT
        self.started = set()     # 已开始过的段名（续页加 - continued）

    def _new_page(self, style):
        p = self.doc.new_page(width=W, height=H)
        self.pages.append(p)
        self.style = style
        tx1 = self.L["tx1"]
        if style == "tx1":
            render_static(p, tx1, self.subs)
            header_strip(p, self.d, 83.04, 576.0)
            self.y = TX1_HDR_BL[self.d["template"]] - 11.73
            self.hdr_d = 17.11
            self.bottom = TX1_LIMIT
        elif style == "sec":
            render_static(p, {"spans": [s for s in tx1["spans"] if s["bl"] < 600],
                              "rules": tx1["rules"], "rects": [], "imgs": [
                                  {"n": "logo", "x": 36.0, "y": 46.8, "w": 169.9, "h": 25.2}]},
                          self.subs)
            header_strip(p, self.d, 83.04, 576.0)
            self.y = SEC_HDR_BL - 11.73
            self.hdr_d = 17.4
            self.bottom = CONT_LIMIT
        else:
            for r in self.L["tx_cont"]["rules"]:
                rule(p, r["x0"], r["y"], r["x1"], r.get("w", 0.239))
            header_strip(p, self.d, 36.24, 555.1)
            self.y = CONT_HDR_BL - 11.73
            self.hdr_d = 16.9
            self.bottom = CONT_LIMIT
        return p

    def _break_page(self, keep_section=None):
        """结尾提示 + 新续页（可带段标题）"""
        if self.pages:
            txt_r(self.pages[-1], 576.8,
                  594.12 if self.style == "tx1" else 744.6,
                  "continued on the next page", "Ita", 6.5, INK)
        self._new_page("cont")

    def _table_header(self, p, y0, desc_hdr="Description"):
        txt(p, DATE_X, y0 + 6.26, "Date", "Reg", 8.0, INK)
        txt(p, DESC_X, y0 + 6.26, desc_hdr, "Reg", 8.0, INK)
        txt_r(p, AMT_R, y0 + 6.26, "Amount", "Reg", 8.0, INK)

    def section(self, title, rows, total_label=None, total_val=None,
                desc_hdr="Description", note=None, rule_w=RULE_W, gap=37.22,
                tbl_d=None, row_d=None):
        d = self.d
        if self.style is None:
            self._new_page("tx1")
        tbl_d = self.hdr_d if tbl_d is None else tbl_d
        row_d = FIRST_ROW_D[d["template"]] if row_d is None else row_d
        min_block = 11.73 + 6.26 + row_d + ROW_PITCH
        title_y0 = self.y
        if title_y0 + min_block > self.bottom:
            self._break_page()
            title_y0 = self.y
        suffix = " - continued" if title in self.started and self.style == "cont" else ""
        self.started.add(title)
        p = self.pages[-1]
        txt(p, 36.0, title_y0 + 11.73, title + suffix, "Med", 15.0, RED)
        hdr_y0 = title_y0 + tbl_d
        self._table_header(p, hdr_y0, desc_hdr)
        row_y = hdr_y0 + row_d
        last_y0 = None
        last_lines = 1

        for (dt, desc, amt) in rows:
            lines = wrap_desc(desc)
            need = ROW_PITCH + LINE_PITCH * (len(lines) - 1)
            if row_y + need > self.bottom:
                self._break_page()
                p = self.pages[-1]
                txt(p, 36.0, self.y + 11.73, title + " - continued", "Med", 15.0, RED)
                hdr_y0 = self.y + 16.9
                self._table_header(p, hdr_y0, desc_hdr)
                row_y = hdr_y0 + 17.6
            rule(p, 36.0, row_y - RULE_ABOVE, 576.0, rule_w)
            last_y0, last_lines = row_y, len(lines)
            txt(p, DATE_X, row_y + 7.04, dt, "Reg", 9.0, INK)
            for j, ln in enumerate(lines):
                txt(p, DESC_X, row_y + 7.04 + LINE_PITCH * j, ln, "Reg", 9.0, INK)
            txt_r(p, AMT_R, row_y + 7.04, amt, "Reg", 9.0, INK)
            row_y += need

        tail = row_y
        if total_label:
            total_y0 = (last_y0 + 18.4 + LINE_PITCH * (last_lines - 1)
                        if last_y0 is not None else row_y + 18.4)
            if total_y0 + 7.11 > self.bottom:
                self._break_page()
                p = self.pages[-1]
                total_y0 = self.y
            rule(p, 36.0, total_y0 - RULE_ABOVE_TOTAL, 576.0, rule_w)
            txt(p, 36.0, total_y0 + 7.82, total_label, "Bol", 10.0, BLUE)
            txt_r(p, AMT_R, total_y0 + 7.82, total_val, "Bol", 10.0, BLUE)
            tail = total_y0
        if note:
            if tail + 16.6 + 7.04 > self.bottom:
                self._break_page()
                p = self.pages[-1]
                tail = self.y
            txt(p, 36.0, tail + 16.6 + 7.04, note, "Ita", 9.0, SOFT)
            tail = tail + 16.6
        self.y = tail + gap


def fees_section(flow, d):
    """Service fees 段：标题 →（商业：导语+资格勾叉）→ 表格 → 合计 → 注"""
    if flow.style is None:
        flow._new_page("tx1")
    L = flow.L
    biz = d["template"] == "business"
    min_h = 11.73 + 20.7 + 15.7 + 18.4 + (150 if biz else 0)
    if flow.y + min_h > flow.bottom:
        flow._break_page()
    title_y0 = flow.y
    p = flow.pages[-1]
    txt(p, 36.0, title_y0 + 11.73, "Service fees", "Med", 15.0, RED)

    if biz:
        fi = L.get("fees_intro", {})
        base = title_y0 - 111.4  # 全部元素相对标题(111.4)的固定平移，不累加
        quals = d.get("qualifications") or [
            {"text": t, "met": m} for t, m in
            zip(fi.get("qual_defaults", []), fi.get("icons", []))]
        pre = [s for s in fi.get("spans", []) if s["bl"] < 200]
        post = [s for s in fi.get("spans", []) if s["bl"] >= 200]
        for s in pre:
            txt(p, s["x"], s["bl"] + base, sub_text(s["text"], flow.subs),
                s["font"], s["size"], s["color"])
        for i, q in enumerate(quals):
            icon = "checkmark" if q.get("met") else "circle"
            iw, ih = (10.6, 10.1) if q.get("met") else (12.2, 12.3)
            img(p, icon, 39.6, (154.4 + 18.48 * i) + base, iw, ih)
            txt(p, 62.76, (162.84 + 18.48 * i) + base, q["text"], "Reg", 9.0, SOFT)
        for s in post:
            txt(p, s["x"], s["bl"] + base, sub_text(s["text"], flow.subs),
                s["font"], s["size"], s["color"])
        tbl_y0 = title_y0 + 131.0
    else:
        tbl_y0 = title_y0 + 20.7

    flow._table_header(p, tbl_y0, "Transaction description")
    row_d = 15.6 if biz else 15.7
    row_y = tbl_y0 + row_d
    last_y0 = None
    last_lines = 1
    for r in d.get("fees", []):
        lines = wrap_desc(r["desc"])
        need = ROW_PITCH + LINE_PITCH * (len(lines) - 1)
        if row_y + need > flow.bottom:
            flow._break_page()
            p = flow.pages[-1]
            txt(p, 36.0, flow.y + 11.73, "Service fees - continued", "Med", 15.0, RED)
            tbl_y0 = flow.y + 16.9
            flow._table_header(p, tbl_y0, "Transaction description")
            row_y = tbl_y0 + 17.6
        rule(p, 36.0, row_y - RULE_ABOVE, 576.0, RULE_THIN)
        last_y0, last_lines = row_y, len(lines)
        txt(p, DATE_X, row_y + 7.04, r["date"], "Reg", 9.0, INK)
        for j, ln in enumerate(lines):
            txt(p, DESC_X, row_y + 7.04 + LINE_PITCH * j, ln, "Reg", 9.0, INK)
        txt_r(p, AMT_R, row_y + 7.04, fmt_plain(r["amount"]), "Reg", 9.0, INK)
        row_y += need
    total_y0 = (last_y0 + 18.4 + LINE_PITCH * (last_lines - 1)
                if last_y0 is not None else row_y + 18.4)
    rule(p, 36.0, total_y0 - RULE_ABOVE_TOTAL, 576.0, RULE_THIN)
    txt(p, 36.0, total_y0 + 7.82, "Total service fees", "Bol", 10.0, BLUE)
    txt_r(p, AMT_R, total_y0 + 7.82, d["totals"]["fees"], "Bol", 10.0, BLUE)
    note_y0 = total_y0 + 16.6
    txt(p, 36.0, note_y0 + 7.04,
        "Note your Ending Balance already reflects the subtraction of Service Fees.",
        "Ita", 9.0, SOFT)
    flow.y = note_y0 + (41.0 if biz else 30.0)


def daily_section(flow, d):
    if flow.style is None:
        flow._new_page("tx1")
    if flow.y + 90 > flow.bottom:
        flow._break_page()
        flow.y = CONT_HDR_BL - 11.73
    p = flow.pages[-1]
    y0 = flow.y
    txt(p, 36.0, y0 + 11.73, "Daily ledger balances", "Med", 15.0, RED)
    items = list(d.get("daily", []))

    def page_rows(p, hy, items):
        txt(p, 36.0, hy + 6.26, "Date", "Reg", 8.0, INK)
        txt(p, 167.9, hy + 6.26, "Balance ($)", "Reg", 8.0, INK)
        txt(p, 221.4, hy + 6.26, "Date", "Reg", 8.0, INK)
        txt(p, 355.4, hy + 6.26, "Balance($)", "Reg", 8.0, INK)
        txt(p, 406.8, hy + 6.26, "Date", "Reg", 8.0, INK)
        txt(p, 538.7, hy + 6.26, "Balance ($)", "Reg", 8.0, INK)
        ry = hy + 17.4
        i = 0
        while i < len(items):
            if ry + 15.6 > flow.bottom:
                break
            rule(p, 36.0, ry - RULE_ABOVE, 205.2)
            rule(p, 221.4, ry - RULE_ABOVE, 390.6)
            rule(p, 406.8, ry - RULE_ABOVE, 576.0)
            for c in range(3):
                if i >= len(items):
                    break
                mm, bal = items[i]
                txt(p, (36.0, 221.4, 406.8)[c], ry + 7.43, mm, "Reg", 9.5, INK)
                txt_r(p, (205.6, 391.0, 576.3)[c], ry + 7.43, bal, "Reg", 9.5, INK)
                i += 1
            ry += 15.6
        return i, ry

    consumed, ry = page_rows(p, y0 + 29.2, items)
    while consumed < len(items):
        flow._break_page()
        p = flow.pages[-1]
        txt(p, 36.0, flow.y + 11.73, "Daily ledger balances - continued", "Med", 15.0, RED)
        consumed, ry = page_rows(p, flow.y + 17.0 + 6.26, items[consumed:])
    flow.y = ry + 19.2




# ---------------------------------------------------------------- CMYK 后处理
# 原稿全部以 CMYK k 算子填色（ICC 转换后与我们的 RGB 目标略有 1-2 级差），
# 为 1:1 一致，输出前把已知 RGB 填色改写为原稿同款 CMYK。
_RGB2CMYK = [
    ((0x23, 0x1F, 0x20), "0 0 0 1 k", "0 0 0 1 K"),
    ((0x40, 0x3F, 0x41), "0 0 0 .90196 k", "0 0 0 .90196 K"),
    ((0x4C, 0x4D, 0x4E), "0 0 0 .85098 k", "0 0 0 .85098 K"),
    ((0x58, 0x59, 0x5B), "0 0 0 .8 k", "0 0 0 .8 K"),
    ((0xE4, 0x17, 0x3E), "0 1 .74902 .039216 k", "0 1 .74902 .039216 K"),
    ((0x00, 0x61, 0xAB), "1 .6 0 .05098 k", "1 .6 0 .05098 K"),
]
_CMYK_MAP_F, _CMYK_MAP_S = {}, {}
for (r, g, b), fk, sk in _RGB2CMYK:
    _CMYK_MAP_F[f"{r/255:.8g} {g/255:.8g} {b/255:.8g} rg"] = fk
    _CMYK_MAP_S[f"{r/255:.8g} {g/255:.8g} {b/255:.8g} RG"] = sk


def cmyk_postpass(doc):
    import re as _re
    for page in doc:
        xrefs = page.get_contents()
        if not xrefs:
            continue
        for xref in xrefs:
            data = doc.xref_stream(xref)
            txt_ = data.decode("latin-1")
            changed = False
            for rg_op, k_op in list(_CMYK_MAP_F.items()) + list(_CMYK_MAP_S.items()):
                if rg_op in txt_:
                    # PyMuPDF 写出的浮点格式探测
                    txt_ = txt_.replace(rg_op, k_op)
                    changed = True
            if not changed:
                # 兼容不同小数位数表示：用正则再扫一遍
                pat = _re.compile(r"([\d.]+) ([\d.]+) ([\d.]+) (rg|RG)")
                def sub(mt):
                    try:
                        r_, g_, b_ = int(float(mt.group(1))*255+0.5), int(float(mt.group(2))*255+0.5), int(float(mt.group(3))*255+0.5)
                    except ValueError:
                        return mt.group(0)
                    for (r, g, b), fk, sk in _RGB2CMYK:
                        if (r_, g_, b_) == (r, g, b):
                            return (fk if mt.group(4) == "rg" else sk).replace(" k", "").replace(" K", "") + " " + mt.group(4).upper() if False else (fk if mt.group(4) == "rg" else sk)
                    return mt.group(0)
                new = pat.sub(sub, txt_)
                if new != txt_:
                    txt_, changed = new, True
            if changed:
                doc.update_stream(xref, txt_.encode("latin-1"))

# ---------------------------------------------------------------- 组装
#: 底稿里那行印刷控制串（`PULL: B CYCLE: 3 SPEC: E ...`）。真印件里 CYCLE 是
#: 本期的第几期、SPEC 随批次轮换；照抄底稿恒定的 "3"/"E" 等于每份都一样。
_CTRL_RE = re.compile(r"(CYCLE:\s*)(\d+)")
_SPEC_RE = re.compile(r"(SPEC:\s*)([A-Z])")


def _cycle_ctrls(L, tag, d):
    """按账单月份派生控制串里的周期号与 SPEC 字母。"""
    text = json.dumps(L)
    if "CYCLE:" not in text:
        return L
    period = str(d.get("period") or d.get("statement_period") or "")
    year = int(period[:4]) if period[:4].isdigit() else 2026
    month = int(period[5:7]) if period[5:7].isdigit() else 1
    cycle_no = (month + 11) % 60 or 1
    spec = "ABCDEGILM"[((year * 7 + month) % 8)]   # 只用 FG 子集里真有轮廓的字母
    L = json.loads(_SPEC_RE.sub(lambda m: m.group(1) + spec,
                                _CTRL_RE.sub(lambda m: m.group(1) + str(cycle_no), text)))
    return _flatten_ctrl_line(L)


def _flatten_ctrl_line(node):
    """把页脚那行印刷控制串的逐字坐标去掉，改成整段单次绘制。

    底稿抓到的是每个字的绝对 x，小字号下本身带 0.2~0.6pt 抖动，沿用会印出
    `DELI VERY`、`I MAGE` 这种断词（实测首屏就这样）。真印件是均匀字距。
    """
    if isinstance(node, dict):
        if isinstance(node.get("text"), str) and "PULL:" in node["text"]:
            node.pop("chars_x", None)
        for v in node.values():
            _flatten_ctrl_line(v)
    elif isinstance(node, list):
        for v in node:
            _flatten_ctrl_line(v)
    return node


def build_pdf(d, out_path, watermark=True):
    d = json.loads(json.dumps(d))
    d["watermark"] = watermark
    apply_auto_calc(d)
    tag = d["template"]
    L = json.load(open(os.path.join(BASE, f"layout_{tag}.json")))
    L = _cycle_ctrls(L, tag, d)
    subs = build_subs(d)
    biz = tag == "business"

    doc = fitz.open()
    p1 = build_page1(doc, d, L, subs)
    if watermark:
        add_watermark(p1)

    p = doc.new_page(width=W, height=H)
    render_static(p, L["legal"], subs)
    header_strip(p, d, 32.64, 555.1)
    if watermark:
        add_watermark(p)

    flow = Flow(doc, d, L, subs)
    dep_key, wd_key = ("credits", "debits") if biz else ("additions", "subtractions")
    flow.section("Deposits and other " + dep_key,
                 [(r["date"], r["desc"], fmt_plain(r["amount"])) for r in d.get("deposits", [])],
                 "Total deposits and other " + dep_key, d["totals"]["deposits"],
                 tbl_d=16.99 if biz else 17.11, row_d=16.02)
    flow.section("Withdrawals and other " + wd_key,
                 [(r["date"], r["desc"], fmt_plain(r["amount"])) for r in d.get("withdrawals", [])],
                 "Total withdrawals and other " + wd_key, d["totals"]["withdrawals"],
                 tbl_d=16.99 if biz else 16.9, row_d=17.6, gap=37.25 if biz else 33.8)
    if biz:
        # 商业版月费段起新页（标准头带 logo，对齐原稿分页）
        flow._new_page("sec")
    fees_section(flow, d)
    if biz and d.get("show_daily", True):
        daily_section(flow, d)

    if not biz:
        p = doc.new_page(width=W, height=H)
        render_static(p, L["braille"], subs)
        header_strip(p, d, 36.24, 555.1)
        if watermark:
            add_watermark(p)
        min_pages = int(d.get("min_pages", 8))
        while len(doc) < min_pages:
            p = doc.new_page(width=W, height=H)
            header_strip(p, d, 32.64, 555.1)
            blank_note(p)
            if watermark:
                add_watermark(p)
    else:
        # 商业版：blank 补至 min_pages-1 后，Important Messages 页为最后一页
        min_pages = int(d.get("min_pages", 8))
        while len(doc) < min_pages - 1:
            p = doc.new_page(width=W, height=H)
            header_strip(p, d, 32.64, 555.1)
            blank_note(p)
            if watermark:
                add_watermark(p)
        p = doc.new_page(width=W, height=H)
        render_static(p, L["messages"], subs)
        if watermark:
            add_watermark(p)

    total = len(doc)
    for i, p in enumerate(doc):
        page_num(p, i + 1, total)

    md = d.get("metadata", {})
    md_all = {}
    for k in ("title", "author", "subject", "keywords", "creator", "producer"):
        if md.get(k):
            md_all[k] = md[k]
    if md.get("creation"):
        md_all["creationDate"] = md["creation"]
        md_all["modDate"] = md["creation"]
    if md_all:
        doc.set_metadata(md_all)
    else:
        doc.set_metadata({})

    cmyk_postpass(doc)
    # 复刻原稿嵌入方式：Type1 + FontFile3(Type1C) + 原字体名/编码（替换默认 CID 嵌入）
    from billgen.themes.boa import orig_embed
    orig_embed.apply_orig_embed(doc)
    doc.save(out_path, garbage=3, deflate=True)
    doc.close()
    return out_path


if __name__ == "__main__":
    import sys
    data_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "editor", "data_personal.json")
    out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/boa_out.pdf"
    dd = json.load(open(data_path))
    build_pdf(dd, out, watermark=dd.get("watermark", True))
    print("saved", out)
