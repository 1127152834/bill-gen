#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Capital One 360 风格个人账户账单模板 — 1:1 排版复刻（v2，依据 20250731 原稿）
==============================================================================
仅供设计/演示用途。所有文本可通过 DATA 字典编辑。

排版规则（均从原稿逐字符逆向测量）：
- 字体：Optimist（zip 完整字体），Regular / Bold / Medium
- 整形：kern/liga 关闭（原稿如此，误差 0.0000pt）
- 动态地址：2-4 行，后续内容按行数上下平移（每行 12pt）
- 现金流摘要：3 项统计（利息/透支费/金融费用），带图标
- 汇总表列宽：随内容自适应（线性拟合原稿两点）
- 横幅标签：跟随余额金额宽度（gap=21）
- 交易描述：支持第二行备注（行高自动增加 7.9875/行，其他列垂直居中）
- 费用汇总：紧跟 Closing Balance 之后
- 续页头部：右对齐块，0.0375pt 网格量化（与原稿生成器一致）
"""
import fitz, os, math
import uharfbuzz as hb

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
    font_cjk = FONT_CJK if data is None else FONT_CJK_BASE
    return RenderContext(d, WATERMARK if watermark is None else watermark,
                         deepcopy(META), font_cjk)


BASE = os.path.dirname(os.path.abspath(__file__))
A = lambda n: os.path.join(BASE, "assets", n)
FONT_REG  = A("c1_regular.ttf")
FONT_BOLD = A("c1_bold.ttf")
FONT_MED  = A("c1_medium.ttf")

# ---------------------------------------------------------------- palette
BLUE     = (0.14510, 0.37255, 0.50980)   # #255f82
TEXT     = (0.07843, 0.07843, 0.07843)   # #141414
FOOT_GRAY= (0.30196, 0.27843, 0.26667)   # #4d4744
GREEN    = (0.0, 0.50588, 0.25098)       # #008140
GRAY_BG  = (0.95686, 0.95686, 0.95686)   # #f4f4f4
GRAY_SEP = (0.83137, 0.83137, 0.83137)   # #d4d4d4
WHITE    = (1, 1, 1)

SZ6, SZ7, SZ8, SZ9, SZ12, SZ14, SZ44 = 6.0, 7.0125, 7.9875, 9.0, 12.0, 13.9875, 43.9875

# ---------------------------------------------------------------- data
DEMO_DATA = {'customer_name': 'Maya Okafor',
 'address_lines': ['Maya Okafor', '2104 S 1st St', 'Austin TX 78704', ''],
 'tagline': 'Thanks for saving with Capital One 360\u00ae',
 'headline_month': 'October 2026',
 'period_range': 'Oct 01 - Oct 31, 2026',
 'summary_start_label': 'Oct 01',
 'summary_end_label': 'Oct 31',
 'account_name': '360 Checking...1347',
 'account_title': '360 Checking',
 'account_number': '410737211347',
 'apy': '0.00%',
 'days_in_cycle': '31',
 'opening_balance': 1000.0,
 'ytd_interest': 0.0,
 'transactions': [{'date': '10/02',
                   'desc': 'KESTREL DESIGN CO PAYROLL PPD ID:4289126633',
                   'amount': 250.0,
                   'id': '9401882733',
                   'txid': '9401882733',
                   'memo': '',
                   'type': 'Deposit'},
                  {'date': '10/07',
                   'desc': 'H-E-B #0611 AUSTIN TX',
                   'amount': -35.5,
                   'id': '9401882741',
                   'txid': '9401882741',
                   'memo': '',
                   'type': 'Withdrawal'}],
 'closing_date': 'Oct 31',
 'interest_earned': 0.0,
 'overdraft_fees': 0.0,
 'finance_charges': 0.0,
 'fees_overdraft_period': 0.0,
 'fees_overdraft_ytd': 0.0,
 'fees_return_period': 0.0,
 'fees_return_ytd': 0.0,
 'footer_site': 'capitalone.com',
 'footer_phone': '1-888-464-0727',
 'footer_address': 'P.O. Box 85123, Richmond, VA 23285',
 'note_text': 'The last four digits of your external accounts may not match your actual account numbers. This is because some banks may  issue a virtual or tokenized number for security. To learn more about tokenized account numbers, contact your external bank.',
 'error_heading': 'If anything in your statement looks incorrect, please let us know immediately.',
 'error_paragraphs': [
                 # 参考件这里是一整段（"In case… 23285. Or, log in…" 连续排三行），
                 # 拆成两段会因为段间距多出 7.3pt，后面所有基线整体下移
                 'In case of error or questions about your electronic transfers, we can be reached by telephone at 1-888-464-0727, or mail at P.O. Box 85123, Richmond, VA 23285. Or, log in to your account at capitalone.com and click  on the transaction. If you think your statement or receipt is wrong or if you need more information about a transfer listed on your statement or receipt, you must let us know within 60 days after we sent you the FIRST statement on which the error appeared.',
                 ],
 'error_items': [
                 'Tell us your name and account number.',
                 'Describe the error or the transfer you are unsure about, and provide an explanation of why you believe it is an error or why you need more information.',
                 'Tell us the dollar amount of the suspected error.',
                 ],
 'final_paragraphs': [
                 'We will investigate your complaint and will correct any error promptly. If we take more than 10 business days to do this, we will credit your account for the amount you think is in error, so that you will have the use of the money during the time it takes us to complete our investigation.',
                 ]}

# 原稿基线：与 asset 里的样张一字不差，供 parity 回归比对（不要改）。
PARITY_DATA = {'customer_name': 'RILEY NAKAMURA',
 'address_lines': ['RILEY NAKAMURA', '4820 SUNSET TRAIL', 'AUSTIN TX 78745', ''],
 'tagline': 'Thanks for saving with Capital One 360\u00ae',
 'headline_month': 'October 2026',
 'period_range': 'Oct 01 - Oct 31, 2026',
 'summary_start_label': 'Oct 01',
 'summary_end_label': 'Oct 31',
 'account_name': '360 Checking...5521',
 'account_title': '360 Checking',
 'account_number': '000000000001',
 'apy': '0.00%',
 'days_in_cycle': '31',
 'opening_balance': 1000.0,
 'ytd_interest': 0.0,
 'transactions': [{'date': '10/02',
                   'desc': 'ACH CREDIT KESTREL DESIGN CO PAYROLL',
                   'amount': 250.0,
                   'id': '9401882733',
                   'txid': '9401882733',
                   'memo': '',
                   'type': 'Deposit'},
                  {'date': '10/07',
                   'desc': 'DEBIT CARD PURCHASE H-E-B #0611 AUSTIN TX',
                   'amount': -35.5,
                   'id': '9401882741',
                   'txid': '9401882741',
                   'memo': '',
                   'type': 'Withdrawal'}],
 'closing_date': 'Oct 31',
 'interest_earned': 0.0,
 'overdraft_fees': 0.0,
 'finance_charges': 0.0,
 'fees_overdraft_period': 0.0,
 'fees_overdraft_ytd': 0.0,
 'fees_return_period': 0.0,
 'fees_return_ytd': 0.0,
 'footer_site': 'capitalone.com',
 'footer_phone': '1-888-464-0727',
 'footer_address': 'P.O. Box 85123, Richmond, VA 23285',
 'note_text': 'The last four digits of your external accounts may not match your actual account numbers. This is because some banks may  issue a virtual or tokenized number for security. To learn more about tokenized account numbers, contact your external bank.',
 'error_heading': 'If anything in your statement looks incorrect, please let us know immediately.',
 'error_paragraphs': [
                 # 参考件这里是一整段（"In case… 23285. Or, log in…" 连续排三行），
                 # 拆成两段会因为段间距多出 7.3pt，后面所有基线整体下移
                 'In case of error or questions about your electronic transfers, we can be reached by telephone at 1-888-464-0727, or mail at P.O. Box 85123, Richmond, VA 23285. Or, log in to your account at capitalone.com and click  on the transaction. If you think your statement or receipt is wrong or if you need more information about a transfer listed on your statement or receipt, you must let us know within 60 days after we sent you the FIRST statement on which the error appeared.',
                 ],
 'error_items': [
                 'Tell us your name and account number.',
                 'Describe the error or the transfer you are unsure about, and provide an explanation of why you believe it is an error or why you need more information.',
                 'Tell us the dollar amount of the suspected error.',
                 ],
 'final_paragraphs': [
                 'We will investigate your complaint and will correct any error promptly. If we take more than 10 business days to do this, we will credit your account for the amount you think is in error, so that you will have the use of the money during the time it takes us to complete our investigation.',
                 ]}

#: 引擎默认数据 = 原稿基线（`build()` 不带 data 时用它，parity 回归依赖这一点）。
DATA = PARITY_DATA



WATERMARK = True
WRAP_RIGHT = 554.4
LINE_PITCH = 7.0125
PAGE_BOTTOM_LIMIT = 732.0       # 内容不得超过（页码区）

# ================================================================ shaping
class Shaper:
    def __init__(self, fontpath):
        self.blob = hb.Blob.from_file_path(fontpath)
        self.face = hb.Face(self.blob)
        self.font = hb.Font(self.face)
        self.upem = self.face.upem

    def layout(self, text, size):
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.font, buf, {"kern": False, "liga": False})
        s = size / self.upem
        out, pen = [], 0.0
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            out.append((text[info.cluster], pen + pos.x_offset * s))
            pen += pos.x_advance * s
        return out, pen

_shapers = {}
def shaper(p, *, ctx=None):
    ctx = ctx if ctx is not None else _render_context()
    if p not in ctx.shapers:
        ctx.shapers[p] = Shaper(p)
    return ctx.shapers[p]

_REG, _BOLD, _MED, _CJK = "reg", "bold", "med", "cjk"
_BOLD_D = "boldd"   # (n) 标签专用嵌入名（与原稿一致）
_FP = {_REG: FONT_REG, _BOLD: FONT_BOLD, _MED: FONT_MED}
FONT_BOLD_D = os.path.join(BASE, "assets", "c1_bold_d.ttf")

from fontTools.ttLib import TTFont as _TT
_CMAP = {k: set(_TT(v).getBestCmap().keys()) for k, v in _FP.items()}
def _resolve_cjk():
    from runtime_paths import cjk_font_path
    return cjk_font_path()
FONT_CJK_BASE = _resolve_cjk()
FONT_CJK = FONT_CJK_BASE
_CJK_CMAP = set()
if FONT_CJK_BASE:
    try:
        _CJK_CMAP = set(_TT(FONT_CJK_BASE, fontNumber=0).getBestCmap().keys())
    except Exception:
        _CJK_CMAP = set()
_fallback_used = set()

def layout_text(text, size, primary, *, ctx=None):
    ctx = ctx if ctx is not None else _render_context()
    cm = _CMAP[primary]
    runs, cur_key, cur = [], None, ""
    for ch in text:
        key = primary if ord(ch) in cm else _CJK
        if key != cur_key and cur:
            runs.append((cur_key, cur)); cur = ""
        cur_key, cur = key, cur + ch
    if cur:
        runs.append((cur_key, cur))
    seq = []
    for key, rs in runs:
        path = ctx.font_cjk if key == _CJK else _FP[key]
        sh = shaper(path, ctx=ctx)
        buf = hb.Buffer(); buf.add_str(rs)
        buf.guess_segment_properties()
        hb.shape(sh.font, buf, {"kern": False, "liga": False})
        s = size / sh.upem
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            seq.append((key, rs[info.cluster], pos.x_advance * s))
            if key == _CJK:
                ctx.fallback_used.add(rs[info.cluster])
    out, pen = [], 0.0
    for key, ch, adv in seq:
        out.append((key, ch, pen))
        pen += adv
    return out, pen

# ================================================================ drawing
_fitz_fonts = {}
def _ffont(key, *, ctx=None):
    """缓存的 fitz.Font（用于快速查询字符自然步进）。"""
    ctx = ctx if ctx is not None else _render_context()
    path = ctx.font_cjk if key == _CJK else _FP[key]
    if path not in ctx.fitz_fonts:
        ctx.fitz_fonts[path] = fitz.Font(fontfile=path)
    return ctx.fitz_fonts[path]

MAX_RUN = 6   # 批量 run 上限（约束 MuPDF 批内量化漂移 <0.05pt）

def put(page, x, baseline, text, size, font=_REG, color=TEXT, ins_name=None, *, ctx=None):
    """绘制整形文本，返回 pen 终点。
    优化：同一字体且步进无偏移的连续字符合并为一次 insert_text。"""
    ctx = ctx if ctx is not None else _render_context()
    glyphs, w = layout_text(text, size, font, ctx=ctx)
    n = len(glyphs)
    i = 0
    while i < n:
        key = glyphs[i][0]
        gx0 = glyphs[i][2]
        ff = _ffont(key, ctx=ctx)
        j = i + 1
        while j < n and j - i < MAX_RUN:
            if glyphs[j][0] != key:
                break
            adv = ff.text_length(glyphs[j - 1][1], size)
            if abs((glyphs[j][2] - glyphs[j - 1][2]) - adv) > 1e-4:
                break
            j += 1
        seg = "".join(g[1] for g in glyphs[i:j])
        if key == _CJK and ctx.font_cjk:
            # 兜底字体按需注册：只在这一页真出现缺字时嵌入
            pages = getattr(ctx, "cjk_pages", None)
            if pages is None:
                pages = ctx.cjk_pages = set()
            if page.xref not in pages:
                page.insert_font(fontname=_CJK, fontfile=ctx.font_cjk)
                pages.add(page.xref)
        page.insert_text((x + gx0, baseline), seg, fontsize=size,
                         fontname=ins_name or key, color=color)
        i = j
    return x + w

def width(text, size, font=_REG, *, ctx=None):
    ctx = ctx if ctx is not None else _render_context()
    _, w = layout_text(text, size, font, ctx=ctx)
    return w

def put_right(page, right, baseline, text, size, font=_REG, color=TEXT, *, ctx=None):
    ctx = ctx if ctx is not None else _render_context()
    put(page, right - width(text, size, font, ctx=ctx), baseline, text, size, font, color, ctx=ctx)

def rect(page, x0, y0, x1, y1, color):
    page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=None, fill=color)

def q0375(v):
    """向上量化到 0.0375pt 网格（原稿生成器行为）。"""
    return math.ceil(v / 0.0375 - 1e-9) * 0.0375

# ================================================================ helpers
def money(v):
    return f"${abs(v):,.2f}"

def signed(v):
    # 真账单的金额不带正号、也不在符号与数字之间留空格：`-$86.42` / `$3,125.80`
    return ("-" if v < 0 else "") + money(v)

def prepare(page, *, ctx=None):
    ctx = ctx if ctx is not None else _render_context()
    for k in (_REG, _BOLD):
        page.insert_font(fontname=k, fontfile=_FP[k])
    # CJK 兜底字体只在真被某个字用到时才嵌入：真账单里没有中文字体，
    # 常驻一份 Hiragino 是明显的机审特征（字体清单直接暴露生成环境）。

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
    if not ctx.watermark:
        return
    tw = fitz.TextWriter(page.rect)
    f = fitz.Font(fontfile=FONT_REG)
    txt = "S A M P L E"
    size = 92
    w = f.text_length(txt, size)
    pt = fitz.Point((612 - w * 0.707) / 2, 792 * 0.62)
    tw.append(pt, txt, font=f, fontsize=size)
    tw.write_text(page, color=(0.93, 0.93, 0.95), morph=(pt, fitz.Matrix(45)))
    # 参考件的水印是「最上层、极浅灰」：0.93 灰压在正文上只会淡一层，不会盖住金额；
    # 反过来把它挪到最底层会被页面背景卡挡住 —— 实测可见面积只剩参考件的三分之一。

def footer(page, page_num, total_pages, *, ctx=None):
    ctx = ctx if ctx is not None else _render_context()
    runs = ["Page ", str(page_num), " of ", str(total_pages)]
    total = sum(width(r, SZ8, _BOLD, ctx=ctx) for r in runs)
    pen = math.floor((305.99 - total / 2) / 0.075) * 0.075
    for r in runs:
        put(page, pen, 740.6625, r, SZ8, _BOLD, FOOT_GRAY, ctx=ctx)
        pen = q0375(pen + width(r, SZ8, _BOLD, ctx=ctx))
    page.insert_image(fitz.Rect(81.4125, 764.625, 94.1625, 777.375),
                      filename=A("icon_globe.png"))
    page.insert_image(fitz.Rect(182.4375, 761.5125, 198.4125, 777.525),
                      filename=A("icon_f1.jpg"))
    page.insert_image(fitz.Rect(289.6875, 761.5125, 305.2125, 777.525),
                      filename=A("icon_f2.jpg"))
    page.insert_image(fitz.Rect(514.9125, 761.5125, 530.5875, 777.525),
                      filename=A("icon_f3.jpg"))
    page.insert_image(fitz.Rect(467.1, 764.6625, 483.1125, 774.3375),
                      filename=A("icon_f5.jpg"))
    put(page, 97.1625, 774.0, ctx.data["footer_site"], SZ8, ctx=ctx)
    put(page, 201.4125, 774.0, ctx.data["footer_phone"], SZ8, ctx=ctx)
    # 地址过长会顶到右侧 MEMBER FDIC 标记上（实测 `90000FDIC` 叠字）：按可用宽度缩字号
    addr, addr_size = ctx.data["footer_address"], SZ8
    avail = 467.1 - 6.0 - 308.2125
    while addr_size > 6.0 and width(addr, addr_size, ctx=ctx) > avail:
        addr_size -= 0.25
    put(page, 308.2125, 774.0, addr, addr_size, ctx=ctx)

def cont_header(page, *, ctx=None):
    """续页头部：右对齐块（0.0375 量化）。"""
    ctx = ctx if ctx is not None else _render_context()
    label_w = width("STATEMENT PERIOD ", SZ9, ctx=ctx)
    range_w = width(ctx.data["period_range"], SZ9, _BOLD, ctx=ctx)
    block_w = q0375(max(label_w, range_w + 2.3625))
    block_left = 554.4 - block_w
    bar_right = block_left - 12.975
    bar_left = bar_right - 0.75
    rect(page, bar_left, 44.0625, bar_right, 62.0625, BLUE)
    put(page, block_left, 53.25, "STATEMENT PERIOD ", SZ9, _REG, BLUE, ctx=ctx)
    put(page, block_left, 62.25, ctx.data["period_range"], SZ9, _BOLD, BLUE, ctx=ctx)
    # 姓名：右对齐到竖条左侧（含不可见尾随空格 run，与原稿一致）
    sp_w = width(" ", SZ7, ctx=ctx)
    put(page, bar_left - 0.0094 - sp_w, 62.25, " ", SZ7, ctx=ctx)   # 空格（不可见）
    put_right(page, bar_left - 9.31, 54.75, ctx.data["customer_name"], SZ7, _BOLD, ctx=ctx)

# 表格几何
COL_SEP_X = (386.7, 463.05)
ROW_SEGS = ((59.1, 91.5), (91.5, 329.1), (329.1, 386.7),
            (386.7, 463.05), (463.05, 553.8))
AMT_RIGHT, BAL_RIGHT, CAT_RIGHT = 452.234, 553.767, 375.892

def table_header(page, top, *, ctx=None):
    ctx = ctx if ctx is not None else _render_context()
    bl, blb = top + 10.65, top + 10.7625
    put(page, 59.1, bl, "DATE", SZ7, _REG, BLUE, ctx=ctx)
    put(page, 102.3, bl, "DESCRIPTION", SZ7, _REG, BLUE, ctx=ctx)
    put(page, 341.925, bl, "CATEGORY", SZ7, _REG, BLUE, ctx=ctx)
    put(page, 421.9875, blb, "AMOUNT", SZ7, _BOLD, BLUE, ctx=ctx)
    put(page, 524.3625, bl, "BALANCE", SZ7, _REG, BLUE, ctx=ctx)
    bot = top + 18.525
    for x in COL_SEP_X:
        rect(page, x, top, x + 0.75, bot, GRAY_SEP)
    return bot

def row_separator(page, y):
    for x0, x1 in ROW_SEGS:
        rect(page, x0, y, x1, y + 0.75, GRAY_SEP)

def band_row(page, top, date_str, label, balance, *, ctx=None):
    ctx = ctx if ctx is not None else _render_context()
    bot = top + 20.25
    rect(page, 0, top, 612.9, bot, GRAY_BG)
    rect(page, 59.1, top, 553.8, top + 0.75, GRAY_SEP)
    for x in COL_SEP_X:
        rect(page, x, top, x + 0.75, bot, GRAY_SEP)
    bl = top + 12.4875
    put(page, 59.1, bl, date_str, SZ8, _BOLD, ctx=ctx)
    put(page, 102.3, bl, label, SZ8, _BOLD, ctx=ctx)
    put_right(page, BAL_RIGHT, bl, balance, SZ8, _BOLD, ctx=ctx)

def tx_row(page, top, t, running, *, ctx=None):
    """交易行：支持 memo 第二行，其他列垂直居中。"""
    ctx = ctx if ctx is not None else _render_context()
    lines = [t["desc"]] + ([t["memo"]] if t.get("memo") else [])
    row_h = 20.25 + (len(lines) - 1) * 7.9875
    row_separator(page, top)
    for x in COL_SEP_X:
        rect(page, x, top, x + 0.75, top + row_h, GRAY_SEP)
    bl_c = top + row_h / 2 + 2.25          # 垂直居中基线
    for i, ln in enumerate(lines):
        put(page, 102.3, top + 12.375 + i * 7.9875, ln, SZ8, ctx=ctx)
    put(page, 59.1, bl_c, t["date"], SZ8, ctx=ctx)
    # CATEGORY 列印的是商户品类（真账单是 Groceries / Dining / Bills & Utilities…），
    # 不是金额方向；数据层没给品类时才退回 Credit/Debit。
    put_right(page, CAT_RIGHT, bl_c,
              t.get("category") or ("Credit" if t["amount"] >= 0 else "Debit"), SZ8, ctx=ctx)
    put_right(page, AMT_RIGHT, bl_c + 0.1125, signed(t["amount"]), SZ8, _BOLD,
              GREEN if t["amount"] >= 0 else TEXT, ctx=ctx)
    put_right(page, BAL_RIGHT, bl_c, money(running), SZ8, ctx=ctx)
    return top + row_h

# ================================================================ 页 1
def page1_layout(*, ctx=None):
    """根据地址行数计算页 1 全部纵向坐标。"""
    ctx = ctx if ctx is not None else _render_context()
    addr = [l for l in ctx.data["address_lines"] if l.strip()]
    last_bl = 134.475 + (len(addr) - 1) * 12.0
    bar_top = last_bl + 58.9525
    divider_top = bar_top + 50.4
    banner_top = divider_top + 75.6
    band_top = banner_top + 108
    return {
        "addr": addr,
        "addr_first_bl": 134.475,
        "bar_top": bar_top,
        "tagline_bl": bar_top + 18.4875,
        "divider": (divider_top, divider_top + 52.575),
        "period_bl": divider_top + 25.7625,
        "headline_bl": divider_top + 32.25,
        "banner_top": banner_top,
        "big_bl": banner_top + 68.925,
        "lbl1_bl": banner_top + 57.0375,
        "lbl2_bl": banner_top + 66.0375,
        "band_top": band_top,
        "sum_heading_bl": band_top + 38.8125,
        "sum_hdr_bl": band_top + 57.30,
        "sum_sep1_y": band_top + 65.175,
        "sum_row1_bl": band_top + 77.6625,
        "sum_sep2_y": band_top + 85.425,
        "sum_row2_bl": band_top + 97.9125,
        "acct_bl": band_top + 161.7,
        "apy_bl": band_top + 188.625,
        "apy_lbl1_bl": band_top + 203.2875,
        "apy_lbl2_bl": band_top + 209.2875,
        "cashflow_bl": band_top + 38.8125,
        "cashflow_first_bl": band_top + 38.8125 + 21.1925,
        "bar_top_cashflow": band_top + 24.75,
        "table_top": band_top + 220.0125,
    }

def summary_columns(*, ctx=None):
    """汇总表动态列宽（线性拟合原稿）。返回 (sep1, sep2, end_bal)。"""
    ctx = ctx if ctx is not None else _render_context()
    end_bal = ctx.data["opening_balance"] + sum(t["amount"] for t in ctx.data["transactions"])
    c2 = max(width(ctx.data["summary_start_label"], SZ7, ctx=ctx),
             width(money(ctx.data["opening_balance"]), SZ8, ctx=ctx))
    c3 = max(width(ctx.data["summary_end_label"], SZ7, ctx=ctx),
             width(money(end_bal), SZ8, _BOLD, ctx=ctx))
    w2 = 0.8976 * c2 + 22.887
    w3 = 0.7367 * c3 + 42.48
    sep2 = 287.1 - w3
    sep1 = sep2 - w2
    return sep1, sep2, end_bal

def page_summary(doc, page_num, total_pages, *, ctx=None):
    ctx = ctx if ctx is not None else _render_context()
    page = doc.new_page(width=612, height=792)
    prepare(page, ctx=ctx)
    L = page1_layout(ctx=ctx)
    sep1, sep2, end_bal = summary_columns(ctx=ctx)

    page.insert_image(fitz.Rect(57.6, 21.6, 201.6, 72.0), filename=A("logo.png"))

    for i, line in enumerate(L["addr"]):
        put(page, 57.6, L["addr_first_bl"] + i * 12.0, line, SZ12, ctx=ctx)

    # 蓝条 + 标语（原稿画两遍）
    rect(page, 57.6, L["bar_top"], 58.6125, L["bar_top"] + 28.8, BLUE)
    tag_end = put(page, 76.6125, L["tagline_bl"], ctx.data["tagline"], SZ12, _REG, BLUE, ctx=ctx)
    put(page, 76.6125, L["tagline_bl"], ctx.data["tagline"], SZ12, _REG, BLUE, ctx=ctx)
    # 原来在句末补 ®（继承自底稿的 “…statement®” 文案）；换成正式产品句后这个
    # 注册商标号没有归属，真件不会在一句普通说明后面挂 ®。

    # 分隔竖线 + 标题
    rect(page, 366.6, L["divider"][0], 367.35, L["divider"][1], BLUE)
    x = put(page, 57.6, L["headline_bl"], "Here's your ", SZ14, _REG, BLUE, ctx=ctx) + 0.046
    x = put(page, x, L["headline_bl"], ctx.data["headline_month"], SZ14, _BOLD, BLUE, ctx=ctx) + 0.064
    put(page, x, L["headline_bl"], " bank statement.", SZ14, _REG, BLUE, ctx=ctx)
    put(page, 385.35, L["period_bl"], "STATEMENT PERIOD ", SZ9, _REG, BLUE, ctx=ctx)
    put(page, 385.35, L["period_bl"] + 9.0, ctx.data["period_range"], SZ9, _BOLD, BLUE, ctx=ctx)

    # 蓝色横幅（标签跟随余额宽度）
    page.insert_image(fitz.Rect(0, L["banner_top"], 612, L["banner_top"] + 108),
                      filename=A("banner.png"))
    bal_str = money(end_bal)
    bal_w = width(bal_str, SZ44, ctx=ctx)
    put(page, 57.6, L["big_bl"], bal_str, SZ44, _REG, WHITE, ctx=ctx)
    lbl_x = 57.6 + bal_w + 21.0
    put(page, lbl_x, L["lbl1_bl"], "TOTAL ENDING BALANCE", SZ9, _REG, WHITE, ctx=ctx)
    put(page, lbl_x, L["lbl2_bl"], "IN ALL ACCOUNTS", SZ9, _REG, WHITE, ctx=ctx)

    # 灰带
    rect(page, 0, L["band_top"], 612, L["band_top"] + 120.675, GRAY_BG)

    # 账户汇总（动态列）
    put(page, 58.35, L["sum_heading_bl"], "Account Summary", SZ14, _REG, BLUE, ctx=ctx)
    put(page, 58.35, L["sum_hdr_bl"], "ACCOUNT NAME", SZ7, _REG, BLUE, ctx=ctx)
    col2_right = sep2 - 0.77
    put_right(page, col2_right, L["sum_hdr_bl"], ctx.data["summary_start_label"], SZ7, _REG, BLUE, ctx=ctx)
    put_right(page, 287.08, L["sum_hdr_bl"], ctx.data["summary_end_label"], SZ7, _REG, BLUE, ctx=ctx)
    for y in (L["sum_sep1_y"], L["sum_sep2_y"]):
        for x0, x1 in ((57.6, sep1), (sep1, sep2), (sep2, 287.1)):
            rect(page, x0, y, x1, y + 0.75, GRAY_SEP)
    put(page, 58.35, L["sum_row1_bl"], ctx.data["account_name"], SZ8, _BOLD, ctx=ctx)
    put_right(page, col2_right, L["sum_row1_bl"] - 0.1125, money(ctx.data["opening_balance"]), SZ8, ctx=ctx)
    put_right(page, 287.08, L["sum_row1_bl"], money(end_bal), SZ8, _BOLD, ctx=ctx)
    put(page, 58.35, L["sum_row2_bl"], "All Accounts", SZ8, _BOLD, ctx=ctx)
    put_right(page, col2_right, L["sum_row2_bl"] - 0.1125, money(ctx.data["opening_balance"]), SZ8, ctx=ctx)
    put_right(page, 287.08, L["sum_row2_bl"], money(end_bal), SZ8, _BOLD, ctx=ctx)

    # 现金流摘要（3 项）
    stats = [
        (money(ctx.data["interest_earned"]), "INTEREST EARNED", "THIS PERIOD", GREEN, "icon_interest.jpg"),
        (money(ctx.data["overdraft_fees"]), "OVERDRAFT AND RETURN", "ITEM FEES THIS PERIOD", TEXT, "icon_fee.jpg"),
        (money(ctx.data["finance_charges"]), "FINANCE CHARGES", "THIS PERIOD", TEXT, "icon_fee.jpg"),
    ]
    vbs = [L["cashflow_first_bl"] + i * 16.9875 for i in range(3)]
    bar_bottom = vbs[-1] + 2.925
    rect(page, 366.6, L["bar_top_cashflow"], 367.35, bar_bottom, BLUE)
    put(page, 386.1, L["cashflow_bl"], "Cashflow Summary", SZ14, _REG, BLUE, ctx=ctx)
    max_val_w = max(width(s[0], SZ14, ctx=ctx) for s in stats)
    val_x = 401.85
    lbl_x2 = val_x + max_val_w + 3.77
    for (val, l1, l2, col, icon), vb in zip(stats, vbs):
        page.insert_image(fitz.Rect(386.1, vb - 11.555, 398.1, vb + 0.445),
                          filename=A(icon))
        put(page, val_x, vb, val, SZ14, _REG, col, ctx=ctx)
        put(page, lbl_x2, vb - 5.55, l1, SZ6, _REG, col, ctx=ctx)
        put(page, lbl_x2, vb + 0.45, l2, SZ6, _REG, col, ctx=ctx)

    # 账户标题 + APY 统计
    w = width(ctx.data["account_title"], SZ12, _BOLD, ctx=ctx)
    sp = width(" ", SZ12, _REG, ctx=ctx)
    put(page, 57.6, L["acct_bl"], ctx.data["account_title"], SZ12, _BOLD, BLUE, ctx=ctx)
    put(page, 57.6 + w + sp, L["acct_bl"], f"- {ctx.data['account_number']}", SZ12, _REG, BLUE, ctx=ctx)
    put(page, 58.35, L["apy_bl"], ctx.data["apy"], SZ9, _REG, BLUE, ctx=ctx)
    put(page, 223.95, L["apy_bl"] + 0.15, money(ctx.data["ytd_interest"]), SZ9, _BOLD, GREEN, ctx=ctx)
    put(page, 389.55, L["apy_bl"], ctx.data["days_in_cycle"], SZ9, _REG, BLUE, ctx=ctx)
    put(page, 58.35, L["apy_lbl1_bl"], "ANNUAL PERCENTAGE YIELD", SZ6, _REG, BLUE, ctx=ctx)
    put(page, 58.35, L["apy_lbl2_bl"], "(APY) EARNED", SZ6, _REG, BLUE, ctx=ctx)
    put(page, 223.95, L["apy_lbl1_bl"], "YTD INTEREST AND BONUSES", SZ6, _REG, BLUE, ctx=ctx)
    put(page, 389.55, L["apy_lbl1_bl"], "DAYS IN STATEMENT", SZ6, _REG, BLUE, ctx=ctx)
    put(page, 389.55, L["apy_lbl2_bl"], "CYCLE", SZ6, _REG, BLUE, ctx=ctx)

    # 页 1 底部表格（放得下才画：表头 + Opening Balance）
    t_top = L["table_top"]
    if t_top + 18.525 + 20.25 + 20.25 <= PAGE_BOTTOM_LIMIT:
        hb_bot = table_header(page, t_top, ctx=ctx)
        band_row(page, hb_bot, ctx.data["summary_start_label"], "Opening Balance",
                 money(ctx.data["opening_balance"]), ctx=ctx)
        table_on_p1 = True
    else:
        table_on_p1 = False

    footer(page, page_num, total_pages, ctx=ctx)
    watermark(page, ctx=ctx)
    return table_on_p1

# ================================================================ 费用汇总
def fees_summary(page, fy, *, ctx=None):
    """费用汇总：fy = 标题基线。返回段落底部 y。"""
    ctx = ctx if ctx is not None else _render_context()
    put(page, 68.36, fy, "Fees Summary", SZ12, _BOLD, BLUE, ctx=ctx)
    put_right(page, 223.40, fy + 18.34, "TOTAL FOR THIS", SZ7, _REG, BLUE, ctx=ctx)
    put_right(page, 223.40, fy + 25.35, "PERIOD", SZ7, _REG, BLUE, ctx=ctx)
    put_right(page, 292.72, fy + 18.34, "TOTAL YEAR-TO-", SZ7, _REG, BLUE, ctx=ctx)
    put_right(page, 292.72, fy + 25.35, "DATE", SZ7, _REG, BLUE, ctx=ctx)
    for yy in (fy + 33.225, fy + 53.475):
        for x0, x1 in ((67.6125, 147.2625), (147.2625, 224.175), (224.175, 293.5125)):
            rect(page, x0, yy, x1, yy + 0.75, GRAY_SEP)
    put(page, 68.36, fy + 45.60, "Total Overdraft Fees", SZ8, ctx=ctx)
    put_right(page, 223.42, fy + 45.71, money(ctx.data["fees_overdraft_period"]), SZ8, _BOLD, ctx=ctx)
    put_right(page, 292.75, fy + 45.71, money(ctx.data["fees_overdraft_ytd"]), SZ8, _BOLD, ctx=ctx)
    put(page, 68.36, fy + 65.85, "Total Return Item", SZ8, ctx=ctx)
    put(page, 68.36, fy + 73.84, "Fees", SZ8, ctx=ctx)
    put_right(page, 223.42, fy + 69.94, money(ctx.data["fees_return_period"]), SZ8, _BOLD, ctx=ctx)
    put_right(page, 292.75, fy + 69.94, money(ctx.data["fees_return_ytd"]), SZ8, _BOLD, ctx=ctx)
    return fy + 78.0

# ================================================================ 交易页
def page_transactions(doc, page_num, total_pages, rows, first_idx,
                      opening_on_this_page, *, ctx=None):
    ctx = ctx if ctx is not None else _render_context()
    page = doc.new_page(width=612, height=792)
    prepare(page, ctx=ctx)
    cont_header(page, ctx=ctx)
    hb_bot = table_header(page, 108.0, ctx=ctx)

    running = ctx.data["opening_balance"] + sum(
        t["amount"] for t in ctx.data["transactions"][:first_idx])

    top = hb_bot
    if opening_on_this_page:
        band_row(page, top, ctx.data["summary_start_label"], "Opening Balance",
                 money(ctx.data["opening_balance"]), ctx=ctx)
        top += 20.25

    for t in rows:
        running += t["amount"]
        top = tx_row(page, top, t, running, ctx=ctx)

    band_row(page, top, ctx.data["closing_date"], "Closing Balance", money(running), ctx=ctx)
    fees_summary(page, top + 20.25 + 54.6375, ctx=ctx)

    footer(page, page_num, total_pages, ctx=ctx)
    watermark(page, ctx=ctx)

# ================================================================ 页 3（法律文本）
def _wrap_lines(text, size, first_prefix_w=0.0, font=_REG, x_start=57.6, *, ctx=None):
    ctx = ctx if ctx is not None else _render_context()
    budget = WRAP_RIGHT - x_start
    words = text.split(" ")
    tokens = [w + " " for w in words[:-1]] + ([words[-1]] if words else [])
    lines, cur, cur_w = [], "", first_prefix_w
    for tk in tokens:
        tw = width(tk, size, font, ctx=ctx)
        if cur and cur_w + tw > budget:
            lines.append(cur)
            cur, cur_w = "", 0.0
        cur += tk
        cur_w += tw
    if cur or not lines:
        lines.append(cur)
    return lines

def page_notes(doc, page_num, total_pages, *, ctx=None):
    ctx = ctx if ctx is not None else _render_context()
    page = doc.new_page(width=612, height=792)
    prepare(page, ctx=ctx)
    # 原稿仅条款页有 medium 与第二个 bold 子集
    page.insert_font(fontname=_MED, fontfile=FONT_MED)
    page.insert_font(fontname=_BOLD_D, fontfile=FONT_BOLD_D)
    cont_header(page, ctx=ctx)

    y = 115.5
    lbl_w = width("Note:", SZ7, _MED, ctx=ctx)
    sp_w = width(" ", SZ7, ctx=ctx)
    prefix = lbl_w + 0.07 + sp_w
    body_lines = _wrap_lines(ctx.data["note_text"], SZ7, first_prefix_w=prefix, ctx=ctx)
    put(page, 57.6, y, "Note:", SZ7, _MED, ctx=ctx)
    put(page, 57.6, y, "Note:", SZ7, _MED, ctx=ctx)
    put(page, 57.6 + prefix, y, body_lines[0], SZ7, ctx=ctx)
    y += LINE_PITCH
    for ln in body_lines[1:]:
        put(page, 57.6, y, ln, SZ7, ctx=ctx)
        y += LINE_PITCH

    y += 19.3875 - LINE_PITCH
    put(page, 57.6, y, ctx.data["error_heading"], SZ12, _BOLD, BLUE, ctx=ctx)
    y += 14.02

    for para in ctx.data["error_paragraphs"]:
        for ln in _wrap_lines(para, SZ7, ctx=ctx):
            put(page, 57.6, y, ln, SZ7, ctx=ctx)
            y += LINE_PITCH
        y += 14.32 - LINE_PITCH

    for n, item in enumerate(ctx.data["error_items"], 1):
        label = f"({n}) "
        lbl_w = width(label, SZ7, _BOLD, ctx=ctx)
        lines = _wrap_lines(item, SZ7, first_prefix_w=lbl_w, x_start=87.6, ctx=ctx)
        put(page, 87.6, y, label, SZ7, _BOLD, ins_name=_BOLD_D, ctx=ctx)
        put(page, 87.6 + lbl_w + 0.03, y, lines[0], SZ7, ctx=ctx)
        y += LINE_PITCH
        for ln in lines[1:]:
            put(page, 87.6, y, ln, SZ7, ctx=ctx)
            y += LINE_PITCH
        y += 14.32 - LINE_PITCH
    y -= 0.11

    for para in ctx.data["final_paragraphs"]:
        for ln in _wrap_lines(para, SZ7, ctx=ctx):
            put(page, 57.6, y, ln, SZ7, ctx=ctx)
            y += LINE_PITCH
        y += 14.32 - LINE_PITCH

    footer(page, page_num, total_pages, ctx=ctx)
    watermark(page, ctx=ctx)

# ================================================================ 组装
def collect_texts(data=None):
    d = data if data is not None else DATA
    out = [d.get(k, "") or "" for k in
           ("customer_name", "tagline", "headline_month", "period_range",
            "summary_start_label", "summary_end_label", "account_name",
            "account_title", "account_number", "apy", "days_in_cycle",
            "closing_date", "footer_site", "footer_phone", "footer_address",
            "note_text", "error_heading")]
    out += [l for l in (d.get("address_lines") or []) if str(l).strip()]
    for t in d.get("transactions") or []:
        out += [str(t.get("date", "")), str(t.get("desc", "")),
                str(t.get("memo", "") or "")]
    out += [p for p in d.get("error_paragraphs") or []]
    out += [i for i in d.get("error_items") or []]
    out += [p for p in d.get("final_paragraphs") or []]
    return [str(s) for s in out]

def autodetect_cjk(data=None):
    global FONT_CJK
    if not FONT_CJK_BASE:
        return
    inter = _CMAP[_REG] & _CMAP[_BOLD]
    need = any(ord(ch) not in inter and ord(ch) in _CJK_CMAP
               for s in collect_texts(data) for ch in s)
    FONT_CJK = FONT_CJK_BASE if need else None

def _estimate_tx_pages(table_on_p1, *, ctx=None):
    """估算交易页数（行高含 memo）。"""
    ctx = ctx if ctx is not None else _render_context()
    top = 126.525 if not table_on_p1 else 126.525
    # 每页从 126.525 开始；opening 已在页1时本页无 opening 行
    pages, cur = 1, 126.525 + (0 if table_on_p1 else 20.25)
    for t in ctx.data["transactions"]:
        rh = 20.25 + (7.9875 if t.get("memo") else 0)
        if cur + rh > 712:
            pages += 1
            cur = 126.525
        cur += rh
    return pages

# ---------------- PDF 元数据（编辑器可编辑）
META = {'title': 'Capital One Statement',
 'author': '',
 'subject': 'Capital One account statement',
 'keywords': 'statement',
 'producer': 'openhtmltopdf.com',
 'creator': '',
 'creationDate': 'D:20261001000000Z',
 'xmp_format': '',
 'xmp_pdf_version': '',
 'xmp_pdfa': '',
 'xmp_pdfua': ''}

# ---------------- XMP 元数据（原稿结构模板）
_XMP_TEMPLATE = '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/">\n  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n    <rdf:Description xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/" rdf:about="">\n      <pdfaid:conformance>B</pdfaid:conformance>\n      <pdfaid:part>3</pdfaid:part>\n    </rdf:Description>\n    <rdf:Description xmlns:pdf="http://ns.adobe.com/pdf/1.3/" rdf:about="">\n      <pdf:PDFVersion>1.7</pdf:PDFVersion>\n      <pdf:Producer>openhtmltopdf.com</pdf:Producer>\n    </rdf:Description>\n    <rdf:Description xmlns:xmp="http://ns.adobe.com/xap/1.0/" rdf:about="">\n      <xmp:CreateDate>2026-10-01T00:00:00+00:00</xmp:CreateDate>\n    </rdf:Description>\n    <rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/" rdf:about="">\n      <dc:format>application/pdf</dc:format>\n      <dc:creator>\n        <rdf:Seq>\n          <rdf:li>openhtmltopdf.com</rdf:li>\n        </rdf:Seq>\n      </dc:creator>\n      <dc:title>\n        <rdf:Alt>\n          <rdf:li xml:lang="x-default">Capital One Statement</rdf:li>\n        </rdf:Alt>\n      </dc:title>\n      <dc:description>\n        <rdf:Alt>\n          <rdf:li xml:lang="x-default">Statement data</rdf:li>\n        </rdf:Alt>\n      </dc:description>\n    </rdf:Description>\n    <rdf:Description xmlns:pdfaExtension="http://www.aiim.org/pdfa/ns/extension/" xmlns:pdfaProperty="http://www.aiim.org/pdfa/ns/property#" xmlns:pdfaSchema="http://www.aiim.org/pdfa/ns/schema#" xmlns:pdfuaid="http://www.aiim.org/pdfua/ns/id/" rdf:about="">\n      <pdfaExtension:schemas>\n        <rdf:Bag>\n          <rdf:li rdf:parseType="Resource">\n            <pdfaSchema:schema>Adobe PDF Schema</pdfaSchema:schema>\n            <pdfaSchema:namespaceURI>http://ns.adobe.com/pdf/1.3/</pdfaSchema:namespaceURI>\n            <pdfaSchema:prefix>pdf</pdfaSchema:prefix>\n            <pdfaSchema:property>\n              <rdf:Seq>\n                <rdf:li rdf:parseType="Resource">\n                  <pdfaProperty:name>PDFVersion</pdfaProperty:name>\n                  <pdfaProperty:valueType>Text</pdfaProperty:valueType>\n                  <pdfaProperty:category>internal</pdfaProperty:category>\n                  <pdfaProperty:description>The PDF file version.</pdfaProperty:description>\n                </rdf:li>\n                <rdf:li rdf:parseType="Resource">\n                  <pdfaProperty:name>Keywords</pdfaProperty:name>\n                  <pdfaProperty:valueType>Text</pdfaProperty:valueType>\n                  <pdfaProperty:category>external</pdfaProperty:category>\n                  <pdfaProperty:description>Keywords.</pdfaProperty:description>\n                </rdf:li>\n                <rdf:li rdf:parseType="Resource">\n                  <pdfaProperty:name>Producer</pdfaProperty:name>\n                  <pdfaProperty:valueType>AgentName</pdfaProperty:valueType>\n                  <pdfaProperty:category>internal</pdfaProperty:category>\n                  <pdfaProperty:description>The name of the tool that created the PDF document.</pdfaProperty:description>\n                </rdf:li>\n              </rdf:Seq>\n            </pdfaSchema:property>\n          </rdf:li>\n          <rdf:li rdf:parseType="Resource">\n            <pdfaSchema:schema>PDF/A ID Schema</pdfaSchema:schema>\n            <pdfaSchema:namespaceURI>http://www.aiim.org/pdfa/ns/id/</pdfaSchema:namespaceURI>\n            <pdfaSchema:prefix>pdfaid</pdfaSchema:prefix>\n            <pdfaSchema:property>\n              <rdf:Seq>\n                <rdf:li rdf:parseType="Resource">\n                  <pdfaProperty:name>part</pdfaProperty:name>\n                  <pdfaProperty:valueType>Integer</pdfaProperty:valueType>\n                  <pdfaProperty:category>internal</pdfaProperty:category>\n                  <pdfaProperty:description>Part of PDF/A standard</pdfaProperty:description>\n                </rdf:li>\n                <rdf:li rdf:parseType="Resource">\n                  <pdfaProperty:name>conformance</pdfaProperty:name>\n                  <pdfaProperty:valueType>Text</pdfaProperty:valueType>\n                  <pdfaProperty:category>internal</pdfaProperty:category>\n                  <pdfaProperty:description>Conformance level of PDF/A standard</pdfaProperty:description>\n                </rdf:li>\n              </rdf:Seq>\n            </pdfaSchema:property>\n          </rdf:li>\n          <rdf:li rdf:parseType="Resource">\n            <pdfaSchema:schema>PDF/UA Universal Accessibility Schema</pdfaSchema:schema>\n            <pdfaSchema:namespaceURI>http://www.aiim.org/pdfua/ns/id/</pdfaSchema:namespaceURI>\n            <pdfaSchema:prefix>pdfuaid</pdfaSchema:prefix>\n            <pdfaSchema:property>\n              <rdf:Seq>\n                <rdf:li rdf:parseType="Resource">\n                  <pdfaProperty:name>part</pdfaProperty:name>\n                  <pdfaProperty:valueType>Integer</pdfaProperty:valueType>\n                  <pdfaProperty:category>internal</pdfaProperty:category>\n                  <pdfaProperty:description>Indicates, which part of ISO 14289 standard is followed</pdfaProperty:description>\n                </rdf:li>\n              </rdf:Seq>\n            </pdfaSchema:property>\n          </rdf:li>\n        </rdf:Bag>\n      </pdfaExtension:schemas>\n      <pdfuaid:part>1</pdfuaid:part>\n    </rdf:Description>\n  </rdf:RDF>\n</x:xmpmeta><?xpacket end="w"?>\n'

def _xesc(s):
    from xml.sax.saxutils import escape
    return escape(str(s))

def _xmp_iso(datestr):
    """D:YYYYMMDDHHmmSSZ → ISO 8601（与 Info /CreationDate 同一瞬间）。"""
    import re
    m = re.match(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?", str(datestr or ""))
    if m:
        y, mo, d, h, mi, s = [x or "00" for x in m.groups()]
        return f"{y}-{mo}-{d}T{h}:{mi}:{s}+00:00"
    return str(datestr)  # 已是 ISO 或其他格式则原样

def _apply_xmp(doc, m):
    """按原稿结构写入 XMP；时间与 Info creationDate 保持一致（同一来源）。"""
    import re as _re
    x = _XMP_TEMPLATE
    # 简单元素：空则移除
    simple = [
        (r"<dc:format>application/pdf</dc:format>", "dc:format", m["xmp_format"]),
        (r"<pdf:Producer>openhtmltopdf.com</pdf:Producer>", "pdf:Producer", m["creator"]),
        (r"<pdf:PDFVersion>1.7</pdf:PDFVersion>", "pdf:PDFVersion", m["xmp_pdf_version"]),
    ]
    for pattern, tag, val in simple:
        if val:
            x = x.replace(pattern, f"<{tag}>{_xesc(val)}</{tag}>")
        else:
            x = x.replace(pattern, "")
    # Alt/Seq 容器元素：与文档元数据（Info）保持一致
    if m["title"]:
        x = x.replace("<rdf:li xml:lang=\"x-default\">Capital One Statement</rdf:li>",
                      f'<rdf:li xml:lang="x-default">{_xesc(m["title"])}</rdf:li>')
    else:
        x = _re.sub(r"<dc:title>.*?</dc:title>\s*", "", x, flags=_re.S)
    if m["author"]:
        x = x.replace("<rdf:li>openhtmltopdf.com</rdf:li>", f"<rdf:li>{_xesc(m['creator'])}</rdf:li>")
    else:
        x = _re.sub(r"<dc:creator>.*?</dc:creator>\s*", "", x, flags=_re.S)
    if m["subject"]:
        x = x.replace("<rdf:li xml:lang=\"x-default\">Statement data</rdf:li>",
                      f'<rdf:li xml:lang="x-default">{_xesc(m["subject"])}</rdf:li>')
    else:
        x = _re.sub(r"<dc:description>.*?</dc:description>\s*", "", x, flags=_re.S)
    # 创建时间：与 Info 同源（无则两处都不写）
    if m["creationDate"]:
        iso = _xmp_iso(m["creationDate"])
        x = x.replace("<xmp:CreateDate>2026-10-01T00:00:00+00:00</xmp:CreateDate>",
                      f"<xmp:CreateDate>{_xesc(iso)}</xmp:CreateDate>")
    else:
        x = _re.sub(r"<rdf:Description xmlns:xmp=.*?</rdf:Description>\s*", "", x, flags=_re.S)
    # PDF/A-3B 声明块
    if not m["xmp_pdfa"]:
        x = _re.sub(r'<rdf:Description xmlns:pdfaid=.*?</rdf:Description>\s*', "", x, flags=_re.S)
    # PDF/UA-1
    if not m["xmp_pdfua"]:
        x = x.replace("      <pdfuaid:part>1</pdfuaid:part>\n", "")
        x = _re.sub(r'<rdf:li rdf:parseType="Resource">\s*<pdfaSchema:schema>PDF/UA Universal Accessibility Schema</pdfaSchema:schema>.*?</rdf:li>\s*', "", x, flags=_re.S)
    doc.set_xml_metadata(x)   # PyMuPDF 接受 str，内部自行 utf-8 编码

def apply_meta(doc, meta=None, *, ctx=None):
    """写入 PDF 元数据；创建时间留空时不写入。"""
    ctx = ctx if ctx is not None else _render_context()
    m = dict(ctx.meta)
    for k, v in (meta or {}).items():
        if k not in m:
            continue
        m[k] = v if isinstance(v, bool) else (str(v) if v else m[k])
    md = {}
    if m["creationDate"]:
        md["creationDate"] = m["creationDate"]   # 默认不写入修改时间
    if m["title"]:    md["title"] = m["title"]
    if m["author"]:   md["author"] = m["author"]
    if m["subject"]:  md["subject"] = m["subject"]
    if m["keywords"]: md["keywords"] = m["keywords"]
    if m["producer"]:
        md["producer"] = m["producer"]
    if m["creator"]:
        md["creator"] = m["creator"]
    doc.set_metadata(md)
    _apply_xmp(doc, m)

def _subset_cjk_font(doc, *, fallback_used=False):
    """Shrink only the fallback font program, preserving PDF text and resources.

    MuPDF's native subsetter does not handle every TTC/CFF fallback. Its
    fontTools fallback replaces font dictionaries and drops the .notdef outline.
    Instead retain glyph IDs and replace only the existing embedded font stream.
    ASCII is retained for MuPDF's inferred space metrics, even in CJK-only runs.
    Names, widths, ToUnicode, content streams and metadata remain untouched.
    Subsetting is optional: unsupported fonts keep their original full program.
    """
    import io
    import warnings
    from fontTools import subset

    fonts = {f[0] for page in doc for f in page.get_fonts() if f[4] == _CJK}
    if not fonts:
        return
    traces = [span for page in doc for span in page.get_texttrace()]
    for xref in fonts:
        try:
            original = doc.extract_font(xref)[-1]
            with _TT(io.BytesIO(original)) as font:
                name = font['name'].getDebugName(6)
                matching = [span for span in traces if span['font'] == name]
                # Never guess glyph usage when a renderer reports another name.
                if not name or (fallback_used and not matching):
                    continue
                glyphs = {ch[1] for span in matching for ch in span['chars']}
                options = subset.Options()
                options.retain_gids = True
                options.notdef_outline = True
                options.layout_features = ['*']
                options.passthrough_tables = True
                subsetter = subset.Subsetter(options=options)
                subsetter.populate(gids=glyphs | {0}, unicodes=range(32, 127))
                subsetter.subset(font)
                output = io.BytesIO()
                font.save(output)
                reduced = output.getvalue()
            if len(reduced) >= len(original):
                continue
            descendants = doc.xref_get_key(xref, 'DescendantFonts')
            if descendants[0] != 'array':
                continue
            descendant = int(descendants[1].split()[0][1:])
            descriptor = int(doc.xref_get_key(descendant, 'FontDescriptor')[1].split()[0])
            for key in ('FontFile2', 'FontFile3'):
                kind, value = doc.xref_get_key(descriptor, key)
                if kind == 'xref':
                    stream = int(value.split()[0])
                    doc.update_stream(stream, reduced)
                    if doc.xref_get_key(stream, 'Length1')[0] != 'null':
                        doc.xref_set_key(stream, 'Length1', str(len(reduced)))
                    break
        except Exception as exc:
            warnings.warn(f'C1 fallback font kept in full: {exc}', RuntimeWarning, stacklevel=2)


def build(meta=None, *, data=None, watermark=None):
    """Build a document from a private request snapshot.

    ``meta`` remains positional for legacy callers. Omitted data/watermark use
    module defaults; explicit values never replace those module attributes.
    The caller owns the returned document and must close it.
    """
    meta = deepcopy(meta)
    ctx = _render_context(data, watermark)
    txs = ctx.data["transactions"]
    table_on_p1 = None
    # 先用布局函数判断（不渲染）
    L = page1_layout(ctx=ctx)
    table_on_p1 = (L["table_top"] + 59.025 <= PAGE_BOTTOM_LIMIT)
    n_tx_pages = _estimate_tx_pages(table_on_p1, ctx=ctx)
    total = 1 + n_tx_pages + 1

    doc = fitz.open()
    try:
        table_on_p1 = page_summary(doc, 1, total, ctx=ctx)

        # 交易分页
        pages, cur_rows, cur_top, cur_first = [], [], 126.525, 0
        top = 126.525 + (0 if table_on_p1 else 20.25)
        for i, t in enumerate(txs):
            rh = 20.25 + (7.9875 if t.get("memo") else 0)
            if top + rh > 712 and cur_rows:
                pages.append((cur_rows, cur_first))
                cur_rows, top = [], 126.525
                cur_first = i
            cur_rows.append(t)
            top += rh
        if cur_rows:
            pages.append((cur_rows, cur_first))

        for idx, (rows, first) in enumerate(pages):
            page_transactions(doc, 2 + idx, total, rows, first,
                              opening_on_this_page=not table_on_p1 and idx == 0, ctx=ctx)
        page_notes(doc, total, total, ctx=ctx)
        apply_meta(doc, meta, ctx=ctx)
        _subset_cjk_font(doc, fallback_used=bool(ctx.fallback_used))
        return doc
    except BaseException:
        doc.close()
        raise


def main():
    autodetect_cjk()
    doc = build(meta=None)
    out = "C1-Personal-Statement.pdf"
    doc.save(out, garbage=4, deflate=True)
    print(f"wrote {out} ({doc.page_count} pages)")

if __name__ == "__main__":
    main()
