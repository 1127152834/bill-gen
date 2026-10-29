#!/usr/bin/env python3
"""
Demo Bank statement template — 1:1 layout replica of the Mercury-style statement,
rebuilt with fictional data for design/demo purposes only.

Fidelity engine:
- Fonts rebuilt from the source PDF's embedded subsets (identical outlines/advances).
- Text positioned via HarfBuzz shaping (same engine Chromium uses), with GPOS
  kerning enabled/disabled per context exactly as in the source:
    page 1 + page 2 title/headers: kern ON
    page 2 transaction rows + Total: kern OFF
- Footer: kern ON + letter-spacing 0.025em (both measured from the source).
- All baselines, pen positions and right edges are exact values from the source.

Edit the DATA dict and run:  python3 generate_statement.py
"""
import fitz, os
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
    inter = _CMAP[_VAR] & _CMAP[_480]
    need = any(ord(ch) not in inter and ord(ch) in _CJK_CMAP
               for text in collect_texts(d) for ch in text)
    font_cjk = FONT_CJK_BASE if need else None
    return RenderContext(d, WATERMARK if watermark is None else watermark,
                         deepcopy(META), font_cjk)


BASE = os.path.dirname(os.path.abspath(__file__))
FONT_VAR = os.path.join(BASE, "assets/rebuilt_var.ttf")
FONT_480 = os.path.join(BASE, "assets/rebuilt_480.ttf")
FONT_480_TOTAL = os.path.join(BASE, "assets/rebuilt_480_total.ttf")  # Total 行专用名（与原稿一致）
FONT_SYM = os.path.join(BASE, "assets/symbols.ttf")
LOGO_PDF = os.path.join(BASE, "assets/logo.pdf")

FOOTER_LS = 0.025            # em — CSS letter-spacing on footer (measured)

# type icons (PUA codepoints of the source symbol font)
ICON_KEYS = {
    "achIn": 0xea01, "achOut": 0xea02, "biArrow": 0xea03, "card": 0xea04,
    "infoCircle": 0xea05, "mercury": 0xea06, "shield": 0xea07, "wire": 0xea08,
}
ICON_ALIAS = {  # 旧数据兼容：按类型文本猜图标
    "ACH In": "achIn", "ACH Out": "achOut", "Card": "card",
    "Wire In": "wire", "Wire Out": "wire", "Fee": "achOut",
}

def _icon_cp(t):
    key = t.get("icon") or ICON_ALIAS.get(t.get("type", ""), "achOut")
    return ICON_KEYS.get(key, 0xea02)

# ---------------------------------------------------------------- palette
INK        = (0x36/255, 0x36/255, 0x44/255)
BLUE       = (0x52/255, 0x66/255, 0xEB/255)
BLUE2      = (0x54/255, 0x66/255, 0xF9/255)
GRAY_LINE  = (0.945, 0.945, 0.957)
ROW_LINE   = (0.937, 0.937, 0.992)
TABLE_HEAD = (0x41/255, 0x41/255, 0x5A/255)
GREEN      = (0x00/255, 0x83/255, 0x75/255)
FOOTER_COL = (0x53/255, 0x54/255, 0x61/255)
TOPBAR     = (0.5529, 0.6431, 0.9608)
BLACK      = (0, 0, 0)

# ---------------------------------------------------------------- data
DEMO_DATA = {'period_label': 'October 2026',
 'period_start': 'Oct 01, 2026',
 'period_end': 'Oct 31, 2026',
 'period_days': '31',
 'timezone': 'All dates in UTC',
 'company': 'Okafor Design Studio LLC',
 'address': ['2104 S 1st St', 'Austin TX 78704'],
 'ein': 'EIN ••3170',
 'account_type': 'Checking',
 'account_number': '410737211347',
 'routing_number': '121140399',
 'beginning_balance': 1000.0,
 'footer_line1': 'Banking services provided by Column N.A., Member FDIC',
 'footer_line2': 'Mercury | help@mercury.com | 2261 Market Street, Suite 86807, San Francisco, CA 94114',
 'transactions': [{'date': 'Oct 2',
                   'desc': 'ACME CORP PAYROLL PPD ID:4289126633',
                   'amount': 250.0,
                   'id': '9401882733',
                   'txid': '9401882733',
                   'memo': '',
                   'type': 'ACH In',
                   'dir': 'in',
                   'icon': 'achIn'},
                  {'date': 'Oct 7',
                   'desc': 'H-E-B #0611 AUSTIN TX',
                   'amount': -35.5,
                   'id': '9401882741',
                   'txid': '9401882741',
                   'memo': '',
                   'type': 'Debit',
                   'dir': 'out',
                   'icon': 'achOut'}]}

