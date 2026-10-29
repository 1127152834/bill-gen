"""Shared Chase mechanics; all variant state is explicit, never generator globals.

Layout, row emission, rounding and replay decisions remain in each generator.
"""
import base64
import hashlib
import tempfile
from pathlib import Path

from billgen.paths import data_path, font_path
import json
import math
import os
import re
from decimal import Decimal

import fitz

PAGE_H = 792.0

class FontCodec:
    """Live references to ONE variant's mutable font tables, including cache loads."""

    def __init__(self, tables, ch2code, widths, code2ch, standard):
        self.tables = tables
        self.ch2code = ch2code
        self.widths = widths
        self.code2ch = code2ch
        self.standard = standard

    def encode(self, tag, font, text):
        m = self.ch2code.get(tag, {}).get(font)
        if f"{tag}:{font}" in self.tables and self.tables[f"{tag}:{font}"]["type"] == "Type3":
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

    def char_w(self, tag, font, ch, size):
        if f"{tag}:{font}" in self.tables and self.tables[f"{tag}:{font}"]["type"] == "Type3":
            m = self.ch2code.get(tag, {}).get(font, {})
            code = m.get(ch, 63)
            w = self.widths.get(tag, {}).get(font)
            if w and code < len(w):
                return w[code] * size
            return 5.0 * size * 20
        f = self.standard.get(font)
        if f is None:
            return size * 0.5
        return f.text_length(ch, fontsize=1000) * size / 1000

    def text_w(self, tag, font, text, size):
        return sum(self.char_w(tag, font, c, size) for c in text)

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

def find(L, pi, pred):
    for s in L[pi]["spans"]:
        if pred(s):
            return s
    return None

def sub_text(s, subs):
    for k, v in subs:
        if k in s:
            s = s.replace(k, v)
    return s

def _col_xs(frag):
    return [v for v in frag.values() if isinstance(v, (int, float)) and v < 600]

def _flat_row_count(rows, kind):
    n = 0
    for r in rows:
        n += 1 + max(0, len(str(r.get("desc", "")).split("\n")) - 1)
    return n

def _pdf_esc(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

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

def add_watermark(doc, page, text_w):
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

def write_meta(doc, tag, md, original_metadata):
    'Use explicit editor metadata or fictional sample metadata.'
    om = original_metadata[tag]
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

def prepared_signature(tag, base, templates):
    """预备模板源指纹：静态模板/字体码表/扩展逻辑任一变化即自动重建。"""
    h = hashlib.md5()
    for p in (templates[tag], os.path.join(base, "assets",
                                           f"font_tables_{tag}.json"),
              os.path.join(base, "extend_fonts.py"),
              os.path.join(base, "assets", f"f3_donor_glyphs_{tag}.json")):
        h.update(f"{p}:{os.path.getmtime(p)}:{os.path.getsize(p)}".encode())
    h.update(b"prepared-v1")
    return h.hexdigest()

def _restore_prepared_fonts(codec, tag, prep):
    for f, m in prep["ch2code"].items():
        codec.ch2code.setdefault(tag, {}).setdefault(f, {}).update(m)
    for f, m in prep["code2ch"].items():
        codec.code2ch.setdefault(tag, {}).setdefault(f, {}).update(
            {int(c): ch for c, ch in m.items()})
    for f, w in prep["widths"].items():
        codec.widths.setdefault(tag, {})[f] = w


def _prepared_cache_key(sig):
    """Derived cache identity includes actual font bytes, not platform labels."""
    donor = hashlib.sha256(Path(font_path("Bold")).read_bytes()).hexdigest()
    return hashlib.sha256(f"prepared-v1:{sig}:{donor}".encode()).hexdigest()


def open_prepared(tag, base, templates, codec, signature, extend_fonts, ensure_digits):
    """Read shipped prepared assets; rebuild only in writable user cache.

    Valid historical prepared-v1 assets retain their original rendering. Derived
    bundles use the actual donor-font hash and are atomically published as one
    file, so concurrent readers never pair a PDF with another font map. Cache
    persistence is optional: read-only user storage still permits an in-memory
    build. The source asset directory is never written.
    """
    assets = Path(base) / "assets"
    sig = signature(tag)
    try:
        meta = json.loads((assets / f"prepared_{tag}.json").read_text())
        if meta.get("sig") == sig:
            prep = json.loads((assets / f"font_tables_{tag}_prepared.json").read_text())
            doc = fitz.open(assets / f"chase_static_{tag}_prepared.pdf")
            _restore_prepared_fonts(codec, tag, prep)
            return doc
    except (OSError, ValueError, KeyError, TypeError, fitz.FileDataError):
        pass

    key = _prepared_cache_key(sig)
    cache = None
    try:
        cache = Path(data_path(f"cache/chase/{tag}/{key}.json"))
        bundle = json.loads(cache.read_text())
        if bundle["key"] == key:
            prep = bundle["fonts"]
            # Validate maps before mutating the live codec.
            for name in ("ch2code", "code2ch", "widths"):
                if not isinstance(prep[name], dict):
                    raise ValueError("Invalid prepared font map")
            doc = fitz.open(stream=base64.b64decode(bundle["pdf"], validate=True), filetype="pdf")
            _restore_prepared_fonts(codec, tag, prep)
            return doc
    except (OSError, ValueError, KeyError, TypeError, fitz.FileDataError):
        pass

    with fitz.open(templates[tag]) as doc:
        extend_fonts(doc, tag)
        ensure_digits(doc)
        _normalize_type3_glyphs(doc)
        pdf = doc.tobytes(garbage=3, deflate=True)
    prep = {"ch2code": codec.ch2code.get(tag, {}),
            "code2ch": {f: {str(c): ch for c, ch in m.items()}
                        for f, m in codec.code2ch.get(tag, {}).items()},
            "widths": codec.widths.get(tag, {})}
    if cache is not None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=cache.parent,
                                             prefix=".prepared-", delete=False) as out:
                temporary = Path(out.name)
                json.dump({"key": key, "fonts": prep,
                           "pdf": base64.b64encode(pdf).decode("ascii")}, out)
            os.replace(temporary, cache)
        except OSError:
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
    return fitz.open(stream=pdf, filetype="pdf")
