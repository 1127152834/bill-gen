#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BOA 原稿嵌入方式复刻：Type1 + FontFile3(Type1C) + 原字体名 + 原 Encoding(Differences)。
Reg/Med/Bol/Ita 的 FontFile3 = 重建 CFF（原字形字节级保留 + 补全字形）；
FG/HS = 原稿 FontFile3 字节直拷。内容流由 CID(Type0) 改写为原编码单字节。"""
import json
import os, re
import fitz
from fontTools.ttLib import TTFont

BASE = os.path.dirname(os.path.abspath(__file__))
META = os.path.join(BASE, "assets", "fonts_meta.json")  # 原稿字体对象资产（随项目携带）

# 我的字体标签 -> 原 BaseFont 关键字
TAG2BASE = {
    "Reg": "Connections_CZEX0A60",
    "Med": "Connections_Medium_CZEX0A80",
    "Bol": "ConnectionsBold_CZEX0AA0",
    "Ita": "ConnectionsIta_CZEX0AC0",
    "FG":  "ITC_Franklin_Gothic_Book_CZEX0080",
    "HS":  "HigherStandards_CZEX0660",
}
REBUILT = ("Reg", "Med", "Bol", "Ita")   # FontFile3 用重建 CFF
DIRECT = ("FG", "HS")                    # 原稿字节直拷


def _cff_bytes(otf_path):
    """提取 OTF 的 CFF 表原始字节（= Type1C 流）"""
    raw = open(otf_path, "rb").read()
    numTables = int.from_bytes(raw[4:6], "big")
    for i in range(numTables):
        off = 12 + i * 16
        tag = raw[off:off + 4]
        if tag == b"CFF ":
            o, l = int.from_bytes(raw[off + 8:off + 12], "big"), int.from_bytes(raw[off + 12:off + 16], "big")
            return raw[o:o + l]
    raise RuntimeError("no CFF table: " + otf_path)


def _gid2uni(font):
    """CID(GID) -> unicode（cmap 反查）"""
    cmap = font.getBestCmap()  # uni -> name
    order = font.getGlyphOrder()
    name2gid = {n: i for i, n in enumerate(order)}
    out = {}
    for uni, name in cmap.items():
        g = name2gid.get(name)
        if g is not None and g not in out:
            out[g] = uni
    return out


def _parse_differences(enc_text):
    """Differences 文本 -> [(code, name)]"""
    m = re.search(r"/Differences\s*\[(.*?)\]", enc_text, re.S)
    if not m:
        return []
    items, code = [], None
    for tok in m.group(1).split():
        if tok.startswith(b"/") if isinstance(tok, bytes) else tok.startswith("/"):
            if code is not None:
                items.append((code, tok[1:]))
                code = None
        else:
            code = int(tok)
    return items


def _obj_text(doc, xref):
    return doc.xref_object(xref, compressed=False)


def _ref(txt, key):
    m = re.search(rf"/{key} (\d+) 0 R", txt)
    return int(m.group(1)) if m else None


def apply_orig_embed(doc):
    """把 doc 的 CID 字体替换为原稿式 Type1/Type1C 嵌入，并改写内容流。"""
    import base64 as _b64
    meta = json.load(open(META))
    ofonts = {tag: tag for tag in meta}  # tag -> meta 键

    class _MetaFont:
        def __init__(self, m):
            self.dict_text = m["font_dict"]
            self.encoding = m["encoding"]
            self.descriptor = m["descriptor"]
            self.fontfile3 = _b64.b64decode(m["fontfile3"])
            self.tounicode = _b64.b64decode(m["tounicode"]) if m["tounicode"] else None

    # 字体内存对象 + CID 映射：重建四件套含 hmtx；FG/HS 只读（cmap+glyphOrder）
    RAWSRC = {t: os.path.join(BASE, "assets", "fonts", f"{t}.otf") for t in TAG2BASE}
    myfonts, gid2uni, myadv = {}, {}, {}
    for t in TAG2BASE:
        if t in REBUILT:
            f = TTFont(os.path.join(BASE, "assets", "fonts", f"{t}.otf"))
            myfonts[t] = f
            gid2uni[t] = _gid2uni(f)
            myadv[t] = {name: f["hmtx"][name][0] for name in f.getGlyphOrder()}
        else:
            # FG/HS：原字体只读（原字形 CID 映射），字体文件用原稿
            f = TTFont(RAWSRC[t], lazy=True)
            gid2uni[t] = _gid2uni(f)

    # ---------- 2. 每个字体：建 Type1 对象树 ----------
    # 资源名（输出里统一用）：F1=Reg F2=Med F3=Bol F4=Ita F5=FG F6=HS
    RES = {"Reg": "F1", "Med": "F2", "Bol": "F3", "Ita": "F4", "FG": "F5", "HS": "F6"}
    font_refs = {}      # tag -> 新字体对象 xref
    uni2code = {}       # tag -> {unicode: 目标码}
    code2name = {}      # tag -> {code: glyphname}

    for tag, oxref in ofonts.items():
        mf = _MetaFont(meta[tag])
        fdict = mf.dict_text
        enc = mf.encoding
        desc = mf.descriptor
        tou_ref = mf.tounicode
        diffs = _parse_differences(enc)
        code2name[tag] = {c: n for c, n in diffs}
        uni2code[tag] = {}
        f = myfonts.get(tag) or TTFont(RAWSRC[tag], lazy=True)
        cmap = f.getBestCmap()
        name2code = {n: c for c, n in diffs}
        for uni, name in cmap.items():
            c = name2code.get(name)
            if c is None:
                c = name2code.get(f"uni{uni:04X}")
            if c is None and name == "space":
                c = name2code.get("space")
            if c is not None:
                uni2code[tag][uni] = c

        # 缺码字符统一补位（重建字体=新字形；FG 等=字形已在但无码位）
        if True:
            f = myfonts.get(tag) or TTFont(RAWSRC[tag], lazy=True)
            order = f.getGlyphOrder()
            first = int(re.search(r"/FirstChar (\d+)", fdict).group(1))
            last = int(re.search(r"/LastChar (\d+)", fdict).group(1))
            used = set(code2name[tag])
            # 宽度数组（原稿值）
            wm = re.search(r"/Widths \[(.*?)\]", fdict, re.S)
            widths = [int(x) for x in wm.group(1).split()]
            free = [c for c in range(first, last + 1) if c not in used]
            cmap_rev = {}
            for u, n in cmap.items():
                cmap_rev.setdefault(n, u)
            extra_needed = [n for n in order
                            if n != ".notdef"
                            and n in cmap_rev
                            and cmap_rev[n] not in uni2code[tag]]
            # 该字形对应的 unicode（可能多个）
            name2uni = {}
            for uni, name in cmap.items():
                name2uni.setdefault(name, uni)
            pos = 0
            for name in extra_needed:
                if pos < len(free):
                    code = free[pos]; pos += 1
                else:
                    code = last + 1 + (pos - len(free))
                    last = code
                    widths.append(myadv[tag].get(name, 600) if tag in myadv else 600)
                diffs.append((code, name))
                code2name[tag][code] = name
                uni = name2uni.get(name)
                if uni is not None:
                    uni2code[tag][uni] = code
                # 已在范围内的 free 码位也要把宽度改为真实值
                if code - first < len(widths):
                    widths[code - first] = myadv[tag].get(name, 600) if tag in myadv else 600
            # 重建 Differences / Widths 文本
            dparts, cur = [], None
            for c, n in sorted(diffs):
                if cur is None or c != cur:
                    dparts.append(str(c))
                dparts.append("/" + n)
                cur = c + 1
            enc = ("<< /Type /Encoding /Differences [ " + " ".join(dparts) + " ] >>")
            fdict = re.sub(r"/LastChar \d+", f"/LastChar {last}", fdict)
            fdict = re.sub(r"/Widths \[.*?\]", "/Widths [ " +
                           " ".join(str(w) for w in widths) + " ]", fdict, flags=re.S)
            ff3 = _cff_bytes(os.path.join(BASE, "assets", "fonts", f"{tag}.otf"))
            sub = "/Subtype /Type1C"
        else:
            # FG/HS：原稿字节直拷
            ff3 = mf.fontfile3
            sub = "/Subtype /Type1C"

        # ---------- 建对象 ----------
        stream_x = doc.get_new_xref()
        doc.update_object(stream_x, f"<< {sub} /Length {len(ff3)} >>")
        doc.update_stream(stream_x, ff3, new=True)

        desc2 = re.sub(r"/FontFile3 \d+ 0 R(\s*<<.*?>>)?",
                       f"/FontFile3 {stream_x} 0 R", desc, flags=re.S)
        desc_x = doc.get_new_xref()
        doc.update_object(desc_x, desc2)

        enc_x = doc.get_new_xref()
        doc.update_object(enc_x, enc)

        tou_x = None
        if tou_ref is not None:
            tou = mf.tounicode.decode("latin-1")
            # 新增码位补 bfchar（原 ToUnicode 不含补全字形）
            extra = []
            for code, name in diffs:
                uni = None
                for u, n in (myfonts.get(tag) or TTFont(RAWSRC[tag], lazy=True)).getBestCmap().items():
                    if n == name:
                        uni = u
                        break
                if uni is not None:
                    already = f"<{code:04X}> <{uni:04X}>" in tou.replace(" ", "").replace("\n", "")
                    if not already:
                        extra.append(f"<{code:04X}> <{uni:04X}>")
            if extra:
                add = "beginbfchar\n" + "\n".join(extra) + "\nendbfchar\n"
                tou = tou.replace("endcmap", add + "endcmap")
            tob = tou.encode("latin-1")
            tou_x = doc.get_new_xref()
            doc.update_object(tou_x, "<< /Length %d >>" % len(tob))
            doc.update_stream(tou_x, tob, new=True)

        fdict2 = re.sub(r"/Encoding \d+ 0 R", f"/Encoding {enc_x} 0 R", fdict)
        fdict2 = re.sub(r"/FontDescriptor \d+ 0 R", f"/FontDescriptor {desc_x} 0 R", fdict2)
        if tou_x:
            fdict2 = re.sub(r"/ToUnicode \d+ 0 R", f"/ToUnicode {tou_x} 0 R", fdict2)
        fx = doc.get_new_xref()
        doc.update_object(fx, fdict2)
        font_refs[tag] = fx

    # ---------- 3. 页面资源名 -> 原稿字体 映射（按我嵌入字体的 BaseFont 辨识） ----------
    tag_of_xref = {}
    for pg in doc:
        for f in pg.get_fonts(full=True):
            xref, base = f[0], f[3]
            for tag in TAG2BASE:
                key = {"Reg": "Regular", "Med": "Medium", "Bol": "Bold",
                       "Ita": "Italic", "FG": "Franklin", "HS": "Higher"}[tag]
                if key in base:
                    tag_of_xref[xref] = tag
                    break

    # ---------- 4. 改写内容流 ----------
    hexstr = re.compile(rb"<([0-9A-Fa-f]+)>")
    tfop = re.compile(rb"/(\w+)\s+([\d.]+)\s+Tf")

    for pg in doc:
        # 本页资源：名字->xref
        res = pg.get_fonts(full=True)  # (xref, ext, type, base, name, enc)
        name2tag = {f[4]: tag_of_xref.get(f[0]) for f in res}
        for xref in pg.get_contents():
            data = doc.xref_stream(xref)
            # Tf 名替换与字符串 CID->单字节由 _rewrite_stream 统一完成
            def sub_str(m):
                hs = m.group(1)
                if len(hs) % 4:
                    return m.group(0)
                out = []
                ok = True
                for i in range(0, len(hs), 4):
                    cid = int(hs[i:i + 4], 16)
                    # 当前字体：取本串所属——统一按页内主字体集合尝试（各字体码表不同）
                    # 先试当前 Tf 的字体（用最近一次替换的 RES 名）——简化：尝试全部 tag，
                    # 要求该 unicode 在该 tag 的 uni2code 中（Reg/Med/Bol/Ita 各自唯一）
                    code = None
                    for tag in ("Reg", "Med", "Bol", "Ita"):
                        uni = gid2uni[tag].get(cid)
                        if uni is None:
                            continue
                        c = uni2code[tag].get(uni)
                        if c is not None:
                            code = c
                            break
                    if code is None:
                        if cid == 0:
                            code = 32
                        else:
                            ok = False
                            break
                    out.append(f"{code:02X}")
                if not ok:
                    return m.group(0)
                return b"<" + "".join(out).encode() + b">"
            # 逐串替换需要知道当前字体——为稳妥：按 Tf 分段处理
            parts = re.split(rb"(BT|ET)", data)
            # 简化：因每页字体的字形集互斥性不足，改为记录当前 Tf 资源名的分段重写
            data = _rewrite_stream(data, name2tag, RES, gid2uni, uni2code)
            doc.update_stream(xref, data)

        # ---------- 5. 页 Resources 指向新字体 ----------
        fonts_dict = " ".join(f"/{RES[t]} {font_refs[t]} 0 R" for t in font_refs)
        # Resources/Font 重指：Resources 可能是间接对象，先解析再设置
        rk = doc.xref_get_key(pg.xref, "Resources")
        target = pg.xref
        if rk and rk[0] == "xref":
            target = int(rk[1].split()[0])
        doc.xref_set_key(target, "Font", f"<< {fonts_dict} >>")

    return font_refs


def _rewrite_stream(data, name2tag, RES, gid2uni, uni2code):
    """按 Tf 分段，把每段内的 CID 串转为该字体的单字节码。"""
    out = []
    pos = 0
    cur_tag = None
    tf_re = re.compile(rb"/(\w+)\s+[\d.]+\s+Tf")
    str_re = re.compile(rb"<([0-9A-Fa-f]{4,})>")
    for m in tf_re.finditer(data):
        seg = data[pos:m.start()]
        out.append(_conv(seg, cur_tag, gid2uni, uni2code, str_re))
        out.append(m.group(0))
        nm = m.group(1).decode()
        cur_tag = name2tag.get(nm)
        if cur_tag:
            out[-1] = b"/" + RES[cur_tag].encode() + b" " + m.group(0).split(b" ", 1)[1]
        pos = m.end()
    out.append(_conv(data[pos:], cur_tag, gid2uni, uni2code, str_re))
    return b"".join(out)


def _conv(seg, tag, gid2uni, uni2code, str_re):
    if tag is None:
        return seg
    g2u = gid2uni.get(tag)
    u2c = uni2code.get(tag)
    if not g2u or not u2c:
        return seg

    def repl(m):
        hs = m.group(1)
        if len(hs) % 4:
            return m.group(0)
        ob = []
        for i in range(0, len(hs), 4):
            cid = int(hs[i:i + 4], 16)
            uni = g2u.get(cid)
            code = u2c.get(uni) if uni is not None else None
            if code is None:
                code = 32 if cid == 0 else None
            if code is None:
                return m.group(0)
            ob.append(f"{code:02X}")
        return b"<" + "".join(ob).encode() + b">"
    return str_re.sub(repl, seg)