# 原稿基线：与 asset 里的样张一字不差，供 parity 回归比对（不要改）。
PARITY_DATA = {'period_label': 'October 2026',
 'period_start': 'Oct 01, 2026',
 'period_end': 'Oct 31, 2026',
 'period_days': '31',
 'timezone': 'All dates in UTC',
 'company': 'EXAMPLE COMPANY LLC',
 'address': ['100 EXAMPLE STREET', 'DEMO CITY CA 90000'],
 'ein': 'EIN ••3170',
 'account_type': 'Checking',
 'account_number': '000000000001',
 'routing_number': '000000000',
 'beginning_balance': 1000.0,
 'footer_line1': 'Banking services provided by Column N.A., Member FDIC',
 'footer_line2': 'Mercury | help@mercury.com | 2261 Market Street, Suite 86807, San Francisco, CA 94114',
 'transactions': [{'date': 'Oct 2',
                   'desc': 'Sample payroll credit',
                   'amount': 250.0,
                   'id': 'DEMO0001',
                   'txid': 'DEMO0001',
                   'memo': '',
                   'type': 'ACH In',
                   'dir': 'in',
                   'icon': 'achIn'},
                  {'date': 'Oct 7',
                   'desc': 'Sample grocery purchase',
                   'amount': -35.5,
                   'id': 'DEMO0002',
                   'txid': 'DEMO0002',
                   'memo': '',
                   'type': 'Debit',
                   'dir': 'out',
                   'icon': 'achOut'}]}

#: 引擎默认数据 = 原稿基线（`build()` 不带 data 时用它，parity 回归依赖这一点）。
DATA = PARITY_DATA



WATERMARK = True

# ================================================================ shaping
class Shaper:
    """HarfBuzz shaper for one font file."""
    def __init__(self, fontpath):
        self.blob = hb.Blob.from_file_path(fontpath)
        self.face = hb.Face(self.blob)
        self.font = hb.Font(self.face)
        self.upem = self.face.upem

    def layout(self, text, size, kern=True, ls_em=0.0):
        """Return ([(char, x_from_start)], total_width) in pt.
        x_from_start includes kern; ls_em adds letter-spacing per gap."""
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.font, buf, {} if kern else {"kern": False})
        s = size / self.upem
        ls = ls_em * size
        out, pen = [], 0.0
        glyphs = list(zip(buf.glyph_infos, buf.glyph_positions))
        for i, (info, pos) in enumerate(glyphs):
            ch = text[info.cluster]      # map glyph back to source char
            out.append((ch, pen + pos.x_offset * s))
            pen += pos.x_advance * s
            if i < len(glyphs) - 1:
                pen += ls
        return out, pen          # pen = width without trailing ls

_shapers = {}
def shaper(fontpath, *, ctx=None):
    ctx = ctx if ctx is not None else _render_context()
    if fontpath not in ctx.shapers:
        ctx.shapers[fontpath] = Shaper(fontpath)
    return ctx.shapers[fontpath]

_VAR = "var"; _480 = "f480"; _CJK = "cjk"
def _fp(which):
    return FONT_VAR if which == _VAR else FONT_480

