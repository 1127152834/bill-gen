"""按分布采样账单流水（而不是让模型逐行编）。

为什么要有这一层
----------------
拟真度本质上是分布问题：一个月的流水里，餐饮占几笔、两笔之间隔几天、单笔金额多重、
周末是不是更密、房租是不是每月 1 号、工资是不是每两周一次——这些都有稳定形状。
LLM 对这些形状没有稳定记忆：它会给出同月两笔电费、275 美元的房租、3 美元的加油、
周日的 Chick-fil-A，然后把账算不平。

所以把职责分开：

* **模型**只回答「这个人的生活长什么样」——收入来源与档位、房租量级、爱去哪几家店、
  订阅了哪些服务（见 ``plan`` 字典）。
* **本模块**把这份计划铺成逐笔事件：笔数走泊松、间隔与星期偏好按品类权重、
  金额走对数正态、固定账单按锚定日、工资按 14 天周期；期初余额按「不透支」兜底。

输出的行字典与 ``pipeline._make_row`` 同形，因此下游 descriptors / 校验 / 渲染不用改。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

# ---- 品类分布参数 ---------------------------------------------------------
# per_month = 每月平均笔数（泊松 λ）；median = 单笔金额中位数（美分）；
# sigma = 对数正态的离散度；weekend = 周末相对工作日的权重倍数。
# 这些数字来自常见个人活期账户的消费形状：餐饮/咖啡笔数多、金额小，
# 房租/保险笔数为 1、金额大且几乎不抖。


@dataclass(frozen=True)
class CategoryModel:
    per_month: float
    median: int
    sigma: float
    weekend: float = 1.0
    pricing: str = "basket"  # basket | fuel | fixed_menu
    weekday_bias: tuple[float, ...] = (1.0,) * 7
    clamp: tuple[int, int] = (0, 0)
    taxable: bool = False
    tip: bool = False

    def amount(self, rng: random.Random) -> int:
        """对数正态取一笔金额，落在 [clamp] 内。"""
        value = self.median * math.exp(rng.gauss(0.0, self.sigma))
        low, high = self.clamp
        if low and value < low:
            value = low + (low - value) * rng.random() * 0.5
        if high and value > high:
            value = high - (value - high) * rng.random() * 0.5
        cents = int(round(value))
        return max(1, cents)


CATEGORIES: dict[str, CategoryModel] = {
    "grocery": CategoryModel(6.4, 8600, 0.45, weekend=2.0, clamp=(1800, 26000), taxable=False),
    "dining": CategoryModel(5.0, 2800, 0.45, weekend=2.6, clamp=(900, 12000), taxable=True, tip=True),
    "coffee": CategoryModel(4.0, 760, 0.30, weekend=1.5, clamp=(350, 1800), taxable=True),
    "fuel": CategoryModel(3.5, 4200, 0.35, weekend=1.3, pricing="fuel", clamp=(1800, 9500)),
    "pharmacy": CategoryModel(1.2, 2400, 0.60, weekend=0.8, clamp=(500, 12000)),
    "shopping": CategoryModel(2.4, 5400, 0.70, weekend=1.6, clamp=(1200, 32000)),
    "rideshare": CategoryModel(3.0, 1650, 0.50, weekend=2.0, clamp=(600, 6500)),
    "convenience": CategoryModel(2.0, 890, 0.40, weekend=1.3, clamp=(200, 3500)),
    "fitness": CategoryModel(1.0, 4500, 0.05, clamp=(1500, 12000)),
    "subscription": CategoryModel(0.0, 1599, 0.02, clamp=(499, 2600)),
}

#: 固定账单的锚定日（真账单基本固定在月初到月中这几天）。
BILL_DAYS: dict[str, int] = {
    "rent": 1,
    "electric": 14,
    "gas": 16,
    "water": 9,
    "internet": 7,
    "phone": 12,
    "insurance": 5,
    "loan": 6,
    "storage": 11,
}

#: 州 → 公用事业品牌（与 postpass 的表一致，这里只用于采样阶段选品牌）。
STATE_UTILITIES: dict[str, dict[str, str]] = {
    "CA": {"electric": "SOUTHERN CALIFORNIA EDISON", "gas": "SO CAL GAS", "water": "SUBURBAN WATER"},
    "TX": {"electric": "AUSTIN ENERGY", "gas": "TEXAS GAS SERVICE", "water": "AUSTIN WATER"},
    "NY": {"electric": "CON EDISON", "gas": "NATIONAL GRID", "water": "NYC WATER BOARD"},
    "WA": {"electric": "SEATTLE CITY LIGHT", "gas": "PUGET SOUND ENERGY", "water": "SEATTLE PUBLIC UTIL"},
    "IL": {"electric": "COMED", "gas": "PEOPLES GAS", "water": "CHICAGO WATER"},
}

#: 州 → 常见零售品牌（用于把品类落成具体门店名）。
STATE_BRANDS: dict[str, dict[str, tuple[str, ...]]] = {
    "CA": {
        "grocery": ("TRADER JOE'S", "VONS", "FOOD 4 LESS", "RALPHS", "SPROUTS FARMERS MKT", "WHOLEFDS MKT"),
        "coffee": ("STARBUCKS", "PEET'S COFFEE", "BLUE BOTTLE"),
        "fuel": ("SHELL", "CHEVRON", "ARCO"),
        "pharmacy": ("CVS PHARMACY", "WALGREENS"),
        "dining": ("CHIPOTLE", "IN-N-OUT", "PANERA BREAD", "SWEETGREEN"),
        "shopping": ("TARGET", "MARSHALLS", "BEST BUY"),
        "convenience": ("7-ELEVEN", "CIRCLE K", "CHEVRON EXTRA MILE", "WALGREENS"),
        "rideshare": ("UBER", "LYFT"),
        "fitness": ("EQUINOX", "PLANET FITNESS"),
    },
    "TX": {
        "grocery": ("H-E-B", "TRADER JOE'S", "WHOLE FOODS"),
        "coffee": ("STARBUCKS", "SUMMER MOON", "MEDICI"),
        "fuel": ("BUC-EE'S", "SHELL", "EXXON"),
        "pharmacy": ("CVS PHARMACY", "WALGREENS"),
        "dining": ("WHATABURGER", "TORCHY'S TACOS", "CHIPOTLE", "PANERA BREAD"),
        "shopping": ("TARGET", "BEST BUY", "OLD NAVY"),
        "convenience": ("7-ELEVEN", "QUIKTRIP"),
        "rideshare": ("UBER", "LYFT"),
        "fitness": ("PLANET FITNESS", "24 HOUR FITNESS"),
    },
}

DEFAULT_BRANDS: dict[str, tuple[str, ...]] = {
    "grocery": ("KROGER", "TRADER JOE'S", "WHOLE FOODS"),
    "coffee": ("STARBUCKS", "DUNKIN"),
    "fuel": ("SHELL", "EXXON", "BP"),
    "pharmacy": ("CVS PHARMACY", "WALGREENS"),
    "dining": ("CHIPOTLE", "PANERA BREAD", "MCDONALD'S"),
    "shopping": ("TARGET", "AMAZON.COM", "BEST BUY"),
    "convenience": ("7-ELEVEN", "CIRCLE K"),
    "rideshare": ("UBER", "LYFT"),
    "fitness": ("PLANET FITNESS", "YMCA"),
}

STORE_NUMBER_RANGE: dict[str, tuple[int, int]] = {
    "grocery": (1200, 9800),
    "coffee": (2000, 9900),
    "fuel": (2200, 8900),
    "pharmacy": (3000, 9900),
    "dining": (700, 9900),
    "shopping": (100, 2900),
    "convenience": (200, 9900),
}

#: 工资/固定收入的发薪星期（周五最常见）。
PAYROLL_WEEKDAY = 4


DEFAULT_CATEGORY_WEIGHTS: dict[str, float] = {
    "grocery": 1.0,
    "dining": 1.0,
    "coffee": 0.9,
    "fuel": 1.0,
    "pharmacy": 0.5,
    "shopping": 0.9,
    "rideshare": 0.5,
    "convenience": 0.6,
    "fitness": 0.3,
}


@dataclass
class Plan:
    """模型该回答的那部分（其余由采样器决定）。"""

    income_channel: str = "ach_client"
    income_payer: str = ""
    income_median: int = 420_000
    income_sigma: float = 0.25
    income_cadence: str = "biweekly"  # biweekly | monthly | irregular
    income_per_month: float = 4.0
    rent_median: int = 240_000
    bills: list[dict[str, Any]] = field(default_factory=list)
    subscriptions: list[dict[str, Any]] = field(default_factory=list)
    category_weights: dict[str, float] = field(default_factory=dict)
    brands: dict[str, list[str]] = field(default_factory=dict)
    opening_cents: int = 0


STATE_MONTHLY_INCOME: dict[str, int] = {
    "CA": 780_000,
    "NY": 820_000,
    "WA": 760_000,
    "MA": 790_000,
    "TX": 660_000,
    "FL": 640_000,
    "IL": 670_000,
    "GA": 620_000,
}
DEFAULT_MONTHLY_INCOME = 640_000


def plan_from_profile(
    profile: Mapping[str, Any],
    *,
    state: str = "",
    months: Sequence[str] = (),
) -> Plan:
    """没有模型可用时的确定性计划：全部来自档案里的收支档。"""
    plan = Plan()
    income = _parse_hint(profile.get("monthly_income_hint"))
    rent = _parse_hint(profile.get("rent_hint"))
    if not income:
        # 档案没填收支档：按州的中位收入兜底，而不是写死一个数字。
        fallback = STATE_MONTHLY_INCOME.get(state.upper(), DEFAULT_MONTHLY_INCOME)
        income = (int(fallback * 0.85), int(fallback * 1.15))
    if not rent:
        # 房租按收入的 28–33% 走，这也是真实家庭最常见的负担区间。
        monthly_mid = (income[0] + income[1]) / 2.0
        rent = (int(monthly_mid * 0.28), int(monthly_mid * 0.33))
    if income:
        monthly = (income[0] + income[1]) / 2.0
        plan.income_sigma = 0.12
        plan.income_cadence = "biweekly" if monthly >= 600_000 else "monthly"
        # 档案里的档位是「每月入账合计」，发薪频率决定单笔金额：
        # 双周薪一年 26 笔 → 单笔 ≈ 月收入 × 12 / 26。
        if plan.income_cadence == "biweekly":
            plan.income_median = int(round(monthly * 12 / 26))
            plan.income_per_month = 26 / 12
        else:
            plan.income_median = int(round(monthly))
            plan.income_per_month = 1.0
    if rent:
        plan.rent_median = rent[0]
    brands = STATE_BRANDS.get(state.upper(), {})
    plan.brands = {key: list(value) for key, value in brands.items()}
    utilities = STATE_UTILITIES.get(state.upper(), {})
    plan.bills = [
        {"family": "rent", "brand": "AVALON MGMT", "amount": plan.rent_median},
        {"family": "electric", "brand": utilities.get("electric", "")},
        {"family": "gas", "brand": utilities.get("gas", "")},
        {"family": "internet", "brand": "XFINITY"},
        {"family": "phone", "brand": "VERIZON WIRELESS"},
        {"family": "insurance", "brand": "USAA INS"},
    ]
    plan.subscriptions = [
        {"brand": "NETFLIX.COM", "amount": 1999, "day": 9},
        {"brand": "SPOTIFY USA", "amount": 1199, "day": 15},
    ]
    if not plan.category_weights:
        plan.category_weights = dict(DEFAULT_CATEGORY_WEIGHTS)
    return plan


def _parse_hint(value: Any) -> tuple[int, int] | None:
    """`"9000-14000"` / `"$2,600-$3,200"` / `2600` → (low_cents, high_cents)。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        cents = int(round(float(value) * 100))
        return (cents, cents)
    text = str(value).replace(",", "").replace("$", "").strip()
    parts = [part for part in text.replace("–", "-").split("-") if part.strip()]
    numbers = []
    for part in parts:
        try:
            numbers.append(int(round(float(part) * 100)))
        except ValueError:
            continue
    if not numbers:
        return None
    return (min(numbers), max(numbers))


