"""适配器共用：身份抬头、样例元数据、分区切分、描述符截断。

适配器只做**展示转换**：不新增交易、不改金额、不静默丢弃放不下的行——截断只作用于
描述符文本（真实账单本来就会截断），金额与余额一律原样带过去。
"""
from __future__ import annotations

import copy
import re
from datetime import date
from typing import Any

from ..ledger import LedgerDocument, LedgerRow
from ..money import cents_to_float, cents_to_grouped, cents_to_plain, digits

__all__ = [
    "SAMPLE_KEYWORDS",
    "SAMPLE_PRODUCER",
    "SAMPLE_SUBJECT",
    "SAMPLE_TITLE",
    "address_lines",
    "bank_block",
    "clip",
    "credit_rows",
    "customer_lines",
    "debit_rows",
    "deep",
    "fee_rows",
    "holder_upper",
    "metadata",
    "pdf_timestamp",
    "split_rows",
]

SAMPLE_TITLE = "Online Statement"
SAMPLE_SUBJECT = ""
SAMPLE_KEYWORDS = "statement"
SAMPLE_PRODUCER = "OpenText Output Transformation Engine 16.6.0"


def deep(defaults: dict[str, Any]) -> dict[str, Any]:
    """模板默认数据永远先深拷贝再用，避免污染注册表里的共享字典。"""
    return copy.deepcopy(defaults)


def pdf_timestamp(when: date) -> str:
    return when.strftime("D:%Y%m%d000000Z")


def metadata(ledger: LedgerDocument) -> dict[str, str]:
    stamp = pdf_timestamp(ledger.period.end)
    return {
        "title": SAMPLE_TITLE,
        "author": "",
        "producer": SAMPLE_PRODUCER,
        "creator": SAMPLE_PRODUCER,
        "subject": SAMPLE_SUBJECT,
        "keywords": SAMPLE_KEYWORDS,
        "creation": stamp,
        "mod": stamp,
    }


def bank_block(ledger: LedgerDocument) -> dict[str, str]:
    return {
        "name": ledger.bank.name,
        "legal": ledger.bank.legal,
        "domain": ledger.bank.domain,
        "po_box": ledger.bank.po_box,
        "po_box_alt": ledger.bank.po_box_alt,
        "city_state_zip": ledger.bank.city_state_zip,
        "phone_service": ledger.bank.phone_service,
        "phone_es": ledger.bank.phone_es,
        "phone_intl": ledger.bank.phone_intl,
        "phone_metro": ledger.bank.phone_metro,
    }


_REFERENCE = re.compile(r"[A-Z0-9#*./ -]{2,20}")


def statement_memo(row: LedgerRow) -> str:
    """只保留像「参考号」的备注，散文化的内部备注不上账单。"""
    memo = " ".join(str(row.memo or "").split())
    if not memo or not _REFERENCE.fullmatch(memo):
        return ""
    return memo if any(ch.isdigit() for ch in memo) else ""


def _city(ledger: LedgerDocument) -> str:
    parts = ledger.holder.city_state.split()
    return " ".join(parts[:-1]) if len(parts) > 1 else parts[0]


def _state(ledger: LedgerDocument) -> str:
    parts = ledger.holder.city_state.split()
    return parts[-1] if len(parts) > 1 else ""


def bank_contact(ledger: LedgerDocument) -> dict[str, str]:
    """银行联系方式：全部由银行名稳定派生，避免版面上留下 0000 占位符。"""
    seed = f"{ledger.bank.domain}|contact"
    zip5 = ledger.bank.city_state_zip.rsplit(" ", 1)[-1] or digits(seed + "|zip", 5)
    return {
        "phone_name": ledger.bank.name,
        "phone_main": ledger.bank.phone_service or ledger.bank.phone,
        "phone_es": ledger.bank.phone_es,
        "po_box": ledger.bank.po_box,
        "po_box2": ledger.bank.po_box_alt,
        "mac_code": digits(seed + "|mac", 4),
        "zip_main": f"{zip5}-{digits(seed + '|zip4', 4)}",
        "des_moines": ledger.bank.city_state_zip,
        "office_code": digits(seed + "|office", 3),
        "nmlsr_id": digits(seed + "|nmlsr", 7),
        "copyright_year": str(ledger.period.end.year),
    }


def customer_lines(ledger: LedgerDocument) -> list[str]:
    """姓名 + 地址行（模板把收件人整块画出来的用这个）。"""
    return list(ledger.holder.address_lines)


def address_lines(ledger: LedgerDocument) -> list[str]:
    """只取地址行；模板已有独立 ``customer_name`` 字段时用它，避免姓名重复。"""
    return list(ledger.holder.street_lines)


def holder_upper(ledger: LedgerDocument) -> str:
    return ledger.holder.name.upper()


_ZELLE_PREFIX = re.compile(r"(?i)^(?:zelle)\b[\s:*-]*")
_ZELLE_VERB = re.compile(r"(?i)^(?:payment|transfer|pmt)\b[\s:*-]*")
_ZELLE_DIR = re.compile(r"(?i)^(?:to|from)\b[\s:*-]*")
_ZELLE_CONF = re.compile(r"(?i)\s*(?:conf\.?\s*#?|confirmation\s*#?)\s*[A-Z0-9]+$")


def zelle_party(row: LedgerRow, fallback: str) -> str:
    """Zelle 行的对手方名字。

    上游描述常是 ``Zelle payment to Jordan R`` / ``Zelle Payment``，直接往模板句式
    ``Zelle payment to {who}`` 里塞会拼成 ``Zelle payment to PAYMENT TO JORDAN R``
    （实测件里就是这个叠字）。这里把 zelle / payment / to / from / Conf# 全部剥掉。
    """
    text = row.description.strip()
    for pat in (_ZELLE_PREFIX, _ZELLE_VERB, _ZELLE_DIR):
        text = pat.sub("", text).strip()
    text = _ZELLE_CONF.sub("", text).strip()
    text = re.sub(r"\s{2,}", " ", text).strip(" -:,")
    return text or fallback


def clip(text: str, limit: int) -> str:
    """描述符按列宽截断（上游描述本来就可能超宽）。"""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def split_rows(ledger: LedgerDocument) -> dict[str, list[LedgerRow]]:
    """按账单分区归类：存款 / 支票 / ATM / 电子扣款 / 其他扣款 / 费用。"""
    out: dict[str, list[LedgerRow]] = {
        "deposits": [],
        "checks": [],
        "atm": [],
        "electronic": [],
        "other": [],
        "fees": [],
    }
    for row in ledger.rows:
        if row.direction == "credit":
            out["deposits"].append(row)
        elif row.type == "CHECK":
            out["checks"].append(row)
        elif row.type == "ATM":
            out["atm"].append(row)
        elif row.type == "FEE":
            out["fees"].append(row)
        elif row.type in {"ACH", "ZELLE", "PAYMENT", "TRANSFER"}:
            out["electronic"].append(row)
        else:
            out["other"].append(row)
    return out


def credit_rows(ledger: LedgerDocument) -> list[LedgerRow]:
    return [r for r in ledger.rows if r.direction == "credit"]


def debit_rows(ledger: LedgerDocument) -> list[LedgerRow]:
    return [r for r in ledger.rows if r.direction == "debit"]


def fee_rows(ledger: LedgerDocument) -> list[LedgerRow]:
    return [r for r in ledger.rows if r.type == "FEE"]


def money_plain(cents: int) -> str:
    return cents_to_plain(cents)


def money_grouped(cents: int) -> str:
    return cents_to_grouped(cents)


def money_float(cents: int) -> float:
    return cents_to_float(cents)