# ---- CJK fallback (template fonts only cover Latin; user text may be CJK) ----
from fontTools.ttLib import TTFont as _TT
_CMAP = {
    _VAR: set(_TT(FONT_VAR).getBestCmap().keys()),
    _480: set(_TT(FONT_480).getBestCmap().keys()),
}
def _resolve_cjk():
    from runtime_paths import cjk_font_path
    return cjk_font_path()
FONT_CJK_BASE = _resolve_cjk()  # 系统中文字体原始路径（稳定不变）
FONT_CJK = FONT_CJK_BASE        # 当前使用的回退字体（服务端会替换为按需子集）
_CJK_CMAP = set()
if FONT_CJK_BASE:
    try:
        _CJK_CMAP = set(_TT(FONT_CJK_BASE, fontNumber=0).getBestCmap().keys())
    except Exception:
        _CJK_CMAP = set()
_fallback_used = set()          # chars rendered with the fallback font

import json as _json
_KERN_T = None    # 2026 逐(对,字号)字距表（assets/kern_2026.json）
def _kern_tab():
    global _KERN_T
    if _KERN_T is None:
        try:
            _KERN_T = _json.load(open(os.path.join(BASE, "assets", "kern_2026.json")))
        except Exception:
            _KERN_T = {}
    return _KERN_T

_KSIZE_CACHE = {}
def _kern(primary, c1, c2, size):
    """查 2026 字距表（按字体种类+字号分桶）。"""
    tab = _kern_tab().get(primary == _480 and "480" or "var") or {}
    if not tab:
        return 0.0
    sz = round(size, 2)
    if sz not in _KSIZE_CACHE:
        _KSIZE_CACHE[sz] = min(tab.keys(), key=lambda k: abs(float(k) - sz)) \
            if tab else None
    bucket = tab.get(_KSIZE_CACHE[sz]) or {}
    return bucket.get(f"{c1}|{c2}", 0.0) * size

def layout_text(text, size, primary, kern=True, ls_em=0.0, *, ctx=None):
    """排版文本：fitz 步进 + 2026 逐(对,字号)字距表；主字体缺字回退 CJK。
    返回 ([(font, char, x)], width)。"""
    ctx = ctx if ctx is not None else _render_context()
    cm = _CMAP[primary]
    # split into per-font runs (kern only applies inside a run)
    runs = []
    cur_key, cur = None, ""
    for ch in text:
        if ord(ch) in cm:
            key = primary
        elif ctx.font_cjk and ord(ch) in _CJK_CMAP:
            key = _CJK
        else:
            continue            # 两套字体都没有的字符：跳过（服务端会提前拦截并提醒）
        if key != cur_key and cur:
            runs.append((cur_key, cur)); cur = ""
        cur_key, cur = key, cur + ch
    if cur:
        runs.append((cur_key, cur))

    seq = []                    # (fontkey, char)
    for key, rs in runs:
        for ch in rs:
            seq.append((key, ch))
            if key == _CJK:
                ctx.fallback_used.add(ch)

    # 累积 pen：fitz 步进 + 2026 字距表 + 字距(letter-spacing)
    ls = ls_em * size
    out, pen = [], 0.0
    n = len(seq)
    for i, (key, ch) in enumerate(seq):
        out.append((key, ch, pen))
        pen += _ffont(key, ctx=ctx).text_length(ch, size)
        if i < n - 1:
            nk, nch = seq[i + 1]
            if kern and nk == key:
                pen += _kern(primary, ch, nch, size)
            pen += ls
    return out, pen

# ================================================================ drawing
_fitz_fonts = {}
def _ffont(which, *, ctx=None):
    """缓存的 fitz.Font（快速查询字符自然步进）。"""
    ctx = ctx if ctx is not None else _render_context()
    if which not in ctx.fitz_fonts:
        path = ctx.font_cjk if which == _CJK else _fp(which)
        ctx.fitz_fonts[which] = fitz.Font(fontfile=path)
    return ctx.fitz_fonts[which]

MAX_RUN = 6   # 批量 run 上限（MuPDF 批内步进有 ~0.0075pt/字符量化漂移）

