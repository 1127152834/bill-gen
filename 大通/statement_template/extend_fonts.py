#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Type3 字体精确扩展：用 Arial Bold 按原稿字形度量渲染缺失字符位图"""
import io, re, os
from runtime_paths import font_path
import fitz
from PIL import Image, ImageDraw, ImageFont

BASE = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = font_path('Bold')

# 原稿 F11 各类字符的典型度量（宽pt × 高pt, advance pt）
METRICS = {
    "upper": {"h": 28, "wx": 25},   # 大写
    "lower": {"h": 20, "wx": 19},   # 小写 x 高
    "lower_desc": {"h": 28, "wx": 19},  # 小写含降部
    "digit": {"h": 27, "wx": 21},   # 数字
    "punct": {"h": 8, "wx": 11},    # 标点
}

# 字符→度量参考字符（用已有字符的实际度量）
REF = {
    "H": "N", "J": "U", "K": "N", "Q": "O", "V": "A",
    "j": "i", "z": "s", "'": ".", "%": "$", "#": "&", "+": "-", "*": "x",
    "!": ".", "(": "(", ")": ")",
}


def render_char(ch, target_h, target_w=None):
    """用 Arial Bold 渲染→精确缩放到目标尺寸，返回 (W, H, packed_bytes)"""
    size = int(target_h * 1.35) + 2
    best = None
    for s in range(max(size - 4, 8), size + 8):
        f = ImageFont.truetype(FONT_PATH, s)
        img = Image.new("L", (100, 80), 255)
        draw = ImageDraw.Draw(img)
        draw.text((20, 10), ch, fill=0, font=f)
        bbox = img.point(lambda x: 255 - x).getbbox()
        if not bbox:
            continue
        h = bbox[3] - bbox[1]
        score = abs(h - target_h)
        if best is None or score < best[0]:
            best = (score, s, bbox)

    if best is None:
        return 1, 1, b"\x00"

    _, best_size, bbox = best
    f = ImageFont.truetype(FONT_PATH, best_size)
    img = Image.new("L", (100, 80), 255)
    draw = ImageDraw.Draw(img)
    draw.text((20, 10), ch, fill=0, font=f)
    img = img.crop(bbox)
    W, H = img.size

    # 精确缩放到目标高度（必要时水平缩放匹配原稿度量）
    if H != target_h or (target_w and W != target_w):
        nw = target_w if target_w else W
        img = img.resize((nw, target_h), Image.LANCZOS)
        W, H = img.size

    # 二值化（暗=1）
    img = img.point(lambda x: 1 if x < 100 else 0, mode="1")

    # 转为 packed bytes
    row_bytes = (W + 7) // 8
    data = bytearray()
    for y in range(H):
        row = bytearray(row_bytes)
        for x in range(W):
            if img.getpixel((x, y)):
                row[x // 8] |= 0x80 >> (x % 8)
        data.extend(row)
    return W, H, bytes(data)


def make_glyph_stream(wx, llx, lly, urx, ury, W, H, data):
    """生成 Type3 CharProc 流"""
    stream = (
        f"{wx} 0 {llx} {lly} {urx} {ury} d1 q "
        f"{W} 0 0 {H} {llx} {lly} cm "
        f"BI /W {W} /H {H} /BPC 1 /D [1 0] /IM true ID "
    ).encode("latin-1") + data + b" EI Q "
    return stream


def extend_font(doc, font_xref, missing_chars, tag=None):
    """扩展 Type3 字体：为缺失字符生成 Arial Bold 位图字形"""
    obj = doc.xref_object(font_xref, compressed=False)

    # 解析现有 Differences（对象流每 ~76 字符换行，码位与字形名可能被换行拆开，必须用 \s+）。
    # 防御：>255 的"码位"一律丢弃——简单字体字符码合法域为 0..255，
    # 历史损坏缓存曾把字形名数字（EX121000→121000）混进码位，绝不能再传播
    enc_ref = int(re.search(r"/Encoding\s+(\d+) 0 R", obj).group(1))
    enc_text = doc.xref_object(enc_ref, compressed=False)
    diffs = [(int(c), n) for c, n in
             re.findall(r"(\d+)\s+/(\w+)", enc_text) if int(c) <= 255]
    used_codes = {c for c, _ in diffs}

    # 解析 CharProcs（同理允许名字与 xref 之间有换行）
    cp_ref = int(re.search(r"/CharProcs\s+(\d+) 0 R", obj).group(1))
    cp_text = doc.xref_object(cp_ref, compressed=False)
    glyph_map = dict(re.findall(r"/(\w+)\s+(\d+) 0 R", cp_text))

    # Widths
    w_ref = int(re.search(r"/Widths\s+(\d+) 0 R", obj).group(1))
    w_text = doc.xref_object(w_ref, compressed=False)
    widths = [int(x) for x in re.search(r"\[(.*?)\]", w_text, re.S).group(1).split()]
    first_char = int(re.search(r"/FirstChar\s+(\d+)", obj).group(1))

    # 从现有字形提取参考度量
    code_to_char = {}
    for code, gname in diffs:
        code = int(code)
        if re.fullmatch(r"EX\d{6}", gname):   # EX+6 位数字，共 8 字符
            try:
                ch = chr(int(gname[2:5]))
                code_to_char[ch] = code
            except:
                pass

    # 逐字形度量提取：字符 → 自身 CharProc 的 d1 参数与位图尺寸（每字体独立度量）
    glyph_info = {}
    code_to_gname = {int(c): gn for c, gn in diffs}
    for ch, code in code_to_char.items():
        xr = glyph_map.get(code_to_gname.get(code, ""))
        if not xr:
            continue
        try:
            st = doc.xref_stream(int(xr))
            m = re.search(rb"BI\s+/W (\d+)\s+/H (\d+)", st)
            d1 = re.search(rb"([\d.\-]+) ([\d.\-]+) ([\d.\-]+) ([\d.\-]+) ([\d.\-]+) ([\d.\-]+) d1", st)
            if m and d1:
                glyph_info[ch] = dict(wx=float(d1.group(1)),
                                      llx=float(d1.group(3)), lly=float(d1.group(4)),
                                      urx=float(d1.group(5)), ury=float(d1.group(6)),
                                      H=int(m.group(2)))
        except Exception:
            pass

    def ref_of(ch):
        """在该字体自身已有字形里找同类参考字符（完全按字体自身度量）"""
        if ch.isdigit():
            for c in "0123456789":
                if c in glyph_info:
                    return c
        if ch.isupper():
            for c in (REF.get(ch, "A"), "A", "H", "N", "M", "E", "D", "O"):
                if c in glyph_info:
                    return c
            for c in sorted(glyph_info):
                if c.isupper():
                    return c
        if ch.islower():
            if ch in "gjpqy":
                for c in "gjpqy":
                    if c in glyph_info:
                        return c
            for c in (REF.get(ch, "e"), "e", "o", "a", "s", "n"):
                if c in glyph_info:
                    return c
            for c in sorted(glyph_info):
                if c.islower():
                    return c
        for c in ".,-'$%&()+*!@":
            if c in glyph_info:
                return c
        for c in "0AEOeo.":
            if c in glyph_info:
                return c
        return next(iter(glyph_info), None)

    # 为缺失字符分配码位
    new_diffs = list(diffs)
    added = []

    for ch in missing_chars:
        code = None
        for candidate in range(250, 1, -1):
            if candidate not in used_codes:
                code = candidate
                break
        if code is None:
            break
        used_codes.add(code)
        gname = f"EX{ord(ch):03d}000"

        # 度量=该字体同类字形的克隆（F9/F11/F2…各自独立，杜绝跨字体混用）
        ref = ref_of(ch)
        if ref:
            gi = glyph_info[ref]
            wx, target_h = gi["wx"], gi["H"]
            llx, lly = gi["llx"], gi["lly"]
            urx, ury = gi["urx"], gi["ury"]
        else:  # 字体无任何同类字形时的兜底
            if ch.isdigit():
                wx, target_h = METRICS["digit"]["wx"], METRICS["digit"]["h"]
            elif ch.isupper():
                wx, target_h = METRICS["upper"]["wx"], METRICS["upper"]["h"]
            elif ch.islower():
                wx, target_h = METRICS["lower"]["wx"], METRICS["lower"]["h"]
            else:
                wx, target_h = METRICS["punct"]["wx"], METRICS["punct"]["h"]
            llx = 1
            lly = -8 if ch in "gjpqy(),;" else 0
            urx, ury = llx + wx, target_h

        # 渲染（位图高=参考字形位图高）
        W, H, data = render_char(ch, target_h)
        if W < 2:
            W = 2
        if H < 2:
            H = 2
        urx = max(urx, llx + W)

        # 创建流
        stream = make_glyph_stream(wx, llx, lly, urx, ury, W, H, data)
        sx = doc.get_new_xref()
        doc.update_object(sx, "<< /Length %d >>" % len(stream))
        doc.update_stream(sx, stream, new=True)
        glyph_map[gname] = str(sx)
        new_diffs.append((code, gname))

        # 更新 Widths
        while code - first_char >= len(widths):
            widths.append(300)
        if code - first_char < len(widths):
            widths[code - first_char] = wx
        added.append((ch, code, gname, wx))

    if not added:
        return []

    # 重建 Differences
    dparts, cur = [], None
    for c, n in sorted(new_diffs, key=lambda t: int(t[0])):
        c = int(c)
        if cur is None or c != cur:
            dparts.append(str(c))
        dparts.append("/" + n)
        cur = c + 1
    new_enc = "<< /Type /Encoding /Differences [ " + " ".join(dparts) + " ] >>"
    doc.update_object(enc_ref, new_enc)

    # 重建 CharProcs
    cp_items = " ".join(f"/{n} {x} 0 R" for n, x in glyph_map.items())
    doc.update_object(cp_ref, f"<< {cp_items} >>")

    # 更新 LastChar：取 原稿值 与 最大实际码位 的较大者，并把 Widths 补齐到
    # LastChar-FirstChar+1——规范要求三者严格配套（Acrobat 严格校验项；
    # 历史版本把 LastChar 收窄到新增码位 250 而 Widths 仍 256 条 = 失配报错根因）
    orig_last = int(re.search(r"/LastChar\s+(\d+)", obj).group(1))
    last = max([orig_last] + [int(c) for c, _ in new_diffs])
    while len(widths) < last - first_char + 1:
        widths.append(300)
    doc.update_object(w_ref, "[" + " ".join(str(x) for x in widths) + "]")
    new_obj = re.sub(r"/LastChar\s+\d+", f"/LastChar {last}", obj)
    doc.update_object(font_xref, new_obj)

    # 更新 ToUnicode CMap
    tou_m = re.search(r"/ToUnicode\s+(\d+) 0 R", obj)
    if tou_m and added:
        tou_xref = int(tou_m.group(1))
        try:
            cmap = doc.xref_stream(tou_xref).decode("latin-1")
            entries = [f"<{code:02X}> <{ord(ch):04X}>" for ch, code, _, _ in added]
            block = "beginbfchar\n" + "\n".join(entries) + "\nendbfchar\n"
            cmap = cmap.replace("endcmap", block + "endcmap")
            doc.update_stream(tou_xref, cmap.encode("latin-1"))
        except:
            pass

    return added