# ---- 采样 ----------------------------------------------------------------

def sample_rows(
    plan: Plan,
    *,
    months: Sequence[str],
    state: str = "",
    seed: str = "",
    tax_rate: float = 0.0,
    row_target: tuple[int, int] = (16, 28),
) -> list[dict[str, Any]]:
    """把计划铺成逐笔事件；同族账单每月只落一笔，固定账单锚定日。"""
    rng = random.Random(f"sampler:{seed}:{','.join(months)}")
    rows: list[dict[str, Any]] = []
    for month in months:
        year, mon = _parse_month(month)
        rows.extend(_sample_month(plan, year, mon, state=state, rng=rng, tax_rate=tax_rate, target=row_target))
    rows.sort(key=lambda row: (row["date"], row["posted"], row["amount_cents"]))
    return rows


def _sample_month(
    plan: Plan,
    year: int,
    mon: int,
    *,
    state: str,
    rng: random.Random,
    tax_rate: float,
    target: tuple[int, int],
) -> list[dict[str, Any]]:
    last = _last_day(year, mon)
    start, end = date(year, mon, 1), date(year, mon, last)
    rows: list[dict[str, Any]] = []

    rows.extend(_sample_income(plan, year, mon, rng=rng))
    rows.extend(_sample_bills(plan, year, mon, state=state, rng=rng))
    subs = _sample_subscriptions(plan, year, mon, rng=rng)
    rows.extend(subs)

    weights = dict(plan.category_weights or {})
    seasonal = 1.0 + 0.08 * math.sin((mon - 1) / 12 * 2 * math.pi)
    # 品类占比要贴着模型，但一个月总笔数受版式行数区间约束：先按比例把 λ 缩放到
    # 区间中点，再各自抽泊松，才不会「先抽 23 笔再砍到 14 笔」（砍完占比就变形）。
    expected_total = sum(
        model.per_month * float(weights.get(category, 0.0)) * seasonal
        for category, model in CATEGORIES.items()
        if category != "subscription" and float(weights.get(category, 0.0)) > 0
    )
    month_mid = max(1.0, (target[0] + target[1]) / 2.0)
    scale = min(1.0, month_mid / expected_total) if expected_total > 0 else 1.0
    for category, weight in weights.items():
        model = CATEGORIES.get(category)
        if model is None or weight <= 0:
            continue
        lam = model.per_month * weight * seasonal * scale
        count = max(0, int(round(rng.gauss(lam, math.sqrt(max(lam, 0.2))))))
        for _ in range(count):
            when = _pick_day(year, mon, model, rng, income_days=[r["date"].day for r in rows if r["type"] == "DEPOSIT"])
            amount = model.amount(rng)
            if model.taxable:
                amount = int(round(amount * (1 + tax_rate)))
            if model.tip:
                amount += int(round(amount * rng.uniform(0.15, 0.22)))
            brand = _pick_brand(plan, category, state, rng)
            rows.append(_row(when, "POS", "debit", amount, brand, category, rng, start, end, state=state))

    # 笔数兜底：太少就补最容易补的消费品类，太多就删最不重要的。
    rows = _balance_count(rows, plan, state=state, rng=rng, year=year, mon=mon, target=target, start=start, end=end, tax_rate=tax_rate)
    return [row for row in rows if start <= row["date"] <= end]


