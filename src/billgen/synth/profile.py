"""PDF 文档画像：让 pdfinfo / exiftool 看到的痕迹与真实账单同类。

真实账单的 Producer/Creator 是「账单合成引擎」的名字（OpenText、AFP2PDF、
Smart Communications…），CreationDate 是周期结束后 1~3 天的某个时刻，而不是
周期最后一天 00:00:00。我们原来统一写 ``BillGen Sample Editor``，一查就露。

这里只改**技术层痕迹**（producer/creator/时间戳/结构标记），不动 title 与
SAMPLE 水印——那是账本自己声明的「这是样例」，保留。
"""
from __future__ import annotations

import hashlib

from .ledger import LedgerDocument

__all__ = ["PRODUCERS", "apply", "build_docinfo", "producer_of"]

#: 每类模板对应的「账单合成引擎」，取自真账单样本实测的 Producer/Creator。
PRODUCERS: dict[str, tuple[str, str]] = {
    "boa-personal": ("TargetStream StreamEDS rv1.7.161 for Windows",
                     "Bank of America"),
    "boa-business": ("TargetStream StreamEDS rv1.7.161 for Windows",
                     "Bank of America"),
    "wellsfargo": ("iText\u00ae Core 9.5.0 (AGPL version) \u00a92000-2025 iText Group NV",
                   ""),
    "chase-personal": ("OpenText Output Transformation Engine 16.6.0", ""),
    "chase-business": ("OpenText Output Transformation Engine 16.6.0", ""),
    "usbank-personal": ("Ricoh Americas Corporation, AFP2PDF v3.2.1", "AIM, F3"),
    "usbank-business": ("Ricoh Americas Corporation, AFP2PDF v3.2.1", "AIM, F3"),
    "amex": ("Smart Communications - SC32", ""),
    "mercury": ("react-pdf", "react-pdf"),
    "capitalone": ("openhtmltopdf.com", ""),
    "sofi": ("LibreOffice 7.0", "Writer"),
}

#: 生成时区：账单多在美西/美东机房出件，统一用太平洋时间偏移。
_TZ = "-07'00'"


def producer_of(template_id: str) -> tuple[str, str]:
    return PRODUCERS.get(template_id, ("", ""))


def _stamp(ledger: LedgerDocument, template_id: str) -> str:
    """出件时间：周期结束后 1~3 天、06:00~22:59，由 (seed, 模板, 周期) 确定性派生。"""
    from datetime import timedelta

    key = f"docinfo:{ledger.seed}:{template_id}:{ledger.period.end.isoformat()}"
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    day = ledger.period.end + timedelta(days=1 + digest[0] % 3)
    hour = 6 + digest[1] % 17
    minute = digest[2] % 60
    second = digest[3] % 60
    return f"D:{day:%Y%m%d}{hour:02d}{minute:02d}{second:02d}{_TZ}"


#: 真账单的 Title/Subject/Keywords 就是行名或留空，没有一个会写 "SAMPLE"。
#: 这几项在 exiftool / pdfinfo / 阅读器属性面板里一眼可见，必须跟 Producer 一致地做旧。
DOC_TITLES: dict[str, tuple[str, str, str]] = {
    "boa-personal": ("Bank of America - Online Banking Statement", "", ""),
    "boa-business": ("Bank of America - Online Banking Statement", "", ""),
    "wellsfargo": ("Wells Fargo Online Statement", "", ""),
    "chase-personal": ("Chase Online Statement", "", ""),
    "chase-business": ("Chase Online Statement", "", ""),
    "usbank-personal": ("U.S. Bank Online Statement", "", ""),
    "usbank-business": ("U.S. Bank Online Statement", "", ""),
    "amex": ("American Express Statement", "", ""),
    "mercury": ("Mercury Statement", "", ""),
    "capitalone": ("Capital One Statement", "", ""),
    "sofi": ("SoFi Bank Statement", "", ""),
}


def build_docinfo(template_id: str, ledger: LedgerDocument) -> dict[str, str]:
    """要写进 PDF 的 docinfo 字段（技术层 + 标题层，一律按真账单样式）。"""
    producer, creator = producer_of(template_id)
    stamp = _stamp(ledger, template_id)
    title, subject, keywords = DOC_TITLES.get(template_id, ("Online Statement", "", ""))
    return {
        "producer": producer,
        "creator": creator,
        "creationDate": stamp,
        "modDate": stamp,
        "title": title,
        "subject": subject,
        "keywords": keywords,
    }


def _rebuild_catalog(doc) -> None:
    """重建 catalog，只留下有值的条目（PyMuPDF 只能把键置成 null，删不掉键名）。"""
    catalog = doc.pdf_catalog()
    entries: list[str] = []
    for key in doc.xref_get_keys(catalog):
        kind, value = doc.xref_get_key(catalog, key)
        if kind in {"null", "none"} or value == "null":
            continue
        entries.append(f"/{key} {value}")
    fresh = doc.get_new_xref()
    doc.update_object(fresh, "<< " + " ".join(entries) + " >>")
    doc.xref_set_key(-1, "Root", f"{fresh} 0 R")


def apply(doc, template_id: str, ledger: LedgerDocument) -> None:
    """就地改 docinfo，并摘掉我们多出来的 tagged-PDF 标记。

    PyMuPDF 保存时默认给文档加了 ``StructTreeRoot``/``MarkInfo``，而真实账单
    基本都不带结构树（真 chase 样本的 catalog 是空的）。留着等于自报家门。
    """
    meta = dict(doc.metadata or {})
    meta.update(build_docinfo(template_id, ledger))
    doc.set_metadata(meta)
    catalog = doc.pdf_catalog()
    for key in ("StructTreeRoot", "MarkInfo"):
        if key in doc.xref_get_keys(catalog):
            doc.xref_set_key(catalog, key, "null")
    _rebuild_catalog(doc)
