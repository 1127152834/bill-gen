"""Shared USBank PDF primitives, independent of personal/business page policy.

Mutable font maps and template documents remain owned by each generator.
This module has no template IO at import time.
"""
import io
import re
from decimal import Decimal

from fontTools.ttLib import TTFont

from billgen.themes.usbank import chstream

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


_DONOR_NAMES = {
    "ArialMT": "Arial.ttf",
    "Arial-BoldMT": "Arial Bold.ttf",
    "Arial-ItalicMT": "Arial Italic.ttf",
    "Arial-BoldItalicMT": "Arial Bold Italic.ttf",
}
_DONORS: dict[str, object] = {}


def _donor(base_font: str):
    """按子集名找系统里的同族字体当「字形供体」（子集只收过原稿用到的字）。"""
    name = (base_font or "").split("+")[-1]
    fname = _DONOR_NAMES.get(name)
    if not fname:
        return None
    if fname not in _DONORS:
        import os

        path = f"/System/Library/Fonts/Supplemental/{fname}"
        _DONORS[fname] = TTFont(path) if os.path.exists(path) else None
    return _DONORS[fname]


def _descendant(doc, font_xref):
    obj = doc.xref_object(font_xref)
    m = re.search(r"/DescendantFonts \[\s*(\d+) 0 R", obj)
    return int(m.group(1)) if m else None


def _fontfile2_xref(doc, font_xref):
    df = _descendant(doc, font_xref)
    if df is None:
        return None
    m = re.search(r"/FontDescriptor (\d+) 0 R", doc.xref_object(df))
    if not m:
        return None
    m2 = re.search(r"/FontFile2 (\d+) 0 R", doc.xref_object(int(m.group(1))))
    return int(m2.group(1)) if m2 else None


def _fill_font_outlines(doc, font_xref, base_font) -> int:
    """给「有码位但轮廓为空」的字补上真轮廓。

    原稿是银行自己打印的账单，PDF 只嵌了它用到的字（`Sep`, `Aug`…），`Jan` 里的
    'J' 在子集 cmap 里有码位、glyf 里却是空壳 —— 主题把日期换成 `Jan 05` 后文本层
    对、像素却缺字（实测肉眼 `an 05`）。这里从系统 Arial 取同字轮廓补进去，再把
    FontFile2 流写回。必须作用在**最终要保存的那个 doc** 上。
    """
    donor = _donor(base_font)
    ff2 = _fontfile2_xref(doc, font_xref)
    if donor is None or ff2 is None:
        return 0
    from fontTools.pens.recordingPen import DecomposingRecordingPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    try:
        tt = TTFont(io.BytesIO(doc.xref_stream(ff2)), fontNumber=0, lazy=True)
    except Exception:
        return 0
    glyf = tt["glyf"]
    order = tt.getGlyphOrder()
    subset_set = tt.getGlyphSet()
    donor_set = donor.getGlyphSet()
    donor_cmap = donor.getBestCmap() or {}
    cid2ch = {}
    obj = doc.xref_object(font_xref)
    mt = re.search(r"/ToUnicode (\d+) 0 R", obj)
    if mt:
        cid2ch = parse_cmap(doc.xref_stream(int(mt.group(1))).decode("latin1"))
    fixed = 0
    for cid, ch in cid2ch.items():
        if cid >= len(order):
            continue
        gname = order[cid]
        try:
            if glyf[gname].numberOfContours != 0:
                continue
        except Exception:
            continue
        dname = donor_cmap.get(ord(ch))
        if not dname or dname not in donor_set:
            continue
        pen = TTGlyphPen(subset_set)
        rec = DecomposingRecordingPen(donor_set)
        donor_set[dname].draw(rec)
        rec.replay(pen)
        glyf[gname] = pen.glyph()
        fixed += 1
    if not fixed:
        return 0
    out = io.BytesIO()
    tt.save(out)
    doc.update_stream(ff2, out.getvalue())
    return fixed


def patch_outlines(doc) -> dict[str, int]:
    """把整份 PDF 里所有 Type0 字体都查一遍空轮廓补齐（返回 {子集名: 补字数}）。"""
    done, report = set(), {}
    for pno in range(len(doc)):
        for f in doc[pno].get_fonts(full=True):
            xref, ftype, base = f[0], f[2], f[3]
            if ftype != "Type0" or xref in done:
                continue
            done.add(xref)
            n = _fill_font_outlines(doc, xref, base)
            if n:
                report[base] = n
    return report


class UsFont:
    """Type0 全量字体程序：char→GID(CID)、per-1000 宽度、ToUnicode/W 增量补写。"""

    def __init__(self, doc, font_xref):
        self.xref = font_xref
        self.cmap, self.widths = {}, {}
        self.new_cids = set()
        obj = doc.xref_object(font_xref)
        mb = re.search(r"/BaseFont /([^\s/]+)", obj)
        self.base = mb.group(1) if mb else ""
        m = re.search(r"/DescendantFonts \[ (\d+) 0 R", obj)
        self.df = int(m.group(1)) if m else None
        mu = re.search(r"/ToUnicode (\d+) 0 R", obj)
        self.tou = int(mu.group(1)) if mu else None
        self.cid2ch = {}
        if self.tou:
            self.cid2ch = parse_cmap(doc.xref_stream(self.tou).decode("latin1"))
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


def find(L, pi, pred):
    for s in L[pi]["spans"]:
        if pred(s):
            return s
    return None


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


def _norm(t):
    return re.sub(r"\s+", " ", str(t)).strip()


def ending_of(sum_lines):
    return sum_lines[-1][0]


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


def write_metadata(doc, md, defaults, now_pdf_date=_now_pdf_date):
    """Info + XMP（结构复刻原稿包：属性式字段 + dc:title/description 的 Alt 容器）"""
    md = md or {}
    def g(k):
        return str(md.get(k, "")).strip() or defaults[k]
    creation = str(md.get("creation", "")).strip() or now_pdf_date()
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
