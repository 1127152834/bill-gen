"""对比两份「月账单 JSON」的流水分布，用来验收逼真度。

用法：
    .venv/bin/python tools/distribution_report.py A.json B.json [--labels model,sampler]

看的是「分布层」指标，而不是单行文案：
    笔数、品类占比、品类间隔的变异系数（CV）、金额分位、周末占比、
    同店同额复读率、固定账单（房租/公用事业/订阅）是否一月一笔。
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_CATEGORY_HINTS = (
    ("grocery", r"WHOLEFDS|WHOLE FOODS|TRADER JOE|SAFEWAY|KROGER|VONS|RALPHS|FOOD 4 LESS|SPROUTS|H-E-B|COSTCO|ALDI|GROCERY"),
    ("dining", r"CHIPOTLE|MCDONALD|PANERA|STARBUCKS|SUSHI|PHO|PIZZA|TACO|BURGER|DINER|CAFE|RESTAURANT|CHICK-FIL|IN-N-OUT|SWEETGREEN|SHAKE SHACK|WHATABURGER|NOBU|RAMEN|THAI|GRILL"),
    ("coffee", r"COFFEE|DUNKIN|PEET|BLUE BOTTLE|LATTE"),
    ("fuel", r"SHELL|CHEVRON|VALERO|MOBIL|EXXON|ARCO|76|CIRCLE K|QUIKTRIP|EXXONMOBIL"),
    ("pharmacy", r"CVS|WALGREENS|RITE AID|PHARMACY"),
    ("convenience", r"7-ELEVEN|CIRCLE K|WALMART|TARGET|DOLLAR"),
    ("rideshare", r"UBER|LYFT"),
    ("subscription", r"NETFLIX|SPOTIFY|APPLE\.COM|GOOGLE|ADOBE|NYTIMES|HULU|DISNEY|AUDIBLE|ICLOUD|OPENAI"),
    ("utilities", r"EDISON|SO CAL GAS|PG&E|CON EDISON|AUSTIN ENERGY|TEXAS GAS|WATER|SEWER|XCEL|DUKE|UTILITY"),
    ("telecom", r"AT&T|VERIZON|T-MOBILE|COMCAST|XFINITY|SPECTRUM|MINT MOBILE"),
    ("income", r"PAYROLL|DIRECT DEP|BLUESTEM|LONGBOW|DEPOSIT|ACH IN|TRANSFER IN"),
    ("rent", r"RENT|AVALON|MGMT|PROPERTY|LEASING|APARTMENT"),
    ("insurance", r"GEICO|PROGRESSIVE|STATE FARM|ALLSTATE|INSURANCE|USAA|LEMONADE"),
)

_AMOUNT_KEYS = ("amount", "withdrawal", "deposit", "credit", "debit")


def _rows(data: dict) -> list[dict]:
    """把任意版式的流水数组摊平成一串行。"""
    found: list[dict] = []
    for key, value in data.items():
        if not isinstance(value, list) or not value or not isinstance(value[0], dict):
            continue
        if not any(k in value[0] for k in ("date", "desc", "description", "detail")):
            continue
        for row in value:
            if isinstance(row, dict):
                found.append({**row, "_array": key})
    return found


def _amount(row: dict) -> float:
    for key in _AMOUNT_KEYS:
        if key in row and row[key] not in (None, ""):
            try:
                return float(row[key])
            except (TypeError, ValueError):
                continue
    return 0.0


def _desc(row: dict) -> str:
    for key in ("desc", "description", "detail", "memo"):
        if row.get(key):
            return str(row[key])
    return ""


def _when(row: dict) -> date | None:
    raw = str(row.get("date") or "").strip()
    for fmt in ("%b %d", "%m/%d/%Y", "%m/%d", "%Y-%m-%d", "%b %d, %Y"):
        try:
            parsed = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        year = parsed.year if parsed.year != 1900 else date.today().year
        return date(year, parsed.month, parsed.day)
    return None


def _category(row: dict) -> str:
    text = _desc(row).upper()
    for name, pattern in _CATEGORY_HINTS:
        if re.search(pattern, text):
            return name
    return "other"


def metrics(data: dict) -> dict:
    rows = _rows(data)
    debit = [r for r in rows if _amount(r) < 0]
    credit = [r for r in rows if _amount(r) > 0]

    by_category: dict[str, list[dict]] = defaultdict(list)
    for row in debit:
        by_category[_category(row)].append(row)

    shares = {name: len(items) / max(1, len(debit)) for name, items in by_category.items()}

    cv: dict[str, float] = {}
    for name, items in by_category.items():
        days = [d for d in (_when(r) for r in items) if d]
        days.sort()
        gaps = [(b - a).days for a, b in zip(days, days[1:])]
        if len(gaps) >= 2:
            mean = statistics.fmean(gaps)
            cv[name] = statistics.pstdev(gaps) / mean if mean else 0.0

    quantiles: dict[str, tuple[float, float]] = {}
    for name, items in by_category.items():
        amounts = sorted(abs(_amount(r)) for r in items)
        if len(amounts) >= 2:
            lo = amounts[len(amounts) // 4]
            hi = amounts[(len(amounts) * 3) // 4]
            quantiles[name] = (round(lo, 2), round(hi, 2))

    weekend = [
        r for r in debit
        if _when(r) and _when(r).weekday() >= 5 and _category(r) in {"grocery", "dining", "coffee", "shopping", "other"}
    ]
    weekend_pool = [
        r for r in debit if _when(r) and _category(r) in {"grocery", "dining", "coffee", "shopping", "other"}
    ]

    repeats = Counter((_desc(r).upper(), round(_amount(r), 2)) for r in debit)
    repeat_rate = sum(count - 1 for count in repeats.values() if count > 1) / max(1, len(debit))

    families = Counter()
    for row in debit:
        cat = _category(row)
        if cat in {"rent", "utilities", "insurance", "telecom", "subscription"}:
            families[cat] += 1

    return {
        "rows": len(rows),
        "debits": len(debit),
        "credits": len(credit),
        "income_total": round(sum(_amount(r) for r in credit), 2),
        "spend_total": round(-sum(_amount(r) for r in debit), 2),
        "shares": shares,
        "interval_cv": cv,
        "amount_iqr": quantiles,
        "weekend_share": len(weekend) / max(1, len(weekend_pool)),
        "repeat_rate": repeat_rate,
        "families": dict(families),
    }


def _fmt(value: float) -> str:
    return f"{value:.2f}"


def compare(reports: list[tuple[str, dict]]) -> str:
    lines: list[str] = []
    names = [name for name, _ in reports]
    width = max(12, *(len(n) for n in names)) + 2
    lines.append("| 指标 | " + " | ".join(names) + " |")
    lines.append("|" + "---|" * (len(names) + 1))

    def row(label: str, values: list[str]) -> None:
        lines.append(f"| {label} | " + " | ".join(values) + " |")

    row("流水行数", [_fmt(r["rows"]) for _, r in reports])
    row("支出笔数", [_fmt(r["debits"]) for _, r in reports])
    row("收入笔数", [_fmt(r["credits"]) for _, r in reports])
    row("支出合计", [f"{r['spend_total']:,.2f}" for _, r in reports])
    row("收入合计", [f"{r['income_total']:,.2f}" for _, r in reports])
    row("周末消费占比", [f"{r['weekend_share'] * 100:.0f}%" for _, r in reports])
    row("同店同额复读率", [f"{r['repeat_rate'] * 100:.0f}%" for _, r in reports])

    categories = sorted({name for _, r in reports for name in r["shares"]})
    for category in categories:
        row(
            f"占比 · {category}",
            [f"{r['shares'].get(category, 0) * 100:.0f}%" for _, r in reports],
        )
    for category in categories:
        row(
            f"间隔 CV · {category}",
            [(_fmt(r["interval_cv"][category]) if category in r["interval_cv"] else "—") for _, r in reports],
        )
    for category in categories:
        row(
            f"金额四分位 · {category}",
            [
                (f"{r['amount_iqr'][category][0]}–{r['amount_iqr'][category][1]}" if category in r["amount_iqr"] else "—")
                for _, r in reports
            ],
        )
    families = sorted({name for _, r in reports for name in r["families"]})
    for family in families:
        row(f"每月固定账单 · {family}", [str(r["families"].get(family, 0)) for _, r in reports])
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="对比两份月账单 JSON 的流水分布")
    parser.add_argument("files", nargs="+", help="要对比的 JSON 文件（2 份起）")
    parser.add_argument("--labels", default="", help="逗号分隔的列名")
    parser.add_argument("--out", default="", help="把 markdown 表格写到这个文件")
    args = parser.parse_args(argv)

    labels = [item.strip() for item in args.labels.split(",") if item.strip()]
    reports: list[tuple[str, dict]] = []
    for index, name in enumerate(args.files):
        data = json.loads(Path(name).read_text(encoding="utf-8"))
        label = labels[index] if index < len(labels) else Path(name).stem
        reports.append((label, metrics(data)))

    table = compare(reports)
    print(table)
    if args.out:
        Path(args.out).write_text(table + "\n", encoding="utf-8")
        print(f"\n已写入 {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
