"""出件前的最后一道「洗版」。

PyMuPDF 存出来的 PDF 有几处一眼可辨的家族特征，真账单不会有：

1. 文件头固定 ``%PDF-1.7\\n%µ¶``（MuPDF 的二进制标记），并且用**经典 xref 表**；
   真实 AFP/OpenText/iText 产出普遍是 ``%âãÏÓ`` 头 + **XRef 流 + 对象流**。
2. 字体子集前缀是 ``AAAAAJ+`` 这种顺序编号，真样本是随机的六字母。
3. trailer 里 ``/ID`` 两个值完全相同。
4. 同数据两次渲染字节完全一致 —— 真出件有毫秒级时间差，永远不同。

这里用 pikepdf 重写一遍（顺便拿到 XRef 流与对象流），把字体前缀按 seed
重命名，必要时再追加一段随机注释把字节打散。任何一步失败都原样返回，
绝不让「洗版」把本来能出的文件搞坏。
"""

from __future__ import annotations

import hashlib
import io
import re
import string

_SUBSET_TAG = re.compile(r"^[A-Z]{6}\+")

_ALPHABET = string.ascii_uppercase

#: 真实账单里压根不会出现的 catalog 键，PyMuPDF 会自作主张加上。
_DROP_CATALOG_KEYS = ("StructTreeRoot", "MarkInfo", "OutputIntents")

#: 这几家的真样本都带 XMP 包（pdfaid:part=3），其余模板原本就不带。
XMP_THEMES = frozenset({"boa", "chase", "usbank"})


def _six(seed: str, index: int) -> str:
    digest = hashlib.sha256(f"subset:{seed}:{index}".encode()).digest()
    return "".join(_ALPHABET[b % 26] for b in digest[:6])


def _rename_subsets(pdf, seed: str) -> int:
    """把 ``AAAAAJ+Roboto`` 换成本次 seed 派生的随机前缀，返回改了几个。"""
    from pikepdf import Dictionary, Name

    targets: dict[str, str] = {}
    index = 0
    for obj in pdf.objects:
        if not isinstance(obj, Dictionary):
            continue
        base = obj.get("/BaseFont")
        if base is None:
            continue
        name = str(base).lstrip("/")
        if not _SUBSET_TAG.match(name) or name in targets:
            continue
        targets[name] = _six(seed, index) + "+" + name[7:]
        index += 1
    if not targets:
        return 0
    changed = 0
    for obj in pdf.objects:
        if not isinstance(obj, Dictionary):
            continue
        for key in ("/BaseFont", "/FontName"):
            if key not in obj:
                continue
            raw = str(obj[key]).lstrip("/")
            if raw in targets:
                obj[key] = Name("/" + targets[raw])
                changed += 1
    return changed


def _minimal_xmp(pdf, producer: str) -> bool:
    """给需要 XMP 的三家补一段最小包（真样本都带 ``pdfaid:part``）。"""
    from pikepdf import Dictionary, Name, String

    if "/Metadata" in pdf.Root:
        return False
    body = (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:pdf="http://ns.adobe.com/pdf/1.3/"'
        f' xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/"><pdf:Producer>{producer}</pdf:Producer>'
        "<pdfaid:part>3</pdfaid:part><pdfaid:conformance>B</pdfaid:conformance>"
        "</rdf:Description></rdf:RDF></x:xmpmeta>"
        '<?xpacket end="w"?>'
    )
    stream = pdf.make_stream(body.encode("utf-8"))
    stream.Type = Name.Metadata
    stream.Subtype = Name.XML
    pdf.Root.Metadata = pdf.make_indirect(stream)
    return True


#: 一眼假的出件时间：0 点 0 分 0 秒，或者整点整分 —— 机器批出来的样子。
_ROUND_STAMP = re.compile(r"^D:(\d{8})(\d{2})0000(?:([+-]\d{2})'(\d{2})'|Z)?$")


