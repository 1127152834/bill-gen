#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SoFi 银行账单模板 — 1:1 排版复刻
====================================
仅供设计/演示用途。所有文本可通过 DATA 字典编辑。

字体：TTNorms Regular/Bold + Montserrat Thin（从原稿提取）
排版：kern 开启（从原稿逐字符提取的 kern 表）
布局：联系信息位置动态（随交易行数自适应），支持分页
"""
import fitz, os, math, json

from copy import deepcopy
from dataclasses import dataclass, field


@dataclass
class RenderContext:
    """Per-build snapshot; never installs request state on the module."""

    data: dict
    watermark: bool
    meta: dict
    font_cjk: str | None
    fallback_used: set = field(default_factory=set)
    fitz_fonts: dict = field(default_factory=dict)
    shapers: dict = field(default_factory=dict)


def _render_context(data=None, watermark=None):
    d = deepcopy(DATA if data is None else data)
    inter = _CMAP[_REG] & _CMAP[_BOLD]
    need = any(ord(ch) not in inter and ord(ch) in _CJK_CMAP
               for text in collect_texts(d) for ch in text)
    font_cjk = FONT_CJK_BASE if need else None
    return RenderContext(d, WATERMARK if watermark is None else watermark,
                         deepcopy(META), font_cjk)


BASE = os.path.dirname(os.path.abspath(__file__))
A = lambda n: os.path.join(BASE, "assets", n)
FONT_REG  = A("sofi_regular.ttf")
FONT_BOLD = A("sofi_bold.ttf")
FONT_THIN = A("sofi_thin.ttf")
FONT_THIN_D = A("sofi_thin_d.ttf")   # DAAAAA+ 子集名（Contact Information 标题 / www.sofi.com）

# ---------------------------------------------------------------- palette
BLACK     = (0, 0, 0)
GRAY_909  = (0x90/255, 0x91/255, 0x96/255)
GRAY_626  = (0x62/255, 0x62/255, 0x62/255)
GRAY_757  = (0x75/255, 0x75/255, 0x75/255)
GRAY_999  = (0x99/255, 0x99/255, 0x99/255)
GREEN     = (0x00/255, 0xa3/255, 0x16/255)
BG_GRAY   = (0xee/255, 0xee/255, 0xee/255)
BG_BEIGE  = (0xf4/255, 0xf2/255, 0xf1/255)
BG_WHITE  = (1, 1, 1)
DIVIDER   = (0x80/255, 0x80/255, 0x80/255)
LOGO_BLUE = (0x00/255, 0xa9/255, 0xce/255)

SZ6, SZ75, SZ8, SZ11, SZ12, SZ18 = 6.0, 7.5, 8.0, 11.0, 12.0, 18.0

# ---------------------------------------------------------------- 几何
CONTENT_L, CONTENT_R = 18.0, 593.95
COL_X  = (23.9, 167.7, 311.7, 455.5)
CARD_B = (18.0, 161.75, 305.75, 450.05, 593.95)
TX_B   = (18.0, 103.05, 215.95, 413.75, 503.85, 593.95)
TX_X   = (20.9, 105.9, 218.9)
TX_R   = (501.3, 591.4)
RH     = 24.4                   # 行距（行顶与文本基线通用，原稿精确测量）
RH_BG  = 24.35                  # 行背景矩形高度（与下一行间留 0.05 缝隙）
TX_HDR_BL  = 415.1              # 表头基线
TX_FIRST_BL = 430.0             # 首行主文本基线
TX_SUB_DY  = 9.4                # Transaction ID 行偏移
TX_HDR_TOP = 419.5              # 表头 bg 顶
# 联系区固定位置（原稿 4 笔交易时 y=581.8，交易更多时自动下移）
CONTACT_MIN_Y = 581.8           # 联系区标题最小 y
FOOTNOTE_Y    = 710.7           # 脚注上标 y
FOOTER_Y      = 744.8           # 页脚 y
LOGO_RECT = (470.85, 49.85, 574.40, 77.45)

# ---------------------------------------------------------------- data
DEMO_DATA = {'holder_name': 'Maya Okafor',
 'member_since': 'May 2024',
 'address_lines': ['2104 S 1st St', 'Austin TX 78704', '', ''],
 'account_number': '410737211347',
 'statement_period': '10/01/2026 - 10/31/2026',
 'account_type': 'Checking',
 'account_last4': '0001',
 'current_balance': 1214.5,
 'current_as_of': '10/31/2026',
 'monthly_interest': 0.0,
 'apy': '0.00%',
 'beginning_balance': 1000.0,
 'beginning_as_of': '10/01/2026',
 'ytd_interest': 0.0,
 'balance_note': 'Balance details for this statement period',
 'transactions': [{'date': '10/07/2026',
                   'desc': 'H-E-B #0611 AUSTIN TX',
                   'amount': -35.5,
                   'id': '9401882741',
                   'txid': '9401882741',
                   'memo': '',
                   'type': 'Withdrawal'},
                  {'date': '10/02/2026',
                   'desc': 'ACME CORP PAYROLL PPD ID:4289126633',
                   'amount': 250.0,
                   'id': '9401882733',
                   'txid': '9401882733',
                   'memo': '',
                   'type': 'Deposit'}],
 'contact_website': 'sofi.com',
 'contact_address': ['2104 S 1st St', 'Austin TX 78704', ''],
 'contact_phone': '1-855-456-7634',
 'legal_footer': 'SoFi Bank, N.A. Member FDIC',
 'footnote1': '©2026 SoFi Bank, N.A. All rights reserved.'}

# 原稿基线：与 asset 里的样张一字不差，供 parity 回归比对（不要改）。
PARITY_DATA = {'holder_name': 'ALEX EXAMPLE',
 'member_since': 'October 2026',
 'address_lines': ['100 EXAMPLE STREET', 'DEMO CITY CA 90000', '', ''],
 'account_number': '000000000001',
 'statement_period': '10/01/2026 - 10/31/2026',
 'account_type': 'Checking',
 'account_last4': '0001',
 'current_balance': 1214.5,
 'current_as_of': '10/31/2026',
 'monthly_interest': 0.0,
 'apy': '0.00%',
 'beginning_balance': 1000.0,
 'beginning_as_of': '10/01/2026',
 'ytd_interest': 0.0,
 'balance_note': 'Balance details for this statement period',
 'transactions': [{'date': '10/07/2026',
                   'desc': 'Sample grocery purchase',
                   'amount': -35.5,
                   'id': 'DEMO0002',
                   'txid': 'DEMO0002',
                   'memo': '',
                   'type': 'Withdrawal'},
                  {'date': '10/02/2026',
                   'desc': 'Sample payroll credit',
                   'amount': 250.0,
                   'id': 'DEMO0001',
                   'txid': 'DEMO0001',
                   'memo': '',
                   'type': 'Deposit'}],
 'contact_website': 'sofi.com',
 'contact_address': ['100 EXAMPLE STREET', 'DEMO CITY CA 90000', ''],
 'contact_phone': '1-855-456-7634',
 'legal_footer': 'SoFi Bank, N.A. Member FDIC',
 'footnote1': '©2026 SoFi Bank, N.A. All rights reserved.'}

#: 引擎默认数据 = 原稿基线（`build()` 不带 data 时用它，parity 回归依赖这一点）。
DATA = PARITY_DATA



WATERMARK = True
TX_PER_PAGE = 6               # 页1 最大交易行数（联系区固定 581.8，第6行子行 561.4 不重叠）
TX_PER_CONT = 27              # 续页行数（参考原版续页实测：27 行，末带底 696.95，页脚 744.8）

# 续页几何（参考原版续页图实测）
CONT_LABEL_BL = 32.4            # 账户标签基线（页面顶部）
CONT_BAND_TOP = 38.2            # 首行背景顶（无表头，紧贴标签下方）
CONT_ROW1_BL  = 48.7            # 首行主文本基线（= 带顶 + 10.5，与页1一致）

# ================================================================ CJK
from fontTools.ttLib import TTFont as _TT
_FP = {"reg": FONT_REG, "bold": FONT_BOLD, "med": FONT_THIN, "cjk": None}
_REG, _BOLD = "reg", "bold"
_CJK = "cjk"
def _resolve_cjk():
    from runtime_paths import cjk_font_path
    return cjk_font_path()
FONT_CJK_BASE = _resolve_cjk()
FONT_CJK = FONT_CJK_BASE
_CJK_CMAP = set()
if FONT_CJK_BASE:
    try: _CJK_CMAP = set(_TT(FONT_CJK_BASE, fontNumber=0).getBestCmap().keys())
    except Exception: pass
_CMAP = {k: set(_TT(v).getBestCmap().keys()) for k, v in _FP.items() if v}
_LATIN_CM = _CMAP[_REG] & _CMAP[_BOLD]
_fallback_used = set()

def collect_texts(data=None):
    d = data if data is not None else DATA
    out = [d.get(k, "") or "" for k in
           ("holder_name", "member_since", "tagline", "headline_month",
            "period_range", "account_number", "apy", "statement_period",
            "account_type", "account_last4", "closing_date",
            "footer_site", "footer_phone", "footer_address",
            "note_text", "error_heading", "legal_footer",
            "footnote1", "current_as_of", "beginning_as_of", "balance_note")]
    out += [l for l in (d.get("address_lines") or []) if str(l).strip()]
    for t in d.get("transactions") or []:
        out += [str(t.get("date", "")), str(t.get("desc", "")),
                str(t.get("type", "")), str(t.get("txid", ""))]
    return [str(s) for s in out]

def autodetect_cjk(data=None):
    global FONT_CJK
    if not FONT_CJK_BASE: return
    inter = _CMAP[_REG] & _CMAP[_BOLD]
    need = any(ord(ch) not in inter and ord(ch) in _CJK_CMAP
               for s in collect_texts(data) for ch in s)
    FONT_CJK = FONT_CJK_BASE if need else None

# ================================================================ kern
_KERN = json.load(open(A("kern_sofi.json"))) if os.path.exists(A("kern_sofi.json")) else {}
_KCACHE = {}
def _kern(font, c1, c2, size):
    tab = _KERN.get(font, {})
    if not tab: return 0.0
    sz = round(size, 2)
    ck = f"{font}:{sz}"
    if ck not in _KCACHE:
        _KCACHE[ck] = min(tab.keys(), key=lambda k: abs(float(k) - sz)) if tab else None
    bucket = tab.get(_KCACHE[ck]) or {}
    v = bucket.get(f"{c1}|{c2}", 0.0)
    # 表是从底稿量出来的字距，个别对（`i|.` = +0.349em、`f|i` = -0.311em）其实量到了
    # 底稿排版里的空隙，照抄会在 `sofi.com` 中间留出一个空格。超过 0.12em 一律当噪声丢掉。
    if abs(v) > 0.12:
        return 0.0
    return v * size

# ================================================================ shaping
_fonts = {}
def _font(which):
    if which not in _fonts:
        p = {"reg": FONT_REG, "bold": FONT_BOLD, "thin": FONT_THIN}.get(which, FONT_REG)
        _fonts[which] = fitz.Font(fontfile=p)
    return _fonts[which]

def width(text, size, font="reg"):
    n = len(text)
    if n == 0: return 0.0
    w = _font(font).text_length(text[-1], size)
    for i in range(n-1):
        w += _font(font).text_length(text[i], size) + _kern(font, text[i], text[i+1], size)
    return w

# ================================================================ drawing
MAX_RUN = 8
KERN_BREAK = 0.15            # 低于此值的 kern 噪声不断行（原稿真实 kern >0.15pt）

def put(page, x, baseline, text, size, font="reg", color=BLACK, ins_name=None):
    n = len(text)
    if n == 0: return x
    ff = _font(font)
    pens = [0.0] * n
    for i in range(1, n):
        pens[i] = pens[i-1] + ff.text_length(text[i-1], size) \
                  + _kern(font, text[i-1], text[i], size)
    i = 0
    while i < n:
        j = i + 1
        while j < n and j - i < MAX_RUN:
            gap = (pens[j] - pens[j-1]) - ff.text_length(text[j-1], size)
            if abs(gap) > KERN_BREAK:
                break
            j += 1
        page.insert_text((x + pens[i], baseline), text[i:j], fontsize=size,
                         fontname=ins_name or font, color=color)
        i = j
    return x + pens[-1] + ff.text_length(text[-1], size)

def put_fast(page, x, baseline, text, size, font="reg", color=BLACK):
    page.insert_text((x, baseline), text, fontsize=size, fontname=font, color=color)

def put_right(page, right, baseline, text, size, font="reg", color=BLACK):
    put(page, right - width(text, size, font), baseline, text, size, font, color)

def rect(page, x0, y0, x1, y1, color):
    page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=None, fill=color)

def hline(page, y, w=0.1):
    page.draw_line(fitz.Point(CONTENT_L, y), fitz.Point(CONTENT_R, y),
                   color=DIVIDER, width=w)

def money(v): return f"${abs(v):,.2f}"
def signed_money(v): return ("-" if v < 0 else "") + f"${abs(v):,.2f}"

def _wrap(text, size, first_w, rest_w):
    """按测量宽度换行：首行与续行可用不同宽度（悬挂缩进）。"""
    words = text.split()
    lines, cur, cur_w, cap = [], [], 0.0, first_w
    for w in words:
        ww = width(w + " ", size) if cur else width(w, size)
        if cur and cur_w + width(" " + w, size) > cap:
            lines.append(" ".join(cur))
            cur, cur_w, cap = [w], width(w, size), rest_w
        else:
            if cur:
                cur_w += width(" " + w, size)
            else:
                cur_w = ww
            cur.append(w)
    if cur: lines.append(" ".join(cur))
    return lines

def prepare(page, *, ctx=None):
    ctx = ctx if ctx is not None else _render_context()
    for name, path in (("reg", FONT_REG), ("bold", FONT_BOLD), ("thin", FONT_THIN),
                       ("thinD", FONT_THIN_D)):
        page.insert_font(fontname=name, fontfile=path)
    if ctx.font_cjk:
        page.insert_font(fontname="cjk", fontfile=ctx.font_cjk)

def _wm_to_back(page):
    """把刚写入的水印内容流挪到 /Contents 最前面（画在正文之下）。

    PyMuPDF 的 TextWriter.write_text 总是追加一条新的内容流 → 水印会盖在金额和
    表格上（实测首屏大数字被压住，一眼假）。这里重排 /Contents 数组顺序即可。
    """
    xrefs = page.get_contents()
    if len(xrefs) < 2:
        return
    order = [xrefs[-1]] + xrefs[:-1]
    page.parent.xref_set_key(page.xref, "Contents",
                             "[" + " ".join(f"{x} 0 R" for x in order) + "]")


def watermark(page, *, ctx=None):
    ctx = ctx if ctx is not None else _render_context()
    if not ctx.watermark: return
    tw = fitz.TextWriter(page.rect)
    f = fitz.Font(fontfile=FONT_REG)
    txt = "S A M P L E"; sz = 92
    w = f.text_length(txt, sz)
    pt = fitz.Point((612 - w * 0.707) / 2, 792 * 0.62)
    tw.append(pt, txt, font=f, fontsize=sz)
    tw.write_text(page, color=(0.93, 0.93, 0.95), morph=(pt, fitz.Matrix(45)))
    _wm_to_back(page)

# ================================================================ logo
_logo = None
def _logo_doc():
    global _logo
    if _logo is None: _logo = fitz.open(A("logo.pdf"))
    return _logo

# ---------------------------------------------------------------- 联系图标
# 原稿是 32×32 的 PNG，渲染到 29pt 大约 79dpi —— 300dpi 扫描下一眼是糊的。
# 改成矢量线条（同色 #32C5F3），任意缩放下都是实心锐边。
_ICON_BLUE = (50 / 255, 197 / 255, 243 / 255)


def _contact_icon(page, kind, rect):
    """画一个 SoFi 风格线性图标：耳麦 / 定位针 / 手机。"""
    x0, y0, x1, y1 = rect.x0, rect.y0, rect.x1, rect.y1
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    w, lw = x1 - x0, 1.05
    sh = page.new_shape()

    if kind == "web":                       # 耳麦：头梁 + 两侧耳罩 + 麦克风臂
        r = w / 2 - 2.2
        top = cy - r * 0.55
        sh.draw_bezier(fitz.Point(cx - r, cy - r * 0.15), fitz.Point(cx - r, top - r * 0.75),
                       fitz.Point(cx + r, top - r * 0.75), fitz.Point(cx + r, cy - r * 0.15))
        cup_h, cup_w = w * 0.34, w * 0.20
        sh.draw_rect(fitz.Rect(cx - r - cup_w / 2, cy - r * 0.20, cx - r + cup_w / 2,
                               cy - r * 0.20 + cup_h), radius=0.5)
        sh.draw_rect(fitz.Rect(cx + r - cup_w / 2, cy - r * 0.20, cx + r + cup_w / 2,
                               cy - r * 0.20 + cup_h), radius=0.5)
        sh.draw_bezier(fitz.Point(cx + r - cup_w * 0.10, cy + cup_h * 0.62),
                       fitz.Point(cx + r - cup_w * 0.20, cy + r * 0.92),
                       fitz.Point(cx + 1.0, cy + r * 1.02),
                       fitz.Point(cx - cup_w * 0.30, cy + r * 0.99))
        sh.finish(color=_ICON_BLUE, width=lw, lineCap=1, lineJoin=1, closePath=False)
        sh.commit()
        dot = page.new_shape()
        dot.draw_circle(fitz.Point(cx - cup_w * 0.55, cy + r * 0.99), cup_w * 0.40)
        dot.finish(color=_ICON_BLUE, fill=_ICON_BLUE, closePath=True)
        dot.commit()

    elif kind == "mail":                    # 定位针：外轮廓 + 中心点
        r = w * 0.235                      # 圆头 + 下方两条切线收成针尖
        head_c = fitz.Point(cx, cy - w * 0.10)
        tip = fitz.Point(cx, cy + w * 0.52)
        k = r * 0.7071
        sh.draw_circle(head_c, r)
        sh.draw_line(fitz.Point(cx + k, cy - w * 0.10 + k), tip)
        sh.draw_line(fitz.Point(cx - k, cy - w * 0.10 + k), tip)
        sh.finish(color=_ICON_BLUE, width=lw, lineCap=1, lineJoin=1, closePath=False)
        sh.commit()
        dot = page.new_shape()
        dot.draw_circle(head_c, w * 0.075)
        dot.finish(color=_ICON_BLUE, fill=_ICON_BLUE, closePath=True)
        dot.commit()

    else:                                   # 手机：圆角外框 + 听筒横线 + Home 点
        bw, bh = w * 0.42, w * 0.78
        sh.draw_rect(fitz.Rect(cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2),
                     radius=0.16)
        sh.draw_line(fitz.Point(cx - bw * 0.22, cy - bh / 2 + w * 0.10),
                     fitz.Point(cx + bw * 0.22, cy - bh / 2 + w * 0.10))
        sh.finish(color=_ICON_BLUE, width=lw, lineCap=1, lineJoin=1, closePath=False)
        sh.commit()
        dot = page.new_shape()
        dot.draw_circle(fitz.Point(cx, cy + bh / 2 - w * 0.10), w * 0.045)
        dot.finish(color=_ICON_BLUE, fill=_ICON_BLUE, closePath=True)
        dot.commit()


# ================================================================ 交易表（页1/续页共用）
def _tx_account_label(page, bl, *, ctx=None):
    """账户标签（页1基线 399.5 / 续页基线 32.4，其余几何一致）"""
    ctx = ctx if ctx is not None else _render_context()
    label = f"{ctx.data['account_type']} Account - "
    put(page, 20.9, bl, label, SZ12, "bold")
    lw = width(label, SZ12, "bold")
    put(page, 20.9 + lw + 0.08, bl, ctx.data["account_last4"], SZ12, "bold")

def _tx_header_row(page):
    """表头（仅页1；续页无表头）"""
    for i in range(5):
        rect(page, TX_B[i], TX_HDR_TOP, TX_B[i+1], TX_HDR_TOP + RH_BG, BG_BEIGE)
    put(page, TX_X[0], TX_HDR_BL, "DATE", SZ75, "bold")
    put(page, TX_X[1], TX_HDR_BL, "TYPE", SZ75, "bold")
    put(page, TX_X[2], TX_HDR_BL, "DESCRIPTION", SZ75, "bold")
    put_right(page, TX_R[0], TX_HDR_BL, "AMOUNT", SZ75, "bold")
    put_right(page, TX_R[1], TX_HDR_BL, "BALANCE", SZ75, "bold")

def _tx_rows(page, txs, balances, band_top, first_bl, first_beige, omit_last_bg):
    """交易行。页1: 带443.9/白色起始/末行无背景；续页: 带38.2/米色起始/全部背景"""
    for i, t in enumerate(txs):
        top = band_top + i * RH
        if not (omit_last_bg and i == len(txs) - 1):
            even_beach = first_beige if i % 2 == 0 else not first_beige
            bg = BG_BEIGE if even_beach else BG_WHITE
            for j in range(5):
                rect(page, TX_B[j], top, TX_B[j+1], top + RH_BG, bg)
        bl  = first_bl + i * RH
        bl2 = bl + TX_SUB_DY
        put(page, TX_X[0], bl, t["date"], SZ8)
        put(page, TX_X[1], bl, t["type"], SZ8)
        put(page, TX_X[2], bl, t["desc"], SZ8)
        put(page, TX_X[2], bl2, f"Transaction ID: {t['txid']}", SZ8)
        put_right(page, TX_R[0], bl, signed_money(t["amount"]), SZ8,
                  color=GREEN if t["amount"] > 0 else BLACK)
        put_right(page, TX_R[1], bl, money(balances[i]), SZ8)

def _tx_table(page, txs, balances, *, ctx=None):
    """页1完整交易表：标签(399.5) + 表头 + 行"""
    ctx = ctx if ctx is not None else _render_context()
    _tx_account_label(page, 399.5, ctx=ctx)
    _tx_header_row(page)
    _tx_rows(page, txs, balances, TX_HDR_TOP + RH, TX_FIRST_BL, False, True)

# ================================================================ 页面
def page_statement(doc, page_num, total_pages, txs=None, balances=None, *, ctx=None):
    ctx = ctx if ctx is not None else _render_context()
    page = doc.new_page(width=612, height=792)
    prepare(page, ctx=ctx)
    txs = ctx.data["transactions"] if txs is None else txs
    acct_label = f"{ctx.data['account_type']} Account - {ctx.data['account_last4']}"

    # ---- 顶部信息区 ----
    rect(page, CONTENT_L, 25.1, CONTENT_R, 320.85, BG_GRAY)
    for i in range(4):
        rect(page, CARD_B[i], 25.1, CARD_B[i+1], 152.25, BG_WHITE)

    put(page, COL_X[0], 38.6, "Primary Account Holder", SZ8)
    put(page, COL_X[0], 52.7, ctx.data["holder_name"], SZ8, color=GRAY_999)
    put(page, COL_X[0], 66.9, ctx.data["member_since"], SZ8, color=GRAY_909)

    put(page, COL_X[1], 38.6, "Address", SZ8)
    addr_txt = [l for l in ctx.data["address_lines"] if l.strip()][:3]
    # 原稿三行基线是 52.7/95.2/123.5（底稿那三行不是同一段地址），换成整段地址后
    # 行距会被撑成 42pt 的空档；这里按与左侧持卡人列一致的 14.2pt 行距排。
    for i, (line_txt, y) in enumerate(zip(addr_txt, (52.7, 66.9, 81.1))):
        put(page, COL_X[1], y, line_txt, SZ8, color=GRAY_909)

    put(page, COL_X[2], 38.6, "Account Number", SZ8)
    put(page, COL_X[2], 52.7, ctx.data["account_number"], SZ8, color=GRAY_909)
    put(page, COL_X[2], 66.9, "Monthly Statement Period", SZ8)
    put(page, COL_X[2], 81.0, ctx.data["statement_period"], SZ8, color=GRAY_909)

    # Logo（矢量，从原稿提取的 #00a9ce 路径）
    page.show_pdf_page(fitz.Rect(*LOGO_RECT), _logo_doc(), 0)

    # ---- 指标区 ----
    rect(page, CONTENT_L, 152.3, CONTENT_R, 178.25, BG_BEIGE)
    label1_w = width(f"{ctx.data['account_type']} Account - ", SZ12, "bold")
    put(page, 23.9, 169.6, f"{ctx.data['account_type']} Account - ", SZ12, "bold")
    put(page, 23.9 + label1_w + 0.08, 169.6, ctx.data["account_last4"], SZ12, "bold")

    rect(page, CONTENT_L, 178.3, CONTENT_R, 229.85, BG_BEIGE)
    put(page, COL_X[0], 191.7, "Current Balance", SZ8)
    put(page, COL_X[0], 210.8, money(ctx.data["current_balance"]), SZ18)
    put(page, COL_X[0], 222.4, ctx.data["current_as_of"], SZ8, color=GRAY_757)
    miw = width("Monthly Interest Paid", SZ8)
    put(page, COL_X[1], 191.7, "Monthly Interest Paid", SZ8)
    put(page, COL_X[1] + miw + 0.063, 188.4, "1", 4.6)
    put(page, COL_X[1], 210.8, money(ctx.data["monthly_interest"]), SZ18)
    apw = width("Annual Percentage Yield Earned", SZ8)
    put(page, COL_X[2], 191.7, "Annual Percentage Yield Earned", SZ8)
    put(page, COL_X[2] + apw + 0.195, 188.4, "1", 4.6)
    put(page, COL_X[2], 210.8, ctx.data["apy"], SZ18)

    rect(page, CONTENT_L, 229.9, CONTENT_R, 298.95, BG_BEIGE)
    put(page, COL_X[0], 243.4, "Beginning Balance", SZ8)
    put(page, COL_X[0], 262.4, money(ctx.data["beginning_balance"]), SZ18)
    put(page, COL_X[0], 274.1, ctx.data["beginning_as_of"], SZ8, color=GRAY_626)
    put(page, COL_X[1], 243.4, "Year-to-date Interest Paid", SZ8)
    put(page, COL_X[1], 262.4, money(ctx.data["ytd_interest"]), SZ18)

    rect(page, CONTENT_L, 299.0, CONTENT_R, 320.85, BG_BEIGE)
    put(page, 23.9, 312.5, ctx.data["balance_note"], SZ8, color=GRAY_626)

    # ---- 交易明细 ----
    put(page, 18.1, 351.6, "Transaction Details", SZ11)
    put(page, 18.1, 362.1,
        "Balances below are the total funds resulting from the transaction(s) posted on that day.",
        SZ8, color=GRAY_909)
    hline(page, 371.05)

    # 余额（从最新到最旧逐行回推；分页时由 build 预计算传入）
    if balances is None:
        balances = []
        bal = ctx.data["current_balance"]
        for t in txs:
            balances.append(bal)
            bal -= t["amount"]

    _tx_table(page, txs, balances, ctx=ctx)

    # ---- 联系信息（固定位置，原稿精确坐标；页1最多6行流水不与此重叠） ----
    contact_y = CONTACT_MIN_Y

    # 间距链（从原稿精确测量）
    sep_y     = contact_y + 9.75     # 分隔线
    icon_y    = contact_y + 25.20    # 图标顶
    label_y   = contact_y + 68.00    # Website/Mailing Address/Contact Us
    value_y   = contact_y + 82.60    # 第一行值
    addr2_y   = contact_y + 93.40    # 地址第二行
    addr3_y   = contact_y + 104.30   # 地址第三行
    fnum_y    = contact_y + 128.90   # 脚注上标 "1"
    ftext_y   = contact_y + 131.30   # 脚注第一行

    hline(page, sep_y)
    put(page, 18.1, contact_y, "Contact Information", SZ11, "thin", ins_name="thinD")

    _contact_icon(page, "web", fitz.Rect(15.75, icon_y, 45.05, icon_y + 29.3))
    _contact_icon(page, "mail", fitz.Rect(212.2, icon_y + 0.45, 239.95, icon_y + 27.75))
    _contact_icon(page, "phone", fitz.Rect(405.45, icon_y + 0.30, 434.5, icon_y + 29.35))

    put(page, 20.9, label_y, "Website", SZ8, "thin")
    put(page, 20.9, value_y, ctx.data["contact_website"], SZ8, "thin", color=GRAY_909,
        ins_name="thinD")
    put(page, 214.4, label_y, "Mailing Address", SZ8, "thin")
    put(page, 214.4, value_y, ctx.data["contact_address"][0], SZ8, color=GRAY_909)
    put(page, 214.4, addr2_y, ctx.data["contact_address"][1], SZ8, color=GRAY_909)
    put(page, 214.4, addr3_y, ctx.data["contact_address"][2], SZ8, color=GRAY_909)
    put(page, 403.4, label_y, "Contact Us", SZ8, "thin")
    put(page, 403.4, value_y, ctx.data["contact_phone"], SZ8, color=GRAY_909)

    # ---- 脚注/页脚（跟随联系区，脚注自动换行匹配原稿宽度） ----
    put(page, 20.9, fnum_y, "1", 3.5)
    fn_lines = _wrap(ctx.data["footnote1"], SZ6, 562.7, 564.6)
    for fi, fl in enumerate(fn_lines):
        put_fast(page, 22.8 if fi == 0 else 20.9, ftext_y + fi * 7.1, fl, SZ6)
    footer_y = ftext_y + (len(fn_lines) - 1) * 7.1 + 24.6
    put_fast(page, 20.9, footer_y, ctx.data["legal_footer"], SZ6)
    put_fast(page, 563.5, footer_y, f"Page {page_num} of {total_pages}", SZ6)

    watermark(page, ctx=ctx)

# ================================================================ 续页（仅账户标签+流水+页脚）
def page_continuation(doc, page_num, total_pages, txs, balances, *, ctx=None):
    """续页（参考原版）：账户标签置顶 → 流水（无表头，从 y=38.2 起）→ 页脚。
    流水下方无联系区/脚注。"""
    ctx = ctx if ctx is not None else _render_context()
    page = doc.new_page(width=612, height=792)
    prepare(page, ctx=ctx)
    _tx_account_label(page, CONT_LABEL_BL, ctx=ctx)
    _tx_rows(page, txs, balances, CONT_BAND_TOP, CONT_ROW1_BL, True, False)
    put_fast(page, 20.9, FOOTER_Y, ctx.data["legal_footer"], SZ6)
    put_fast(page, 563.5, FOOTER_Y, f"Page {page_num} of {total_pages}", SZ6)
    watermark(page, ctx=ctx)

# ================================================================ 法律页
LEGAL_LINE_GAP = 8.15               # 正文行距网格（原稿精确：8.15pt）
LEGAL_SIGN_X   = 223.2              # 落款 x（原稿居中位置）
LEGAL_NAVY     = (0, 0, 0x80/255)   # 链接藏青 #000080

def _put_legal_line(page, y, text, bold):
    """渲染一行条款文本；独立的 www.sofi.com 渲染为藏青 + 下划线 + 链接注释（原稿样式）。"""
    f = "bold" if bold else "reg"
    i = text.find(" www.sofi.com ")
    if i >= 0:
        i += 1
        pre, post = text[:i], text[i + len("www.sofi.com"):]
        put_fast(page, 18.1, y, pre, SZ6, f)
        put_fast(page, 366.1, y, "www.sofi.com", SZ6, "reg", color=LEGAL_NAVY)
        page.draw_line(fitz.Point(366.1, y + 0.60), fitz.Point(402.5, y + 0.60),
                       color=LEGAL_NAVY, width=0.3)
        page.insert_link({"kind": fitz.LINK_URI,
                          "from": fitz.Rect(365.99, y - 6.8, 402.46, y + 1.35),
                          "uri": "http://www.sofi.com/"})
        if post.strip():
            put_fast(page, 402.5, y, post, SZ6, f)
    else:
        put_fast(page, 18.1, y, text, SZ6, f)

# ---------------------------------------------------------------- 行名替换
# 法务段落按原稿逐行断好了字，行数与 y 值都是量出来的：虚构行名比 “SoFi Bank”
# 长，直接逐行改字会撞行，所以品牌态下把整段拼回去重新断行，再由长到短试字号，
# 直到塞回原来的行数。默认数据没有 bank 时走老路径，逐字不动。
_LEGAL_W = CONTENT_R - 18.1 - 0.6      # 正文可用宽度（右边留 0.6pt 余量）


def _brand_texts(ctx):
    """返回 (name, legal, domain)；没有虚构行名时返回 None。"""
    if ctx is None:
        return None
    bank = ctx.data.get("bank")
    if not isinstance(bank, dict):
        return None
    name = str(bank.get("name") or "").strip()
    if not name:
        return None
    legal = str(bank.get("legal") or "").strip() or name
    domain = str(bank.get("domain") or "").strip() or name.lower().replace(" ", "-") + ".example"
    return name, legal, domain


def _subst(text, brand, contact=None):
    if brand is None:
        return text
    name, legal, domain = brand
    if contact:
        # 原稿把「联系我们」写成真实总部地址与客服线，整句一起换
        for old, new in contact:
            text = text.replace(old, new)
    for old, new in (
        ("SoFi Insured Deposit Program", f"{name} Insured Deposit Program"),
        ("SoFi Bank, N.A.", legal),
        ("SoFi Bank", name),
        ("SoFi", name),
        ("sofi.com", domain),
    ):
        text = text.replace(old, new)
    return text


def _contact_subs(ctx):
    """原稿法务段落里的总部地址与客服热线 → 数据里的虚构联系方式。

    地址在原稿里跨了两行（`…Cottonwood Heights,` / `Utah 84121 as soon as possible.`），
    而替换是逐行做的，所以这里按行拆成两条，最后再补一条整句匹配给不分行的场合。
    """
    if ctx is None:
        return ()
    phone = str(ctx.data.get("contact_phone") or "").strip()
    lines = [str(x).strip() for x in (ctx.data.get("contact_address") or []) if str(x).strip()]
    subs: list[tuple[str, str]] = []
    if phone:
        subs.append(("1-855-456-7634", phone))
    if lines:
        joined = ", ".join(lines)
        subs.append(("2750 East Cottonwood Parkway #300, Cottonwood Heights, Utah 84121", joined))
        subs.append(("2750 East Cottonwood Parkway #300, Cottonwood Heights,", f"{lines[0]},"))
        if len(lines) > 1:
            subs.append(("Utah 84121", lines[-1]))
    return tuple(subs)


def _legal_text(page, x, y, text, brand, font, size):
    """写一行法务文本；出现域名时按原稿样式加下划线与链接注释。"""
    put(page, x, y, text, size, font)
    if brand is None:
        return
    domain = brand[2]
    for token in (f"www.{domain}", domain):
        i = text.find(token)
        if i < 0:
            continue
        left = x + width(text[:i], size, font)
        right = left + width(token, size, font)
        page.draw_line(fitz.Point(left, y + 0.60), fitz.Point(right, y + 0.60),
                       color=LEGAL_NAVY, width=0.3)
        page.insert_link({"kind": fitz.LINK_URI,
                          "from": fitz.Rect(left - 0.02, y - 6.8, right + 0.02, y + 1.35),
                          "uri": f"https://{domain}/"})
        return


def _put_legal_para(page, para, brand, contact=()):
    """一段法务文本：标题单独一行，正文拼回去重断行、必要时缩字号。"""
    body_ys: list[float] = []
    body_parts: list[str] = []
    for bold, y, text in para:
        if bold:
            line = _subst(text, brand, contact)
            size = SZ6
            while size > 5.0 and width(line, size, "bold") > _LEGAL_W:
                size -= 0.1
            _legal_text(page, 18.1, y, line, brand, "bold", size)
        else:
            body_ys.append(y)
            body_parts.append(_subst(text, brand, contact).strip())
    if not body_parts:
        return
    joined = " ".join(body_parts)
    size = SZ6
    lines = _wrap(joined, size, _LEGAL_W, _LEGAL_W)
    while len(lines) > len(body_ys) and size > 5.0:
        size = round(size - 0.1, 2)
        lines = _wrap(joined, size, _LEGAL_W, _LEGAL_W)
    for y, line in zip(body_ys, lines):
        _legal_text(page, 18.1, y, line, brand, "reg", size)


def page_legal(doc, page_num, total_pages, *, ctx=None):
    ctx = ctx if ctx is not None else _render_context()
    page = doc.new_page(width=612, height=792)
    prepare(page, ctx=ctx)
    put(page, 18.1, 28.6, "Important Information", SZ11)
    # 标题下分隔线（原稿 y=38.25, #808080, 0.1pt）
    page.draw_line(fitz.Point(CONTENT_L, 38.25), fitz.Point(CONTENT_R, 38.25),
                   color=DIVIDER, width=0.1)
    brand = _brand_texts(ctx)
    # 正文：默认数据逐行使用原稿精确 y 值；有虚构行名时重排
    if brand is None:
        for para in LEGAL_PARAS:
            for bold, y, text in para:
                _put_legal_line(page, y, text, bold)
    else:
        contact = _contact_subs(ctx)
        for para in LEGAL_PARAS:
            _put_legal_para(page, para, brand, contact)
    # 落款：居中粗体（原稿 y=457.5, x=223.2）
    put_fast(page, LEGAL_SIGN_X, LEGAL_SIGN_Y,
             _subst(LEGAL_SIGN, brand, _contact_subs(ctx) if brand else ()), SZ6, "bold")
    # 页脚（原稿页码 x=562.3）
    put_fast(page, 20.9, FOOTER_Y, ctx.data["legal_footer"], SZ6)
    put_fast(page, 562.3, FOOTER_Y, f"Page {page_num} of {total_pages}", SZ6)
    watermark(page, ctx=ctx)

LEGAL_PARAS = [
    [
        (True, 58.2, 'SoFi Insured Deposit Program'),
        (False, 66.3, 'For participants in the SoFi Insured Deposit Program (the “Program”), SoFi Bank may place your cash balances, including amounts below the Standard Maximum Deposit Insurance Amount (the “SMDIA,” currently'),
        (False, 74.5, '$250,000 for individual accounts and $500,000 for joint accounts), into deposit accounts at multiple FDIC-insured participating banks, as set forth in the Program Terms and Conditions, which can be found at'),
        (False, 82.6, 'https://www.sofi.com/banking/fdic/sidpterms. The maximum FDIC insurance coverage available through the Program is $3.0 million per depositor per legal category of account ownership (including balances retained'),
        (False, 90.8, 'at SoFi Bank). You can find the most recent list of participating banks and the how funds are distributed at https://www.sofi.com/my/banking/accounts/sidp'),
    ],
    [
        (False, 107.1, 'If you have designated Payable on Death (“POD”) beneficiaries to your Checking and Savings accounts, your FDIC insurance coverage may be increased. For individual accounts, deposits will be insured up to $250,000'),
        (False, 115.2, 'per unique primary beneficiary, up to a maximum of $1,250,000 if five or more beneficiaries are named. For joint accounts, deposits will be insured up to $500,000 per unique primary beneficiary, up to a maximum of'),
        (False, 123.4, '$1,250,000 per account owner, subject to FDIC rules. If you have designated only secondary beneficiaries (i.e., no primary beneficiaries), SoFi Bank will treat these secondary beneficiaries as primary beneficiaries for'),
        (False, 131.5, 'purposes of calculating FDIC insurance coverage and determining the amount of your cash balances placed into the Program.'),
    ],
    [
        (True, 147.8, 'How to Contact Us'),
        (False, 156.0, 'You may call us using the phone number on the front of this statement.'),
    ],
    [
        (True, 172.3, 'Deposit Agreement'),
        (False, 180.4, 'When you opened your account, you received the SoFi Bank Deposit Account Agreement (the “Customer Agreement”), which discussed the terms and conditions governing your account, as well as a fee schedule, a'),
        (False, 188.6, 'rate sheet and, if applicable, a debit card agreement. You agreed that your account would be governed by these documents as amended from time to time, as well as any other documents provided to you at account'),
        (False, 196.7, 'opening or subsequently. Copies of the Customer Agreement, the Fee Sheet and Rate Sheet are available through the SoFi website www.sofi.com or mobile app.'),
    ],
    [
        (True, 213.0, 'Questions About Your Statement or Electronic Transfer Errors'),
        (False, 221.2, 'You are in the best position to discover errors and unauthorized transactions on your account statement. It is your duty to review your account statements promptly and carefully and immediately notify us of any errors. If'),
        (False, 229.3, 'you (a) have questions about your statement, (b) think your statement or receipt is wrong, (c) need more information about a transaction, or  (d) think an electronic transaction (e.g., ATM transaction, direct deposit,'),
        (False, 237.5, 'withdrawal, or point of sale transaction) is incorrect or unauthorized,  please telephone us at 1-855-456-7634 immediately or write us at SoFi Bank, N.A., 2750 East Cottonwood Parkway #300, Cottonwood Heights,'),
        (False, 245.6, 'Utah 84121 as soon as possible.'),
    ],
    [
        (False, 261.9, 'We must hear from you no later than 60 days after we sent the FIRST statement on which the problem or error appeared.  We are not liable to you for any damages resulting from an error you fail to timely report and you'),
        (False, 270.1, 'agree to not make a claim against us for such damages.'),
    ],
    [
        (False, 286.4, 'If you write us, please include the following information: (1) Your full name and account number (if any); (2) describe the error, transfer or suspected unauthorized transaction you are unsure  about and explain as clearly'),
        (False, 294.5, 'as you can, why you believe there is an error or why you need more information; and  (3) the dollar amount of any suspected error.'),
    ],
    [
        (False, 310.8, 'We will investigate your complaint and will correct any error promptly. If we take more than 10 business days to do this, we will credit your account for the amount you think is in error, so that you will have the use of the'),
        (False, 319.0, 'money during the time it takes us to complete our investigation.'),
    ],
    [
        (True, 335.3, 'Preauthorized Credits'),
        (False, 343.4, 'If you have arranged to have direct deposits made to your account at least once every 60 days from the same person or company, you can call us at 1-855-456-7634 to find out whether or not the deposit has been made.'),
    ],
    [
        (True, 359.7, 'Reporting Other Problems'),
        (False, 367.9, 'You are in the best position to discover errors and unauthorized transactions on your account statement. It is your duty to review your account statements promptly and carefully and immediately notify us of any errors or'),
        (False, 376.0, 'concerns.'),
    ],
    [
        (True, 392.3, 'Interest/Dividends'),
        (False, 400.5, 'We are required by law to report annually to you and to the Internal Revenue Service on Form 1099 any taxable interest credited to your account, as well as any taxes withheld. The year-to-date figures shown on your'),
        (False, 408.6, 'statement reflect these amounts classified to the best of our current knowledge. However, some payments are subject to reclassification, which will be reflected on subsequent statements if we are advised of them prior'),
        (False, 416.8, 'to the end of the calendar year.'),
    ],
]
LEGAL_SIGN  = 'SoFi Bank, N.A. Member of FDIC and Equal Housing Lender'
LEGAL_SIGN_Y = 457.5


# ================================================================ PDF 元数据（编辑器可编辑，创建时间留空不写入）
META = {'title': 'SoFi Bank Statement',
 'author': '',
 'subject': 'SoFi Bank account statement',
 'keywords': 'statement',
 'producer': 'LibreOffice 7.0',
 'creator': 'Writer',
 'creationDate': 'D:20261001000000Z'}

def apply_meta(doc, meta=None, *, ctx=None):
    """写入 PDF 元数据；创建时间留空时不写入。"""
    ctx = ctx if ctx is not None else _render_context()
    m = dict(ctx.meta)
    for k, v in (meta or {}).items():
        if k in m and v:
            m[k] = str(v)
    md = {}
    if m["creationDate"]:
        md["creationDate"] = m["creationDate"]
        md["modDate"] = m["creationDate"]
    if m["title"]:    md["title"] = m["title"]
    if m["author"]:   md["author"] = m["author"]
    if m["subject"]:  md["subject"] = m["subject"]
    if m["keywords"]: md["keywords"] = m["keywords"]
    if m["producer"]:
        md["producer"] = m["producer"]
    if m["creator"]:
        md["creator"] = m["creator"]
    doc.set_metadata(md)

# ================================================================ build
def build(meta=None, *, data=None, watermark=None):
    """Build a document from a private request snapshot.

    ``meta`` remains positional for legacy callers. Omitted data/watermark use
    module defaults; explicit values never replace those module attributes.
    The caller owns the returned document and must close it.
    """
    meta = deepcopy(meta)
    ctx = _render_context(data, watermark)
    doc = fitz.open()
    try:
        txs = ctx.data["transactions"]
        # 全局余额（从最新到最旧回推，跨页一致）
        balances = []
        bal = ctx.data["current_balance"]
        for t in txs:
            balances.append(bal)
            bal -= t["amount"]
        # 分页：页1 最多 TX_PER_PAGE 行（联系区固定 581.8），
        # 续页各 TX_PER_CONT 行（仅标签+流水+页脚），末页固定为条款页
        n_extra = len(txs) - TX_PER_PAGE
        n_cont = max(0, math.ceil(n_extra / TX_PER_CONT)) if n_extra > 0 else 0
        total = 1 + n_cont + 1  # 页1 + 续页 + 条款页
        page_statement(doc, 1, total,
                       txs[:TX_PER_PAGE], balances[:TX_PER_PAGE], ctx=ctx)
        for p in range(n_cont):
            lo = TX_PER_PAGE + p * TX_PER_CONT
            page_continuation(doc, p + 2, total,
                              txs[lo:lo + TX_PER_CONT],
                              balances[lo:lo + TX_PER_CONT], ctx=ctx)
        page_legal(doc, total, total, ctx=ctx)
        apply_meta(doc, meta, ctx=ctx)
        return doc
    except BaseException:
        doc.close()
        raise


def main():
    doc = build(meta=None)
    out = "SoFi-Statement-SAMPLE.pdf"
    doc.save(out, garbage=4, deflate=True)
    print(f"wrote {out} ({doc.page_count} pages)")

if __name__ == "__main__":
    main()
