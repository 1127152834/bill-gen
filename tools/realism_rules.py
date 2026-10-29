"""账单规则检查：把「真账单长什么样」变成可执行的断言。

    python tools/realism_rules.py                    # 全部模板
    python tools/realism_rules.py --templates chase-personal capitalone
    python tools/realism_rules.py --json             # 机器可读，供 CI / 测试用

检查分两类：

1. **通用反模式**（任何模板都不许出现）：占位词、省略号、`- $` / `+ $` 写法、
   全零路由号、全零 ACH trace。
2. **逐家行文**（只有这家才该出现）：Chase/BoA 的卡尾号、Capital One 的品类列、
   SoFi 的 `Direct Payment`、Mercury 的 `ACH In` / `End of Day Balance`、
   U.S. Bank 的 `Ref Number`、Amex 的 `DEBIT CARD PURCHASE` + ID 行。

退出码 1 表示有规则不满足。新增模板/改行文时先跑这个，再跑 pytest。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import fitz  # noqa: E402

from billgen.synth import from_game_result, render, supported_ids  # noqa: E402

PERSONAL = {
    "id": "realism-rules",
    "seed": "realism-rules",
    "createdAt": "2027-02-01T00:00:00.000Z",
    "subject": {
        "name": "Maya Okafor",
        "age": 34,
        "address": {"street": "2104 S 1st St", "city": "Austin", "state": "TX", "zip": "78704"},
        "occupation": "Designer",
        "household": "single",
        "hobbies": [],
    },
    "statements": [
        {
            "bankName": "JPMorgan Chase Bank, N.A.",
            "accountType": "checking",
            "accountLast4": "1347",
            "periodStart": "2027-01-01",
            "periodEnd": "2027-01-31",
            "months": [
                {
                    "cycleStart": "2027-01-01",
                    "cycleEnd": "2027-01-31",
                    "openingBalanceCents": 480000,
                    "closingBalanceCents": 512345,
                    "totalCreditsCents": 320000,
                    "totalDebitsCents": 287655,
                    "transactionCount": 8,
                }
            ],
            "transactions": [
                {"id": "t0", "transactionDate": "2027-01-04", "postedDate": "2027-01-04",
                 "description": "ACME CORP PAYROLL PPD", "memo": "", "type": "ACH",
                 "direction": "credit", "amountCents": 312580},
                {"id": "t1", "transactionDate": "2027-01-05", "postedDate": "2027-01-05",
                 "description": "WHOLE FOODS MKT 105 AUSTIN TX", "memo": "", "type": "POS",
                 "direction": "debit", "amountCents": 8642},
                {"id": "t2", "transactionDate": "2027-01-07", "postedDate": "2027-01-07",
                 "description": "CAMDEN PROPERTY MGMT RENT", "memo": "", "type": "ACH",
                 "direction": "debit", "amountCents": 215000},
                {"id": "t3", "transactionDate": "2027-01-09", "postedDate": "2027-01-09",
                 "description": "ZELLE PAYMENT TO JORDAN R", "memo": "", "type": "ZELLE",
                 "direction": "debit", "amountCents": 12000},
                {"id": "t4", "transactionDate": "2027-01-12", "postedDate": "2027-01-12",
                 "description": "NETFLIX.COM", "memo": "", "type": "POS",
                 "direction": "debit", "amountCents": 2299},
                {"id": "t5", "transactionDate": "2027-01-15", "postedDate": "2027-01-15",
                 "description": "ATM WITHDRAWAL 0115 TERMINAL 7014", "memo": "", "type": "ATM",
                 "direction": "debit", "amountCents": 20000},
                {"id": "t6", "transactionDate": "2027-01-18", "postedDate": "2027-01-18",
                 "description": "CHECK 1042", "memo": "", "type": "CHECK",
                 "direction": "debit", "amountCents": 45000},
                {"id": "t7", "transactionDate": "2027-01-31", "postedDate": "2027-01-31",
                 "description": "INTEREST PAYMENT", "memo": "", "type": "INTEREST",
                 "direction": "credit", "amountCents": 82},
            ],
        }
    ],
    "audit": {"rules": [], "findings": []},
    "usage": {"llmCalls": 0, "tokens": 0, "tavilyQueries": 0},
    "warnings": [],
    "status": "completed",
}

#: 通用反模式：任何模板出现即是假件特征。
COMMON_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    ("placeholder", "占位词", re.compile(r"\b(SAMPLE|EXAMPLE|DEMO|DUMMY|LOREM IPSUM)\b")),
    ("ellipsis", "省略号（真账单是硬切）", re.compile(r"…")),
    ("plus-sign", "金额带正号 `+ $1.00`", re.compile(r"\+[^\S\n]*\$\d")),
    ("spaced-minus", "符号与金额之间有空格 `- $1.00`", re.compile(r"(?<![\w$])-[^\S\n]+\$\d")),
    ("zero-routing", "全零路由号", re.compile(r"\b0{9}\b")),
    ("zero-trace", "全零 ACH trace", re.compile(r"\b0{8}\d")),
]

#: 逐家行文：只有这家才该出现的东西（以及别家不该有的）。
BANK_RULES: dict[str, list[tuple[str, str, re.Pattern[str]]]] = {
    "chase-personal": [
        ("card-tail", "卡行带 `Card 1347`", re.compile(r"Card \d{4}")),
        ("zelle-to", "Zelle 行有 `to`", re.compile(r"Zelle Payment to ")),
        ("ach-label", "ACH 行有类型前缀", re.compile(r"ACH (Credit|Debit)")),
    ],
    "chase-business": [
        ("card-tail", "卡行带 `Card 1347`", re.compile(r"Card \d{4}")),
        ("check-paid", "支票行有 `Check Paid`", re.compile(r"Check Paid")),
    ],
    "boa-personal": [
        ("card-tail", "卡行带 `Card 1347`", re.compile(r"Card \d{4}")),
        ("co-id", "ACH 行有 `CO ID:XXXXX#####`", re.compile(r"CO ID:XXXXX\d{5}")),
        ("des", "ACH 行是 `DES:…` 结构", re.compile(r"DES:[A-Z]")),
    ],
    "boa-business": [
        ("co-id", "ACH 行有 `CO ID:XXXXX#####`", re.compile(r"CO ID:XXXXX\d{5}")),
    ],
    "capitalone": [
        ("category", "CATEGORY 列是品类词", re.compile(r"\b(Groceries|Dining|Bills & Utilities|Income|Transfers|Cash|Merchandise)\b")),
    ],
    "sofi": [
        ("direct-payment", "TYPE 列有 `Direct Payment`", re.compile(r"Direct Payment")),
        ("txid", "有 `Transaction ID:` 续行", re.compile(r"Transaction ID:")),
    ],
    "mercury": [
        ("ach-in", "Type 列有 `ACH In`", re.compile(r"ACH In")),
        ("eod", "有 `End of Day Balance` 列", re.compile(r"End of Day Balance")),
    ],
    "amex": [
        ("card-purchase", "有 `DEBIT CARD PURCHASE`", re.compile(r"DEBIT CARD PURCHASE")),
        ("id-line", "有 `ID ####` 续行", re.compile(r"ID \d{10,}")),
    ],
    "usbank-personal": [
        ("ref-number", "有 `Ref Number` 列", re.compile(r"Ref Number")),
    ],
    "usbank-business": [
        ("ref-number", "有 `Ref Number` 列", re.compile(r"Ref Number")),
    ],
    "wellsfargo": [
        ("card-purchase", "有 `Card Purchase` 行文", re.compile(r"Card Purchase")),
        ("daily-balance", "有 `Ending daily balance` 列", re.compile(r"Ending daily")),
    ],
}

#: 别家不该出现的标记（避免「全都一样」）。
FORBIDDEN_PER_BANK: dict[str, list[tuple[str, str, re.Pattern[str]]]] = {
    "capitalone": [("card-tail", "Capital One 行内不印卡尾号", re.compile(r"Card \d{4}"))],
    "sofi": [("card-tail", "SoFi 行内不印卡尾号", re.compile(r"Card \d{4}"))],
    "mercury": [("card-tail", "Mercury 行内不印卡尾号", re.compile(r"Card \d{4}"))],
    "amex": [("card-tail", "Amex 行内不印卡尾号", re.compile(r"Card \d{4}"))],
    "wellsfargo": [("card-tail", "Wells Fargo 行内不印卡尾号", re.compile(r"Card \d{4}"))],
    "usbank-personal": [("card-tail", "U.S. Bank 行内不印卡尾号", re.compile(r"Card \d{4}"))],
    "usbank-business": [("card-tail", "U.S. Bank 行内不印卡尾号", re.compile(r"Card \d{4}"))],
}


def _payload() -> dict:
    payload = json.loads(json.dumps(PERSONAL))
    business = json.loads(json.dumps(payload["statements"][0]))
    business["accountType"] = "business_checking"
    business["accountLast4"] = "8821"
    payload["statements"].append(business)
    return payload



#: 日期写法 → (月, 日)。各家版式各不相同（`Aug 29` / `08/29` / `August 29, 2026`）。
_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}
_DATE_PATTERNS = (
    re.compile(r"^([A-Z][a-z]{2})[a-z]*\s+(\d{1,2})(?:,\s*\d{4})?$"),
    re.compile(r"^(\d{1,2})/(\d{1,2})(?:/\d{2,4})?$"),
)


def _to_month_day(value: str) -> tuple[int, int] | None:
    text = str(value or "").strip()
    for idx, pattern in enumerate(_DATE_PATTERNS):
        hit = pattern.match(text)
        if not hit:
            continue
        if idx == 0:
            month = _MONTHS.get(hit.group(1).lower()[:3])
            if month:
                return (month, int(hit.group(2)))
        else:
            return (int(hit.group(1)), int(hit.group(2)))
    return None


def _row_lists(node, path=""):
    """把所有「带 date 字段的 dict 列表」挖出来（它们就是版面上的一列流水）。"""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _row_lists(value, f"{path}.{key}")
    elif isinstance(node, list):
        if node and all(isinstance(item, dict) and "date" in item for item in node):
            yield path, node
        else:
            for i, item in enumerate(node):
                yield from _row_lists(item, f"{path}[{i}]")


def date_order_problems(template: str, data: dict) -> list[dict[str, str]]:
    """流水必须按日期升序印：分区拼接最容易漏排（实测 USB 印出 Sep 6 → Aug 29 → Sep 4）。

    跨年账期（12 月 → 1 月）允许一次回绕，别的一律算错序。
    """
    problems: list[dict[str, str]] = []
    for path, rows in _row_lists(data):
        if len(rows) < 2:
            continue
        prev = None
        wrap_seen = False
        for item in rows:
            cur = _to_month_day(item.get("date", ""))
            if cur is None:
                prev = None
                continue
            if prev is not None and cur < prev:
                wraps = prev[0] >= 11 and cur[0] <= 2
                if wraps and not wrap_seen:
                    wrap_seen = True
                else:
                    problems.append(
                        {
                            "template": template,
                            "rule": "date-order",
                            "label": "流水日期必须升序",
                            "hit": f"{path}: {prev[0]:02d}/{prev[1]:02d} → {cur[0]:02d}/{cur[1]:02d}",
                        }
                    )
                    break
            prev = cur
    return problems


def statement_text(template: str, ledger, *, watermark: bool = False) -> str:
    """渲染并取正文文本。

    默认**不画水印**：水印是我们给样张加的标记，真实出件没有；规则检查看的是出件形态。
    """
    with fitz.open(stream=render(template, ledger).pdf, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def check_template(template: str, ledger) -> list[dict[str, str]]:
    text = statement_text(template, ledger)
    problems: list[dict[str, str]] = []
    for key, label, pattern in COMMON_RULES:
        hit = pattern.search(text)
        if hit:
            problems.append({"template": template, "rule": key, "label": label, "hit": hit.group(0)})
    for key, label, pattern in BANK_RULES.get(template, []):
        if not pattern.search(text):
            problems.append({"template": template, "rule": key, "label": label, "hit": "（缺失）"})
    try:
        from billgen.synth.bridge import build_data

        problems.extend(date_order_problems(template, build_data(template, ledger)))
    except Exception as exc:  # noqa: BLE001 - 拿不到装配数据就当规则不满足
        problems.append({"template": template, "rule": "date-order", "label": "无法检查日期顺序", "hit": str(exc)[:120]})
    for key, label, pattern in FORBIDDEN_PER_BANK.get(template, []):
        hit = pattern.search(text)
        if hit:
            problems.append({"template": template, "rule": key, "label": label, "hit": hit.group(0)})
    return problems


def _ledger_for(template: str):
    """business 版式要 business_checking 账本，个人版式要 checking。"""
    payload = _payload()
    want = "business_checking" if template.endswith("-business") else "checking"
    payload["statements"] = [s for s in payload["statements"] if s["accountType"] == want]
    return from_game_result(payload, watermark=False)


def run(templates: list[str] | None = None) -> list[dict[str, str]]:
    ids = templates or list(supported_ids())
    problems: list[dict[str, str]] = []
    for template in ids:
        try:
            problems.extend(check_template(template, _ledger_for(template)))
        except Exception as exc:  # noqa: BLE001 - 渲染失败也是规则不满足
            problems.append({"template": template, "rule": "render", "label": "渲染失败", "hit": str(exc)[:120]})
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--templates", nargs="*", help="只检查这些模板（默认全部）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    problems = run(args.templates)
    if args.json:
        print(json.dumps(problems, ensure_ascii=False, indent=2))
    else:
        ids = args.templates or list(supported_ids())
        for template in ids:
            mine = [p for p in problems if p["template"] == template]
            mark = "✔" if not mine else "✘"
            print(f"{mark} {template}")
            for p in mine:
                print(f"    - [{p['rule']}] {p['label']}：{p['hit']}")
        print(f"\n{len(problems)} 条规则不满足" if problems else "\n全部规则通过")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
