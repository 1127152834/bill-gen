"""账单契约：把「每家银行流水怎么写」变成可校验的数据，而不是散落的 if-else。

设计见 ``docs/statement-contract.md``：

- 契约从**真实样本**提取，每条规则带 ``evidence``（哪个文件的哪一页/哪段代码）。
- 运行时只读契约：渲染器与校验器都不需要知道银行名。
- ``check()`` 只做**与版式无关**的语义校验（类型枚举、描述符语法、金额精度、禁止字面量），
  版式本身由 ``tools/realism_rules.py`` 与模板回归测试负责。

契约文件是 JSON（仓库不带 PyYAML 依赖），路径 ``contracts/<template>.json``，
安装环境下退回到 :data:`CONTRACTS_DIR`（内置于包内资源）。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Iterable, Mapping

from .paths import ROOT, relocated

__all__ = ["CONTRACTS_DIR", "contract_path", "load", "available", "resolve", "check", "check_data", "conform"]

CONTRACTS_DIR: Final[Path] = ROOT / "contracts"

_MONEY_EPS = 0.005
_PAYMENT_TAIL: Final[re.Pattern[str]] = re.compile(r"\s+PAYMENT$")
_CARD_TAIL: Final[re.Pattern[str]] = re.compile(r"\bCard\s+(\d{4})\b")


def contract_path(template_id: str) -> Path:
    """契约文件位置：先查仓库根，再查打包资源。"""
    for base in (CONTRACTS_DIR, Path(__file__).parent / "data" / "contracts"):
        candidate = base / f"{template_id}.json"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"没有 {template_id} 的账单契约（contracts/{template_id}.json）")


@lru_cache(maxsize=64)
def load(template_id: str) -> dict[str, Any]:
    return json.loads(contract_path(template_id).read_text(encoding="utf-8"))


def resolve(template_id: str) -> str:
    """把渲染器 id 解析成契约 id：``mercury`` → ``mercury-personal``，找不到就原样返回。"""
    names = available()
    if template_id in names:
        return template_id
    for suffix in ("-personal", "-business"):
        if f"{template_id}{suffix}" in names:
            return f"{template_id}{suffix}"
    return template_id


def available() -> list[str]:
    if not CONTRACTS_DIR.is_dir():
        return []
    return sorted(p.stem for p in CONTRACTS_DIR.glob("*.json"))


def _rows(data: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    """账本行可能在 ``transactions``，也可能在 boa/usbank 的``deposits``/``withdrawals``。"""
    for key in ("transactions", "deposits", "withdrawals", "checks", "fees",
                "electronic_withdrawals", "other_withdrawals", "daily_balance"):
        items = data.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    yield item


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _city_state(data: Mapping[str, Any]) -> tuple[str, str]:
    address = data.get("address") or []
    lines = address if isinstance(address, list) else [str(address)]
    for line in reversed(lines):
        match = re.search(r"([A-Za-z .'-]+),\s*([A-Z]{2})\s*\d{5}", str(line))
        if match:
            return match.group(1).strip().upper(), match.group(2)
    return "", ""


def check_data(contract: Mapping[str, Any], data: Mapping[str, Any]) -> list[str]:
    """按契约校验一份月模板数据，返回问题列表（空列表 = 通过）。"""
    problems: list[str] = []
    allowed = contract.get("type_enum") or []
    forbidden = contract.get("forbidden_literals") or []
    descriptors = contract.get("descriptor") or {}
    rules = contract.get("row_rules") or {}
    city, _state = _city_state(data)

    for text in _strings(data):
        for bad in forbidden:
            if bad and bad.lower() in text.lower():
                problems.append(f"forbidden-literal: 出现 {bad!r}")

    decimals = rules.get("amount_decimals")
    for row in _rows(data):
        row_type = row.get("type")
        if allowed and isinstance(row_type, str) and row_type.strip() and row_type not in allowed:
            problems.append(f"type-enum: {row_type!r} 不在 {allowed}")
        desc = row.get("desc") or row.get("description")
        if isinstance(desc, str):
            if rules.get("no_leading_padding") and desc != desc.lstrip():
                problems.append(f"row-format: 描述有前导空白 {desc[:24]!r}")
            spec = descriptors.get("card") if _looks_like_card(row) else descriptors.get("ach")
            if spec:
                max_len = spec.get("max_len")
                if isinstance(max_len, int) and len(desc) > max_len:
                    problems.append(f"descriptor-length: {desc[:24]!r} 超过 {max_len} 字")
                if "no_city" in (spec.get("rules") or []) and city and city in desc.upper():
                    problems.append(f"descriptor-city: 卡行描述里带了所在地 {city} — {desc!r}")
                if "no_card_tail" in (spec.get("rules") or []) and _CARD_TAIL.search(desc):
                    problems.append(f"descriptor-card-tail: 行尾不该出现卡号 — {desc!r}")
                if "no_payment_tail" in (spec.get("rules") or []) and _PAYMENT_TAIL.search(desc.upper()):
                    head = _PAYMENT_TAIL.sub("", desc.strip())
                    if " " in head:  # 只剩一个词时别剥，剥完 "CLIENT PAYMENT" 会变成 "CLIENT"
                        problems.append(f"descriptor-tail: 描述多了支付尾巴 — {desc!r}")
                if "has_card_tail" in (spec.get("rules") or []) and not _CARD_TAIL.search(desc):
                    problems.append(f"descriptor-card-tail: 这家银行的行尾要带卡号 — {desc!r}")
        amount = row.get("amount")
        if isinstance(amount, (int, float)) and decimals is not None:
            if abs(round(float(amount), int(decimals)) - float(amount)) > _MONEY_EPS:
                problems.append(f"amount-precision: {amount!r} 超过 {decimals} 位小数")
    return problems


def _looks_like_card(row: Mapping[str, Any]) -> bool:
    text = f"{row.get('type') or ''} {row.get('desc') or row.get('description') or ''}".upper()
    return any(word in text for word in ("CARD", "PURCHASE", "POS"))


def check(template_id: str, data: Mapping[str, Any]) -> list[str]:
    """按 ``template_id`` 的契约校验，返回问题列表。"""
    return check_data(load(template_id), data)


_CITY_STATE_TAIL: Final[re.Pattern[str]] = re.compile(r"\s+[A-Z][A-Z .'-]{1,20}?\s+[A-Z]{2}$")


def conform(template_id: str, data: dict[str, Any]) -> dict[str, int]:
    """按契约把描述符改成这家银行的样子（就地改 data），返回每类改动计数。

    这是「格式常识」的唯一执行点：流水线其它地方只产出语义字段，
    谁家的行尾带不带城市、带不带卡号，全由契约决定。
    """
    contract = load(template_id)
    descriptors = contract.get("descriptor") or {}
    aliases = contract.get("type_aliases") or {}
    allowed = contract.get("type_enum") or []
    counts = {"types": 0, "cities": 0, "tails": 0, "clipped": 0}
    for row in _rows(data):
        row_type = row.get("type")
        if isinstance(row_type, str):
            fixed = aliases.get(row_type.strip(), row_type.strip())
            if allowed and fixed not in allowed:
                fixed = row_type.strip()
            if fixed != row_type:
                row["type"] = fixed
                counts["types"] += 1
        key = "card" if _looks_like_card(row) else "ach"
        spec = descriptors.get(key) or {}
        rules = spec.get("rules") or []
        desc = row.get("desc") or row.get("description")
        if not isinstance(desc, str) or not desc:
            continue
        out = desc
        if "no_payment_tail" in rules:
            stripped = _PAYMENT_TAIL.sub("", out.strip())
            if stripped != out and " " in stripped and len(stripped) >= 6:
                out = stripped
                counts["tails"] += 1
        if "no_city" in rules:
            trimmed = _CITY_STATE_TAIL.sub("", out)
            if trimmed != out and len(trimmed) >= 4:
                out = trimmed
                counts["cities"] += 1
        max_len = spec.get("max_len")
        if isinstance(max_len, int) and len(out) > max_len:
            out = out[:max_len].rstrip()
            counts["clipped"] += 1
        if out != desc:
            if "desc" in row:
                row["desc"] = out
            else:
                row["description"] = out
    return counts
