#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
银行月结单（对账单）模板生成器 — 复刻自原稿版式（Letter 612x792, Myriad Pro 声明字体）。
用法:
    python3 generate.py <data.json> <output.pdf> [--watermark] [--no-mark]

说明:
- 版式坐标、字号、线宽、颜色均按原稿 1:1 复刻（见 layout_static.json）。
- 文字内容全部来自 data.json；静态文案中的银行名称/电话/地址按 data.json
  的 bank 字段做字符串替换，便于演示与测试。
- 交易流水超过 10 条自动分页：续表页复刻原表头样式，每页最多 55 条；
  月费区块在溢出时整体后移到独立页；信息/披露/工作表页顺延，页码自动计算。
- --watermark 在每页叠加居中大号 45° SAMPLE 水印。
"""
import json, os, sys, re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
import fitz

BASE = os.path.dirname(os.path.abspath(__file__))
W, H = 612.0, 792.0
GRAY = (0.808, 0.808, 0.808)   # 原稿浅灰规则线 #CECECE
BLACK = (0, 0, 0)

FONT_FILES = {
    "MyriadPro-Regular": "MyriadPro-Regular.otf",
    "MyriadPro-Bold":    "MyriadPro-Bold.otf",
    "MyriadPro-It":      "MyriadPro-It.otf",
    "MyriadPro-BoldIt":  "MyriadPro-BoldIt.otf",
}
_font_cache = {}

def get_font(name):
    if name not in _font_cache:
        path = os.path.join(BASE, "fonts", FONT_FILES[name])
        _font_cache[name] = fitz.Font(fontfile=path)
    return _font_cache[name]

def tlen(text, font_name, size):
    return get_font(font_name).text_length(text, fontsize=size)

def insert(page, x, y, text, font_name, size, color=BLACK, hscale=None):
    morph = None
    if hscale and abs(hscale - 1.0) > 0.003:
        # 水平缩放至原稿 span 实际宽度（原稿排版带光学字距，比字体裸度量略窄）
        morph = (fitz.Point(x, y), fitz.Matrix(hscale, 1.0))
    page.insert_text(
        fitz.Point(x, y), text, fontsize=size,
        fontname=font_name, fontfile=os.path.join(BASE, "fonts", FONT_FILES[font_name]),
        color=color, morph=morph,
    )

def line(page, x0, y0, x1, y1, width, color=BLACK):
    shape = page.new_shape()
    shape.draw_line(fitz.Point(x0, y0), fitz.Point(x1, y1))
    shape.finish(color=color, width=width)
    shape.commit()

# ---------------------------------------------------------------- 数据与替换
def build_subs(d):
    bank = d["bank"]
    legal_short = bank["legal_na"].removesuffix(", N.A.")  # "Wells Fargo Bank" / "Sample Bank"
    return [
        ("Wells Fargo Bank, N.A.", bank["legal_na"]),
        ("Wells Fargo Bank",       legal_short),
        ("Wells Fargo",            bank["name"]),
        ("wellsfargo.com",         bank["domain"]),
        ("1-800-TO-WELLS",         bank["phone_name"]),
        ("1-800-TO WELLS",         bank["phone_name"]),
        ("(1-800-869-3557)",       bank["phone_main"]),
        ("1-877-727-2932",         bank["phone_es"]),
        ("P.O. Box 6995",          bank["po_box"]),
        ("PO Box 50947",           bank["po_box2"]),
        ("MAC F2304-019",          bank["mac_code"]),
        ("97228-6995",             bank["zip_main"]),
        ("Des Moines, IA 50340",   bank["des_moines"]),
        ("©2021",                  "©" + str(bank["copyright_year"])),
        ("N.A. (115)",             "N.A. (" + bank["office_code"] + ")"),
        ("NMLSR ID 399801",        "NMLSR ID " + bank["nmlsr_id"]),
    ]

def apply_subs(text, subs):
    for old, new in subs:
        if old != new:
            text = text.replace(old, new)
    return text

# ---------------------------------------------------------------- 版式数据
with open(os.path.join(BASE, "layout_static.json"), encoding="utf-8") as fh:
    LAYOUT = json.load(fh)

# 第 2 页静态内容按交易表上/下分区：溢出分页时月费区块整体移到独立页
_SP2 = LAYOUT["spans"]["1"]
_L2 = LAYOUT["lines"]["1"]
P2_UPPER_SPANS = [s for s in _SP2 if s[4] < 395]
P2_LOWER_SPANS = [s for s in _SP2 if s[4] >= 395]
P2_UPPER_LINES = [l for l in _L2 if l[1] < 400]
P2_LOWER_LINES = [l for l in _L2 if l[1] >= 400]
# 交易表脚注（斜体两行）：位置随表格结束点动态渲染，溢出时跟到末页 Totals 之后
FOOTNOTE_SPANS = [s for s in P2_UPPER_SPANS if s[1] == "MyriadPro-It" and 350 <= s[4] <= 380]
P2_UPPER_SPANS = [s for s in P2_UPPER_SPANS if s not in FOOTNOTE_SPANS]
FOOTNOTE_BASE0 = min(s[4] for s in FOOTNOTE_SPANS) if FOOTNOTE_SPANS else 356.25
# 月费区块首行（节标题）基线；独立页时整体上移至标准节标题位
FEE_HEAD_BASE = min(s[4] for s in P2_LOWER_SPANS) if P2_LOWER_SPANS else 402.0
FEE_DY = 105.75 - FEE_HEAD_BASE
PAGES_STATIC = {
    "cover":   (LAYOUT["spans"]["0"], LAYOUT["lines"]["0"]),
    "info":    (LAYOUT["spans"]["2"], LAYOUT["lines"]["2"]),
    "disc":    (LAYOUT["spans"]["3"], LAYOUT["lines"]["3"]),
    "work":    (LAYOUT["spans"]["4"], LAYOUT["lines"]["4"]),
}
# 交易表列头（斜体 8pt），续表页复用并整体上移
HEADER_SPANS = [s for s in P2_UPPER_SPANS if s[1] == "MyriadPro-It" and 259 <= s[4] <= 277]

# ---------------------------------------------------------------- 渲染部件
def render_spans(page, spans, subs, dy=0.0):
    for text, font, size, x, y, orig_w in spans:
        out = apply_subs(text, subs)
        if out == text:
            natural = tlen(text, font, size)
            insert(page, x, y + dy, text, font, size,
                   hscale=(orig_w / natural if natural else 1.0))
        else:
            insert(page, x, y + dy, out, font, size)

def render_lines(page, lines, dy=0.0):
    for x0, y0, x1, y1, w, col in lines:
        line(page, x0, y0 + dy, x1, y1 + dy, w, tuple(col))

def render_fills(page, pno_key, dy=0.0):
    """原稿中的实心图形（如披露页节标题前的小黑方块）。"""
    for x0, y0, x1, y1 in LAYOUT.get("fills", {}).get(str(pno_key), []):
        shape = page.new_shape()
        shape.draw_rect(fitz.Rect(x0, y0 + dy, x1, y1 + dy))
        shape.finish(color=BLACK, fill=BLACK, width=0.75)
        shape.commit()

def render_logo(page):
    page.insert_image(fitz.Rect(516.0, 21.8, 564.8, 70.5),
                      filename=os.path.join(BASE, "assets", "logo.jpeg"))

def render_date_header(page, page_no, total_pages, d, mark=True):
    date = d["statement_date"]
    base_y = 54.0 if page_no == 1 else 34.5
    mark_y = 48.0 if page_no == 1 else 28.5
    insert(page, 36, base_y, date, "MyriadPro-Regular", 10)
    mx1 = None
    if mark:
        # 黑色方块跟在日期文字之后（原稿为邮件分拣标记），页码标签跟随方块之后
        mx = 36 + tlen(date, "MyriadPro-Regular", 10) + 5.15
        mx1 = mx + 5.25
        shape = page.new_shape()
        shape.draw_rect(fitz.Rect(mx, mark_y, mx1, mark_y + 6.0))
        shape.finish(color=BLACK, fill=BLACK, width=0.75)
        shape.commit()
    gap = 5.25 if page_no == 1 else 6.75
    px = (mx1 + gap) if mx1 is not None else (105.0 if page_no == 1 else 106.5)
    insert(page, px, base_y, "Page %d of %d" % (page_no, total_pages),
           "MyriadPro-Regular", 10)

def render_page1(page, d):
    insert(page, 36, 41.25, "%s %s" % (d["bank"]["name"], d["account"]["product"]),
           "MyriadPro-Bold", 20)
    addr = d["customer"]
    insert(page, 36, 177.75, addr["name"], "MyriadPro-Regular", 10)
    ys = (189.0, 200.25, 211.5, 222.75)
    for i, ln in enumerate(addr["address_lines"][:4]):
        insert(page, 36, ys[i], ln, "MyriadPro-Regular", 10)

def render_summary(page, d):
    s = d["summary"]
    insert(page, 64.5, 118.5, "Beginning balance on %s " % s["period_start"],
           "MyriadPro-Regular", 8)
    insert(page, 64.5, 156.75, "Ending balance on %s  " % s["period_end"],
           "MyriadPro-Bold", 8)
    # 金额右对齐（锚点按原稿校准：含原排版尾随空格的实际右缘）
    for text, y, font in ((s["begin_amount"], 118.5, "MyriadPro-Regular"),
                          (s["deposits_amount"], 130.5, "MyriadPro-Regular"),
                          (s["withdrawals_amount"], 142.5, "MyriadPro-Regular"),
                          (s["ending_amount"], 156.75, "MyriadPro-Bold")):
        x = 326.8 - tlen(text, font, 8)
        insert(page, x, y, text, font, 8)
    a = d["account"]
    insert(page, 419.25, 102.75, "%s  %s" % (a["number"], a["primary_label"]),
           "MyriadPro-Bold", 9)
    insert(page, 356.25, 115.5, d["customer"]["name"].upper(), "MyriadPro-Bold", 8)
    insert(page, 356.25, 152.25, "Routing Number (RTN):  %s " % a["rtn"],
           "MyriadPro-Regular", 8)

# 交易表参数（坐标均取自原稿）
FIRST_BASE = 284.25          # 第 2 页首行基线
PITCH = 9.0                  # 行距
PAGE2_ROWS = 42              # 第 2 页流水容量（页面物理上限：末行基线 653 + Totals/脚注）
FEE_ON_PAGE2_MAX = 11        # 流水不超过此数时月费区块可排在流水下方（同页）
FEE_GAP = FEE_HEAD_BASE - (FIRST_BASE + PITCH + 15.75)  # 原稿 Totals→月费标题固定间距
CONT_BASE = 145.5            # 续表页首行基线
CONT_CAP = 55                # 续表页容量
CONT_SHIFT = FIRST_BASE - CONT_BASE   # 列头整体上移量
COL = dict(date=61.5, desc=142.5, deposit=411.75, withdrawal=489.0, balance=537.75)

def render_tx_rows(page, rows, first_base, is_final, totals):
    """渲染一组交易行；is_final 时在末行下画粗黑线并渲染 Totals 与脚注。返回 Totals 基线。"""
    b = first_base
    for i, tx in enumerate(rows):
        insert(page, COL["date"], b, tx["date"] + "  ", "MyriadPro-Regular", 8)
        insert(page, COL["desc"], b, " " + tx["desc"], "MyriadPro-Regular", 8)
        if tx.get("deposit"):
            insert(page, COL["deposit"], b, tx["deposit"] + "    ", "MyriadPro-Regular", 8)
        if tx.get("withdrawal"):
            insert(page, COL["withdrawal"], b, tx["withdrawal"] + "    ", "MyriadPro-Regular", 8)
        if tx.get("balance"):
            insert(page, COL["balance"], b, tx["balance"] + "    ", "MyriadPro-Regular", 8)
        line(page, 61.5, b + 2.25, 567.75, b + 2.25, 0.75, GRAY)  # 原稿每行（含末行）下方均有灰线
        b += PITCH
    last_b = b - PITCH if rows else first_base - 9.0
    totals_b = None
    if is_final:
        line(page, 61.5, last_b + 6.0, 567.75, last_b + 6.0, 2.25, BLACK)
        totals_b = last_b + 15.75
        insert(page, 61.5, totals_b, "Totals ", "MyriadPro-Bold", 8)
        if totals.get("deposits"):
            insert(page, 406.5, totals_b, totals["deposits"] + " ", "MyriadPro-Bold", 8)
        if totals.get("withdrawals"):
            insert(page, 483.75, totals_b, totals["withdrawals"] + " ", "MyriadPro-Bold", 8)
    return last_b, totals_b

def render_footnote(page, subs, totals_b):
    """交易表脚注：固定跟随 Totals 之后（原稿 2 行流水的间距即 47.25pt）。"""
    if not FOOTNOTE_SPANS:
        return
    render_spans(page, FOOTNOTE_SPANS, subs, dy=totals_b + 47.25 - FOOTNOTE_BASE0)

def render_cont_header(page, subs):
    """续表页顶部：细黑线 + 标题（不带 continued）+ 原样式列头 + 列头下细黑线。"""
    line(page, 36.0, 94.5, 567.0, 94.5, 0.75, BLACK)
    insert(page, 36, 105.75, "Transaction history ", "MyriadPro-Bold", 11)
    render_spans(page, HEADER_SPANS, subs, dy=-CONT_SHIFT)
    line(page, 61.5, 277.5 - CONT_SHIFT, 567.75, 277.5 - CONT_SHIFT, 0.75, BLACK)

def render_fees(page, d, dy=0.0):
    f = d["fees"]
    insert(page, 63.0, 447.0 + dy, "Fee period: %s " % f["period"], "MyriadPro-Regular", 8)
    insert(page, 282.75, 447.0 + dy, "Standard monthly service fee %s " % f["standard"],
           "MyriadPro-Regular", 8)
    insert(page, 459.0, 447.0 + dy, "You paid %s " % f["paid"], "MyriadPro-Regular", 8)

def add_watermark(page):
    """页面中央一个大号 45° S A M P L E。"""
    import math
    f = "MyriadPro-Bold"
    size = 100
    txt = "S A M P L E"
    w = tlen(txt, f, size)
    cx, cy = W / 2, H / 2
    # 沿 45° 视觉方向退半个字宽使整体居中；+17.7/+17.65 为 morph 旋转实测校准
    dx, dy = math.cos(math.radians(45)), -math.sin(math.radians(45))
    x = cx - w / 2 * dx + 17.7
    y = cy - w / 2 * dy + 17.65
    page.insert_text(
        fitz.Point(x, y), txt, fontsize=size,
        fontname=f, fontfile=os.path.join(BASE, "fonts", FONT_FILES[f]),
        color=(0.78, 0.78, 0.78), fill_opacity=0.32,
        morph=(fitz.Point(x, y), fitz.Matrix(45)),
    )

# ---------------------------------------------------------------- 金额自动计算
def _to_cents(v):
    """宽松解析金额（$、逗号、空格、负号），返回整数分；解析失败按 0。"""
    t = str(v or "").replace("$", "").replace(",", "").replace(" ", "").strip()
    if not t:
        return 0
    try:
        return int(Decimal(t).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100)
    except InvalidOperation:
        return 0

def _fmt_plain(cents):
    """千分位 + 两位小数：185053 -> '1,850.53'（负数带 -）。"""
    neg = cents < 0
    c = abs(int(cents))
    return ("-" if neg else "") + "{:,}.{:=02d}".format(c // 100, c % 100)

def _fmt_usd(cents):
    """带 $：367018 -> '$3,670.18'；负数 '-$12.34'。"""
    return ("-" if cents < 0 else "") + "$" + _fmt_plain(abs(int(cents)))

def apply_auto_calc(d):
    """summary.auto（默认开）时，由期初余额与流水自动推导：
    逐行余额、存款/取款合计、期末余额、Totals。存入/取出原样显示，仅参与计算。"""
    s = d.get("summary") or {}
    if not s.get("auto", True):
        return d
    txs = d.get("transactions") or []
    begin = _to_cents(s.get("begin_amount"))
    dep_c = sum(_to_cents(tx.get("deposit")) for tx in txs)
    wd_c = sum(_to_cents(tx.get("withdrawal")) for tx in txs)
    bal = begin
    for tx in txs:
        bal += _to_cents(tx.get("deposit")) - _to_cents(tx.get("withdrawal"))
        tx["balance"] = _fmt_plain(bal)
    s["deposits_amount"] = _fmt_plain(dep_c)
    s["withdrawals_amount"] = ("-  " if wd_c >= 0 else "") + _fmt_plain(wd_c)
    s["ending_amount"] = _fmt_usd(bal)
    t = d.setdefault("totals", {})
    t["deposits"] = _fmt_usd(dep_c)
    t["withdrawals"] = _fmt_usd(wd_c)
    return d

# ---------------------------------------------------------------- 声明字体方案
# 与原稿逐对象一致：简单 TrueType/Type1 + WinAnsi/MacRoman 声明（无 FontFile），
# 内容流由 CID(Identity-H) 改写为 WinAnsi 单字节。资产提取自原稿（declared_fonts.py）。
try:
    from declared_fonts import DECLARED_FONTS, ENCODING_OBJ, META_DEFAULTS
except ImportError:  # 允许独立运行于无资产环境
    DECLARED_FONTS, ENCODING_OBJ, META_DEFAULTS = {}, "", {}

# 我们的嵌入字体名 → 原稿资源键
_OUR_TO_DECLARED = {
    "Myriad Pro Regular": "F7",
    "Myriad Pro Bold": "F10",
    "Myriad Pro Italic": "F215",
    "Myriad Pro BoldItalic": "F184",
}

def _tounicode_map(doc, font_xref):
    """解析字体的 ToUnicode CMap → {CID: unicode 字符}。"""
    t = doc.xref_get_key(font_xref, "ToUnicode")
    if t[0] != "xref":
        return {}
    tx = int(re.search(r"(\d+)", t[1]).group(1))
    data = doc.xref_stream(tx).decode("latin-1", "replace")
    cmap = {}
    for sec in re.findall(r"beginbfchar(.*?)endbfchar", data, re.S):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", sec):
            cmap[int(src, 16)] = "".join(
                chr(int(dst[i:i + 4], 16)) for i in range(0, len(dst), 4))
    for sec in re.findall(r"beginbfrange(.*?)endbfrange", data, re.S):
        for lo, hi, dst in re.findall(
                r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", sec):
            base = int(dst, 16)
            if base <= 0xFFFF:  # 单目标码的连续区间
                for k in range(int(lo, 16), int(hi, 16) + 1):
                    cmap[k] = chr(base + k - int(lo, 16))
    return cmap

def _declared_font_objects(doc):
    """按原稿对象源码创建全部 11 个字体对象（含未使用者）+ 描述符 + 共享编码字典。"""
    enc_x = doc.get_new_xref()
    doc.update_object(enc_x, ENCODING_OBJ)
    font_xrefs = {}
    for key, spec in DECLARED_FONTS.items():
        desc_x = None
        if spec.get("desc"):
            desc_x = doc.get_new_xref()
            doc.update_object(desc_x, spec["desc"])
        body = spec["font"].replace("@DESC@", f"{desc_x} 0 R" if desc_x else "null")
        body = body.replace("@ENC@", f"{enc_x} 0 R")
        fx = doc.get_new_xref()
        doc.update_object(fx, body)
        font_xrefs[key] = fx
    return font_xrefs

# 原稿的隐形文字运行（空白字符，不影响视觉但使字体在对应页"被使用"）
_BLANK_COVER = [            # 封面页
    ("F77", 6.0, 100.50, 54.00, "    "),    # ZapfDingbats（邮件分拣字符位）
    ("F184", 9.0, 420.75, 213.75, " "),     # MyriadPro-BoldIt 唯一使用处
]
_BLANK_OTHER = [            # 其余各页
    ("F77", 6.0, 100.50, 34.50, "     "),
]
_BLANK_WORK = [             # 工作表页（末页）
    ("F149", 10.0, 501.48, 488.25, "  "),   # MyriadPro-Black 唯一使用处
]

def _blank_ops(runs):
    out = b""
    for key, size, x, y, txt in runs:
        out += (f"\nBT\n/{key} {size:g} Tf\n1 0 0 1 {x:g} {y:g} Tm\n"
                .encode() + b"(" + txt.encode() + b") Tj\nET\n")
    return out

def apply_declared_fonts(doc):
    """把嵌入字体版转换为原稿同构的声明字体版。"""
    font_xrefs = _declared_font_objects(doc)
    n = len(doc)
    for i, page in enumerate(doc):
        fonts = page.get_fonts(full=True)          # (xref, ext, type, name, key, enc, ..)
        keymap, cidmaps = {}, {}
        for f in fonts:
            tgt = _OUR_TO_DECLARED.get(f[3])
            if tgt:
                keymap[f[4]] = tgt
                cidmaps[f[4]] = _tounicode_map(doc, f[0])
        state = {"font": None}
        # 资源名可含连字符/点（如 /MyriadPro-Regular）
        token = re.compile(rb"(/[A-Za-z0-9.\-+#]+)\s+[-\d.]+\s+Tf|<([0-9A-Fa-f]+)>")

        def _repl(m):
            if m.group(1):                          # Tf：字体资源键改为原稿键名
                name = m.group(1)[1:].decode("latin-1")
                state["font"] = name
                tgt = keymap.get(name)
                if tgt:
                    return b"/" + tgt.encode() + m.group(0)[len(m.group(1)):]
                return m.group(0)
            key = state["font"]                     # 十六进制 CID 串 → WinAnsi 字面串
            if key in cidmaps:
                hx = m.group(2).decode()
                s = "".join(cidmaps[key].get(int(hx[i:i + 4], 16), "\x00")
                           for i in range(0, len(hx), 4))
                lit = (s.encode("cp1252", "replace")
                        .replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)"))
                return b"(" + lit + b")"
            return m.group(0)

        for sx in page.get_contents():
            doc.update_stream(sx, token.sub(_repl, doc.xref_stream(sx)))
        # 追加原稿的隐形空白运行（ZapfDingbats 每页 / BoldIt 封面 / Black 末页）
        runs = _BLANK_COVER if i == 0 else _BLANK_OTHER
        if i == n - 1:
            runs = runs + _BLANK_WORK
        last_sx = page.get_contents()[-1]
        doc.update_stream(last_sx, doc.xref_stream(last_sx) + _blank_ops(runs))
        # /Resources 常为间接对象，需定位其 xref 后直接替换 /Font
        res = doc.xref_get_key(page.xref, "Resources")
        target = (int(re.search(r"(\d+)", res[1]).group(1))
                  if res[0] == "xref" else page.xref)
        fontdict = " ".join(f"/{k} {v} 0 R" for k, v in sorted(font_xrefs.items()))
        doc.xref_set_key(target, "Font", f"<< {fontdict} >>")

# ---------------------------------------------------------------- 元数据
def build_metadata(d):
    """组装 PDF Info 字典。完全以 data.metadata（即编辑器字段）为准：
    有值就写、留空不写；data 中无 metadata 段时才回退到原稿默认值。
    creation_date 支持 "YYYY-MM-DD HH:MM:SS[±HH:MM]"，带偏移时输出原稿同款
    D:...+HH'mm' 格式；modDate 与 creationDate 同值（复刻原稿行为）。"""
    if not isinstance(d.get("metadata"), dict):
        # metadata 段缺失：沿用原稿默认（producer/日期），标题等原稿本无则不写
        out = {}
        for k in ("producer", "creationDate", "modDate"):
            if META_DEFAULTS.get(k):
                out[k] = META_DEFAULTS[k]
        return out
    m = d["metadata"]
    out = {}
    for key in ("title", "author", "subject", "keywords", "producer", "creator"):
        v = str(m.get(key, "") or "").strip()
        if v:
            out[key] = v
    cd = str(m.get("creation_date", "") or "").strip()
    if cd:
        native = re.fullmatch(r"D:?(\d{14})(?:([+-]\d{2})'(\d{2})'|Z)?", cd)
        if native:
            # PDF 原生格式直通：D:YYYYMMDDHHMMSS[±HH'mm'|Z]
            tz = (f"{native.group(2)}'{native.group(3)}'" if native.group(2)
                  else ("Z" if cd.endswith("Z") else ""))
            out["creationDate"] = "D:" + native.group(1) + tz
        else:
            t = cd[2:] if cd.startswith("D:") else cd
            t = t.replace("T", " ")
            tz = ""
            mt = re.search(r"([+-]\d{2}):?(\d{2})$", t)
            if mt:
                tz = f"{mt.group(1)}'{mt.group(2)}'"
                t = t[:mt.start()].rstrip()
            elif t.endswith("Z"):
                tz = "Z"
                t = t[:-1].rstrip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                        "%Y%m%d%H%M%S"):  # 裸 14 位数字兜底
                try:
                    stamp = datetime.strptime(t, fmt).strftime("D:%Y%m%d%H%M%S")
                    out["creationDate"] = stamp + (tz if tz and tz != "Z" else "Z")
                    break
                except ValueError:
                    continue
    if out.get("creationDate"):
        out["modDate"] = out["creationDate"]   # 原稿两者同值
    return out

# ---------------------------------------------------------------- 主流程
def build_pdf(d, out_path, watermark=True, mark=True, fonts="declared"):
    """根据数据字典生成对账单 PDF（可被 CLI 或编辑器调用）。
    fonts: "declared"（声明字体，默认，与原稿同构）或 "embedded"（嵌入字体，视觉基准）。"""
    # 编辑器表单可能传回字符串数字，这里防御性转型
    for box, keys in ((d, ["total_pages"]), (d.get("bank", {}), ["copyright_year"])):
        for k in keys:
            if k in box:
                try: box[k] = int(str(box[k]).strip())
                except ValueError: pass
    apply_auto_calc(d)
    subs = build_subs(d)
    txs = list(d.get("transactions", []))

    # 分页规划：流水尽量装满第 2 页；月费装不下时另起一页；超出 42 行才产生续表页
    n = len(txs)
    if n <= PAGE2_ROWS:
        first_rows, rest = txs, []
    else:
        first_rows, rest = txs[:PAGE2_ROWS], txs[PAGE2_ROWS:]
    cont_chunks = [rest[i:i + CONT_CAP] for i in range(0, len(rest), CONT_CAP)]
    fee_sep = bool(cont_chunks) or n > FEE_ON_PAGE2_MAX
    total_pages = 2 + len(cont_chunks) + (1 if fee_sep else 0) + 3

    doc = fitz.open()
    def new_page(page_no, logo=True):
        page = doc.new_page(width=W, height=H)
        if logo:
            render_logo(page)
        render_date_header(page, page_no, total_pages, d, mark)
        return page

    # 第 1 页：封面
    page = new_page(1)
    render_lines(page, PAGES_STATIC["cover"][1])
    render_spans(page, PAGES_STATIC["cover"][0], subs)
    render_page1(page, d)

    # 第 2 页：期间摘要 + 交易表（尽量装满；≤11 行时月费区块排在流水下方）
    page = new_page(2)
    render_lines(page, P2_UPPER_LINES)
    render_spans(page, P2_UPPER_SPANS, subs)
    render_summary(page, d)
    _, totals_b = render_tx_rows(page, first_rows, FIRST_BASE,
                                 is_final=not cont_chunks, totals=d["totals"])
    if not cont_chunks:
        render_footnote(page, subs, totals_b)
        if not fee_sep:
            # 月费区块跟随流水下方（dy 相对原稿位置 = Totals 相对原稿 309 的位移）
            dy = totals_b - (FIRST_BASE + PITCH + 15.75)
            render_lines(page, P2_LOWER_LINES, dy=dy)
            render_spans(page, P2_LOWER_SPANS, subs, dy=dy)
            render_fees(page, d, dy=dy)
            tip = fitz.Rect(37.5, 642.0 + dy, 60.0, 665.2 + dy)
            page.insert_image(tip, filename=os.path.join(BASE, "assets", "icon_tip.jpeg"))

    # 续表页（>42 行时；末页带 Totals + 脚注）
    no = 3
    for ci, chunk in enumerate(cont_chunks):
        page = new_page(no); no += 1
        render_cont_header(page, subs)
        _, totals_b = render_tx_rows(page, chunk, CONT_BASE,
                                     is_final=(ci == len(cont_chunks) - 1),
                                     totals=d["totals"])
        if ci == len(cont_chunks) - 1:
            render_footnote(page, subs, totals_b)

    # 月费独立页（流水装不下时另起一页，区块上移至标准节标题位）
    if fee_sep:
        page = new_page(no); no += 1
        render_lines(page, P2_LOWER_LINES, dy=FEE_DY)
        render_spans(page, P2_LOWER_SPANS, subs, dy=FEE_DY)
        render_fees(page, d, dy=FEE_DY)
        tip = fitz.Rect(37.5, 642.0 + FEE_DY, 60.0, 665.2 + FEE_DY)
        page.insert_image(tip, filename=os.path.join(BASE, "assets", "icon_tip.jpeg"))

    # 信息页 / 披露页 / 工作表页
    for key, pkey, icon in (("info", 2, ("check", fitz.Rect(36.0, 105.8, 60.0, 129.8))),
                            ("disc", 3, None),
                            ("work", 4, ("housing", fitz.Rect(531.0, 693.0, 558.0, 720.0)))):
        page = new_page(no); no += 1
        render_lines(page, PAGES_STATIC[key][1])
        render_spans(page, PAGES_STATIC[key][0], subs)
        render_fills(page, pkey)
        if icon:
            page.insert_image(icon[1], filename=os.path.join(
                BASE, "assets", {"check": "icon_check.png", "housing": "icon_housing.jpeg"}[icon[0]]))

    for page in doc:
        if watermark:
            add_watermark(page)
    if fonts == "declared":
        apply_declared_fonts(doc)
    meta = build_metadata(d)
    if meta:
        doc.set_metadata(meta)
    doc.save(out_path, garbage=4, deflate=True)
    return out_path

def main():
    data_path, out_path = sys.argv[1], sys.argv[2]
    watermark = "--watermark" in sys.argv
    mark = "--no-mark" not in sys.argv
    fonts = "embedded" if "--fonts=embedded" in sys.argv else "declared"
    with open(data_path, encoding="utf-8") as fh:
        d = json.load(fh)
    build_pdf(d, out_path, watermark, mark, fonts)
    print("written:", out_path)

if __name__ == "__main__":
    main()