def _sample_income(plan: Plan, year: int, mon: int, *, rng: random.Random) -> list[dict[str, Any]]:
    last = _last_day(year, mon)
    rows: list[dict[str, Any]] = []
    if plan.income_cadence == "biweekly":
        # 每 14 天一笔，锚在同一个周五：真发薪日不会「这个月少一笔、下个月多一笔」。
        anchor = date(2020, 1, 3)  # 一个周五
        start = date(year, mon, 1)
        offset = (start - anchor).days % 14
        first = start if offset == 0 else start + timedelta(days=14 - offset)
        when = first
        while when.month == mon and when.year == year:
            rows.append(_income_row(when, plan, rng))
            when += timedelta(days=14)
    elif plan.income_cadence == "monthly":
        when = date(year, mon, min(last, 5))
        rows.append(_income_row(when, plan, rng))
    else:
        count = max(1, int(round(rng.gauss(plan.income_per_month, 1.0))))
        for _ in range(count):
            day = rng.randint(1, last)
            rows.append(_income_row(date(year, mon, day), plan, rng))
    return rows


def _income_row(when: date, plan: Plan, rng: random.Random) -> dict[str, Any]:
    amount = int(round(plan.income_median * math.exp(rng.gauss(0, plan.income_sigma))))
    payer = plan.income_payer or "CLIENT PAYMENT"
    return {
        "date": when,
        "posted": when,
        "type": "DEPOSIT",
        "direction": "credit",
        "amount_cents": max(1, amount),
        "merchant_key": "income",
        "fact_id": "",
        "category": "income",
        "city": "",
        "state": "",
        "base_description": payer,
        "memo": "Client payment",
        "location": "local",
        "source": "income",
    }


