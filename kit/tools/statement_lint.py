#!/usr/bin/env python3
"""账单契约校验器（零依赖，只用标准库）。

用法：
  python3 statement_lint.py --contract contracts/chase-personal.json --data month.json
  python3 statement_lint.py --contract ... --data ... --conform      # 先按契约整形再校验
  python3 statement_lint.py --contract ... --dump-profile            # 生成 bank_profiles 骨架
  python3 statement_lint.py --list-rules

退出码：0 = 通过；1 = 有问题；2 = 输入/契约本身有问题。

设计约束：
- 只做与版式无关的语义校验；版式（坐标、字体、页数）由渲染器的回归测试负责。
- 契约是唯一权威：这个脚本里不许出现任何银行名。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

MONEY_EPS = 0.005
CARD_WORDS = re.compile(r"\b(CARD|CARD PURCHASE|PURCHASE|POS|DEBIT CARD)\b")
CARD_TAIL = re.compile(r"\bCard\s+(\d{4})\b")
PAYMENT_TAIL = re.compile(r"\s+PAYMENT$")
STATE_CODES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","DC","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA","ME",
    "MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI",
    "SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY",
}
LABEL_SPLIT = re.compile(r"\s{2,}")
PLACE_FIND = re.compile(r"\b([A-Z][A-Z .'-]{1,20})\s+([A-Z]{2})\b")
LEADING_PAD = re.compile(r"^[\s\u00a0]")

ROW_KEYS = ("transactions", "deposits", "withdrawals", "checks", "fees",
            "electronic_withdrawals", "other_withdrawals", "daily_balance")

RULES_HELP = {
    "has_card_tail": "行尾必须是 `Card 1234`",
    "no_card_tail": "行尾不许出现卡号",
    "has_place": "描述里必须带城市/州",
    "no_city": "描述里不许带持卡人所在城市",
    "no_payment_tail": "描述不许以 `PAYMENT` 结尾（剥完只剩一个词时不剥）",
    "upper": "描述必须全大写",
    "no_leading_padding": "描述不许有前导空格",
}


def rows(data: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    for key in ROW_KEYS:
        items = data.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    yield item


def all_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from all_strings(item)


def city_state(data: Mapping[str, Any]) -> tuple[str, str]:
    address = data.get("address") or []
    lines = address if isinstance(address, list) else [str(address)]
    for line in reversed(lines):
        match = re.search(r"([A-Za-z .'-]+),\s*([A-Z]{2})\s*\d{5}", str(line))
        if match:
            return match.group(1).strip().upper(), match.group(2)
    return "", ""


def desc_of(row: Mapping[str, Any]) -> str:
    value = row.get("desc") or row.get("description") or ""
    return value if isinstance(value, str) else ""


def body_of(row: Mapping[str, Any], contract: Mapping[str, Any]) -> str:
    """描述符本体：有的银行把行标签与对齐空档塞进同一个字段（契约 desc_has_label）。"""
    desc = desc_of(row)
    if contract.get("desc_has_label") and desc:
        parts = [p for p in LABEL_SPLIT.split(desc.strip()) if p.strip()]
        if parts:
            return parts[-1].strip()
    return desc


def has_place(desc_upper: str) -> bool:
    for match in PLACE_FIND.finditer(desc_upper):
        if match.group(2) in STATE_CODES:
            return True
    return False


def is_card(row: Mapping[str, Any], contract: Mapping[str, Any]) -> bool:
    card_types = contract.get("card_types") or []
    row_type = row.get("type")
    if card_types and isinstance(row_type, str) and row_type in card_types:
        return True
    return bool(CARD_WORDS.search(body_of(row, contract).upper()))


def spec_for(row: Mapping[str, Any], contract: Mapping[str, Any]) -> Mapping[str, Any]:
    descriptors = contract.get("descriptor") or {}
    key = "card" if is_card(row, contract) else "ach"
    spec = descriptors.get(key)
    if spec:
        return spec
    return descriptors.get("card") or {}


CITY_STATE_TAIL_SUB = re.compile(r"\s+[A-Z][A-Za-z .'-]+\s+[A-Z]{2}$")


def check_data(contract: Mapping[str, Any], data: Mapping[str, Any]) -> list[str]:
    """返回问题列表，空 = 通过。"""
    problems: list[str] = []
    allowed = contract.get("type_enum") or []
    forbidden = contract.get("forbidden_literals") or []
    required = contract.get("required_literals") or []
    rules = contract.get("row_rules") or {}
    decimals = rules.get("amount_decimals")
    city, _state = city_state(data)

    for text in all_strings(data):
        for bad in forbidden:
            if bad and bad.lower() in text.lower():
                problems.append(f"forbidden-literal: 出现 {bad!r}")

    blob = "\n".join(all_strings(data))
    for want in required:
        if want and want not in blob:
            problems.append(f"required-literal: 缺少 {want!r}")

    index = 0
    for row in rows(data):
        index += 1
        row_type = row.get("type")
        if allowed and isinstance(row_type, str) and row_type.strip() and row_type not in allowed:
            problems.append(f"type-enum: 第{index}行 {row_type!r} 不在 {allowed}")
        desc = body_of(row, contract)
        if desc:
            spec = spec_for(row, contract)
            spec_rules = list(spec.get("rules") or [])
            if rules.get("no_leading_padding") and LEADING_PAD.match(desc):
                problems.append(f"no-leading-padding: 第{index}行 {desc[:24]!r}")
            max_len = spec.get("max_len")
            if isinstance(max_len, int) and len(desc) > max_len:
                problems.append(f"descriptor-length: 第{index}行 {desc[:24]!r} 超过 {max_len} 字")
            if "no_city" in spec_rules and city and city in desc.upper():
                problems.append(f"descriptor-city: 第{index}行带了所在地 {city} — {desc!r}")
            if "has_place" in spec_rules and not has_place(desc.upper()):
                problems.append(f"descriptor-place: 第{index}行缺城市/州 — {desc!r}")
            if "has_card_tail" in spec_rules and not CARD_TAIL.search(desc):
                problems.append(f"descriptor-card-tail: 第{index}行缺 `Card 1234` — {desc!r}")
            if "no_card_tail" in spec_rules and CARD_TAIL.search(desc):
                problems.append(f"descriptor-card-tail: 第{index}行不该出现卡号 — {desc!r}")
            if "no_payment_tail" in spec_rules:
                head = PAYMENT_TAIL.sub("", desc.strip())
                if head != desc and " " in head:
                    problems.append(f"descriptor-tail: 第{index}行多了 PAYMENT 尾巴 — {desc!r}")
            if "upper" in spec_rules and desc != desc.upper():
                problems.append(f"descriptor-case: 第{index}行应全大写 — {desc!r}")
        amount = row.get("amount")
        if isinstance(amount, (int, float)) and decimals is not None:
            if abs(round(float(amount), int(decimals)) - float(amount)) > MONEY_EPS:
                problems.append(f"amount-precision: 第{index}行 {amount!r} 超过 {decimals} 位小数")
    return problems


def conform(contract: Mapping[str, Any], data: dict[str, Any]) -> dict[str, int]:
    """按契约整形（就地改 data），返回计数。整形只该有一个执行点。"""
    aliases = contract.get("type_aliases") or {}
    allowed = contract.get("type_enum") or []
    city, _state = city_state(data)
    counts = {"types": 0, "cities": 0, "tails": 0, "clipped": 0, "cased": 0}
    for row in rows(data):
        row_type = row.get("type")
        if isinstance(row_type, str):
            fixed = aliases.get(row_type.strip(), row_type.strip())
            if allowed and fixed not in allowed:
                fixed = row_type.strip()
            if fixed != row_type:
                row["type"] = fixed
                counts["types"] += 1
        raw = desc_of(row)
        desc = body_of(row, contract)
        if not desc:
            continue
        spec = spec_for(row, contract)
        spec_rules = list(spec.get("rules") or [])
        out = desc
        if "no_payment_tail" in spec_rules:
            stripped = PAYMENT_TAIL.sub("", out.strip())
            if stripped != out and " " in stripped and len(stripped) >= 6:
                out = stripped
                counts["tails"] += 1
        if "no_city" in spec_rules and city:
            trimmed = CITY_STATE_TAIL_SUB.sub("", out)
            if trimmed != out and city in out.upper() and len(trimmed) >= 4:
                out = trimmed
                counts["cities"] += 1
        if "upper" in spec_rules and out != out.upper():
            out = out.upper()
            counts["cased"] += 1
        max_len = spec.get("max_len")
        if isinstance(max_len, int) and len(out) > max_len:
            out = out[:max_len].rstrip()
            counts["clipped"] += 1
        if out != desc:
            key = "desc" if "desc" in row else "description"
            row[key] = out if not contract.get("desc_has_label") else raw.replace(desc, out, 1)
    return counts


def dump_profile(contract: Mapping[str, Any]) -> dict[str, Any]:
    bank = contract.get("bank_identity") or {}
    rules = contract.get("row_rules") or {}
    return {
        "account": contract.get("template_id"),
        "bank": bank.get("name") or bank.get("legal"),
        "legal": bank.get("legal"),
        "domain": bank.get("domain"),
        "phone": bank.get("phone"),
        "address": bank.get("city_state_zip"),
        "account_format": rules.get("account_format", "masked-last4"),
        "statement_cycle": rules.get("statement_cycle", "monthly-calendar"),
        "card_tail_in_row": contract.get("card_tail") == "in_row",
        "type_column": bool(contract.get("type_column")),
        "sort": rules.get("order", "asc"),
        "forbidden_literals": contract.get("forbidden_literals") or [],
        "open_questions": contract.get("open_questions") or [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按契约校验一份月账单数据")
    parser.add_argument("--contract", type=Path, help="契约 JSON 路径")
    parser.add_argument("--data", type=Path, help="月账单 JSON 路径")
    parser.add_argument("--conform", action="store_true", help="先按契约整形再校验")
    parser.add_argument("--dump-profile", action="store_true", help="打印 bank_profiles 骨架")
    parser.add_argument("--list-rules", action="store_true", help="列出支持的描述符规则")
    parser.add_argument("--json", action="store_true", help="问题以 JSON 数组输出")
    args = parser.parse_args(argv)

    if args.list_rules:
        for name, text in RULES_HELP.items():
            print(f"{name:20s} {text}")
        return 0

    if not args.contract:
        parser.error("需要 --contract")
    try:
        contract = json.loads(args.contract.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"契读不了：{exc}", file=sys.stderr)
        return 2

    if args.dump_profile:
        print(json.dumps(dump_profile(contract), ensure_ascii=False, indent=2))
        return 0

    if not args.data:
        parser.error("需要 --data（或用 --dump-profile）")
    try:
        data = json.loads(args.data.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"数据读不了：{exc}", file=sys.stderr)
        return 2

    joined = json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    digest = hashlib.sha256(joined).hexdigest()[:12]

    if args.conform:
        counts = conform(contract, data)
        if any(counts.values()):
            print(f"[conform] {counts}")

    problems = check_data(contract, data)
    if args.json:
        print(json.dumps(problems, ensure_ascii=False, indent=2))
    elif problems:
        for item in problems:
            print(f"✗ {item}")
    else:
        print(f"✓ 0 问题（data sha256:{digest}）")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
