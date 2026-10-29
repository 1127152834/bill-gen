#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大通（Chase）个人版生成器（独立模块，不与另一版本混用）
模板=原稿字节级静态克隆；动态区经内容流单元精确重写。
同数据回放：文本相同即跳过 → 页面内容流零改动，与原稿一致。
几何常量均从原稿逐 span 实测（layout_raw.json）。"""
import hashlib
import io, json, math, os, re, sys
from decimal import Decimal

import fitz

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import chstream
import extend_fonts as EF
import barcode as BC

PAGE_H = 792.0
TABLES = json.load(open(os.path.join(BASE, "assets", "font_tables_personal.json")))
TEMPLATES = { "personal": os.path.join(BASE, "assets", "chase_static_personal.pdf") }
LAYOUTS = { "personal": json.load(open(os.path.join(BASE, "layout_personal.json"))) }

# 原稿 Info 元数据（字节级原样，含尾随空格与 UTC00'00' 格式）
ORIG_META = {'personal': {'title': 'SAMPLE — NOT A REAL BANK STATEMENT', 'author': '', 'creator': 'BillGen Sample Editor', 'producer': 'BillGen Sample Editor', 'subject': 'Fictional sample statement', 'keywords': 'sample'}}

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
PITCH, CONT_PITCH = 12.25, 9.4           # 主行距 / 描述续行距（两份原稿实测 0.1 对齐）


def encode(tag, font, text):
    m = CH2CODE.get(tag, {}).get(font)
    if f"{tag}:{font}" in TABLES and TABLES[f"{tag}:{font}"]["type"] == "Type3":
        out = bytearray()
        for ch in text:
            c = m.get(ch) if m else None
            if c is None:
                c = 63
            out.append(c)
        return bytes(out)
    try:
        return text.encode("cp1252")
    except Exception:
        return text.encode("latin-1", "replace")


def char_w(tag, font, ch, size):
    if f"{tag}:{font}" in TABLES and TABLES[f"{tag}:{font}"]["type"] == "Type3":
        m = CH2CODE.get(tag, {}).get(font, {})
        code = m.get(ch, 63)
        w = WIDTHS.get(tag, {}).get(font)
        if w and code < len(w):
            return w[code] * size
        return 5.0 * size * 20
    f = _STD.get(font)
    if f is None:
        return size * 0.5
    return f.text_length(ch, fontsize=1000) * size / 1000


def text_w(tag, font, text, size):
    return sum(char_w(tag, font, c, size) for c in text)


def fmt_amount(v):
    """金额统一千分位（保留 $/负号，与原稿样式一致）"""
    v = str(v).strip()
    m = re.match(r"^(\$?)(-?)([\d,]+)(\.\d+)?$", v)
    if not m:
        return v
    dol, sign, intpart, frac = m.groups()
    intpart = f"{int(intpart.replace(',', '')):,}"
    return f"{dol}{sign}{intpart}{frac or ''}"


RULE_RE = re.compile(rb"([\d.]+) ([\d.]+) m ([\d.]+) ([\d.]+) l S\s*")


def make_units(tag, text, font, size, x, bl, res=None, scale=1.0):
    """逐字符单元；scale<1 时压缩字距（行内替换变长时保住右缘不叠印）"""
    out = []
    cx = x
    pdf_y = PAGE_H - bl
    fname = res or font
    for ch in text:
        b = encode(tag, font, ch)
        tok = b"<" + b.hex().encode() + b">"
        out.append(f"1 0 0 1 {cx:.2f} {pdf_y:.2f} Tm\n/{fname} {size} Tf\n".encode() + tok + b" Tj\n")
        cx += char_w(tag, font, ch, size) * scale
    return b"".join(out), cx - x


def _cents(v):
    try:
        s = str(v).replace(",", "").replace("$", "").replace(" ", "").strip()
        return int((Decimal(s) * 100).quantize(Decimal("1")))
    except Exception:
        return 0


def _fmt(cents, dollar=False):
    q = abs(cents)
    s = f"{q // 100:,}.{q % 100:02d}"
    if dollar:
        s = "$" + s
    return ("-" if cents < 0 else "") + s


# ============ 布局常量（全部实测自原稿） ============
# p2 续页为流式（D933FADE 实证）：行从 138 一路下排、EB 紧跟末行、
# dre 披露块整体随 (EB新位 − 187) 平移；wipe_hi 只清到静态行线区（不碰 dre）。
PER_TX = {"frags": [
    {"pg": 0, "first": 601.2, "bottom": 725.2, "date": 36.4, "desc": 79.9,
     "amt_r": 462.9, "bal_r": 534.7, "rule": (31.4, 536.4)},
    {"pg": 1, "first": 138.0, "bottom": 470.0, "wipe_hi": 191.0,
     "date": 34.3, "desc": 77.7, "amt_r": 460.7, "bal_r": 532.5,
     "rule": (29.2, 534.2)}]}

PER_SUMMARY = [
    ("Beginning Balance", "begin", True, False),
    ("Deposits and Additions", "deposits", False, False),
    ("ATM & Debit Card Withdrawals", "atm_withdrawals", False, False),
    ("Electronic Withdrawals", "electronic_withdrawals", False, False),
    ("Ending Balance", "ending", True, False),
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
        # 状态物化（惰性）：继承的 Tf/rg 记为 pending_pre，不落盘——
        # 仅当单元被修改（set_* 自动物化）或前驱被删（delete_units 触发向后修复）时写入，
        # 未修改单元保持原稿字节，内容流字节级 1:1。
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
                    u.pending_pre = pre
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

    def spans_units(self, span):
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
        """[lo,hi) 基线带相交的全部矢量线段（不限方向/长度）。
        返回 (seg下标, 整算子span, y0组span, y1组span, y0, y1)"""
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
        """带内线段纵向平移 dy（PDF 坐标，正值上移），与文字上移保持同带"""
        hits = {}
        for si, full, g2, g4, y0, y1 in self._line_ops(lo, hi):
            hits.setdefault(si, []).append((g2, g4, y0, y1))
        for si, items in hits.items():
            seg = self.segs[si]
            # 组 span 倒序重写，前面的 span 不受影响
            for g2, g4, y0, y1 in sorted(items, key=lambda t: -t[1][0]):
                seg.raw = (seg.raw[:g4[0]] + f"{y1 + dy:.2f}".encode() + seg.raw[g4[1]:])
                seg.raw = (seg.raw[:g2[0]] + f"{y0 + dy:.2f}".encode() + seg.raw[g2[1]:])

    def add_rule(self, bl, x0, x1, w=0.72):
        y = PAGE_H - bl
        op = f"q {w} w 0 0 0 RG {x0} {y:.2f} m {x1} {y:.2f} l S Q ".encode()
        for seg in reversed(self.segs):
            if seg.kind == "raw":
                seg.raw = seg.raw + op
                return
        self.segs.append(chstream.RawSeg(op))

    def append_text(self, raw_units):
        """自含 Tf 的逐字单元包上 BT..ET 追加到页内容尾（位置独立，渲染顺序无关）。
        显式置黑填充：页尾继承到的可能是 1 g（原稿隐层白字），否则整体不可见。"""
        op = b"BT\n0 g\n" + raw_units + b"ET\n"
        for seg in reversed(self.segs):
            if seg.kind == "raw":
                seg.raw = seg.raw + op
                return
        self.segs.append(chstream.RawSeg(op))

    def _heal_state(self, si, ui):
        """被删单元可能承载后继继承的 Tf/rg：从其后起物化，直到状态自含的单元截断。"""
        for k in range(si, len(self.segs)):
            seg = self.segs[k]
            if seg.kind != "btseg":
                continue
            units = seg.units
            rng = range(0, len(units)) if k > si else range(ui + 1, len(units))
            for j in rng:
                u = units[j]
                if not u.raw:
                    continue
                has_tf = chstream.TF_RE.search(u.raw) is not None
                has_rg = chstream.RG_RE.search(u.raw) is not None
                if has_tf and (has_rg or u.rgb == (0, 0, 0)):
                    return
                u.materialize_pending()

    def delete_units(self, locs):
        for si, ui in sorted(set(locs), key=lambda t: -t[1]):
            self.segs[si].units[ui].raw = b""
            self._heal_state(si, ui)

    def insert_before(self, loc, raw_bytes):
        si, ui = loc
        u = self.segs[si].units[ui]
        u.raw = raw_bytes + u.raw

    def replace_span(self, span, new_text, align=None, right_edge=None):
        if new_text.rstrip() == span["text"].rstrip():
            return True                      # 同文跳过：保住原稿字节
        locs = self.spans_units(span)
        if not locs:
            return False
        new_x = span["x"]
        scale = 1.0
        if align == "r" and right_edge is not None:
            new_x = right_edge - text_w(self.tag, span["font"], new_text, span["size"])
        else:
            # 新文本实际越过行内后续 span（上标/接续词/邮编列）时才压缩字距
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
    size, t = 100, "S A M P L E"
    w = text_w("x", "HeB", t, size)
    cx, cy = 612 / 2, 792 / 2
    c45, s45 = math.cos(math.radians(45)), math.sin(math.radians(45))
    x = cx - w / 2 * c45
    y = (PAGE_H - cy) + w / 2 * s45
    ops = (f"q 0.78 0.78 0.78 rg BT "
           f"1 0 0 1 {x:.2f} {y:.2f} Tm {c45:.4f} {s45:.4f} {-s45:.4f} {c45:.4f} 0 0 Tm "
           f"/W {size} Tf ({t}) Tj ET Q")
    # 字体须注册在 Resources/Font 子字典（挂在 Resources 顶层时严格解析器
    # 报"找不到字体 /W"）；每 doc 共用一个字体对象，追加带去重
    if not hasattr(doc, "_wm_font_xref"):
        doc._wm_font_xref = doc.get_new_xref()
        doc.update_object(doc._wm_font_xref,
                          "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    xref = doc._wm_font_xref
    rk = doc.xref_get_key(page.xref, "Resources/Font")
    if rk[0] == "xref":                     # Font 字典为共享/间接对象
        fo = int(rk[1].split()[0])
        old = doc.xref_object(fo).rstrip()
        if "/W " not in old:
            doc.update_object(fo, old[:-2] + f" /W {xref} 0 R>>")
    else:
        old = rk[1].rstrip()
        if "/W " not in old:
            doc.xref_set_key(page.xref, "Resources/Font",
                             old[:-2] + f" /W {xref} 0 R>>")
    cx2 = page.get_contents()[0]
    doc.update_stream(cx2, doc.xref_stream(cx2) + ops.encode())


def find(L, pi, pred):
    for s in L[pi]["spans"]:
        if pred(s):
            return s
    return None


def build_subs(d):
    b = d.get("bank", {})
    subs = []
    if b.get("legal"):
        subs.append(("JPMorgan Chase Bank, N.A.", b["legal"]))
    if b.get("name"):
        subs.append(("JPMorgan Chase", b["name"]))
        subs.append(("Chase", b["name"]))
    if b.get("domain"):
        subs.append(("Chase.com", b["domain"]))
        subs.append(("chase.com", b["domain"]))
    return subs


def sub_text(s, subs):
    for k, v in subs:
        if k in s:
            s = s.replace(k, v)
    return s


def extend_all_fonts(doc, tag):
    """扩展全部 Type3 字体覆盖可编辑字符集，并同步内存码表。
    F3（跟踪号字体）除外——由 ensure_f3_digits 用原稿字形/合成位图专管，
    否则 EF 的 Arial 合成字形与动态改码会污染 F3 的原生数字。"""
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


# ---------- F3 跟踪号字体补全（缺 5/8/9；原稿号码从不用 8 故无现成字形） ----------
_D_NAME = lambda c: f"EX0{48 + c}000"      # 原稿命名 EX048000..EX057000


def _f3_bits(stream):
    """解析 F3 字形流 → (头前缀, 宽, 高, 位图bytes, 尾后缀)。
    1bpc 行按字节对齐（PDF 规范）：stride=(W+7)//8，位图长 stride*H。
    ID 后有 1 个空白分隔符，数据从其后开始。"""
    w = int(re.search(rb"/W (\d+)", stream).group(1))
    h = int(re.search(rb"/H (\d+)", stream).group(1))
    i = stream.find(b"ID")
    j = i + 2
    while j < len(stream) and stream[j:j+1].isspace():
        j += 1
    stride = (w + 7) // 8
    n = stride * h
    pre, body, suf = stream[:j], stream[j: j + n], stream[j + n:]
    return pre, w, h, body, suf


def _synth_8(stream0):
    """由 '0' 字形合成 '8'：中段两行补墨形成收腰（同款 12×18 位图，行字节对齐）"""
    pre, w, h, body, suf = _f3_bits(stream0)
    stride = (w + 7) // 8
    bits = []
    for r in range(h):
        row = []
        base = r * stride
        for c in range(w):
            byte = body[base + c // 8]
            row.append((byte >> (7 - c % 8)) & 1)
        bits.append(row)
    for r in (8, 9):                     # 收腰：孔位(2-9 列)整行补墨连接两侧环壁
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
    """补齐 F3 数字字形：5/9 克隆原稿同款（'0' 位图一致已验证），8 用 '0' 收腰合成。
    同步 CharProcs/Differences/ToUnicode/Widths。幂等。"""
    xref = cp = enc = tu = wd = None
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

    # 取得 '0' 流（合成 8 的底版）
    m0 = re.search(rf"/{_D_NAME(0)} (\d+) 0 R", cp_txt)
    stream0 = doc.xref_stream(int(m0.group(1)))

    # 供体字形已提取为项目资产 JSON（原稿 PDF 无需在本地保留）
    donors = {}
    glyph_fp = os.path.join(BASE, "assets", "f3_donor_glyphs_personal.json")
    if any(c in need for c in (5, 9)) and os.path.exists(glyph_fp):
        bank = json.load(open(glyph_fp))
        for c in (5, 9):
            if str(c) in bank:
                donors[c] = bytes.fromhex(bank[str(c)])

    new_entries = []
    for c in need:
        name = _D_NAME(c)
        if c in donors:
            data = donors[c]
        elif c == 8:
            data = _synth_8(stream0)
        else:
            continue                      # 无字形来源，跳过（编码回落 '?'）
        nx = doc.get_new_xref()
        doc.update_object(nx, "<< >>")
        doc.update_stream(nx, data, compress=False)
        new_entries.append((c, name, nx))

    if new_entries:
        add = " ".join(f"/{n} {x} 0 R" for _, n, x in new_entries)
        doc.update_object(cp, cp_txt.rstrip()[:-2] + " " + add + " >>")
        # Differences：克隆出 F3 专属编码对象再追加。以模板常量为基底——
        # 编码对象可能被其他字体的 EF 重建覆盖（且条目间换行必须 \s+ 解析）
        F3_BASE = {240: "EX048000", 241: "EX049000", 242: "EX050000",
                   243: "EX051000", 244: "EX052000", 246: "EX054000",
                   247: "EX055000"}
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
        # ToUnicode 追加 bfchar（并删除第二块字母表里的同码冲突条目，
        # 如 <F5>→'D'——原稿码位 245 本无字形，字母表占位映射需让位）
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
        # Widths 数组置 14（仅当确为 256 元素数组，防共享对象误伤）
        if wd is not None:
            arr = doc.xref_object(wd).strip()
            nums = arr.strip("[] \n").split()
            if len(nums) == 256:
                for c, _, _ in new_entries:
                    nums[240 + c] = "14"
                doc.update_object(wd, "[ " + " ".join(nums) + " ]")


def replace_barcode(doc, tracking_num):
    if not tracking_num:
        return
    ensure_f3_digits(doc)
    img = BC.generate_barcode(tracking_num).convert("1")
    W, H = img.size
    row_bytes = (W + 7) // 8
    data = bytearray()
    px = img.load()
    for y in range(H):
        row = bytearray(row_bytes)
        for x in range(W):
            if px[x, y] == 0:          # 黑条=0（DeviceGray 1bpc: 0=黑）
                row[x // 8] |= 0x80 >> (x % 8)
        data.extend(row)
    blob = bytes(data)
    for pg in doc:
        for im in pg.get_images(full=True):
            info = doc.extract_image(im[0])
            if info["width"] == 262 and info["height"] == 910:
                doc.xref_set_key(im[0], "Filter", "null")
                doc.xref_set_key(im[0], "ImageMask", "null")
                doc.xref_set_key(im[0], "Decode", "null")
                doc.xref_set_key(im[0], "ColorSpace", "/DeviceGray")
                doc.xref_set_key(im[0], "BitsPerComponent", "1")
                # compress=False：默认会把原始像素 zlib 压缩并补 /FlateDecode，
                # 解压失败被 MuPDF 渲成噪点
                doc.update_stream(im[0], blob, compress=False)
                return


# ---------- 自动计算 ----------
TX_TYPES = ("deposit", "atm", "electronic", "fee")


def date_key(s):
    """归一化日期排序键（编辑器/服务端/生成器三方一致）：
    M/D[/Y] → 补零数字串；空或非法日期排最前（稳定排序保持录入顺序）"""
    parts = re.findall(r"\d+", str(s or ""))
    if not parts:
        return ""
    m = int(parts[0])
    d = int(parts[1]) if len(parts) > 1 else 0
    y = int(parts[2]) if len(parts) > 2 else 0
    return f"{y:04d}{m:02d}{d:02d}"


def collect_rows(d):
    """统一流水（每笔带 type/bold）；兼容旧分段数据（deposits/atm_withdrawals/…）。"""
    if d.get("transactions") is not None:
        return [dict(r) for r in d["transactions"]]
    rows = []
    sec_map = (("deposits", "deposit"), ("atm_withdrawals", "atm"),
               ("electronic_withdrawals", "electronic"), ("fees", "fee"))
    for sec, typ in sec_map:
        src = d.get(sec, [])
        if sec == "electronic_withdrawals" and not src:
            src = d.get("withdrawals", [])
        for r in src:
            r2 = dict(r)
            r2.setdefault("type", typ)
            rows.append(r2)
    return rows


def per_calc(d):
    begin = _cents(d.get("begin_balance", 0))
    rows = collect_rows(d)
    sums = {t: 0 for t in TX_TYPES}
    for r in rows:
        sums[r.get("type") if r.get("type") in TX_TYPES else "electronic"] += \
            _cents(r["amount"])
    d["summary"] = {
        "begin": _fmt(begin, dollar=True),
        "deposits": _fmt(sums["deposit"]),
        "atm_withdrawals": _fmt(sums["atm"]),
        "electronic_withdrawals": _fmt(sums["electronic"]),
        "ending": _fmt(begin + sum(sums.values()), dollar=True),
    }


# ---------- 段落渲染 ----------
def _row_cells(tag, kind, r, frag):
    """一段一行 → [(text, x, align_r)]；金额右对齐"""
    cells = []
    if False:
        pass
    else:
        cells.append((r.get("date", ""), frag.get("date", 35.0), None))
        cells.append((r.get("desc", ""), frag.get("desc", 70.3), None))
        cells.append((r["amount"], None, frag["amt_r"]))
        if kind == "personal":
            cells.append((r.get("balance", ""), None, frag["bal_r"]))
    return [(t, x, ra) for t, x, ra in cells if t]


def _col_xs(frag):
    return [v for v in frag.values() if isinstance(v, (int, float)) and v < 600]


def _render_rows(pages, tag, rows, frags, kind, first_dollar, thick_last_page=None):
    """跨页片段依序渲染；与原稿同文本则整段跳过（回放模式）。
    返回最后片段的下一行槽位（跳过时 None）——溢出时 EB 须紧跟末行。
    thick_last_page：EB 所在页号；末行恰落该页时叠加末行粗实线（原稿样式）。"""
    if rows and _rows_equal_original(tag, rows, frags, kind, first_dollar):
        return None
    y_tail = None
    tail = None
    fonts = ("F10", "F11") if kind == "personal" else ("F11",)
    for frag in frags:
        L = LAYOUTS[tag][frag["pg"]]["spans"]
        pg = pages[frag["pg"]]
        wipe_hi = frag.get("wipe_hi", frag["bottom"] + 3.0)
        locs = []
        for s in L:
            if s["font"] not in fonts:
                continue
            if not (frag["first"] - 4 <= s["bl"] < wipe_hi):
                continue
            # p2 静态 Ending Balance 行保留（溢出时整行移动而非重建）
            if frag.get("wipe_hi") and s["bl"] >= 185.0:
                continue
            if any(abs(s["x"] - v) < 2.0 for v in _col_xs(frag)) \
                    or s["x"] > frag["amt_r"] - 60 \
                    or (kind == "checks" and s["text"].strip() == "^" and 50 < s["x"] < 62):
                locs.extend(pg.spans_units(s))
        pg.delete_units(locs)
        pg.delete_rules(frag["first"] + 1.5, wipe_hi)
    for frag in frags:
        pg = pages[frag["pg"]]
        res = pg.res_of.get(ROW_FONT, ROW_FONT)
        y = frag["first"]
        blob = []
        while rows and y <= frag["bottom"]:
            r = rows.pop(0)
            lines = str(r.get("desc", "")).split("\n")
            amt = fmt_amount(r["amount"])
            if first_dollar and not amt.startswith("$") and not amt.startswith("-") \
                    and r.get("_first") is True:
                amt = "$" + amt
            row2 = dict(r)
            row2["amount"] = amt
            row2["desc"] = lines[0]      # 换行部分由续行渲染，避免 \n 编码成 ?
            for txt, x, ra in _row_cells(tag, kind, row2, frag):
                fnt = ROW_FONT
                if kind == "personal" and ra is not None and txt == amt and txt \
                        and r.get("bold_amt"):
                    fnt = "F10"          # 个人版金额加粗=F10（原稿正数样式）
                    res_f = pg.res_of.get(fnt, fnt)
                    x = ra - text_w(tag, fnt, txt, ROW_SIZE)
                    raw, _ = make_units(tag, txt, fnt, ROW_SIZE, x, y, res=res_f)
                    blob.append(raw)
                    continue
                if ra is not None:
                    x = ra - text_w(tag, ROW_FONT, txt, ROW_SIZE)
                raw, _ = make_units(tag, txt, ROW_FONT, ROW_SIZE, x, y, res=res)
                blob.append(raw)
            last_bl = y
            y += CONT_PITCH if len(lines) > 1 else PITCH   # 续行紧贴 +9.4（D933 实证）
            for cl in lines[1:]:
                raw, _ = make_units(tag, cl, ROW_FONT, ROW_SIZE,
                                    frag.get("desc", frag.get("date", 35.0)), y, res=res)
                blob.append(raw)
                last_bl = y
                y += PITCH                     # 续行后回到主行距进下一行
            if frag.get("rule"):
                pg.add_rule(last_bl + 2.75, *frag["rule"])
            tail = (pg, frag, last_bl)
        y_tail = y
        if blob:
            anchor = _frag_anchor(pages, tag, frag)
            pg.insert_before(anchor, b"".join(blob))
    # 末行粗实线：仅当末行与 EB 同页（原稿=细线同位叠加 1.44 粗线、高 0.1）
    if tail is not None:
        tpg, tfrag, tbl = tail
        if thick_last_page == tpg.page.number and tfrag.get("rule"):
            tpg.add_rule(tbl + 2.65, *tfrag["rule"], w=1.44)
    return y_tail


def _frag_anchor(pages, tag, frag):
    """插入锚点：片段内最后一个原稿行单元（清除后 raw 已空但位置保留）"""
    L = LAYOUTS[tag][frag["pg"]]["spans"]
    pg = pages[frag["pg"]]
    cands = []
    for s in L:
        if s["font"] == "F11" and frag["first"] - 4 <= s["bl"] < frag["bottom"]:
            locs = pg.spans_units(s)
            if locs:
                cands.append((s["bl"], locs[0]))
    if cands:
        return max(cands)[1]
    # 兜底：段内任意 btseg 首单元
    for si, seg in enumerate(pg.segs):
        if seg.kind == "btseg" and seg.units:
            for ui, u in enumerate(seg.units):
                if u.text is not None:
                    return (si, ui)
    return (0, 0)


def _rows_equal_original(tag, rows, frags, kind, first_dollar):
    """生成的行文本与原稿 span 逐行一致则跳过（回放模式）"""
    orig_rows = []
    fonts = ("F10", "F11") if kind == "personal" else ("F11",)
    for frag in frags:
        L = LAYOUTS[tag][frag["pg"]]["spans"]
        by_bl = {}
        for s in L:
            if s["font"] not in fonts:
                continue
            if not (frag["first"] - 4 <= s["bl"] < frag["bottom"]):
                continue
            if frag.get("wipe_hi") and s["bl"] >= 185.0:
                continue          # p2 静态 EB 不算行（与渲染擦除口径一致）
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
        if False:
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


def _flat_row_count(rows, kind):
    n = 0
    for r in rows:
        n += 1 + max(0, len(str(r.get("desc", "")).split("\n")) - 1)
    return n


# ---------- 元数据（原样格式） ----------
def _pdf_esc(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_meta(doc, tag, md):
    'Use explicit editor metadata or fictional sample metadata.'
    om = ORIG_META[tag]
    md = md or {}
    # 缺省（用户未填）：Producer 固定 23.4.26 版串；创建/修改时间=生成时实时时间
    # （原稿同款 D:…UTC00'00' 格式）；显式填写一律优先
    creation = md.get("creation") or _now_pdf_date()
    doc.set_metadata({
        "title": md.get("title", om.get("title", "") or ""),
        "author": md.get("author", om.get("author", "") or ""),
        "creator": md.get("creator", om.get("creator", "") or ""),
        "subject": om.get("subject", "") or "",
        "keywords": om.get("keywords", "") or "",
        "producer": md.get("producer")
        or 'BillGen Sample Editor',
        "creationDate": creation,
        "modDate": md.get("mod") or creation,
    })


def _now_pdf_date():
    from datetime import datetime, timezone
    t = datetime.now(timezone.utc)
    return f"D:{t:%Y%m%d%H%M%S}UTC00'00'"


def _normalize_type3_glyphs(doc):
    """Type3 字形过程规范化：PDF 规范要求 d0/d1 是字形首个操作符。
    原稿与 EF 合成字形均为 q … d1 … cm BI…EI Q 序——把开头 q 移到 d1 之后，
    绘图指令与渲染像素完全不变，消除严格解析器"指令顺序异常"告警。"""
    seen, n = set(), 0
    for page in doc:
        for f in page.get_fonts(full=True):
            if f[2] != "Type3" or f[0] in seen:
                continue
            seen.add(f[0])
            m = re.search(r"/CharProcs (\d+) 0 R", doc.xref_object(f[0]))
            if not m:
                continue
            for name, x in re.findall(r"/([A-Za-z0-9.]+) (\d+) 0 R",
                                      doc.xref_object(int(m.group(1)))):
                x = int(x)
                st = doc.xref_stream(x)
                if not st.startswith(b"q "):
                    continue
                i = st.find(b" d1 ")
                if i < 0 or i > 80:
                    continue
                doc.update_stream(x, st[2:i + 4] + b" q " + st[i + 4:],
                                  compress=False)
                n += 1
    return n


def _prep_sig(tag):
    """预备模板源指纹：静态模板/字体码表/扩展逻辑任一变化即自动重建。"""
    h = hashlib.md5()
    for p in (TEMPLATES[tag], os.path.join(BASE, "assets",
                                           f"font_tables_{tag}.json"),
              os.path.join(BASE, "extend_fonts.py"),
              os.path.join(BASE, "assets", f"f3_donor_glyphs_{tag}.json")):
        h.update(f"{p}:{os.path.getmtime(p)}:{os.path.getsize(p)}".encode())
    h.update(b"prepared-v1")
    return h.hexdigest()


def _open_prepared(tag):
    """预备模板缓存：字体扩展(EF 位图合成)+F3 补字与账单数据无关——
    一次构建落盘复用（实测占单次构建绝大部分耗时）。
    EF 运行期码表（新增字符→码位）同步持久化；命中时载入，
    保证 make_units 编码与预备模板字形一一对应。"""
    pdf_fp = os.path.join(BASE, "assets", f"chase_static_{tag}_prepared.pdf")
    tbl_fp = os.path.join(BASE, "assets", f"font_tables_{tag}_prepared.json")
    meta_fp = os.path.join(BASE, "assets", f"prepared_{tag}.json")
    sig = _prep_sig(tag)
    hit = False
    try:
        hit = (json.load(open(meta_fp)).get("sig") == sig
               and os.path.exists(pdf_fp) and os.path.exists(tbl_fp))
    except Exception:
        hit = False
    if not hit:
        doc = fitz.open(TEMPLATES[tag])
        extend_all_fonts(doc, tag)
        ensure_f3_digits(doc)
        _normalize_type3_glyphs(doc)
        doc.save(pdf_fp, garbage=3, deflate=True)
        doc.close()
        json.dump({"ch2code": CH2CODE.get(tag, {}),
                   "code2ch": {f: {str(c): ch for c, ch in m.items()}
                               for f, m in CODE2CH.get(tag, {}).items()},
                   "widths": WIDTHS.get(tag, {})}, open(tbl_fp, "w"))
        json.dump({"sig": sig}, open(meta_fp, "w"))
    else:
        prep = json.load(open(tbl_fp))
        for f, m in prep["ch2code"].items():
            CH2CODE.setdefault(tag, {}).setdefault(f, {}).update(m)
        for f, m in prep["code2ch"].items():
            CODE2CH.setdefault(tag, {}).setdefault(f, {}).update(
                {int(c): ch for c, ch in m.items()})
        for f, w in prep["widths"].items():
            WIDTHS.setdefault(tag, {})[f] = w
    return fitz.open(pdf_fp)


def build_pdf(d, out_path, watermark=True):
    d = json.loads(json.dumps(d))
    tag = d["template"]
    if tag != "personal":
        raise ValueError(f"本模块仅处理 personal，收到 {tag}")
    per_calc(d) if d.get("auto", True) else None
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
                pages[pi].replace_span(s, d["period"]["label"])
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
    spec = PER_SUMMARY
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

    # ===== 期初余额行（交易明细顶部）=====
    bb = find(L, 0, lambda s: s["text"] == "Beginning Balance" and s["font"] == "F10"
              and s["bl"] > 500)
    if bb:
        amt = find(L, 0, lambda x: abs(x["bl"] - bb["bl"]) < 1.5 and x["x"] > 400
                   and x["text"].strip().startswith("$"))
        if amt:
            pages[0].replace_span(amt, _fmt(_cents(d.get("begin_balance", 0)), dollar=True),
                                  align="r", right_edge=amt.get("x1"))

    # ===== 条码（图像 + 旁边 F3 竖排跟踪号文字）=====
    bc_num = d.get("barcode_num")
    replace_barcode(doc, bc_num)
    if bc_num:
        f3 = find(L, 0, lambda s: s["font"] == "F3" and len(s["text"]) >= 15)
        if f3 and str(bc_num) != f3["text"]:
            # 原地换码：保留原稿旋转 Tm（0 1 -1 0 …，竖排），仅替换单元串
            for si, ui in pages[0].spans_units(f3):
                codes = b"".join(encode(tag, "F3", ch) for ch in str(bc_num))
                pages[0].segs[si].units[ui].set_text(codes)

    # ===== 流水段 =====
    if tag == "personal":
        # 统一流水：仅按日期排序（同日期保持录入顺序），带运行余额
        rows = collect_rows(d)
        rows.sort(key=lambda r: date_key(r.get("date")))
        for r in rows:
            # 加粗按交易类型自动：存款=加粗（原稿正数样式 F10），其余不加粗
            r["bold_amt"] = (r.get("type") == "deposit")
        bal = _cents(d.get("begin_balance", 0))
        for r in rows:
            bal += _cents(r["amount"])
            r["balance"] = _fmt(bal)
        frag0 = PER_TX["frags"][0]
        # 容量模拟：全部行落在 p1 且结余行也放得下 → 结余上提 p1、p2 仅留 FDIC
        y, fits = frag0["first"], True
        for r in rows:
            if y > frag0["bottom"] + 0.01:      # 行起点超出 p1 容量
                fits = False
                break
            cont = (len(str(r.get("desc", "")).split("\n")) - 1) * CONT_PITCH
            y += PITCH + cont
        if fits and y <= frag0["bottom"] + 0.01:   # 结余行位置也须在 p1 容量内
            _render_rows(pages, tag, rows, [frag0], "personal", False,
                         thick_last_page=0)
            # 结余行上提 p1（紧跟末行；EB 行下无线——原稿 p2 实证）
            res_f10 = pages[0].res_of.get("F10", "F10")
            raw_lab, _ = make_units(tag, "Ending Balance", "F10", ROW_SIZE,
                                    87.1, y, res=res_f10)
            endv = _fmt(bal, dollar=True)
            raw_amt, _ = make_units(tag, endv, "F10", ROW_SIZE,
                                    frag0["bal_r"] - text_w(tag, "F10", endv, ROW_SIZE),
                                    y, res=res_f10)
            pages[0].append_text(raw_lab + raw_amt)
            # p2 交易区整体不存在（含标题/表头/静态行/EB/起止标记/方框行线）
            locs = []
            for sp in L[1]["spans"]:
                if 88 <= sp["bl"] < 196:
                    locs.extend(pages[1].spans_units(sp))
            pages[1].delete_units(locs)
            pages[1].delete_lines(88, 196)
            # dre 披露块上移到 p2 流式起点（原稿两份实证：段起点=91.0，
            # 即整块自 197.8 上移 106.8；标题落 117.2、上下边框 100.5/336.2）
            for seg in pages[1].segs:
                if seg.kind != "btseg":
                    continue
                for u in seg.units:
                    if u.pdf_y is not None and 196 < (792 - u.pdf_y) < 760:
                        u.set_y_pdf(u.pdf_y + 106.8)
            pages[1].shift_lines(196, 760, 106.8)
        else:
            # 先算 p2 末行槽位（与渲染同款网格双片段流），渲染前平移静态下方块——
            # 行会流进 ≥193 区域，渲染后再移会把新行一起搬走
            simrows = [dict(r) for r in rows]
            y2 = PER_TX["frags"][-1]["first"]
            for frag in PER_TX["frags"]:
                y = frag["first"]
                while simrows and y <= frag["bottom"]:
                    r = simrows.pop(0)
                    nl = len(str(r.get("desc", "")).split("\n"))
                    y += PITCH if nl == 1 else (CONT_PITCH + (nl - 1) * PITCH)
                y2 = y
            if abs(y2 - 187.0) > 0.5:
                delta = y2 - 187.0          # 正=下移（dre 起点=EB+10.8，D933 实证）
                for seg in pages[1].segs:
                    if seg.kind != "btseg":
                        continue
                    for u in seg.units:
                        if u.pdf_y is not None and 193 <= (792 - u.pdf_y) < 760:
                            u.set_y_pdf(u.pdf_y - delta)
                pages[1].shift_lines(193, 760, -delta)
            placed = _render_rows(pages, tag, rows, PER_TX["frags"], "personal",
                                  False, thick_last_page=1)
            # 交易区 Ending Balance 行金额（p2）
            eb = next((s for s in L[1]["spans"]
                       if s["text"] == "Ending Balance" and s["font"] == "F10"), None)
            if eb:
                eam = next((s for s in L[1]["spans"]
                            if abs(s["bl"] - eb["bl"]) < 1.5 and s["x"] > 400), None)
                # 先取原稿单元位置（替换后文本不再匹配 span，届时找不到）
                eb_locs = pages[1].spans_units(eb)
                eam_locs = pages[1].spans_units(eam) if eam else []
                if eam:
                    endv = _fmt(bal, dollar=True)
                    pages[1].replace_span(eam, endv, align="r", right_edge=eam.get("x1"))
                # EB 原稿固定在 187；行数不同时紧跟末行（真实生成器流式）
                if placed is not None and abs(placed - eb["bl"]) > 0.5:
                    for si, ui in eb_locs + eam_locs:
                        pages[1].segs[si].units[ui].set_y_pdf(PAGE_H - placed)
    # ===== 页码（页号 span 与 总页数 span 分开，且同值跳过）=====
    for pi in range(len(pages)):
        f4 = sorted([s for s in L[pi]["spans"]
                     if s["font"] == "F4" and len(s["text"].strip()) <= 2
                     and s["text"].strip().isdigit()], key=lambda s: s["x"])
        if len(f4) >= 1:
            pages[pi].replace_span(f4[0], f" {pi + 1} ")
        if len(f4) >= 2:
            pages[pi].replace_span(f4[1], f" {len(pages)}")

    for p in pages:
        p.commit()
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