def _sample_bills(plan: Plan, year: int, mon: int, *, state: str, rng: random.Random) -> list[dict[str, Any]]:
    last = _last_day(year, mon)
    start, end = date(year, mon, 1), date(year, mon, last)
    utilities = STATE_UTILITIES.get(state.upper(), {})
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for bill in plan.bills:
        family = str(bill.get("family") or "")
        if not family or family in seen:
            continue  # 同族只落一笔，这正是模型最容易违反的约束
        seen.add(family)
        day = int(bill.get("day") or BILL_DAYS.get(family, 8))
        when = date(year, mon, max(1, min(last, day)))
        brand = str(bill.get("brand") or utilities.get(family) or family.upper())
        amount = int(bill.get("amount") or 0)
        if not amount:
            model = CATEGORIES.get("subscription" if family == "subscription" else family)
            base = _BILL_MEDIANS.get(family, 9500)
            amount = int(round(base * math.exp(rng.gauss(0, 0.18))))
            if model:
                amount = model.amount(rng)
        rows.append(_row(when, "ACH", "debit", amount, brand, family, rng, start, end, state=state))
    return rows


def _sample_subscriptions(plan: Plan, year: int, mon: int, *, rng: random.Random) -> list[dict[str, Any]]:
    last = _last_day(year, mon)
    start, end = date(year, mon, 1), date(year, mon, last)
    rows = []
    for sub in plan.subscriptions:
        day = int(sub.get("day") or 9)
        when = date(year, mon, max(1, min(last, day)))
        brand = str(sub.get("brand") or "SUBSCRIPTION")
        amount = int(sub.get("amount") or 1599)
        row = _row(when, "POS", "debit", amount, brand, "subscription", rng, start, end, state="", city="")
        row["location"] = "online"
        rows.append(row)
    return rows