def put(page, x, baseline, text, size, font=_VAR, color=INK,
        kern=True, ls_em=0.0, ins_name=None, *, ctx=None):
    """Draw shaped text. (x, baseline) = pen position of first glyph.
    优化：无字距/字距偏移的连续字符合并为一次 insert_text（累积漂移 <0.05pt）。"""
    ctx = ctx if ctx is not None else _render_context()
    glyphs, _ = layout_text(text, size, font, kern=kern, ls_em=ls_em, ctx=ctx)
    n = len(glyphs)
    i = 0
    while i < n:
        key = glyphs[i][0]
        gx0 = glyphs[i][2]
        ff = _ffont(key if key in (_VAR, _480) else font, ctx=ctx)
        j = i + 1
        while j < n and j - i < MAX_RUN:
            if glyphs[j][0] != key:
                break
            adv = ff.text_length(glyphs[j - 1][1], size)
            if abs((glyphs[j][2] - glyphs[j - 1][2]) - adv) > 1e-4:
                break
            j += 1
        seg = "".join(g[1] for g in glyphs[i:j])
        page.insert_text((x + gx0, baseline), seg, fontsize=size,
                         fontname=ins_name or key, color=color)
        i = j

def put_right(page, right_edge, baseline, text, size, font=_VAR, color=INK, kern=True, ins_name=None, *, ctx=None):
    ctx = ctx if ctx is not None else _render_context()
    _, w = layout_text(text, size, font, kern=kern, ctx=ctx)
    put(page, right_edge - w, baseline, text, size, font, color, kern=kern, ins_name=ins_name, ctx=ctx)

def put_center_ls(page, center, baseline, text, size, font, color, ls_em, *, ctx=None):
    """Center text with letter-spacing (footer).
    CSS text-align:center uses the element box width, which includes
    trailing letter-spacing after the last character."""
    ctx = ctx if ctx is not None else _render_context()
    _, w = layout_text(text, size, font, kern=True, ls_em=ls_em, ctx=ctx)
    w += ls_em * size                    # trailing letter-spacing
    put(page, center - w / 2, baseline, text, size, font, color,
        kern=True, ls_em=ls_em, ctx=ctx)

# ------------------------------------------------ symbols.ttf → 矢量轮廓缓存
_SYM_CACHE = None
def _sym_paths():
    """从 symbols.ttf 提取图标轮廓（字体单位）。字体仅作字形来源，不嵌入 PDF。"""
    global _SYM_CACHE
    if _SYM_CACHE is not None:
        return _SYM_CACHE
    from fontTools.pens.basePen import BasePen

    class _Pen(BasePen):
        def __init__(self):
            super().__init__(None)
            self.contours, self.cur = [], None
        def _moveTo(self, p):
            self.cur = [("M", p)]
            self.contours.append(self.cur)
        def _lineTo(self, p):
            self.cur.append(("L", p))
        def _curveToOne(self, p1, p2, p3):
            self.cur.append(("C", p1, p2, p3))
        def _qCurveToOne(self, p1, p2):
            p0 = self.cur[-1][-1]
            c1 = (p0[0] + (p1[0]-p0[0]) * 2/3, p0[1] + (p1[1]-p0[1]) * 2/3)
            c2 = (p2[0] + (p1[0]-p2[0]) * 2/3, p2[1] + (p1[1]-p2[1]) * 2/3)
            self.cur.append(("C", c1, c2, p2))
        def _closePath(self):
            pass

    ttf = _TT(FONT_SYM)
    upem = ttf["head"].unitsPerEm
    cmap = ttf.getBestCmap()
    gs = ttf.getGlyphSet()
    paths = {}
    for key, cp in ICON_KEYS.items():
        gname = cmap.get(cp)
        if not gname:
            continue
        pen = _Pen()
        gs[gname].draw(pen)
        paths[cp] = pen.contours
    _SYM_CACHE = (upem, paths)
    return _SYM_CACHE

