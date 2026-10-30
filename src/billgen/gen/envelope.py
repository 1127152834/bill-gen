"""按收入档算出「每月各类目该花多少」，把模型的金额从自由发挥变成有约束。

为什么需要这一层：模型很会写人物故事，但对「量级」没有稳定记忆——同一个档案
能写出房租 $275、加点 $4.56、同月三份电费。与其事后一笔笔去改（`postpass` 干的就是
这个），不如**先把预算信封算出来**在提示词里给死，再拿它校验产出的分布。

占比参考美国住户消费结构（房租 25–32%、食品 8–12%、餐饮 4–7%、交通燃油 3–6%…），
按城市消费层级做缩放。输出一律是**整数分**，方便和账本对齐。
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

__all__ = [
    "CATEGORY_SHARES",
    "COL_SCALE",
    "envelope",
    "envelope_line",
    "envelope_report",
    "parse_income_hint",
]

#: 类目 → (占净收入下限, 上限)。类目名与 `postpass._category_of()` 的输出保持一致。
CATEGORY_SHARES: dict[str, tuple[float, float]] = {
    "rent": (0.25, 0.32),
    "groceries": (0.08, 0.12),
    "dining": (0.04, 0.07),
    "fuel": (0.03, 0.06),
    "utilities": (0.03, 0.06),
    "phone": (0.010, 0.020),
    "insurance": (0.02, 0.05),
    "subscription": (0.005, 0.020),
    "shopping": (0.03, 0.08),
    "health": (0.010, 0.030),
}

#: 城市消费层级 → 非房租类目的缩放（住房本身由房租提示项决定）。
COL_SCALE: dict[str, float] = {"low": 0.88, "mid": 1.0, "high": 1.18, "very-high": 1.3}

_DEFAULT_INCOME = 6200.0

#: 到手可支配比例：扣掉税与储蓄后真正花掉的份额（房租另按提示项，不参与缩放）。
SPENDABLE = 0.62


def parse_income_hint(value: Any) -> tuple[float, float] | None:
    """`"9000-14000"` / `"$6,000"` → 月收入区间（美元，浮点）。"""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (round(float(value) * 0.9, 2), round(float(value) * 1.1, 2))
    numbers = [float(item.replace(",", "")) for item in re.findall(r"\d[\d,]*(?:\.\d+)?", str(value))]
    if not numbers:
        return None
    if len(numbers) == 1:
        return (numbers[0] * 0.9, numbers[0] * 1.1)
    low, high = min(numbers[0], numbers[1]), max(numbers[0], numbers[1])
    return (low, high)


def _income(profile: Mapping[str, Any], norm: Mapping[str, Any]) -> tuple[float, float]:
    hint = profile.get("monthly_income_hint") or profile.get("incomeHint") or profile.get("monthlyIncomeHint")
    parsed = parse_income_hint(hint)
    if parsed:
        return parsed
    for key in ("monthly_income", "monthlyIncome", "income"):
        value = profile.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return (float(value) * 0.9, float(value) * 1.1)
    tier = str((norm.get("location") or {}).get("col_tier") or "mid")
    return (_DEFAULT_INCOME * COL_SCALE.get(tier, 1.0),) * 2


def envelope(
    profile: Mapping[str, Any],
    norm: Mapping[str, Any],
    *,
    rent_hint: Any = None,
) -> dict[str, tuple[int, int]]:
    """算每月各类目的预算区间（整数分）。``rent`` 若给了房租提示，直接用提示区间。"""
    low, high = _income(profile, norm)
    mid = (low + high) / 2
    tier = str((norm.get("location") or {}).get("col_tier") or "mid")
    scale = COL_SCALE.get(tier, 1.0)
    out: dict[str, tuple[int, int]] = {}
    rent = parse_income_hint(rent_hint or profile.get("rent_hint") or profile.get("rentHint"))
    for name, (lo_share, hi_share) in CATEGORY_SHARES.items():
        if name == "rent" and rent:
            out[name] = (int(round(rent[0] * 100)), int(round(rent[1] * 100)))
            continue
        factor = 1.0 if name == "rent" else scale * SPENDABLE
        out[name] = (int(round(mid * lo_share * factor * 100)), int(round(mid * hi_share * factor * 100)))
    return out


def envelope_line(env: Mapping[str, tuple[int, int]]) -> str:
    """把信封写成提示词里的一句话（美元两位小数，模型读得懂）。"""
    if not env:
        return ""
    parts = []
    for name, (lo, hi) in env.items():
        parts.append(f"{name} ${lo / 100:,.0f}–${hi / 100:,.0f}")
    return (
        "Monthly budget envelope (hard, USD; each category's monthly total must stay inside): "
        + "; ".join(parts)
        + "。房租一个月一笔；电费/燃气各一笔；工资按固定周期入账。"
    )


def _realized(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """把流水按类目汇总（负数取绝对值），返回分。"""
    from . import postpass

    totals: dict[str, int] = {}
    for row in rows:
        desc = str(row.get("description") or row.get("desc") or row.get("detail") or "")
        category = postpass._category_of(desc) or "other"
        entry = row.get("merchant") if isinstance(row.get("merchant"), Mapping) else {}
        if category == "other":
            category = str(entry.get("category") or "other")
        cents = row.get("amount_cents")
        if isinstance(cents, int) and not isinstance(cents, bool):
            cents = abs(cents)
        else:
            amount = postpass._row_amount(row)
            if amount is None:
                continue
            cents = int(round(abs(amount) * 100))
        totals[category] = totals.get(category, 0) + cents
    return totals


def envelope_report(
    rows: Iterable[Mapping[str, Any]],
    env: Mapping[str, tuple[int, int]],
    *,
    tolerance: float = 0.35,
) -> list[str]:
    """对照信封检查实际分布，返回人能读的偏差描述（空列表表示都在区间附近）。"""
    totals = _realized(rows)
    problems: list[str] = []
    for name, (lo, hi) in env.items():
        actual = totals.get(name)
        if not actual:
            if name in {"rent", "utilities"}:
                problems.append(f"{name} 一笔都没有（信封 ${lo / 100:,.0f}–${hi / 100:,.0f}）")
            continue
        floor = lo * (1 - tolerance)
        ceil = hi * (1 + tolerance)
        if actual < floor:
            problems.append(
                f"{name} 合计 ${actual / 100:,.2f} 远低于信封 ${lo / 100:,.0f}–${hi / 100:,.0f}"
            )
        elif actual > ceil:
            problems.append(
                f"{name} 合计 ${actual / 100:,.2f} 远超信封 ${lo / 100:,.0f}–${hi / 100:,.0f}"
            )
    return problems