_BILL_MEDIANS = {
    "electric": 11_800,
    "gas": 4_600,
    "water": 6_200,
    "internet": 8_000,
    "phone": 7_500,
    "insurance": 13_500,
    "loan": 42_000,
    "storage": 9_000,
}


def _pick_day(
    year: int,
    mon: int,
    model: CategoryModel,
    rng: random.Random,
    *,
    income_days: Sequence[int] = (),
) -> date:
    """按「星期偏好 + 发薪日邻近」抽一天，而不是均匀乱撒。"""
    last = _last_day(year, mon)
    days = list(range(1, last + 1))
    weights = []
    for day in days:
        when = date(year, mon, day)
        weight = model.weekday_bias[when.weekday()] * (model.weekend if when.weekday() >= 5 else 1.0)
        if income_days:
            gap = min(abs(day - pay) for pay in income_days)
            weight *= 1.6 if gap <= 2 else 1.0
        weights.append(weight)
    return date(year, mon, rng.choices(days, weights=weights, k=1)[0])


def _pick_brand(plan: Plan, category: str, state: str, rng: random.Random) -> str:
    pool = plan.brands.get(category) or STATE_BRANDS.get(state.upper(), {}).get(category) or DEFAULT_BRANDS.get(category)
    if not pool:
        return category.upper()
    # 只给品牌名：门店号由描述层按 merchant_key 确定性补一次，
    # 采样器自己再补就会印成 `VONS 5624 3335` 两串数字。
    return rng.choice(list(pool))