def _fix_stamp(doc, seed: str) -> bool:
    """把「整点整秒」的 creationDate 打散到真实出件时段（06–22 点，带秒）。"""
    meta = dict(doc.docinfo or {})
    raw = str(meta.get("/CreationDate") or "")
    match = _ROUND_STAMP.match(raw)
    if not match:
        return False
    digest = hashlib.sha256(f"stamp:{seed}".encode()).digest()
    day, hour, tz, tzmin = match.groups()
    new_hour = 6 + digest[0] % 17
    new_min = digest[1] % 60
    new_sec = digest[2] % 60
    if hour != "00":
        # 原本就有小时，只在分秒上打散，别把时段改掉
        new_hour = int(hour)
    suffix = f"{tz}'{tzmin}'" if tz else "Z"
    stamp = f"D:{day}{new_hour:02d}{new_min:02d}{new_sec:02d}{suffix}"
    doc.docinfo["/CreationDate"] = stamp
    if "/ModDate" in doc.docinfo:
        doc.docinfo["/ModDate"] = stamp
    return True


def _add_grain(pdf: bytes, seed: str) -> bytes:
    """叠一层极淡的扫描颗粒（每页不同），把「纯矢量白底」的机器感打掉。

    密度按经验调过：35‰ 的中灰点 + 21‰ 的浅灰点，用 2pt 一颗的 Pixmap 拉伸铺满
    整页，肉眼是复印纸的麻点，不影响文字识别，也不改任何内容流几何。
    """
    try:
        import fitz
        import random
    except ModuleNotFoundError:  # pragma: no cover
        return pdf
    try:
        doc = fitz.open(stream=pdf, filetype="pdf")
    except Exception:  # pragma: no cover
        return pdf
    try:
        for i, page in enumerate(doc):
            w = max(1, int(page.rect.width / 2))
            h = max(1, int(page.rect.height / 2))
            rng = random.Random(f"grain:{seed}:{i}")
            buf = bytearray()
            for _ in range(w * h):
                r = rng.random()
                if r < 0.035:
                    buf += bytes((205, 10))
                elif r < 0.056:
                    buf += bytes((235, 6))
                else:
                    buf += bytes((255, 0))
            pix = fitz.Pixmap(fitz.csGRAY, w, h, bytes(buf), True)
            page.insert_image(page.rect, pixmap=pix, overlay=True)
        return doc.tobytes(garbage=3, deflate=True, no_new_id=True)
    except Exception:  # pragma: no cover
        return pdf
    finally:
        doc.close()


def finalize(
    pdf: bytes,
    *,
    seed: str = "",
    producer: str = "",
    xmp: bool = False,
    entropy: bool = False,
    fix_stamp: bool = True,
    grain: bool = False,
) -> bytes:
    """重写对象结构、随机化字体前缀，可选补 XMP 与打散字节。

    ``grain`` 默认关闭：真实账单是银行系统直出的电子 PDF，纸面干净无扫描噪点。
    只有需要「扫描件」观感时才打开。
    """
    try:
        import pikepdf
    except ModuleNotFoundError:  # pragma: no cover - 依赖缺失时保持原样
        return pdf
    try:
        doc = pikepdf.open(io.BytesIO(pdf))
    except Exception:  # 加密 / 结构异常：不动原文件
        return pdf
    if doc.is_encrypted:  # 加密件（amex 默认）重写会掉加密，宁可不动
        doc.close()
        return pdf
    doc.close()
    if grain:
        pdf = _add_grain(pdf, seed or "grain")
    try:
        doc = pikepdf.open(io.BytesIO(pdf))
    except Exception:  # pragma: no cover
        return pdf
    try:
        for key in _DROP_CATALOG_KEYS:
            if key in doc.Root:
                del doc.Root[key]
        if seed:
            _rename_subsets(doc, seed)
        if fix_stamp:
            _fix_stamp(doc, seed or "stamp")
        if xmp and producer:
            _minimal_xmp(doc, producer)
        buf = io.BytesIO()
        doc.save(
            buf,
            object_stream_mode=pikepdf.ObjectStreamMode.generate,
            compress_streams=True,
            recompress_flate=True,
            deterministic_id=False,
        )
        out = buf.getvalue()
    except Exception:  # pragma: no cover - 保守失败
        return pdf
    finally:
        doc.close()
    # 换掉 qpdf/MuPDF 的二进制标记行，改成像 Acrobat 出件的 %âãÏÓ
    match = re.match(rb"^(%PDF-1\.\d\n%)[^\n\r]{4}", out)
    if match:
        out = match.group(1) + b"\xe2\xe3\xcf\xd3" + out[match.end():]
    if entropy:
        # 真出件每次都有毫秒级差异，这里补一段合法注释把字节打散。
        import os

        padding = os.urandom(1 + (os.urandom(1)[0] % 23))
        out = out.rstrip(b"\r\n") + b"\n%" + padding.hex().encode() + b"\n"
    return out