# ------------------------------------------------ PDF 元数据（编辑器可编辑，创建时间留空不写入）
META = {'title': 'Mercury Statement',
 'author': '',
 'subject': 'Mercury account statement',
 'keywords': 'statement',
 'producer': 'react-pdf',
 'creator': 'react-pdf',
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

def draw_icon(page, codepoint, baseline):
    """图标以矢量路径直接渲染（Symbols Regular 不再嵌入 PDF）。"""
    upem, paths = _sym_paths()
    contours = paths.get(codepoint)
    if not contours:
        return
    s = 7.5 / upem
    shape = page.new_shape()
    for cont in contours:
        prev = None
        for seg in cont:
            if seg[0] == "M":
                prev = seg[1]
                continue
            if seg[0] == "L":
                p = seg[1]
                shape.draw_line(fitz.Point(ICON_X + prev[0]*s, baseline - prev[1]*s),
                                fitz.Point(ICON_X + p[0]*s,  baseline - p[1]*s))
                prev = p
            else:  # "C" 三次贝塞尔
                p1, p2, p3 = seg[1], seg[2], seg[3]
                shape.draw_bezier(
                    fitz.Point(ICON_X + prev[0]*s, baseline - prev[1]*s),
                    fitz.Point(ICON_X + p1[0]*s,  baseline - p1[1]*s),
                    fitz.Point(ICON_X + p2[0]*s,  baseline - p2[1]*s),
                    fitz.Point(ICON_X + p3[0]*s,  baseline - p3[1]*s))
                prev = p3
    shape.finish(fill=BLACK, color=None)
    shape.commit()

# ================================================================ helpers
def money(v):
    return f"${abs(v):,.2f}"

def signed_money(v):
    """交易金额：正='$X'（绿），负='-$X'（黑）"""
    return ("-" if v < 0 else "") + f"${abs(v):,.2f}"

def prepare(page, *, ctx=None):
    ctx = ctx if ctx is not None else _render_context()
    page.insert_font(fontname=_VAR, fontfile=FONT_VAR)
    page.insert_font(fontname=_480, fontfile=FONT_480)
    if ctx.font_cjk:
        page.insert_font(fontname=_CJK, fontfile=ctx.font_cjk)
    page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
    page.draw_rect(fitz.Rect(0, 0, 612, 4), color=None, fill=TOPBAR)

def footer(page, baselines, *, ctx=None):
    """baselines = (line1_baseline, line2_baseline) — exact from source."""
    ctx = ctx if ctx is not None else _render_context()
    lines = (ctx.data["footer_line1"], ctx.data["footer_line2"])
    for line, bl in zip(lines, baselines):
        put_center_ls(page, 306, bl, line, 8, _480, FOOTER_COL, FOOTER_LS, ctx=ctx)

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
    f = fitz.Font(fontfile=FONT_VAR)
    txt = "S A M P L E"
    size = 92
    w = f.text_length(txt, size)
    pt = fitz.Point((612 - w * 0.707) / 2, 792 * 0.62)
    tw.append(pt, txt, font=f, fontsize=size)
    tw.write_text(page, color=(0.93, 0.93, 0.95), morph=(pt, fitz.Matrix(45)))
    _wm_to_back(page)

def collect_texts(data=None):
    """收集数据中所有会被渲染的文本。"""
    d = data if data is not None else DATA
    out = [d.get(k, "") or "" for k in
           ("period_label", "period_start", "period_end", "period_days",
            "timezone", "company", "ein", "account_type", "account_number",
            "routing_number", "footer_line1", "footer_line2")]
    out += list(d.get("address") or [])
    for t in d.get("transactions") or []:
        out += [str(t.get("date", "")), str(t.get("desc", "")),
                str(t.get("type", ""))]
    return [str(s) for s in out]


def autodetect_cjk(data=None):
    """数据中没有需要中文回退的字符时置 FONT_CJK=None（避免嵌入 10MB 字体）。"""
    global FONT_CJK
    if not FONT_CJK_BASE:
        return
    inter = _CMAP[_VAR] & _CMAP[_480]
    need = any(ord(ch) not in inter and ord(ch) in _CJK_CMAP
               for s in collect_texts(data) for ch in s)
    FONT_CJK = FONT_CJK_BASE if need else None


# ================================================================ page 1
def _wrap_words(text, size, font, max_w, *, ctx=None):
    """按词贪婪换行（用于公司名/地址，边界 170pt，与原稿一致）。"""
    ctx = ctx if ctx is not None else _render_context()
    words = text.split(" ")
    lines, cur = [], ""
    for wd in words:
        trial = (cur + " " + wd).strip()
        if cur and layout_text(trial, size, font, kern=True, ctx=ctx)[1] > max_w:
            lines.append(cur)
            cur = wd
        else:
            cur = trial
    if cur or not lines:
        lines.append(cur)
    return lines

def page_summary(doc, *, ctx=None):
    ctx = ctx if ctx is not None else _render_context()
    page = doc.new_page(width=612, height=792)
    prepare(page, ctx=ctx)
    # vector logo (redacted copy: only logo paths, no text)
    page.show_pdf_page(fitz.Rect(38, 38, 181, 74), _logo_doc(), 0,
                       clip=fitz.Rect(38, 38, 181, 74))

    # 顶部期间块：右锚定（x = 572 - 最宽行宽度，与原稿 2025/2026 均一致）
    w1 = layout_text(ctx.data["period_label"], 10, _VAR, kern=True, ctx=ctx)[1]
    w2 = 0.0
    for seg in (ctx.data["period_start"], "-", ctx.data["period_end"],
                " (", ctx.data["period_days"], " days)"):
        w2 += layout_text(seg, 10, _VAR, kern=True, ctx=ctx)[1]
    w3 = layout_text(ctx.data["timezone"], 10, _VAR, kern=True, ctx=ctx)[1]
    px = 572.0 - max(w1, w2, w3)
    put(page, px, 45.502, ctx.data["period_label"], 10, ctx=ctx)
    x = px
    for seg in (ctx.data["period_start"], "-", ctx.data["period_end"],
                " (", ctx.data["period_days"], " days)"):
        put(page, x, 59.502, seg, 10, ctx=ctx)
        _, w = layout_text(seg, 10, _VAR, kern=True, ctx=ctx)
        x += w
    put(page, px, 73.502, ctx.data["timezone"], 10, ctx=ctx)

    # 左列：公司名按词换行（15pt/行距21/边界170），地址与 EIN 顺移
    COL_W = 170.0
    name_lines = _wrap_words(ctx.data["company"], 15, _480, COL_W, ctx=ctx)
    for i, ln in enumerate(name_lines):
        put(page, 40.0, 132.25 + i * 21.0, ln, 15, _480, ctx=ctx)
    y_addr = 132.25 + len(name_lines) * 21.0 + 11.25
    addr_lines = []
    for a in ctx.data["address"]:
        addr_lines.extend(_wrap_words(a, 10, _VAR, COL_W, ctx=ctx))
    for i, ln in enumerate(addr_lines):
        put(page, 40.0, y_addr + i * 16.0, ln, 10, ctx=ctx)
    put(page, 40.0, y_addr + len(addr_lines) * 16.0 + 16.0, ctx.data["ein"], 10, ctx=ctx)

    # 右栏（2026 版式 x=252.20；值右对齐 572 不变；标题用 480 字体）
    RC = 252.20
    put(page, RC, 134.352, "Account details", 13, _480, BLUE, ctx=ctx)
    page.draw_line(fitz.Point(RC, 141), fitz.Point(572, 141), color=GRAY_LINE, width=2)
    rows = [("Account Type", ctx.data["account_type"]),
            ("Account Number", ctx.data["account_number"]),
            ("Routing Number", ctx.data["routing_number"])]
    for i, (k, v) in enumerate(rows):
        bl = 154.552 + i * 24
        put(page, RC, bl, k, 9, ctx=ctx)
        put_right(page, 572.0, bl, v, 9, ctx=ctx)

    # Account activity
    txs = ctx.data["transactions"]
    deposits = sum(t["amount"] for t in txs if t["amount"] > 0)
    withdrawals = -sum(t["amount"] for t in txs if t["amount"] < 0)
    end = ctx.data["beginning_balance"] + deposits - withdrawals

    put(page, RC, 229.352, "Account activity", 13, _480, BLUE, ctx=ctx)
    page.draw_line(fitz.Point(RC, 236), fitz.Point(572, 236), color=GRAY_LINE, width=2)
    rows = [("Beginning Balance", money(ctx.data["beginning_balance"])),
            ("Total withdrawals", signed_money(-withdrawals)),
            ("Total deposits", money(deposits)),
            ("Statement balance", money(end))]
    for i, (k, v) in enumerate(rows):
        bl = 249.552 + i * 24
        put(page, RC, bl, k, 9, ctx=ctx)
        put_right(page, 572.0, bl, v, 9, ctx=ctx)

    footer(page, (743.602, 763.602), ctx=ctx)
    watermark(page, ctx=ctx)

# ================================================================ page 2
# 2026 交易表几何（从 Signature-Pasta 2026-10 原稿精确测量）
ROW_TOP    = 78.0          # first row band top (separator line y)
ROW_H      = 15.928        # row height (2026)
TEXT_DY    = 10.928        # row text baseline = band top + TEXT_DY
TOTAL_DY   = 8.929         # Total baseline = last separator + TOTAL_DY
DATE_X     = 48.000        # date column pen x
DESC_X     = 99.600        # description column pen x
TYPE_X     = 318.830       # type text pen x
AMT_RIGHT  = 477.434       # amount right edge
BAL_RIGHT  = 563.981       # balance right edge
TOTAL_RIGHT = 563.917      # Total amount right edge (480 font)
ICON_X     = 306.830       # icon pen x
ICON_DY    = 0.572         # icon baseline = row text baseline + ICON_DY
TRANSACTION_FOOTER_BASELINES = (739.602, 759.602)
# Reserve room for the Total row and a clear gap before the footer. Deriving
# capacity from the geometry keeps later row-height changes from overlapping it.
ROWS_PER_PAGE = int((TRANSACTION_FOOTER_BASELINES[0] - 20 - ROW_TOP - TOTAL_DY) // ROW_H)

def page_transactions(doc, page_index, page_count, rows, first, last_total, *, ctx=None):
    ctx = ctx if ctx is not None else _render_context()
    page = doc.new_page(width=612, height=792)
    prepare(page, ctx=ctx)

    # title (baseline 47.905, pen 48.0)
    label = "All Transactions /" + (f" cont. ({page_index+1}/{page_count})" if page_count > 1 else "")
    put(page, 48.0, 47.905, label, 11, color=BLUE2, ctx=ctx)

    # column headers (baseline 73.126)
    put(page, 48.0,   73.126, "Date (UTC)",         7.5, color=TABLE_HEAD, ctx=ctx)
    put(page, 99.6,   73.126, "Description",        7.5, color=TABLE_HEAD, ctx=ctx)
    put(page, 306.83, 73.126, "Type",               7.5, color=TABLE_HEAD, ctx=ctx)
    put_right(page, 477.423, 73.126, "Amount",            7.5, color=TABLE_HEAD, ctx=ctx)
    put_right(page, 563.959, 73.126, "End of Day Balance", 7.5, color=TABLE_HEAD, ctx=ctx)

    # source content-stream order: title, headers, footer, rows, total
    footer(page, TRANSACTION_FOOTER_BASELINES, ctx=ctx)

    # rows
    transactions = ctx.data["transactions"]
    running = ctx.data["beginning_balance"] + sum(
        t["amount"] for t in transactions[:first])

    page.draw_line(fitz.Point(48, ROW_TOP), fitz.Point(564, ROW_TOP),
                   color=ROW_LINE, width=1)

    prev_date = None
    n = len(rows)
    for i, t in enumerate(rows):
        band = ROW_TOP + i * ROW_H
        bl = band + TEXT_DY
        running += t["amount"]
        next_index = first + i + 1
        # Page boundaries are not day boundaries: look beyond this chunk before
        # presenting a running balance as the final balance for the date.
        day_end = next_index == len(transactions) or transactions[next_index]["date"] != t["date"]

        if t["date"] != prev_date:          # 2026: 交易行 kern ON
            put(page, DATE_X, bl, t["date"], 7.5, color=BLACK, ctx=ctx)
            prev_date = t["date"]
        put(page, DESC_X, bl, t["desc"], 7.5, color=BLACK, ctx=ctx)
        draw_icon(page, _icon_cp(t), bl + ICON_DY)
        put(page, TYPE_X, bl, t["type"], 7.5, color=BLACK, ctx=ctx)
        put_right(page, AMT_RIGHT, bl, signed_money(t["amount"]), 7.5,
                  color=GREEN if t["dir"] == "in" else BLACK, ctx=ctx)
        if day_end:
            put_right(page, BAL_RIGHT, bl, money(running), 7.5, color=BLACK, ctx=ctx)
        page.draw_line(fitz.Point(48, band + ROW_H), fitz.Point(564, band + ROW_H),
                       color=ROW_LINE, width=1)

    # Total row (480 font, 原稿专用嵌入名 RVFNLP+…，字形/排版与 480 完全一致)
    tbl = ROW_TOP + n * ROW_H + TOTAL_DY
    page.insert_font(fontname="f480t", fontfile=FONT_480_TOTAL)
    put(page, DATE_X, tbl, "Total", 7.5, _480, BLACK, ins_name="f480t", ctx=ctx)
    put_right(page, TOTAL_RIGHT, tbl, last_total, 7.5, _480, BLACK, ins_name="f480t", ctx=ctx)

    watermark(page, ctx=ctx)

# ================================================================ logo
_logo = None
def _logo_doc():
    global _logo
    if _logo is None:
        _logo = fitz.open(LOGO_PDF)
    return _logo

# ================================================================ main
_FONT_NAME_FIX = [  # MuPDF 写入 BaseFont 有截断，按原稿补全（截断名必为全名前缀）
    "TRGABZ+ArcadiaTextVariable-Variable",
    "RVFNLP+1695149660304911865-0-Arcadia-Text-480",
]

def _fix_font_names(doc):
    seen = set()
    for page in doc:
        for f in doc.get_page_fonts(page.number, full=True):
            xref = f[0]
            if xref in seen:
                continue
            seen.add(xref)
            full = next((s for s in _FONT_NAME_FIX
                         if s.startswith(f[3]) and f[3] != s), None)
            if not full:
                continue
            doc.xref_set_key(xref, "BaseFont", "/" + full)
            t, d = doc.xref_get_key(xref, "DescendantFonts/[0]")
            if t == "xref":
                dx = int(d.split()[0])
                doc.xref_set_key(dx, "BaseFont", "/" + full)
                t2, d2 = doc.xref_get_key(dx, "FontDescriptor")
                if t2 == "xref":
                    doc.xref_set_key(int(d2.split()[0]), "FontName", "/" + full)

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
        page_summary(doc, ctx=ctx)

        txs = ctx.data["transactions"]
        total = money(ctx.data["beginning_balance"] + sum(t["amount"] for t in txs))
        chunks = [txs[i:i + ROWS_PER_PAGE] for i in range(0, len(txs), ROWS_PER_PAGE)]
        for idx, chunk in enumerate(chunks):
            page_transactions(doc, idx, len(chunks), chunk,
                              idx * ROWS_PER_PAGE, total, ctx=ctx)
        _fix_font_names(doc)
        apply_meta(doc, meta, ctx=ctx)
        return doc
    except BaseException:
        doc.close()
        raise


def main():
    doc = build(meta=None)
    out = "Demo-Bank-Statement-SAMPLE.pdf"
    doc.save(out, garbage=4, deflate=True)
    print(f"wrote {out} ({doc.page_count} pages)")

if __name__ == "__main__":
    main()