def _row(
    when: date,
    txn_type: str,
    direction: str,
    amount: int,
    brand: str,
    category: str,
    rng: random.Random,
    start: date,
    end: date,
    *,
    state: str = "",
    city: str = "",
) -> dict[str, Any]:
    if txn_type == "POS":
        posted = min(end, when + timedelta(days=rng.randint(1, 2)))
    else:
        posted = when
    return {
        "date": when,
        "posted": posted,
        "type": txn_type,
        "direction": direction,
        "amount_cents": max(1, int(amount)),
        "merchant_key": f"{category}:{brand}",
        "fact_id": "",
        "category": category,
        "city": city,
        "state": state,
        "base_description": brand,
        "memo": "",
        "location": "local",
        "source": category,
    }


def _balance_count(
    rows: list[dict[str, Any]],
    plan: Plan,
    *,
    state: str,
    rng: random.Random,
    year: int,
    mon: int,
    target: tuple[int, int],
    start: date,
    end: date,
    tax_rate: float,
) -> list[dict[str, Any]]:
    """把笔数收进 [target] 区间：多了删最不重要的消费，少了补日用品。"""
    low, high = target
    # 目标口径是「消费笔数」（POS），不是含工资/固定账单的总行数。
    # 修剪/补齐都按「相对期望笔数的盈余/缺口」来挑类目，这样一份账单的品类占比
    # 始终贴着模型走；随机挑会把咖啡/便利店系统性清零、杂货堆到 10 笔。
    spend = [row for row in rows if row["type"] == "POS" and row["category"] != "subscription"]

    def expected_count(category: str) -> float:
        model = CATEGORIES.get(category)
        if model is None:
            return 0.0
        weight = float((plan.category_weights or {}).get(category, 0.0))
        return max(0.0, model.per_month * weight * (1.0 + 0.08 * math.sin((mon - 1) / 12 * 2 * math.pi)))

    actual: dict[str, int] = {}
    for row in spend:
        actual[str(row["category"])] = actual.get(str(row["category"]), 0) + 1

    def pick(by_deficit: bool) -> str:
        pool = [name for name in actual] if by_deficit else [name for name in actual if actual[name] > 0]
        if not pool:
            pool = ["convenience"]
        scored = []
        for name in pool:
            gap = expected_count(name) - actual.get(name, 0)
            scored.append((gap if by_deficit else -gap, name))
        scored.sort(key=lambda item: (-(item[0] + rng.random() * 0.9), item[1]))
        return scored[0][1]

    while len(spend) > high:
        category = pick(by_deficit=False)
        victim = next(row for row in spend if str(row["category"]) == category)
        rows.remove(victim)
        spend.remove(victim)
        actual[category] = actual.get(category, 0) - 1

    while len(spend) < low:
        category = pick(by_deficit=True)
        if expected_count(category) <= 0:
            category = "convenience"
        model = CATEGORIES[category]
        when = _pick_day(year, mon, model, rng)
        amount = model.amount(rng)
        if model.taxable:
            amount = int(round(amount * (1 + tax_rate)))
        if model.tip:
            amount += int(round(amount * rng.uniform(0.15, 0.22)))
        row = _row(
            when,
            "POS",
            "debit",
            amount,
            _pick_brand(plan, category, state, rng),
            category,
            rng,
            start,
            end,
            state=state,
        )
        rows.append(row)
        spend.append(row)
        actual[category] = actual.get(category, 0) + 1
    return rows


def opening_for(rows: Sequence[Mapping[str, Any]], *, floor_cents: int = 250_00) -> int:
    """给出一个期初余额，保证按账面顺序走下来任何时候都不透支。"""
    balance = 0
    lowest = 0
    for row in sorted(rows, key=lambda item: (item["date"], item["posted"])):
        signed = int(row["amount_cents"]) * (1 if row.get("direction") == "credit" else -1)
        balance += signed
        lowest = min(lowest, balance)
    return max(floor_cents, -lowest + floor_cents)


def _parse_month(value: str) -> tuple[int, int]:
    year, month = value.split("-")
    return int(year), int(month)


def _last_day(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - timedelta(days=1)).day
