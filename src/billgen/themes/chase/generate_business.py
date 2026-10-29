#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大通（Chase）商业版生成器（独立模块，不与另一版本混用）
模板=原稿字节级静态克隆；动态区经内容流单元精确重写。
同数据回放：文本相同即跳过 → 页面内容流零改动，与原稿一致。
几何常量均从原稿逐 span 实测（layout_raw.json）。"""
import io, json, os, re

from billgen.themes.chase import common

import fitz

BASE = os.path.dirname(os.path.abspath(__file__))
from billgen.themes.chase import chstream
from billgen.themes.chase import extend_fonts as EF
from billgen.themes.chase import barcode as BC

PAGE_H = 792.0
TABLES = json.load(open(os.path.join(BASE, "assets", "font_tables_business.json")))
TEMPLATES = { "business": os.path.join(BASE, "assets", "chase_static_business.pdf") }
LAYOUTS = { "business": json.load(open(os.path.join(BASE, "layout_business.json"))) }

# 原稿 Info 元数据（字节级原样，含尾随空格与 UTC00'00' 格式）
ORIG_META = {'business': {'title': 'SAMPLE — NOT A REAL BANK STATEMENT', 'author': '', 'creator': 'BillGen Sample Editor', 'producer': 'BillGen Sample Editor', 'subject': 'Fictional sample statement', 'keywords': 'sample'}}

# 码表（本模块仅含本模板字体，物理隔离，不与另一版本混用）
CH2CODE, WIDTHS, CODE2CH = {}, {}, {}
for key, ent in TABLES.items():
    if ":" not in key:
        continue                     # 标准字体表（Helvetica 等）无需码位
    tag, fname = key.split(":", 1)
    if ent["type"] == "Type3" and ent.get("codes"):
        m = CH2CODE.setdefault(tag, {}).setdefault(fname, {})
        for code, ch in ent["codes"].items():
            if ch not in m:
                m[ch] = int(code)
        CODE2CH.setdefault(tag, {})[fname] = {int(c): ch for c, ch in ent["codes"].items()}
    if ent["type"] == "Type3" and ent.get("widths"):
        WIDTHS.setdefault(tag, {})[fname] = ent["widths"]
_STD = {"Hel": fitz.Font("helv"), "HeB": fitz.Font("hebo"), "Tim": fitz.Font("tiro"),
        "Sym": fitz.Font("symb")}

ROW_FONT, ROW_SIZE = "F11", 0.24
PITCH, CONT_PITCH = 12.25, 9.35          # 主行距 / 描述续行距（实测）


# References stay live when prepared fonts extend or replace width tables.
_FONT_CODEC = common.FontCodec(TABLES, CH2CODE, WIDTHS, CODE2CH, _STD)
encode = _FONT_CODEC.encode
char_w = _FONT_CODEC.char_w
text_w = _FONT_CODEC.text_w


def fmt_amount(v):
    """金额统一千分位（保留 $/负号，与原稿样式一致）；尾随空白原样保留
    （原稿 fees 首笔 '$25.00  ' 带两个尾空格实证）"""
    v = str(v)
    trail = v[len(v.rstrip()):]
    v = v.rstrip()
    m = re.match(r"^(\$?)(-?)([\d,]+)(\.\d+)?$", v)
    if not m:
        return v + trail
    dol, sign, intpart, frac = m.groups()
    intpart = f"{int(intpart.replace(',', '')):,}"
    return f"{dol}{sign}{intpart}{frac or ''}" + trail


RULE_RE = re.compile(rb"([\d.]+) ([\d.]+) m ([\d.]+) ([\d.]+) l S\s*")


def make_units(tag, text, font, size, x, bl, res=None, scale=1.0):
    """scale=1 时单 Tm + 单 Tj 整串（与原稿行单元同构：文本抽取不产生假空格、
    尾随空格不丢）；scale<1 时逐字符单元压缩字距（行内替换变长时保住右缘）"""
    pdf_y = PAGE_H - bl
    fname = res or font
    if scale == 1.0:
        bs = b"".join(encode(tag, font, ch) for ch in text)
        tok = b"<" + bs.hex().encode() + b">"
        return (f"1 0 0 1 {x:.2f} {pdf_y:.1f} Tm\n/{fname} {size} Tf\n".encode()
                + tok + b" Tj\n"), text_w(tag, font, text, size)
    out = []
    cx = x
    for ch in text:
        b = encode(tag, font, ch)
        tok = b"<" + b.hex().encode() + b">"
        # y 走原稿 1 位小数网格（行距序列复刻）；x 保留 2 位精度——逐字符
        # 累加位置若也量化到 .1，字距会出毛刺，文本抽取出现假空格
        out.append(f"1 0 0 1 {cx:.2f} {pdf_y:.1f} Tm\n/{fname} {size} Tf\n".encode() + tok + b" Tj\n")
        cx += char_w(tag, font, ch, size) * scale
    return b"".join(out), cx - x


_cents = common._cents


def date_key(s):
    """归一化日期排序键：M/D[/Y] → 补零数字串；空/非法排最前（稳定排序）"""
    parts = re.findall(r"\d+", str(s or ""))
    if not parts:
        return ""
    m = int(parts[0])
    d = int(parts[1]) if len(parts) > 1 else 0
    y = int(parts[2]) if len(parts) > 2 else 0
    return f"{y:04d}{m:02d}{d:02d}"


_fmt = common._fmt


# ============ 布局常量（全部实测自原稿） ============
BIZ_SECTIONS = {
    "deposits": {"frags": [
        {"pg": 0, "first": 642.0, "bottom": 729.3, "date": 36.4, "desc": 86.6, "amt_r": 535.2,
         "rule": (31.4, 536.6)},
        {"pg": 1, "first": 138.0, "bottom": 499.0, "date": 34.3, "desc": 84.4, "amt_r": 533.0,
         "rule": (29.2, 534.4)}],
        "total": ("Total Deposits and Additions", 1)},
    "checks": {"frags": [
        {"pg": 1, "first": 568.8, "bottom": 741.8, "no": 32.1, "caret": 55.2, "paid": 427.9,
         "desc": 112.8, "amt_r": 533.0, "rule": (29.2, 534.4)},
        {"pg": 2, "first": 146.0, "bottom": 182.0, "no": 32.1, "caret": 55.2, "paid": 427.9,
         "desc": 112.8, "amt_r": 533.0, "rule": (29.2, 534.4)}],
        "total": ("Total Checks Paid", 2)},
    "electronic_withdrawals": {"frags": [
        {"pg": 2, "first": 450.0, "bottom": 632.0, "date": 35.0, "desc": 70.8, "amt_r": 533.3,
         "rule": (30.0, 534.9), "rule_p": (417.1, 534.9)}],
        "total": ("Total Electronic Withdrawals", 2)},
    "other_withdrawals": {"frags": [
        {"pg": 2, "first": 691.5, "bottom": 723.0, "date": 35.0, "desc": 70.3, "amt_r": 533.7,
         "rule": (30.0, 535.2), "rule_p": (418.3, 535.4)}],
        "total": ("Total Other Withdrawals", 2)},
    "fees": {"frags": [
        {"pg": 3, "first": 138.0, "bottom": 249.0, "date": 35.0, "desc": 70.3, "amt_r": 533.7,
         "rule": (30.0, 535.2), "rule_p": (418.3, 535.4)}],
        "total": ("Total Fees", 3)},
}
BIZ_DAILY = {"pg": 3, "first": 434.0, "bottom": 523.0, "pitch": 16.8,
             "cols": [(30.7, 172.5), (214.3, 356.1), (397.9, 539.7)]}

# ============ 流式布局（两原稿实测：段距 4.0、合计=末行+14.4、
# *end*=合计+6.7、续页行 138 起、页行容量 ~740）============
FLOW_EPS = 0.15           # 平移阈值：≤0.15pt 视为不动（保回放字节）
TOTAL_AFTER_LAST = 14.4
END_AFTER_TOTAL = 6.7
SEC_GAP = 4.0
CHECKS_FOOT = (17.6, 9.3, 12.7, 9.4, 12.7)   # 合计→脚注 5 行的行距（实测）

# 各段模板锚点：head=(头块范围 mk..首行前)；cont_head=(页,范围)；
# tail=(页, 范围, 模板合计y)；total_lbl=合计标签
SEC_FLW = {
    "deposits":  {"head": (0, 593.6, 641.5), "cont_head": (1, 91.0, 135.0),
                  "tail": (1, 495.0, 510.5, 501.6), "p1_endmk": (728.0, 733.0),
                  "mk": 593.6, "end": 508.4,
                  "blk": [(0, 593.6, 733.0), (1, 91.0, 510.5)],
                  "total_lbl": "Total Deposits and Additions"},
    "checks":    {"head": (1, 512.4, 568.3), "cont_head": (2, 91.0, 135.5),
                  "tail": (2, 180.0, 253.5, 184.8), "mk": 512.4, "end": 252.3,
                  "end_off": 67.5, "footnote": True,
                  "blk": [(1, 512.4, 746.0), (2, 91.0, 253.5)],
                  "total_lbl": "Total Checks Paid"},
    "electronic_withdrawals": {"head": (2, 403.7, 449.5),
                  "tail": (2, 628.0, 642.5, 634.4), "mk": 403.7, "end": 641.1,
                  "blk": [(2, 403.7, 642.5)],
                  "total_lbl": "Total Electronic Withdrawals"},
    "other_withdrawals": {"head": (2, 644.4, 691.0),
                  "tail": (2, 718.0, 732.5, 724.6), "mk": 644.4, "end": 731.3,
                  "blk": [(2, 644.4, 732.5)],
                  "total_lbl": "Total Other Withdrawals"},
    "fees":      {"head": (3, 91.0, 137.5),
                  "tail": (3, 244.0, 258.5, 250.4), "mk": 91.0, "end": 257.1,
                  "blk": [(3, 91.0, 258.5)],
                  "total_lbl": "Total Fees"},
}
ATM_BLK = (2, 256.1, 400.0)      # 静态 ATM & DEBIT CARD SUMMARY 块
PF_BLK = (3, 262.1, 379.5)       # 静态 post fees message 块
DAILY_BLK = {"mk": 381.9, "head": (381.9, 433.5), "rows_first": 434.0}
BIZ_SUMMARY = [  # (标签, 数据键, $前缀, 带笔数列)
    ("Beginning Balance", "begin", True, False),
    ("Deposits and Additions", "deposits", False, True),
    ("Checks Paid", "checks", False, True),
    ("Electronic Withdrawals", "electronic_withdrawals", False, True),
    ("Other Withdrawals", "other_withdrawals", False, True),
    ("Fees", "fees", False, True),
    ("Ending Balance", "ending", True, True),
]
class Page:
    def __init__(self, doc, page, tag):
        self.doc = doc
        self.page = page
        self.tag = tag
        self.res_of = {}
        base_of = {"Helvetica": "Hel", "Helvetica-Bold": "HeB",
                   "Times-Roman": "Tim", "Symbol": "Sym"}
        for f in page.get_fonts(full=True):
            self.res_of[base_of.get(f[3], f[3])] = f[4]
        self.code2ch = CODE2CH.get(tag, {})
        xrefs = page.get_contents()
        self.stream_xrefs = list(xrefs)
        self.data = b"".join(doc.xref_stream(x) for x in xrefs)
        self.segs = chstream.parse(self.data)
        # 状态物化：把继承的 Tf/rg 写进单元自身，删除相邻单元不再破坏状态
        # （惰性物化属个人版字节级方案，商业版实测破坏基准回放，勿改回）
        for seg in self.segs:
            if seg.kind != "btseg":
                continue
            for u in seg.units:
                pre = b""
                if u.font and not chstream.TF_RE.search(u.raw):
                    pre += f"/{u.font} {u.size} Tf\n".encode()
                if u.rgb != (0, 0, 0) and not chstream.RG_RE.search(u.raw):
                    pre += f"{u.rgb[0]:.2f} {u.rgb[1]:.2f} {u.rgb[2]:.2f} rg\n".encode()
                if pre:
                    u.raw = pre + u.raw
        self.units = chstream.all_units(self.segs)
        self.lspans = LAYOUTS[tag][page.number]["spans"]
        self._loc = []
        for si, s in enumerate(self.segs):
            if s.kind != "btseg":
                continue
            for ui, u in enumerate(s.units):
                if u.text is not None and u.font:
                    self._loc.append((si, ui))

    def _decode(self, font, raw):
        c2c = self.code2ch.get(font)
        out = []
        for b in raw:
            out.append(c2c.get(b, chr(b)) if c2c else chr(b))
        return "".join(out)

    def spans_units(self, span, allow_overflow=False):
        """精确匹配：按 span 文本的前缀逐单元解码对齐，杜绝误删相邻 span。"""
        want = span["text"]
        x1 = span.get("x1", span["x"] + len(want) * 5)
        out = []
        consumed = ""
        # 候选：同一基线、起点在 span 范围内
        cand = []
        for k, u in enumerate(self.units):
            if u.x is None or abs(u.bl - span["bl"]) >= 1.5:
                continue
            if u.x >= span["x"] - 1.8 and u.x <= x1 + 1.0:
                cand.append(k)
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
            # 允许单词边界处空格差异（跨单元串词的空格在相邻单元间）
            for cand_dec in (dec, " " + dec.lstrip(), dec.rstrip(), dec.strip(),
                             " " + dec.strip()):
                if want.startswith(consumed + cand_dec):
                    consumed += cand_dec
                    out.append(self._loc[k])
                    break
            else:
                continue
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

    def rules(self, lo, hi):
        """[lo,hi) 基线范围内的水平长线（交易行线）"""
        out = []
        for si, seg in enumerate(self.segs):
            if seg.kind != "raw":
                continue
            for m in RULE_RE.finditer(seg.raw):
                y0, y1 = float(m.group(2)), float(m.group(4))
                if abs(y0 - y1) > 0.01:
                    continue
                x0, x1 = float(m.group(1)), float(m.group(3))
                if x1 - x0 < 100:
                    continue
                bl = PAGE_H - y0
                if lo <= bl < hi:
                    out.append((si, m.span()))
        return out

    def delete_rules(self, lo, hi):
        for si, (a, b) in sorted(self.rules(lo, hi), key=lambda t: (t[0], -t[1][0])):
            seg = self.segs[si]
            seg.raw = seg.raw[:a] + seg.raw[b:]

    def _line_ops(self, lo, hi):
        """[lo,hi) 基线带相交的全部矢量线段（不限方向/长度）"""
        out = []
        for si, seg in enumerate(self.segs):
            if seg.kind != "raw":
                continue
            for m in RULE_RE.finditer(seg.raw):
                y0, y1 = float(m.group(2)), float(m.group(4))
                top, bot = PAGE_H - max(y0, y1), PAGE_H - min(y0, y1)
                if top < hi and bot >= lo:
                    out.append((si, m.span(), m.span(2), m.span(4), y0, y1))
        return out

    def delete_lines(self, lo, hi):
        hits = {}
        for si, full, g2, g4, _, _ in self._line_ops(lo, hi):
            hits.setdefault(si, []).append(full)
        for si, sps in hits.items():
            seg = self.segs[si]
            for a, b in sorted(set(sps), key=lambda t: -t[0]):
                seg.raw = seg.raw[:a] + seg.raw[b:]

    def shift_lines(self, lo, hi, dy):
        """带内线段纵向平移 dy（PDF 坐标，正值上移）"""
        hits = {}
        for si, full, g2, g4, y0, y1 in self._line_ops(lo, hi):
            hits.setdefault(si, []).append((g2, g4, y0, y1))
        for si, items in hits.items():
            seg = self.segs[si]
            for g2, g4, y0, y1 in sorted(items, key=lambda t: -t[1][0]):
                seg.raw = (seg.raw[:g4[0]] + f"{y1 + dy:.2f}".encode() + seg.raw[g4[1]:])
                seg.raw = (seg.raw[:g2[0]] + f"{y0 + dy:.2f}".encode() + seg.raw[g2[1]:])

    def append_text(self, raw_units):
        """自含 Tf 的逐字单元包上 BT..ET 追加到页内容尾。
        显式置黑填充：页尾可能继承 1 g（原稿隐层白字），否则整体不可见。"""
        op = b"BT\n0 g\n" + raw_units + b"ET\n"
        for seg in reversed(self.segs):
            if seg.kind == "raw":
                seg.raw = seg.raw + op
                return
        self.segs.append(chstream.RawSeg(op))

    def add_rule(self, bl, x0, x1, w=0.72):
        y = PAGE_H - bl
        op = f"q {w} w 0 0 0 RG {x0} {y:.1f} m {x1} {y:.1f} l S Q ".encode()
        for seg in reversed(self.segs):
            if seg.kind == "raw":
                seg.raw = seg.raw + op
                return
        self.segs.append(chstream.RawSeg(op))

    def delete_units(self, locs):
        for si, ui in sorted(set(locs), key=lambda t: -t[1]):
            self.segs[si].units[ui].raw = b""

    def insert_before(self, loc, raw_bytes):
        si, ui = loc
        u = self.segs[si].units[ui]
        u.raw = raw_bytes + u.raw

    def replace_span(self, span, new_text, align=None, right_edge=None):
        if new_text.rstrip() == span["text"].rstrip():
            return True                      # 同文跳过：保住原稿字节
        locs = self.spans_units(span, allow_overflow=True)
        if not locs:
            return False
        new_x = span["x"]
        scale = 1.0
        if align == "r" and right_edge is not None:
            new_x = right_edge - text_w(self.tag, span["font"], new_text, span["size"])
        else:
            # 新文本实际越过行内后续 span（SM/® 上标、接续词、邮编列）时才压缩字距
            x1 = span.get("x1", 0)
            fol = None
            for s2 in self.lspans:
                if s2 is span or abs(s2["bl"] - span["bl"]) >= 5.5:
                    continue
                if x1 - 0.5 <= s2["x"] <= x1 + 3.5 and s2.get("x1", s2["x"]) > x1:
                    if fol is None or s2["x"] < fol["x"]:
                        fol = s2
            if fol is not None:
                nat = text_w(self.tag, span["font"], new_text, span["size"])
                nat_end = span["x"] + nat
                if nat_end > fol["x"]:
                    target = fol["x"] - 0.3 - span["x"]
                    scale = max(0.75, target / nat)
        res = self.res_of.get(span["font"], span["font"])
        raw, _ = make_units(self.tag, new_text, span["font"], span["size"], new_x,
                            span["bl"], res=res, scale=scale)
        self.delete_units(locs)
        self.insert_before(locs[0], raw)
        return True

    def commit(self):
        data = chstream.serialize(self.segs)
        if data == self.data:
            return                      # 零修改：原字节直通（内容流字节级 1:1）
        if self.stream_xrefs:
            self.doc.update_stream(self.stream_xrefs[0], data)
            for x in self.stream_xrefs[1:]:
                self.doc.update_stream(x, b"")


def add_watermark(doc, page):
    return common.add_watermark(doc, page, text_w)


find = common.find


def build_subs(d):
    b = d.get("bank", {})
    subs = []
    if b.get("legal"):
        subs.append(("JPMorgan Chase Bank, N.A.", b["legal"]))
    # 域名必须先于银行短名：否则 Chase.com 先被 (Chase→name) 吃掉，
    # 编辑器"域名"字段对 Chase.com 字样失效（实测坑）
    if b.get("domain"):
        # 带路径的串必须再先一步：只换域名时路径会留着，替换后比原 span 长得多，
        # 会压到同一行后文的 " or call us at the number…"（实测）
        subs.append(("chase.com/business/disclosures", b["domain"]))
        subs.append(("chase.com/disclosures", b["domain"]))
        subs.append(("Chase.com", b["domain"]))
        subs.append(("chase.com", b["domain"]))
    if b.get("name"):
        subs.append(("JPMorgan Chase", b["name"]))
        subs.append(("Chase", b["name"]))
    return subs


sub_text = common.sub_text


def extend_all_fonts(doc, tag):
    """扩展全部 Type3 字体覆盖可编辑字符集。F3（跟踪号字体）除外——
    由 ensure_f3_digits 用原稿字形/合成位图专管，防止 EF 动态改码污染。"""
    NEED = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz.,'-/$%()#&+:*!@ "
    seen = set()
    for pg in doc:
        for f in pg.get_fonts(full=True):
            if f[0] in seen or f[2] != "Type3":
                continue
            seen.add(f[0])
            if f[4] == "F3":
                continue
            key = f"{tag}:{f[4]}"
            codes = set(TABLES.get(key, {}).get("codes", {}).values())
            missing = [c for c in NEED if c not in codes]
            if missing:
                added = EF.extend_font(doc, f[0], missing)
                fname = f[4]
                CH2CODE.setdefault(tag, {}).setdefault(fname, {})
                CODE2CH.setdefault(tag, {}).setdefault(fname, {})
                WIDTHS.setdefault(tag, {}).setdefault(fname, [500] * 256)
                for ch, code, gname, wx in added:
                    CH2CODE[tag][fname][ch] = code
                    CODE2CH[tag][fname][code] = ch
                    w = WIDTHS[tag][fname]
                    while len(w) <= code:
                        w.append(0)
                    w[code] = wx


# ---------- 条码（每页一张，页间变号）与 F3 跟踪号 ----------
BC_PAGES = (0, 2, 4)          # 条码所在页：第 1/3/5 页（两原稿实证）
_D_NAME = lambda c: f"EX0{48 + c}000"


def bc_seq_num(base, seq):
    """第 seq 个条码页的号码：d10=序号；非首页 d0=1、d18=6（两原稿逐页实测）"""
    d = list(BC._norm(base))
    d[10] = str(seq)
    if seq > 1:
        d[0] = "1"
        d[18] = "6"
    return "".join(d)


def _f3_bits(stream):
    """解析 F3 字形流；1bpc 行按字节对齐，ID 后有 1 个空白分隔符"""
    w = int(re.search(rb"/W (\d+)", stream).group(1))
    h = int(re.search(rb"/H (\d+)", stream).group(1))
    i = stream.find(b"ID")
    j = i + 2
    while j < len(stream) and stream[j:j + 1].isspace():
        j += 1
    stride = (w + 7) // 8
    n = stride * h
    return stream[:j], w, h, stream[j: j + n], stream[j + n:]


def _synth_8(stream0):
    """由 '0' 字形合成 '8'：中段两行补墨连接两侧环壁"""
    pre, w, h, body, suf = _f3_bits(stream0)
    stride = (w + 7) // 8
    bits = []
    for r in range(h):
        row = []
        base = r * stride
        for c in range(w):
            row.append((body[base + c // 8] >> (7 - c % 8)) & 1)
        bits.append(row)
    for r in (8, 9):
        for c in range(2, w - 2):
            if bits[r][c] == 0:
                bits[r][c] = 1
    out = bytearray(stride * h)
    for r in range(h):
        base = r * stride
        for c in range(w):
            if bits[r][c]:
                out[base + c // 8] |= 0x80 >> (c % 8)
    return pre + bytes(out) + suf


def ensure_f3_digits(doc):
    """补齐 F3 数字字形：7 克隆 67974A46 原稿同款（'0' 位图一致已验证）、
    8 由 '0' 收腰合成。克隆专属编码对象，杜绝共享连坐。幂等。"""
    xref = None
    for f in doc[0].get_fonts(full=True):
        if f[4] == "F3":
            xref = f[0]
    if xref is None:
        return
    obj = doc.xref_object(xref)
    m = re.search(r"/CharProcs (\d+) 0 R", obj)
    if not m:
        return
    cp = int(m.group(1))
    cp_txt = doc.xref_object(cp)
    have = set(re.findall(r"/(EX\d{6}) \d+ 0 R", cp_txt))
    need = [c for c in range(10) if _D_NAME(c) not in have]
    if not need:
        return
    enc = int(re.search(r"/Encoding (\d+) 0 R", obj).group(1))
    mu = re.search(r"/ToUnicode (\d+) 0 R", obj)
    tu = int(mu.group(1)) if mu else None
    mw = re.search(r"/Widths (\d+) 0 R", obj)
    wd = int(mw.group(1)) if mw else None
    m0 = re.search(rf"/{_D_NAME(0)} (\d+) 0 R", cp_txt)
    stream0 = doc.xref_stream(int(m0.group(1)))

    # 供体字形已提取为项目资产 JSON（原稿 PDF 无需在本地保留）
    donors = {}
    glyph_fp = os.path.join(BASE, "assets", "f3_donor_glyphs_business.json")
    if 7 in need and os.path.exists(glyph_fp):
        bank = json.load(open(glyph_fp))
        if "7" in bank:
            donors[7] = bytes.fromhex(bank["7"])

    new_entries = []
    for c in need:
        name = _D_NAME(c)
        if c in donors:
            data = donors[c]
        elif c == 8:
            data = _synth_8(stream0)
        else:
            continue
        nx = doc.get_new_xref()
        doc.update_object(nx, "<< >>")
        doc.update_stream(nx, data, compress=False)
        new_entries.append((c, name, nx))

    if new_entries:
        add = " ".join(f"/{n} {x} 0 R" for _, n, x in new_entries)
        doc.update_object(cp, cp_txt.rstrip()[:-2] + " " + add + " >>")
        F3_BASE = {240: "EX048000", 241: "EX049000", 242: "EX050000",
                   243: "EX051000", 244: "EX052000", 245: "EX053000",
                   246: "EX054000", 249: "EX057000"}
        etxt = doc.xref_object(enc)
        pairs = {int(k): v for k, v in re.findall(r"(\d+)\s+/(EX\d{6})", etxt)}
        for c, n in F3_BASE.items():
            pairs.setdefault(c, n)
        for c, n, _ in new_entries:
            pairs[240 + c] = n
        dd = " ".join(f"{k} /{pairs[k]}" for k in sorted(pairs))
        nenc = doc.get_new_xref()
        doc.update_object(nenc, f"<< /Type /Encoding /Differences [ {dd} ] >>")
        doc.xref_set_key(xref, "Encoding", f"{nenc} 0 R")
        if tu is not None:
            tm = doc.xref_stream(tu).decode("latin1")
            cnt = re.search(r"(\d+) beginbfchar", tm)
            add_lines = "".join(f"<f{c}><003{c}>\n" for c, _, _ in new_entries)
            tm = tm.replace(f"{cnt.group(1)} beginbfchar",
                            f"{int(cnt.group(1)) + len(new_entries)} beginbfchar", 1)
            tm = tm.replace("endbfchar", add_lines + "endbfchar", 1)
            for c, _, _ in new_entries:
                tm = re.sub(rf"\n?<F{c:X}> <[0-9A-Fa-f]{{4}}>", "", tm)
            doc.update_stream(tu, tm.encode("latin1"), compress=False)
        if wd is not None:
            arr = doc.xref_object(wd).strip()
            nums = arr.strip("[] \n").split()
            if len(nums) == 256:
                for c, _, _ in new_entries:
                    nums[240 + c] = "14"
                doc.update_object(wd, "[ " + " ".join(nums) + " ]")


def replace_barcodes(doc, pages, L, base):
    """三张条码（第1/3/5页）+ 每页 F3 竖排号同步换码。
    d10=条码序号；非首页 d0=1、d18=6（两原稿逐页实证）。"""
    if not base:
        return
    ensure_f3_digits(doc)
    nums = {pi: bc_seq_num(base, i + 1) for i, pi in enumerate(BC_PAGES)}
    for pi in BC_PAGES:
        if pi >= doc.page_count:
            break
        for im in doc[pi].get_images(full=True):
            if im[2] != 262 or im[3] != 910:
                continue
            img = BC.generate_barcode(nums[pi]).convert("1")
            W, H = img.size
            row_bytes = (W + 7) // 8
            data = bytearray()
            px = img.load()
            for y in range(H):
                row = bytearray(row_bytes)
                for x in range(W):
                    if px[x, y] == 0:
                        row[x // 8] |= 0x80 >> (x % 8)
                data.extend(row)
            doc.xref_set_key(im[0], "Filter", "null")
            doc.xref_set_key(im[0], "ImageMask", "null")
            doc.xref_set_key(im[0], "Decode", "null")
            doc.xref_set_key(im[0], "ColorSpace", "/DeviceGray")
            doc.xref_set_key(im[0], "BitsPerComponent", "1")
            # compress=False：默认 zlib+补 FlateDecode 会被渲成噪点
            doc.update_stream(im[0], bytes(data), compress=False)
        f3 = find(L, pi, lambda s: s["font"] == "F3" and len(s["text"].strip()) >= 15)
        if f3 and nums[pi] != f3["text"]:
            # 原地换码保留原稿旋转 Tm（竖排）
            for si, ui in pages[pi].spans_units(f3):
                codes = b"".join(encode("business", "F3", ch) for ch in nums[pi])
                pages[pi].segs[si].units[ui].set_text(codes)


# ---------- 自动计算 ----------
def biz_calc(d):
    begin = _cents(d.get("begin_balance", 0))
    sums = {k: sum(_cents(r["amount"]) for r in d.get(k, []))
            for k in ("deposits", "checks", "electronic_withdrawals",
                      "other_withdrawals", "fees")}
    counts = {k: len(d.get(k, [])) for k in sums}
    ending = begin + sums["deposits"] - sums["checks"] - sums["electronic_withdrawals"] \
        - sums["other_withdrawals"] - sums["fees"]
    # 各段显示：存款原号；其余段行内/合计显示为正、摘要取负
    d["summary"] = {
        "begin": _fmt(begin, dollar=True),
        "deposits": _fmt(sums["deposits"]),
        "checks": _fmt(-abs(sums["checks"])),
        "electronic_withdrawals": _fmt(-abs(sums["electronic_withdrawals"])),
        "other_withdrawals": _fmt(-abs(sums["other_withdrawals"])),
        "fees": _fmt(-abs(sums["fees"])),
        "ending": _fmt(ending, dollar=True),
        "n_deposits": str(counts["deposits"]),
        "n_checks": str(counts["checks"]),
        "n_electronic_withdrawals": str(counts["electronic_withdrawals"]),
        "n_other_withdrawals": str(counts["other_withdrawals"]),
        "n_fees": str(counts["fees"]),
        "n_ending": str(sum(counts.values())),
    }
    # 每日期末余额（按日期合并全部段；checks 段日期键=paid）
    rows = []
    for k in ("deposits", "checks", "electronic_withdrawals", "other_withdrawals", "fees"):
        for r in d.get(k, []):
            dt = r.get("date") or r.get("paid") or ""
            rows.append((dt, _cents(r["amount"]) * (1 if k == "deposits" else -1)))
    rows.sort(key=lambda t: date_key(t[0]))
    bal = begin
    daily, last = [], None
    for dt, c in rows:
        bal += c
        if not date_key(dt):
            continue          # 空日期行只参与余额、不产生日终格
        if date_key(dt) != last:
            daily.append([dt, _fmt(bal)])
            last = date_key(dt)
        else:
            daily[-1][1] = _fmt(bal)
    d["daily_balance"] = daily


# ---------- 段落渲染 ----------
def _row_cells(tag, kind, r, frag):
    """一段一行 → [(text, x, align_r)]；金额右对齐"""
    cells = []
    if kind == "checks":
        cells.append((str(r.get("no", "")), frag["no"], None))
        if r.get("caret"):
            cells.append(("^", frag["caret"], None))
        if r.get("desc"):
            cells.append((r["desc"], frag["desc"], None))
        cells.append((r.get("paid", r.get("date", "")), frag["paid"], None))
        cells.append((r["amount"], None, frag["amt_r"]))
    else:
        cells.append((r.get("date", ""), frag.get("date", 35.0), None))
        cells.append((r.get("desc", ""), frag.get("desc", 70.3), None))
        cells.append((r["amount"], None, frag["amt_r"]))
        if kind == "personal":
            cells.append((r.get("balance", ""), None, frag["bal_r"]))
    return [(t, x, ra) for t, x, ra in cells if t]


_col_xs = common._col_xs


def _rows_equal_original(tag, rows, frags, kind, first_dollar):
    """生成的行文本与原稿 span 逐行一致则跳过（回放模式）"""
    orig_rows = []
    fonts = ("F11",)
    for frag in frags:
        L = LAYOUTS[tag][frag["pg"]]["spans"]
        by_bl = {}
        for s in L:
            if s["font"] not in fonts:
                continue
            if not (frag["first"] - 4 <= s["bl"] < frag["bottom"]):
                continue
            if any(abs(s["x"] - v) < 2.0 for v in _col_xs(frag)) \
                    or s["x"] > frag["amt_r"] - 60 \
                    or (kind == "checks" and s["text"].strip() == "^" and 50 < s["x"] < 62):
                by_bl.setdefault(round(s["bl"], 1), []).append((s["x"], s["text"]))
        for bl in sorted(by_bl):
            cells = [t for _, t in sorted(by_bl[bl]) if t.strip()]
            if not cells:
                continue
            orig_rows.append(" ".join(cells).rstrip())
    if len(orig_rows) != _flat_row_count(rows, kind):
        return False
    k = 0
    for i, r in enumerate(rows):
        lines = str(r.get("desc", "")).split("\n")
        amt = str(r["amount"])
        if first_dollar and i == 0 and not amt.startswith("$") and not amt.startswith("-"):
            amt = "$" + amt
        if kind == "checks":
            parts = [str(r.get("no", ""))]
            if r.get("caret"):
                parts.append("^")
            want = " ".join(parts + [r.get("desc", ""),
                                     r.get("paid", r.get("date", "")), amt])
        elif kind == "personal":
            want = " ".join([r.get("date", ""), lines[0], amt, r.get("balance", "")])
        else:
            want = " ".join([r.get("date", ""), lines[0], amt])
        if k >= len(orig_rows) or orig_rows[k].split() != want.rstrip().split():
            return False
        k += 1
        for cl in lines[1:]:
            if k >= len(orig_rows) or orig_rows[k].split() != cl.split():
                return False
            k += 1
    return True


_flat_row_count = common._flat_row_count


# ---------- 元数据（原样格式） ----------
_pdf_esc = common._pdf_esc


def write_meta(doc, tag, md):
    return common.write_meta(doc, tag, md, ORIG_META)


_now_pdf_date = common._now_pdf_date


# ---------- 流式引擎 v2（989BD2E8/67974A46/14A6DFD6×2 四原稿实证） ----------
# 全链一条流：deposits→checks→ATM→electronic→other→fees→post-fees→daily→DRE，
# 段落不足时逐段上移/跨页提前（67974A46 4 页实证）；尾页奇数则补
# "This Page Intentionally Left Blank"（三原稿尾页均如此，双面打印惯例）。
# 同数据且落点=模板 → 整段零操作（回放字节保真）；同数据仅位移 → 整块平移。
PAGE_BOTTOM = (733.6, 741.8, 741.8, 741.8, 741.8, 741.8)   # p1=733.6：67974A46 首页末行 mk 733.2 实证
TOP_FIRST = {"deposits": 138.0, "checks": 146.0, "electronic_withdrawals": 137.1,
             "other_withdrawals": 137.1, "fees": 138.0}     # 页顶首行（各段实测）
HEAD_H = {"deposits": 48.4, "checks": 56.4, "electronic_withdrawals": 46.3,
          "other_withdrawals": 47.1, "fees": 47.0}          # mk→首行
# checks/electronic 必须保持模板实测值——GAP 参与段头 d0=(chain+gap)-tpl_mk 的
# inplace 判定，改动即破坏基准回放字节保真；67974A46 实测的 4.3/4.2 走数据覆盖
# d["gap_to"]（见 flow_engine）。
GAP_TO = {"checks": 4.0, "atm": 3.8, "electronic_withdrawals": 4.5,
          "other_withdrawals": 3.3, "fees": 4.0, "pf": 5.0,
          "daily": 3.6, "dre": 7.2}                          # 前段 end→本段 mk
TAIL_NEED = {"deposits": 6.7, "checks": 67.5, "electronic_withdrawals": 6.4,
             "other_withdrawals": 6.7, "fees": 6.7}          # 合计后的尾量(checks=脚注)
ATM_H, ATM_END_OFF = 144.1, 143.1        # ATM 块高 / mk→end 标记
PF_H, PF_END_OFF = 116.4, 116.2
DAILY_HEAD_H, DAILY_TOP_FIRST, DAILY_PITCH_B = 52.1, 143.1, 16.8
DRE_H = 246.5

# 模板块带（每页一条互不重叠；含隐藏 *start*/*end* 标记）
SEC_BANDS = {
    "deposits":  [(0, 593.6, 733.0), (1, 91.0, 510.5)],
    "checks":    [(1, 512.4, 745.0), (2, 91.0, 253.5)],
    "electronic_withdrawals": [(2, 403.7, 642.5)],
    "other_withdrawals": [(2, 644.4, 732.5)],
    "fees":      [(3, 91.0, 258.5)],
}
SEC_HEAD = {
    "deposits": (0, 593.6, 641.5), "checks": (1, 512.4, 568.3),
    "electronic_withdrawals": (2, 403.7, 449.5),
    "other_withdrawals": (2, 644.4, 691.0), "fees": (3, 91.0, 137.5),
}
SEC_CONT = {"deposits": (1, 91.0, 135.0), "checks": (2, 91.0, 135.5)}
SEC_TAIL = {
    "deposits": (1, 495.0, 510.5), "checks": (2, 180.0, 253.5),
    "electronic_withdrawals": (2, 628.0, 642.5),
    "other_withdrawals": (2, 718.0, 732.5), "fees": (3, 244.0, 258.5),
}
# 行区带（重渲染时只擦行；头/尾块可能原位复用，不可预先擦除）
SEC_ROWS = {
    "deposits":  [(0, 641.5, 733.0), (1, 135.0, 494.0)],
    "checks":    [(1, 568.3, 745.0), (2, 135.5, 179.0)],
    "electronic_withdrawals": [(2, 449.5, 627.0)],
    "other_withdrawals": [(2, 691.0, 717.0)],
    "fees":      [(3, 137.5, 243.0)],
}
ATM_BAND = (2, 256.1, 400.2)
PF_BAND = (3, 262.1, 378.5)
DAILY_HEAD_BAND = (3, 381.9, 433.5)
DAILY_ROWS_BAND = (3, 434.0, 523.5)
DRE_BAND = (4, 91.0, 337.5)
BLANK_BAND = (5, 355.0, 366.0)


def _band_locs(pg, lo, hi):
    """带内全部文本单元（bl∈[lo,hi)；F2/F3/F4=页装置不随段走）。
    不做 span 文本匹配——Symbol 圆点等非 Type3 字形也能可靠命中。"""
    out = []
    for si, s in enumerate(pg.segs):
        if s.kind != "btseg":
            continue
        for ui, u in enumerate(s.units):
            if u.bl is None or not (lo <= u.bl < hi):
                continue
            if u.font in ("F2", "F3", "F4"):
                continue
            out.append((si, ui))
    return out


def _shift_band(pages, pg_i, lo, hi, dy):
    """整带平移 dy（bl 增量，正=下移）；<0.15pt 视为不动（保回放字节）"""
    if abs(dy) < FLOW_EPS:
        return False
    pg = pages[pg_i]
    for si, ui in _band_locs(pg, lo, hi):
        u = pg.segs[si].units[ui]
        if u.pdf_y is not None:
            u.set_y_pdf(u.pdf_y - dy)
    pg.shift_lines(lo, hi, -dy)
    return True


def _erase_band(pages, pg_i, lo, hi):
    pg = pages[pg_i]
    pg.delete_units(_band_locs(pg, lo, hi))
    pg.delete_rules(lo, hi)
    pg.delete_lines(lo, hi)


def _ensure_font(doc, src_page, dst_page, fname):
    """目标页 Resources/Font 补齐源页字体名（模板同名=同对象，可直接引用）"""
    if not fname or fname in ("F2", "F3", "F4"):
        return
    if any(f[4] == fname for f in dst_page.get_fonts(full=True)):
        return
    xref = next((f[0] for f in src_page.get_fonts(full=True) if f[4] == fname), None)
    if xref is None:
        return
    rk = doc.xref_get_key(dst_page.xref, "Resources/Font")
    if rk[0] == "xref":                       # Font 字典为间接对象
        fo = int(rk[1].split()[0])
        old = doc.xref_object(fo).rstrip()
        doc.update_object(fo, old[:-2] + f" /{fname} {xref} 0 R>>")
    else:
        old = rk[1].rstrip()
        doc.xref_set_key(dst_page.xref, "Resources/Font",
                         old[:-2] + f" /{fname} {xref} 0 R>>")


def _line_records(pg, lo, hi):
    """带内矢量线段（含线宽/描边色状态，供跨页拷贝重建自包含 op）"""
    out = []
    pat = re.compile(rb"([\d.]+) w|([\d.]+ [\d.]+ [\d.]+) RG|([\d.]+) G|"
                     rb"([\d.]+) ([\d.]+) m ([\d.]+) ([\d.]+) l S")
    for si, seg in enumerate(pg.segs):
        if seg.kind != "raw":
            continue
        lw, stroke = None, b""
        for m in pat.finditer(seg.raw):
            if m.group(1) is not None:
                lw = m.group(1)
            elif m.group(2) is not None:
                stroke = m.group(2) + b" RG"
            elif m.group(3) is not None:
                stroke = m.group(3) + b" G"
            else:
                top, bot = PAGE_H - max(float(m.group(5)), float(m.group(7))), \
                    PAGE_H - min(float(m.group(5)), float(m.group(7)))
                if top < hi and bot >= lo:
                    out.append((float(m.group(4)), float(m.group(5)),
                                float(m.group(6)), float(m.group(7)), lw, stroke))
    return out


def _ensure_row_fonts(doc, pages, dst_i, fonts=("F11", "F10")):
    """行/合计字体补齐到目标页（第 4 页为字体源；p5/p6 缺 F10/F11 会渲染成乱码）"""
    for f in fonts:
        _ensure_font(doc, pages[3].page, pages[dst_i].page, f)


def _is_marker(text):
    """原稿生成器遗留的 1pt 隐藏标记单元（*start*…/*end*…）——交付不携带。
    匹配单元解码串（raw 里 Tm/Tf 在前，不能对 raw 做前缀判断）。"""
    t = (text or b"").lstrip()
    return t.startswith(b"*start*") or t.startswith(b"*end*")


def _snap_band(pages, band, keep_markers=False):
    """模板带内容快照（任何修改前捕获）：单元 raw 列表 + 矢量线记录。
    落位期一律从快照发射——移动过的内容可能落入其他段模板带，
    逐段边移边擦会误删/误拷（电子取款合计并入 other 尾块的实证教训）。
    *start*/*end* 标记单元不入快照（发射内容不携带）。"""
    pg_i, lo, hi = band
    pg = pages[pg_i]
    units = [u.pending_pre + u.raw for si, ui in _band_locs(pg, lo, hi)
             for u in (pg.segs[si].units[ui],)
             if keep_markers or not _is_marker(u.text)]   # pending 前缀并入快照副本：发射自含字体/颜色状态
    return {"units": units, "lines": _line_records(pg, lo, hi), "src": pg_i}


def _emit_band(snap, pages, dst_pg, dy, dx=0.0):
    """快照发射到目标页（dy/dx=bl/x 增量）；补字体资源、线段自包含重建。"""
    dpg, spg = pages[dst_pg], pages[snap["src"]]
    blob, fonts = [], set()
    for raw in snap["units"]:
        c = chstream.Unit(raw)
        if c.pdf_y is not None:
            c.set_y_pdf(c.pdf_y - dy)
        if dx:
            c.set_x(c.x + dx)
        blob.append(c.raw)
        if c.font:
            fonts.add(c.font)
    for x0, y0, x1, y1, lw, stroke in snap["lines"]:
        st = b"q " + (lw + b" w " if lw else b"") + (stroke + b" " if stroke else b"")
        dpg.append_text(st + (f"{x0 + dx:.1f} {y0 - dy:.1f} m {x1 + dx:.1f} "
                              f"{y1 - dy:.2f} l S Q ").encode())
    if blob:
        dpg.append_text(b"".join(blob))
    for f in sorted(fonts):
        _ensure_font(dpg.doc, spg.page, dpg.page, f)


def _emit_tail(snap, pages, L, tag, key, dst_pg, total_bl, new_val, footnote_short=False):
    """尾块（合计+脚注）发射。金额单元：值变→按新值重建（右缘=模板金额右缘）；
    值同→保留模板金额单元原样（等值不重建，否则金额凭空消失——实测坑）。"""
    flw = SEC_FLW[key]
    tpg, tlo, thi, tpl_tot = flw["tail"]
    tspan = find(L, tpg, lambda s: s["text"] == flw["total_lbl"] and s["font"] == "F10")
    tamt = find(L, tpg, lambda x: tspan and abs(x["bl"] - tspan["bl"]) < 1.5
                and x["x"] > 400) if tspan else None
    rebuild = bool(tamt and new_val and new_val != tamt["text"].strip())
    keep = []
    for raw in snap["units"]:
        if rebuild:
            u = chstream.Unit(raw)
            if u.font == "F10" and u.bl is not None and tamt \
                    and abs(u.bl - tamt["bl"]) < 1.5 and u.x is not None and u.x > 400:
                continue                    # 模板金额单元丢弃，按新值重建
        if key == "checks" and footnote_short:
            u = chstream.Unit(raw)
            if u.bl is not None:
                if 390.5 < u.bl < 403.5:    # 基准多出的两行说明（67974A46 无）
                    continue
                if 411.0 < u.bl < 418.0:    # "^ An image..." 上移补位
                    u.set_y_pdf(u.pdf_y + 21.9)
                    raw = u.raw
        keep.append(raw)
    _emit_band({"units": keep, "lines": snap["lines"], "src": snap["src"]},
               pages, dst_pg, total_bl - tpl_tot)
    if rebuild:
        dpg = pages[dst_pg]
        _ensure_font(dpg.doc, pages[snap["src"]].page, dpg.page, "F10")
        res = dpg.res_of.get("F10", "F10")
        x = tamt.get("x1", 533.0) - text_w(tag, "F10", new_val, tamt["size"])
        raw, _ = make_units(tag, new_val, "F10", tamt["size"], x, total_bl, res=res)
        dpg.append_text(raw)


_CONT_RAW_CACHE = []


def _warm_cont_cache(pages, L):
    """引擎开头（任何擦除前）捕获 '(continued)' 模板单元——它位于 checks
    续头带内，checks 不续页时会被擦除，届时再取就迟了。"""
    if _CONT_RAW_CACHE:
        return
    src = find(L, 2, lambda s: s["font"] == "F14"
               and s["text"].strip() == "(continued)")
    locs = pages[2].spans_units(src) if src else []
    _CONT_RAW_CACHE.append(
        pages[2].segs[locs[0][0]].units[locs[0][1]].raw if locs else b"")


def _cont_unit(pages, L, dst_pg, x, bl):
    """合成 '(continued)'：克隆模板 F14 单元改位（各段续页头实证结构）"""
    _warm_cont_cache(pages, L)
    raw = _CONT_RAW_CACHE[0]
    if not raw:
        return
    u = chstream.Unit(raw)
    u.set_x(x)
    u.set_y_pdf(PAGE_H - bl)
    pages[dst_pg].append_text(u.raw)
    _ensure_font(pages[dst_pg].doc, pages[2].page, pages[dst_pg].page, u.font)


def _walk_rows(rows, pg, y0, key, tn=None):
    """单页行放置（与模板行距同款数学）：返回 (placed, 剩余)。
    placed=[(y_main, last_bl, row)]；段末行须再容纳 合计14.4+段尾量（tn=变体感知
    的段尾预留，如 checks 短注脚 45.0 / 全注脚 67.5）。"""
    bot = PAGE_BOTTOM[pg]
    placed, i, y = [], 0, y0
    while i < len(rows):
        r = rows[i]
        nl = max(1, len(str(r.get("desc", "")).split("\n")))
        last_bl = y + CONT_PITCH * (nl - 1)
        # 中间行不预留合计空间（跨页时合计随段走，67974A46 p1 末行实证）；
        # 仅段末行要求合计+段尾空间与末行同页。
        # 行 y 不做额外量化：原稿各行序列（如 67974A46 fees 段 629.1/641.3/
        # 653.6/665.8/678/690.3）不服从任何闭合式舍入模型，%.1f 浮点自然
        # 舍入已是最优逼近（半数行逐点命中，其余 ±0.1pt 亚像素）。
        need = (TOTAL_AFTER_LAST + (tn if tn is not None else TAIL_NEED[key])) \
            if i == len(rows) - 1 else 0.0
        if last_bl + need > bot + 1e-6:
            break
        placed.append((y, last_bl, r))
        i += 1
        y = last_bl + PITCH
    return placed, rows[i:]


def _sim_on_tpl(rows, frags, d0):
    """用户行在模板片段网格(+d0)上的分布（等值位移是否改变跨页分布用）"""
    rleft, counts = list(rows), []
    for k, frag in enumerate(frags):
        y = frag["first"] + (d0 if k == 0 else 0.0)
        n0 = len(rleft)
        while rleft and y <= frag["bottom"] + 1e-6:
            r = rleft.pop(0)
            nl = max(1, len(str(r.get("desc", "")).split("\n")))
            y += CONT_PITCH * (nl - 1) + PITCH if nl > 1 else PITCH
        counts.append(n0 - len(rleft))
    return tuple(counts), (not rleft)


def flow_engine(pages, tag, d):
    """全链流式引擎 v2：串行规划（等值段锚模板坐标，数据变化段全流式重排）
    → 落位 → 尾空白页。返回最终页数；删页与页码重写在 build_pdf 收尾。
    段链（四原稿实证）：deposits→checks→ATM→electronic→other→fees→
    post-fees→daily→DRE；流水不足逐段上移、可跨页提前（67974A46 4 页）。"""
    L = LAYOUTS[tag]
    doc = pages[0].doc
    _warm_cont_cache(pages, L)
    order = ["deposits", "checks", "electronic_withdrawals", "other_withdrawals", "fees"]

    # ---------- Other Withdrawals 行缺省 → p1 该行以下整体上提（67974A46 实证 13.2） ----------
    sm = d.get("summary", {})
    p1_lift = 0.0
    if not str(sm.get("n_other_withdrawals", "")).strip() and not d.get("other_withdrawals"):
        p1_lift = 13.2
        lab = find(L, 0, lambda s: s["text"] == "Other Withdrawals")
        if lab:
            locs = list(pages[0].spans_units(lab))
            for sib in L[0]["spans"]:
                if abs(sib.get("bl", 0) - lab.get("bl", 0)) < 1.5 and sib["x"] > 230:
                    locs += list(pages[0].spans_units(sib))
            if locs:
                pages[0].delete_units(locs)
        snap_fe = _snap_band(pages, (0, 361.0, 402.0), bool(d.get("keep_markers")))
        snap_ms = _snap_band(pages, (0, 402.0, 595.0), bool(d.get("keep_markers")))
        _erase_band(pages, 0, 361.0, 402.0)
        _erase_band(pages, 0, 402.0, 595.0)
        _emit_band(snap_fe, pages, 0, -p1_lift)
        _emit_band(snap_ms, pages, 0, -p1_lift)

    rows_of = {}
    for key in order:
        rows = [dict(r) for r in d.get(key, [])]
        rows.sort(key=lambda r: (date_key(r.get("date", r.get("paid", ""))),
                                 date_key(r.get("paid", "")), str(r.get("no", ""))))
        if rows:
            rows[0] = dict(rows[0], _first=True)
        rows_of[key] = rows

    # ---------- 规划：等值段=模板坐标(+需求位移)；否则流式模拟 ----------
    plans = {}
    # 段尾预留（变体感知）：checks 全注脚=67.5（模板实证），短注脚=删两行说明后 45.0
    # ——混用会让 ATM 块压上注脚行（用户实测叠印事故）
    tail_need = dict(TAIL_NEED)
    if d.get("checks_footnote") == "short":
        tail_need["checks"] = 45.0
    dep_mk0 = 593.6 - p1_lift                 # deposits 段头锚（随 summary 行数联动）
    chain = (0, dep_mk0)                      # 上一原子 (页, end)
    for key in order:
        rows, flw, tpl_frags = rows_of[key], SEC_FLW[key], BIZ_SECTIONS[key]["frags"]
        tpl_mk, tpl_mk_pg, tpl_end, tpl_end_pg = \
            flw["mk"], SEC_HEAD[key][0], flw["end"], flw["tail"][0]
        gap = 0.0 if key == "deposits" else \
            float(d.get("gap_to", {}).get(key, GAP_TO[key]))
        data_eq = bool(rows) and _rows_equal_original(tag, rows, tpl_frags, key, True)
        plan = None
        if data_eq:
            top_mk = TOP_FIRST[key] - HEAD_H[key]
            if chain[0] == tpl_mk_pg:
                d0 = (chain[1] + gap) - tpl_mk
            elif chain[0] + 1 == tpl_mk_pg and abs(top_mk - tpl_mk) < FLOW_EPS:
                d0 = 0.0            # 需求=次页顶，模板恰在页顶（如 fees p4 顶）
            else:
                d0 = None
            if d0 is not None:
                if abs(d0) < FLOW_EPS:
                    plan = {"mode": "inplace"}     # 回放：模板原样（字节保真）
                    chain = (tpl_end_pg, tpl_end)
                else:
                    # 位移后跨页分布不变（且不越页底/页顶）→ 整块平移
                    c0, ok0 = _sim_on_tpl(rows, tpl_frags, 0.0)
                    c1, ok1 = _sim_on_tpl(rows, tpl_frags, d0)
                    firsts = [f["first"] + (d0 if k == 0 else 0.0)
                              for k, f in enumerate(tpl_frags)]
                    fits = all(fr["first"] + (d0 if k == 0 else 0.0) >= 89.0
                               and fr["first"] + (d0 if k == 0 else 0.0)
                               + 12.25 <= PAGE_BOTTOM[fr["pg"]] + 1e-6
                               for k, fr in enumerate(tpl_frags))
                    if c0 == c1 and ok0 and ok1 and fits:
                        plan = {"mode": "rigid", "dy": d0}
                        chain = (tpl_end_pg, tpl_end + d0)
        if plan is None:
            # —— 流式模拟（数据变了/页面变了）：绝对坐标落位 ——
            if not rows:
                plan = {"mode": "empty"}
            else:
                mk_pg = chain[0]
                first = chain[1] + gap + HEAD_H[key] if key != "deposits" \
                    else dep_mk0 + HEAD_H[key]
                if key != "deposits" and (mk_pg > 5 or \
                        first + PITCH > PAGE_BOTTOM[min(mk_pg, 5)] + 1e-6):
                    mk_pg += 1
                    first = TOP_FIRST[key]
                frags, remaining, pg = [], list(rows), mk_pg
                while remaining and pg <= 5:
                    y0 = first if not frags else TOP_FIRST[key]
                    placed, remaining = _walk_rows(remaining, pg, y0, key,
                                                   tn=tail_need[key])
                    if placed:
                        frags.append({"pg": pg, "first": y0, "placed": placed,
                                      "last_bl": placed[-1][1]})
                        pg += 1
                    elif frags:
                        break
                    else:
                        pg += 1
                        first = TOP_FIRST[key]
                if remaining:
                    print(f"[flow] 警告: {key} 截断 {len(remaining)} 行",
                          file=sys.stderr)
                if frags:
                    total_bl = frags[-1]["last_bl"] + TOTAL_AFTER_LAST
                    plan = {"mode": "rerender", "frags": frags,
                            "total_bl": total_bl, "total_pg": frags[-1]["pg"]}
                    chain = (frags[-1]["pg"], total_bl + tail_need[key])
                else:
                    plan = {"mode": "empty"}
        plans[key] = plan

        # ---- 链上静态原子（ATM 仅在 checks 后、PF 在 fees 后）----
        if key == "checks":
            if _atm_visible(d):
                chain = _plan_static(plans, chain, "atm", ATM_BAND, ATM_END_OFF,
                                     tpl_mk=256.1, tpl_end=399.2)
            else:
                plans["atm"] = {"pg": -1, "mk": 0.0, "mode": "empty"}   # 无此块：擦不发、链不推
        elif key == "fees":
            if d.get("show_pf", True):
                chain = _plan_static(plans, chain, "pf", PF_BAND, PF_END_OFF,
                                     tpl_mk=262.1, tpl_end=378.3)
            else:
                plans["pf"] = {"pg": -1, "mk": 0.0, "mode": "empty"}
            chain = _plan_daily(plans, chain, d, tag)
    chain = _plan_static(plans, chain, "dre", DRE_BAND, DRE_H, tpl_mk=91.0,
                         tpl_end=337.0)
    content_pages = chain[0] + 1
    # 空白页数可设置：blank_pages 显式指定（0~N，受模板 6 页上限）；
    # 缺省=原稿惯例——内容页为奇数自动补 1 页（双面打印）
    bp_raw = str(d.get("blank_pages", "")).strip()
    if bp_raw.isdigit():
        n_blank = min(int(bp_raw), 6 - content_pages)
    else:
        n_blank = 1 if content_pages % 2 == 1 else 0
    n_pages = min(6, content_pages + n_blank)
    # 模板第 6 页自带空白块：仅当它落在空白页区间才保留
    keep_tpl_blank = content_pages <= 5 < n_pages

    # ---------- 快照（任何修改前；规划是纯数学不影响模板） ----------
    _km = bool(d.get("keep_markers"))
    snaps = {}
    for key in order:
        snaps[f"head:{key}"] = _snap_band(pages, SEC_HEAD[key], _km)
        snaps[f"tail:{key}"] = _snap_band(pages, SEC_FLW[key]["tail"][:3], _km)
        snaps[f"band:{key}"] = [_snap_band(pages, b, _km) for b in SEC_BANDS[key]]
    for nm, band in (("atm", ATM_BAND), ("pf", PF_BAND), ("dre", DRE_BAND),
                     ("dlyhead", DAILY_HEAD_BAND), ("blank", BLANK_BAND)):
        snaps[nm] = _snap_band(pages, band, _km)
    for key in order:
        chb = SEC_FLW[key].get("cont_head")
        if chb:
            snaps[f"cont:{key}"] = _snap_band(pages, chb, _km)
    if _km:
        # 断点标记样本：段跨页断点处原稿补 *end*（本页末行+3.6，
        # 67974A46 p1 733.2→736.8 实证）
        for key in order:
            for b in snaps[f"band:{key}"]:
                hit = next((r for r in b["units"]
                            if (chstream.Unit(r).text or b"").startswith(b"*end*")), None)
                if hit:
                    snaps[f"brk:{key}"] = hit
                    break
        # daily 行区重排后 *end* 标记随末行重发（模板 518.0→521.6 = +3.6 实证）
        for s in pages[3].segs:
            if s.kind != "btseg":
                continue
            for u in s.units:
                if _is_marker(u.text) and (u.text or b"").startswith(b"*end*daily"):
                    snaps["dly_end_marker"] = u.raw

    # ---------- 擦除期：所有非原位带一次清空（杜绝移动内容被后续带误吞） ----------
    for key in order:
        if plans[key]["mode"] != "inplace":
            for (pg_i, lo, hi) in SEC_BANDS[key]:
                _erase_band(pages, pg_i, lo, hi)
    for atom, band, tpl_mk in (("atm", ATM_BAND, 256.1), ("pf", PF_BAND, 262.1),
                               ("dre", DRE_BAND, 91.0)):
        a = plans[atom]
        if not (a["pg"] == band[0] and abs(a["mk"] - tpl_mk) < FLOW_EPS):
            _erase_band(pages, band[0], band[1], band[2])
    dplan = plans["daily"]
    if dplan["mode"] != "inplace":
        _erase_band(pages, *DAILY_HEAD_BAND)
        _erase_band(pages, *DAILY_ROWS_BAND)
    # 尾空白块让位：仅当它恰好留在第 6 页原地（内容 5 页+空白）才保留
    if not keep_tpl_blank:
        _erase_band(pages, *BLANK_BAND)

    # ---------- 发射期：全部从快照落位 ----------
    for key in order:
        plan = plans[key]
        if plan["mode"] == "inplace":
            continue
        if plan["mode"] == "rigid":
            for b in snaps[f"band:{key}"]:
                _emit_band(b, pages, b["src"], plan["dy"])
            continue
        if plan["mode"] == "empty":
            continue
        hlo = SEC_HEAD[key][1]
        f0 = plan["frags"][0]
        _emit_band(snaps[f"head:{key}"], pages, f0["pg"],
                   f0["first"] - HEAD_H[key] - hlo)
        for fr in plan["frags"][1:]:
            _place_synth_cont(snaps, pages, L, tag, key, fr["pg"],
                              dy=float(d.get("cont_head_dy", 0.0)))
        tpl_frags = BIZ_SECTIONS[key]["frags"]
        for k, fr in enumerate(plan["frags"]):
            _emit_rows(pages, tag, fr, key, tpl_frags[min(k, len(tpl_frags) - 1)],
                       sec_end=(k == len(plan["frags"]) - 1))
        mraw = snaps.get(f"brk:{key}")
        if mraw:
            # 跨页断点：原稿在本页末行 +3.6 处补 *end* 标记（deposits p1 断点实证）
            for fr in plan["frags"][:-1]:
                mu = chstream.Unit(mraw)
                mu.set_y_pdf(PAGE_H - (fr["last_bl"] + 3.6))
                pages[fr["pg"]].append_text(mu.raw)
        tot = sum(_cents(r["amount"]) for r in d.get(key, []))
        _emit_tail(snaps[f"tail:{key}"], pages, L, tag, key, plan["total_pg"],
                   plan["total_bl"], _fmt(abs(tot), dollar=True),
                   footnote_short=(key == "checks" and d.get("checks_footnote") == "short"))

    # checks 注脚短版：发射字节里删基准多出的两行说明、"^ An image..." 上移补位（67974A46 实证）
    if d.get("checks_footnote") == "short":
        import re as _re2
        # 注脚两行说明删除——但须放过恰好落入同 y 窗口的 *start*/*end* 标记
        # （67974A46 的 *start*electronic 落 pdf 398.35，曾被此式误删）
        _unit = _re2.compile(rb"1 0 0 1 ([\d.-]+) (39[6-9]\.[\d]+|40[0-9]\.[\d]+) Tm\n?"
                             rb"(?:(?!\(\*start\*|\(\*end\*)[^E])*?Tj\n?")
        # 脚注缩短后 *end*checks 标记随之上移（模板距 Total 65.1 → 原稿 45.4 = -22.2...
        # pdf 向上 = +22.2；67974A46 412.0→389.8 实证）
        _mk_end = _re2.compile(rb"1 0 0 1 ([\d.-]+) ([\d.]+) Tm\n?(/F1 [^\n]*\n)?\(\*end\*checks")
        _aim = _re2.compile(rb"1 0 0 1 ([\d.-]+) 385\.85 Tm")
        if float(d.get("cont_head_dy", 0.0)):
            _hdr = _re2.compile(rb"1 0 0 1 ([\d.-]+) 671\.90 Tm")
            _cnt = _re2.compile(rb"1 0 0 1 ([\d.-]+) 695\.20 Tm")
        for seg in pages[1].segs:
            if seg.kind != "raw":
                continue
            seg.raw = _unit.sub(b"", seg.raw)
            seg.raw = _aim.sub(rb"1 0 0 1 \1 407.95 Tm", seg.raw)
            seg.raw = _mk_end.sub(
                lambda m: b"1 0 0 1 " + m.group(1) + b" " +
                f"{float(m.group(2)) + 22.2:.2f}".encode() + b" Tm\n" +
                (m.group(3) or b"") + b"(*end*checks", seg.raw)
            if float(d.get("cont_head_dy", 0.0)):
                seg.raw = _hdr.sub(rb"1 0 0 1 \1 665.70 Tm", seg.raw)
                seg.raw = _cnt.sub(rb"1 0 0 1 \1 689.90 Tm", seg.raw)
                # 头区三条装饰线保持原稿原位（随快照位移了 → 回移；原稿实证线不动文字动）
                for old_y, new_y in ((b"703.20", b"697.40"), (b"703.60", b"697.40"),
                                      (b"688.80", b"682.60"), (b"683.30", b"677.10")):
                    seg.raw = seg.raw.replace(b" " + old_y + b" m", b" " + new_y + b" m")
                    seg.raw = seg.raw.replace(b" " + old_y + b" l", b" " + new_y + b" l")
            # fees Total 行的部分宽细线（418.3-535.4，67974A46 实证）
            if b"99 m" not in seg.raw:
                seg.raw += b"q 0.72 w 0 0 0 RG 418.3 99 m 535.4 99 l S Q\n"

    # ATM / PF / DRE 静态块
    for atom, band, tpl_mk in (("atm", ATM_BAND, 256.1), ("pf", PF_BAND, 262.1),
                               ("dre", DRE_BAND, 91.0)):
        a = plans[atom]
        if a.get("mode") == "empty":
            continue
        if a["pg"] == band[0] and abs(a["mk"] - tpl_mk) < FLOW_EPS:
            if atom == "atm" and d.get("atm"):
                _atm_apply_page(pages, L, d)   # 原位块：活页替换
            continue
        snap = snaps[atom]
        if atom == "atm" and d.get("atm"):
            snap = _atm_patch_snap(snap, L, tag, d)   # 移动块：快照内替换
        _emit_band(snap, pages, a["pg"], a["mk"] - band[1])
    _apply_daily(pages, L, tag, d, plans, snaps)

    # 尾空白页（仅内容页为奇数时存在；模板空白块在第 6 页）
    # 尾空白页（模板空白块在第 6 页；其余空白页从快照发射，含多页场景）
    for bpi in range(content_pages, n_pages):
        if bpi != 5:                          # 第 6 页沿用模板自带块
            _emit_band(snaps["blank"], pages, bpi, 0.0)
        # 末页页眉/页脚 p6 式变体由 build_pdf 统一后处理（内容末页同样适用）
    # 清除全部 *start*/*end* 隐藏标记（默认交付不携带；keep_markers=true 时保留=与原稿一致）
    for pi in range(n_pages if not d.get("keep_markers") else 0):
        pg = pages[pi]
        locs = [(si, ui) for si, ui in pg._loc
                if _is_marker(pg.segs[si].units[ui].text)]
        pg.delete_units(locs)
    return n_pages


def _atm_targets(L, d):
    """ATM 块可替换单元定位（模板坐标）：卡片行两段 + 三组金额各两处
    （卡下小计行 + "ATM & Debit Card Totals" 区，同值重复出现）。
    返回 [(模板span, 新文本)]；金额自动补 $。"""
    atm = d.get("atm") or {}
    out = []
    lo, hi = ATM_BAND[1], ATM_BAND[2]
    spans = L[ATM_BAND[0]]["spans"]
    if str(atm.get("name", "")).strip():
        for s in spans:
            if lo <= s["bl"] < hi and s["font"] == "F11" and s["x"] < 60 \
                    and s["text"].strip() and "Card" not in s["text"]:
                out.append((s, str(atm["name"]).strip()))
    if str(atm.get("card_no", "")).strip():
        for s in spans:
            if lo <= s["bl"] < hi and s["text"].strip().startswith("Card "):
                out.append((s, f"Card {str(atm['card_no']).strip()}"))
    amt_map = {}
    for lbl_t, key in (("Withdrawals", "withdrawals"), ("Purchases", "purchases"),
                       ("Deposits & Credits", "credits")):
        v = str(atm.get(key, "")).strip()
        if v:
            amt_map[lbl_t] = v if v.startswith("$") else "$" + v
    if amt_map:
        for lab in spans:
            if not (lo <= lab["bl"] < hi and lab["font"] == "F11" and lab["x"] < 200):
                continue
            hit = next((k for k in amt_map if k in lab["text"]), None)
            if not hit:
                continue
            for s in spans:
                if abs(s["bl"] - lab["bl"]) < 1.5 and s["x"] > 400 \
                        and s["text"].strip().startswith("$"):
                    out.append((s, amt_map[hit]))
                    break
    return out


def _atm_apply_page(pages, L, d):
    """原位 ATM 块：活页直接替换（同文自动跳过=保回放字节）"""
    pg = pages[ATM_BAND[0]]
    for s, txt in _atm_targets(L, d):
        if s["text"].strip().startswith("$"):
            pg.replace_span(s, txt, align="r", right_edge=s.get("x1"))
        else:
            pg.replace_span(s, txt)


def _atm_patch_snap(snap, L, tag, d):
    """移动 ATM 块：发射前在快照单元上替换（dy 由 _emit_band 统一应用）。
    按 (bl, 字体, x 窗口) 命中——卡户名与 "Card NNNN" 同基线同字体，须靠 x 区分。"""
    out = list(snap["units"])
    for s, txt in _atm_targets(L, d):
        x = s["x"]
        if s["text"].strip().startswith("$"):
            x = s.get("x1", x + 60) - text_w(tag, s["font"], txt, s["size"])
        raw, _ = make_units(tag, txt, s["font"], s["size"], x, s["bl"])
        for i, r in enumerate(out):
            u = chstream.Unit(r)
            if u.bl is None or u.font != s["font"]:
                continue
            if abs(u.bl - s["bl"]) < 0.6 and abs(u.x - s["x"]) < 2.0:
                out[i] = raw
                break
    return {"units": out, "lines": snap["lines"], "src": snap["src"]}


def _atm_visible(d):
    """ATM 块显隐：显式 show_atm 优先；缺省自动判断——三组金额全部显式填 0
    （本期无 ATM/借记卡相关交易）则隐藏，电子取款等后续段落随链自动上移
    （67974A46 原稿实证）。留空=模板原值（非零）→ 显示。"""
    if "show_atm" in d:
        return bool(d["show_atm"])
    atm = d.get("atm") or {}
    def _explicit_zero(v):
        v = str(v or "").strip()
        return bool(v) and _cents(v.lstrip("$")) == 0
    return not (_explicit_zero(atm.get("withdrawals"))
                and _explicit_zero(atm.get("purchases"))
                and _explicit_zero(atm.get("credits")))


def _plan_static(plans, chain, atom, band, end_off, tpl_mk, tpl_end):
    """静态原子（ATM/PF/DRE）：需求=链上同页 mk（放得下）或次页顶 91。
    是否原位由落位阶段按 (pg, mk) 对模板判定。"""
    h = band[2] - band[1]
    mk = chain[1] + GAP_TO[atom]
    if chain[0] <= 5 and mk + h <= PAGE_BOTTOM[chain[0]] + 1e-6:
        pg, m = chain[0], mk
    else:
        pg, m = min(chain[0] + 1, 5), 91.0
    plans[atom] = {"pg": pg, "mk": m}
    return (pg, m + end_off)


def _plan_daily(plans, chain, d, tag):
    """daily：数据同表且链条允许 → 模板原位/整块平移；否则流式分块。
    返回推进后的 chain。"""
    cells = d.get("daily_balance") or []
    if not cells:
        plans["daily"] = {"mode": "empty"}
        return chain
    flat_n = [str(t) for a, b in cells for t in (str(a), str(b))]
    by_col, by_row = _daily_orig_flats(LAYOUTS[tag])
    data_eq = flat_n == by_col or flat_n == by_row
    if data_eq and chain[0] == 3:
        mk = chain[1] + GAP_TO["daily"]
        if abs(mk - 381.9) < FLOW_EPS:
            plans["daily"] = {"mode": "inplace"}
            return (3, 521.6)
        if mk + DAILY_HEAD_H + DAILY_PITCH_B <= PAGE_BOTTOM[3] + 1e-6:
            plans["daily"] = {"mode": "rigid", "dy": mk - 381.9}
            return (3, 521.6 + (mk - 381.9))
    # 流式分块
    if chain[0] >= 5 or chain[1] + GAP_TO["daily"] + DAILY_HEAD_H + DAILY_PITCH_B \
            > PAGE_BOTTOM[chain[0]] + 1e-6:
        pg0, first = min(chain[0] + 1, 5), DAILY_TOP_FIRST
    else:
        pg0, first = chain[0], chain[1] + GAP_TO["daily"] + DAILY_HEAD_H
    chunks, pg, left, cells_left = [], pg0, first, list(cells)
    while cells_left and pg <= 5:
        rows_avail = int((PAGE_BOTTOM[pg] - left) // DAILY_PITCH_B) + 1
        chunk, cells_left = cells_left[:rows_avail * 3], cells_left[rows_avail * 3:]
        if not chunk:
            break
        chunks.append({"pg": pg, "first": left, "cells": chunk})
        pg += 1
        left = DAILY_TOP_FIRST
    plans["daily"] = {"mode": "rerender", "chunks": chunks}
    last = chunks[-1]
    nrows = max(1, -(-len(last["cells"]) // 3))
    return (last["pg"], last["first"] + (nrows - 1) * DAILY_PITCH_B + 3.6)


def _place_synth_cont(snaps, pages, L, tag, key, dst_pg, dy=0.0):
    """合成续页头：头块快照发射到页顶落位 + '(continued)'
    （14A 电子/日终续页实证：103 续标 + 108 标题 + 列头 + 137/140 行）"""
    hlo = SEC_HEAD[key][1]
    top_first = TOP_FIRST[key]
    csnap = snaps.get(f"cont:{key}")
    if csnap:                                 # 模板续页头快照原位回填（保真坐标/字距）
        _emit_band(csnap, pages, dst_pg, dy)
        if dy:                                # 仅标题/续标位移时：表头行回位（原稿实证表头不随动）
            dpg = pages[dst_pg]
            chb = SEC_FLW[key]["cont_head"]
            for si in range(len(dpg.segs)):
                seg = dpg.segs[si]
                if seg.kind != "btseg":
                    continue
                for u in seg.units:
                    if u.bl is None or u.pdf_y is None:
                        continue
                    bl0 = u.bl + dy           # 发射后 bl
                    if chb[1] <= bl0 < chb[2] and bl0 > 116:   # 表头行区
                        u.set_y_pdf(u.pdf_y - dy)
                    elif chb[1] <= bl0 < chb[2] and u.x is not None and u.x > 150 \
                            and b"continued" not in (u.text or b""):
                        pass
        return
    _emit_band(snaps[f"head:{key}"], pages, dst_pg,
               top_first - HEAD_H[key] - hlo)
    hpg = SEC_HEAD[key][0]
    title = next((s for s in L[hpg]["spans"]
                  if hlo <= s["bl"] < SEC_HEAD[key][2] and s["font"] == "F12"), None)
    if title:
        t_bl = top_first - HEAD_H[key] + (title["bl"] - hlo)
        x = title["x"] + text_w(tag, "F12", title["text"], title["size"]) + 6.0
        _cont_unit(pages, L, dst_pg, x, t_bl - 5.5)


def _emit_rows(pages, tag, fr, key, frag_def, sec_end=False):
    """按模拟结果渲染行（单元+行线），追加到目标页。
    sec_end=本片段含全段末行：末行下叠 1.44 粗线（+2.65 于 0.72 细线 +2.75，
    模板五段合计上方实测一致；跨页行只有细线）。"""
    pg = pages[fr["pg"]]
    _ensure_row_fonts(pages[0].doc, pages, fr["pg"])
    res = pg.res_of.get(ROW_FONT, ROW_FONT)
    blob = []
    n = len(fr["placed"])
    for ri, (y, last_bl, r) in enumerate(fr["placed"]):
        lines = str(r.get("desc", "")).split("\n")
        amt = fmt_amount(r["amount"])
        if r.get("_first") and not amt.startswith("$") and not amt.startswith("-"):
            amt = "$" + amt
        row2 = dict(r, amount=amt, desc=lines[0])
        for txt, x, ra in _row_cells(tag, key, row2, frag_def):
            if ra is not None:
                x = ra - text_w(tag, ROW_FONT, txt, ROW_SIZE)
            raw, _ = make_units(tag, txt, ROW_FONT, ROW_SIZE, x, y, res=res)
            blob.append(raw)
        yy = y + CONT_PITCH
        for j, cl in enumerate(lines[1:]):
            raw, _ = make_units(tag, cl, ROW_FONT, ROW_SIZE,
                                frag_def.get("desc", frag_def.get("date", 35.0)),
                                yy, res=res)
            blob.append(raw)
            yy += CONT_PITCH if j < len(lines) - 2 else PITCH
        # 模板线型实证：每行只在最后一根文本线下画一条 0.72 整线；
        # 多行行的主行/中间续行下【无】部分线（多画=用户所见"多半条线"）；
        # 段末行=1.44 粗线(+2.65) 叠 整线(+2.75)，且多行行的段末额外叠
        # 金额列部分线(+2.75)（模板 elec/other/fees 段末与 checks/deposits
        # 单行段末逐线对比）。
        if frag_def.get("rule"):
            pg.add_rule(last_bl + 2.75, *frag_def["rule"])
            if sec_end and ri == n - 1:
                pg.add_rule(last_bl + 2.65, *frag_def["rule"], w=1.44)
                if len(lines) > 1 and frag_def.get("rule_p"):
                    pg.add_rule(last_bl + 2.75, *frag_def["rule_p"])
    if blob:
        pg.append_text(b"".join(blob))


def _daily_orig_flats(L):
    """模板日终表两种读序扁平化：列序（自上而下逐列=时间序）与行序（阅读序）。
    新数据为时间序——列序比对判"同表"；行序兼容回放 JSON 的存储序。"""
    spec = BIZ_DAILY
    xs = [c[0] for c in spec["cols"]] + [c[1] for c in spec["cols"]]
    cells = {}
    for s in L[spec["pg"]]["spans"]:
        if s["font"] not in ("F10", "F11"):
            continue
        if not (spec["first"] - 6 <= s["bl"] < spec["bottom"] + 7):
            continue
        if any(abs(s["x"] - v) < 2.0 for v in xs) or any(
                s.get("x1", 0) > v - 3 and abs(s.get("x1", 0) - v) < 3
                for v in [c[1] for c in spec["cols"]]):
            cells.setdefault(round(s["bl"], 1), []).append((s["x"], s["text"].strip()))
    by_row = [t for bl in sorted(cells) for _, t in sorted(cells[bl])]
    ncol = len(spec["cols"])
    nrows = -(-len(by_row) // (2 * ncol)) if by_row else 0
    by_col = []
    for c in range(ncol):
        for r in range(nrows):
            for i in (2 * (c + r * ncol), 2 * (c + r * ncol) + 1):
                if i < len(by_row):
                    by_col.append(by_row[i])
    return by_col, by_row


def _apply_daily(pages, L, tag, d, plans, snaps):
    """daily 落位：inplace 原样（擦除期已统一处理其余模式的清理）；
    rigid/reerender 均从快照重排（平衡列填充，块内 ceil(n/3) 行/列——
    67974: 5,5,4 / 14A 续页: 4,4,3 实证）。"""
    plan = plans["daily"]
    if plan["mode"] in ("inplace", "empty"):
        return
    cells = [[str(a), str(b)] for a, b in d.get("daily_balance") or []]
    chunks = (list(plan["chunks"]) if plan["mode"] == "rerender"
              else [{"pg": 3, "first": BIZ_DAILY["first"] + plan["dy"],
                     "cells": cells}])
    first_chunk = chunks[0]
    _emit_band(snaps["dlyhead"], pages, first_chunk["pg"],
               first_chunk["first"] - DAILY_HEAD_H - DAILY_HEAD_BAND[1])
    for ci, ch in enumerate(chunks):
        if ci > 0:                              # 续页：头块发射到页顶 + (continued)
            _emit_band(snaps["dlyhead"], pages, ch["pg"],
                       DAILY_TOP_FIRST - DAILY_HEAD_H - DAILY_HEAD_BAND[1])
            title = next((s for s in L[3]["spans"] if DAILY_HEAD_BAND[1] <= s["bl"]
                          < DAILY_HEAD_BAND[2] and s["font"] == "F12"), None)
            if title:
                x = title["x"] + text_w(tag, "F12", title["text"], title["size"]) + 6.0
                _cont_unit(pages, L, ch["pg"], x, 108.5 - 5.5)
        pg = pages[ch["pg"]]
        _ensure_row_fonts(pages[0].doc, pages, ch["pg"])
        res = pg.res_of.get(ROW_FONT, ROW_FONT)
        n = len(ch["cells"])
        per = -(-n // 3)
        blob = []
        first_cell = (ci == 0)
        for ri in range(per):
            y = ch["first"] + ri * DAILY_PITCH_B
            for col in range(3):
                idx = col * per + ri
                if idx >= n:
                    continue
                dt, amt = ch["cells"][idx]
                raw, _ = make_units(tag, dt, ROW_FONT, ROW_SIZE,
                                    BIZ_DAILY["cols"][col][0], y, res=res)
                blob.append(raw)
                a = amt
                if first_cell and not a.startswith("$"):
                    a = "$" + amt
                    first_cell = False
                x = BIZ_DAILY["cols"][col][1] - text_w(tag, ROW_FONT, a, ROW_SIZE)
                raw, _ = make_units(tag, a, ROW_FONT, ROW_SIZE, x, y, res=res)
                blob.append(raw)
        if blob:
            pg.append_text(b"".join(blob))
        mraw = snaps.get("dly_end_marker") if ci == len(chunks) - 1 else None
        if mraw:      # 原稿 *end*daily 标记贴末行 +3.6（模板 518→521.6、本稿 210.3→213.9）
            mu = chstream.Unit(mraw)
            mu.set_y_pdf(PAGE_H - (ch["first"] + (per - 1) * DAILY_PITCH_B + 3.6))
            pg.append_text(mu.raw)


_normalize_type3_glyphs = common._normalize_type3_glyphs


def _prep_sig(tag):
    return common.prepared_signature(tag, BASE, TEMPLATES)


def _open_prepared(tag):
    return common.open_prepared(
        tag, BASE, TEMPLATES, _FONT_CODEC, _prep_sig,
        extend_all_fonts, ensure_f3_digits,
    )


def build_pdf(d, out_path, watermark=True):
    d = json.loads(json.dumps(d))
    tag = d["template"]
    if tag != "business":
        raise ValueError(f"本模块仅处理 business，收到 {tag}")
    biz_calc(d) if d.get("auto", True) else None
    doc = _open_prepared(tag)
    L = LAYOUTS[tag]
    subs = build_subs(d)
    pages = [Page(doc, p, tag) for p in doc]
    anchors = {}

    def R(pi, old, new, align=None, right=None):
        s = by_text(L, pi, old)
        if s:
            return pages[pi].replace_span(s, sub_text(new, subs), align, right)
        return False

    def by_text(L, pi, text):
        for s in L[pi]["spans"]:
            if s["text"] == text:
                return s
        return None

    # ===== 字体扩展（最先：保证后续编码有码位）=====
    # 已随预备模板缓存完成（_open_prepared），此处不再重复

    # ===== 公共页头：期间 / 银行名替换 =====
    for pi in range(len(pages)):
        for s in L[pi]["spans"]:
            if "through" in s["text"] and "202" in s["text"]:
                # 原稿期间行按列中心居中（非右对齐）：模板/原稿各页中心一致
                # p1≈440.45、中间页 438.5、末页 440.65（双稿实证）
                lab = d["period"]["label"]
                cx = (s["x"] + s.get("x1", s["x"])) / 2.0
                re_ = cx + text_w(tag, s["font"], lab, s["size"]) / 2.0
                pages[pi].replace_span(s, lab, align="r", right_edge=re_)
            new = sub_text(s["text"], subs)
            if new != s["text"]:
                pages[pi].replace_span(s, new)

    # ===== 账号显示（右上 F2 数字串）=====
    acc = d.get("account_display")
    if acc:
        for pi in range(len(pages)):
            s = find(L, pi, lambda s: s["font"] == "F2" and s["bl"] < 100 and s["x"] > 300)
            if s:
                pages[pi].replace_span(s, acc)

    # ===== MICR 行 =====
    micr = d.get("micr")
    if micr:
        micr_spans = [s for s in L[0]["spans"] if s["font"] == "F9" and s["bl"] < 145]
        parts = [micr.get("routing", ""), micr.get("dre", ""),
                 micr.get("cycle", ""), micr.get("extra", "")]
        for s, val in zip(micr_spans, parts):
            if val:
                pages[0].replace_span(s, val)

    # ===== 客户信息（3 行 + 邮编）=====
    cust = [s for s in L[0]["spans"]
            if s["font"] == "F11" and 145 < s["bl"] < 185 and s["x"] < 200]
    cust.sort(key=lambda s: (s["bl"], s["x"]))
    lines = [s for s in cust if abs(s["x"] - 55.4) < 2]
    zips = [s for s in cust if s["x"] > 100]
    for i, ln in enumerate(d.get("customer_lines", [])[:3]):
        if i < len(lines):
            R(0, lines[i]["text"], ln)
    if d.get("zip") and zips:
        R(0, zips[0]["text"], d["zip"])

    # ===== 摘要区（按标签匹配标签/笔数/金额）=====
    sm = d.get("summary", {})
    spec = BIZ_SUMMARY
    for label, key, dollar, has_count in spec:
        lab = find(L, 0, lambda s: s["text"] == label and 275 < s["bl"] < 415)
        if not lab:
            continue
        same = [s for s in L[0]["spans"]
                if abs(s["bl"] - lab["bl"]) < 1.5 and s["x"] > lab.get("x1", lab["x"])]
        for s in same:
            if s["x"] > 300:  # 金额
                val = sm.get(key)
                if val:
                    pages[0].replace_span(s, val, align="r", right_edge=s.get("x1"))
            elif has_count and s["x"] > 230:  # 笔数
                val = sm.get("n_" + key)
                if val:
                    pages[0].replace_span(s, val, align="r", right_edge=s.get("x1"))

    # ===== MSF 豁免说明行内数字（静态区，仅数字随数据变）=====
    msf = d.get("msf") or {}
    if msf.get("lowest_daily"):
        s0 = find(L, 0, lambda s: "lowest daily ending balance was" in s["text"])
        if s0:
            pages[0].replace_span(
                s0, re.sub(r"\$[\d,]+\.\d\d", "$" + msf["lowest_daily"], s0["text"], count=1))
    if msf.get("payment_solutions"):
        s0 = find(L, 0, lambda s: "was deposited into this account" in s["text"])
        if s0:
            pages[0].replace_span(
                s0, re.sub(r"\$[\d,]+\.\d\d", "$" + msf["payment_solutions"], s0["text"], count=1))

    # ===== 银行地址（左上第二/三行）=====
    baddr = d.get("bank_addr")
    if baddr:
        for old, new in (("P O Box 182051", baddr[0]),
                         ("Columbus, OH 43218 - 2051", baddr[1] if len(baddr) > 1 else "")):
            if not new:
                continue
            sp = by_text(L, 0, old)
            if not sp:
                continue
            if " - " in new and " - " in old:
                # 原稿生成器把城市行 " - " 的两个空格渲染成 F6 0.24pt 微标（@ 字节），
                # 整段 Helve 重建会丢失——按原稿几何分段重建；整行一次 append
                # （分段 replace+append 会让文本抽取把行拆成三条）
                part1, part2 = new.split(" - ", 1)
                part1, part2 = sub_text(part1, subs), sub_text(part2, subs)
                pg0 = pages[0]
                res = pg0.res_of.get(sp["font"], sp["font"])
                x0, bl = sp["x"], sp["bl"]
                blob, w1 = make_units(tag, part1, sp["font"], sp["size"], x0, bl, res=res)
                dash = text_w(tag, sp["font"], "-", sp["size"])
                y = PAGE_H - bl
                blob += (f"1 0 0 1 {x0 + w1:.2f} {y:.2f} Tm\n/F6 0.24 Tf\n(@)Tj\n").encode()
                r2, _ = make_units(tag, "-", sp["font"], sp["size"],
                                   x0 + w1 + 1.2, bl, res=res)
                blob += r2
                blob += (f"1 0 0 1 {x0 + w1 + 1.2 + dash:.2f} {y:.2f} Tm\n"
                         f"/F6 0.24 Tf\n(@)Tj\n").encode()
                r3, _ = make_units(tag, part2, sp["font"], sp["size"],
                                   x0 + w1 + 1.2 + dash + 1.2, bl, res=res)
                blob += r3
                pg0.delete_units(pg0.spans_units(sp))
                pg0.append_text(blob)
            else:
                pages[0].replace_span(sp, sub_text(new, subs))

    # ===== 期初余额行（交易明细顶部）=====
    bb = find(L, 0, lambda s: s["text"] == "Beginning Balance" and s["font"] == "F10"
              and s["bl"] > 500)
    if bb:
        amt = find(L, 0, lambda x: abs(x["bl"] - bb["bl"]) < 1.5 and x["x"] > 400
                   and x["text"].strip().startswith("$"))
        if amt:
            pages[0].replace_span(amt, _fmt(_cents(d.get("begin_balance", 0)), dollar=True),
                                  align="r", right_edge=amt.get("x1"))

    # ===== 条码（三页各自变号 + F3 竖排号同步）=====
    replace_barcodes(doc, pages, L, d.get("barcode_num"))

    # ===== 流水段（全链流式引擎 v2：段落上移/跨页/页数自适应）=====
    n_pages = flow_engine(pages, tag, d)

    # ===== 页码（页号/总页数分开；同值跳过保回放字节）=====
    for pi in range(n_pages):
        f4 = sorted([s for s in L[pi]["spans"]
                     if s["font"] == "F4" and len(s["text"].strip()) <= 2
                     and s["text"].strip().isdigit()], key=lambda s: s["x"])
        if len(f4) >= 1:
            pages[pi].replace_span(f4[0], f" {pi + 1} ")
        if len(f4) >= 2:
            pages[pi].replace_span(f4[1], f" {n_pages}")

    # ===== 末页页眉/页脚变体（首页与末页共用 p6 式坐标，中间页另一套——
    #       模板 p1 433.6/p6 433.9 与 67974A46 p1/p4 双稿实证）=====
    last = n_pages - 1
    if last not in (0, 5):
        lg = pages[last]
        # 期间行/F2 账号此前已被 replace 重建，spans_units 按模板文本匹配会落空——
        # 改几何匹配（同基线 + span x 窗口内取全部文本单元）
        def _shift_span(sp, nx):
            if not sp:
                return
            x1 = sp.get("x1", sp["x"] + 90)
            for u in lg.units:
                if not u.raw or u.text is None or u.bl is None or u.x is None:
                    continue
                # 上界 x1-0.3：相邻 span 在 x1 处无缝相接（AN|F2），避免边界单元双重位移
                if abs(u.bl - sp["bl"]) < 1.5 and sp["x"] - 1.5 <= u.x < x1 - 0.3:
                    u.set_x(nx + (u.x - sp["x"]))
        _shift_span(find(L, last, lambda s: s["font"] == "F2"
                         and s["bl"] < 100 and s["x"] > 300), 433.9)
        _shift_span(find(L, last, lambda s: s["text"].startswith("Account Number")), 362.4)
        pd_ = find(L, last, lambda s: "through" in s["text"] and "202" in s["text"])
        if pd_:
            w = text_w(tag, pd_["font"], d["period"]["label"], pd_["size"])
            _shift_span(pd_, 440.65 - w / 2)
        # 页脚 "Page N of M"：768.5→766.6 上提 1.9、右移 1.6（=模板 p6 坐标）。
        # 逐单元直取（_band_locs 设计上排除 F2/F3/F4 页装置字体，用它会漏 F4 数字）
        for si, s in enumerate(lg.segs):
            if s.kind != "btseg":
                continue
            for u in s.units:
                if not u.raw or u.bl is None or not (764.0 <= u.bl < 772.0):
                    continue
                if u.pdf_y is not None:
                    u.set_y_pdf(u.pdf_y + 1.9)
                if u.x is not None:
                    u.set_x(u.x + 1.6)

    for p in pages:
        p.commit()
    # 每页字体资源裁剪：仅保留内容流实际引用的资源名（原稿每页按需子集实证）
    for pi in range(len(doc)):
        page = doc[pi]
        try:
            data = b"".join(doc.xref_stream(x) for x in page.get_contents())
        except Exception:
            continue
        used = set(m.group(1) for m in re.finditer(rb"/(\w+)\s+[\d.]+\s+Tf", data))
        rk = doc.xref_get_key(page.xref, "Resources")
        tgt = page.xref
        if rk and rk[0] == "xref":
            tgt = int(rk[1].split()[0])
        fd = doc.xref_get_key(tgt, "Resources/Font") if tgt == page.xref \
            else doc.xref_get_key(tgt, "Font")
        if fd and fd[0] == "dict":
            entries = re.findall(r"/(\w+)\s+(\d+)\s+0\s+R", fd[1])
            keep = " ".join(f"/{n} {x} 0 R" for n, x in entries if n in used)
            if keep:
                key = "Resources/Font" if tgt == page.xref else "Font"
                doc.xref_set_key(tgt, key, f"<< {keep} >>")
    # 多余尾页删除（引擎已把内容/尾空白页前移收敛）
    for pi in range(len(pages) - 1, n_pages - 1, -1):
        doc.delete_page(pi)
    if watermark:
        for p in doc:
            add_watermark(doc, p)

    # ===== 元数据（缺省=原稿原样）=====
    write_meta(doc, tag, d.get("metadata", {}))

    # ===== 保存（默认不加密=与原稿一致；可选加密=标准安全处理器 V1/R2/40位RC4、
    # P=-12：禁止修改内容，允许批注/复制/打印；空阅读密码=无密码直接打开——
    # 与大通原稿 989BD2E8/67974A46 的 /Encrypt 字典同构）=====
    if d.get("encrypt"):
        doc.save(out_path, garbage=3, deflate=True,
                 encryption=fitz.PDF_ENCRYPT_RC4_40, owner_pw="chase_owner",
                 user_pw="", permissions=-12)
    else:
        doc.save(out_path, garbage=3, deflate=True)
    doc.close()
    return out_path
