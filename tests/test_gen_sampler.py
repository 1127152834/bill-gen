"""分布采样器：笔数、间隔、金额、星期、周期都要有稳定形状。"""
from __future__ import annotations

import statistics
from collections import Counter
from datetime import date

import pytest

from billgen.gen import sampler


PROFILE = {"monthly_income_hint": "9000-14000", "rent_hint": "2600-3200"}


def _plan(state: str = "CA") -> sampler.Plan:
    plan = sampler.plan_from_profile(PROFILE, state=state)
    plan.income_payer = "BLUESTEM PARTNERS"
    plan.category_weights = {
        "grocery": 1.0,
        "dining": 1.0,
        "coffee": 1.0,
        "fuel": 1.0,
        "pharmacy": 0.6,
        "shopping": 1.0,
    }
    return plan


def _months(count: int = 12) -> list[str]:
    return [f"2026-{month:02d}" for month in range(1, count + 1)]


def test_each_bill_family_lands_once_per_month():
    rows = sampler.sample_rows(_plan(), months=_months(6), state="CA", seed="dup")
    for month in _months(6):
        rent = [row for row in rows if row["category"] == "rent" and row["date"].strftime("%Y-%m") == month]
        assert len(rent) == 1, [row["base_description"] for row in rent]
        electric = [
            row
            for row in rows
            if row["base_description"] == "SOUTHERN CALIFORNIA EDISON" and row["date"].strftime("%Y-%m") == month
        ]
        assert len(electric) == 1


def test_rent_is_anchored_to_the_first_days_of_the_month():
    rows = sampler.sample_rows(_plan(), months=_months(6), state="CA", seed="rent")
    for row in rows:
        if row["category"] == "rent":
            assert row["date"].day <= 5, row["date"]


def test_biweekly_income_keeps_a_fourteen_day_rhythm():
    rows = sampler.sample_rows(_plan(), months=_months(6), state="CA", seed="pay")
    deposits = [row["date"] for row in rows if row["type"] == "DEPOSIT"]
    assert len(deposits) >= 12
    gaps = {(b - a).days for a, b in zip(deposits, deposits[1:])}
    assert gaps == {14}, gaps
    assert all(day.weekday() == sampler.PAYROLL_WEEKDAY for day in deposits)


def test_category_amount_medians_stay_near_the_model():
    rows = sampler.sample_rows(_plan(), months=_months(12), state="CA", seed="amt", tax_rate=0.0)
    by_category: dict[str, list[int]] = {}
    for row in rows:
        by_category.setdefault(str(row["category"]), []).append(int(row["amount_cents"]))
    for category in ("grocery", "dining", "coffee", "fuel"):
        model = sampler.CATEGORIES[category]
        median = statistics.median(by_category[category])
        assert model.median * 0.55 <= median <= model.median * 1.8, (category, median, model.median)


def test_dining_prefers_the_weekend():
    rows = sampler.sample_rows(_plan(), months=_months(12), state="CA", seed="wknd")
    dining = [row["date"] for row in rows if row["category"] == "dining"]
    weekend = sum(1 for day in dining if day.weekday() >= 5)
    # 周末权重要看得出来：12 个月里周末占比明显高于 2/7≈0.29 的均匀分布即可。
    assert weekend / len(dining) > 0.40, weekend / len(dining)


def test_inter_arrival_gaps_are_poisson_like():
    rows = sampler.sample_rows(_plan(), months=_months(12), state="CA", seed="gap")
    grocery = sorted(row["date"] for row in rows if row["category"] == "grocery")
    gaps = [(b - a).days for a, b in zip(grocery, grocery[1:])]
    assert 0.6 <= statistics.pstdev(gaps) / statistics.mean(gaps) <= 1.6


def test_discretionary_amounts_never_repeat_the_same_cent_pair():
    """消费类不该出现「同一家店、同一笔到分」的复读；房租/订阅本来月月一样，不算。"""
    rows = sampler.sample_rows(_plan(), months=_months(3), state="CA", seed="rep")
    fixed = {"rent", "subscription", "electric", "gas", "water", "internet", "phone", "insurance"}
    pairs = Counter(
        (row["base_description"], row["amount_cents"])
        for row in rows
        if row["type"] == "POS" and row["category"] not in fixed
    )
    assert max(pairs.values()) <= 2, pairs.most_common(3)


def test_opening_balance_keeps_the_account_positive():
    rows = sampler.sample_rows(_plan(), months=_months(6), state="CA", seed="open")
    opening = sampler.opening_for(rows)
    balance = opening
    for row in sorted(rows, key=lambda item: (item["date"], item["posted"])):
        balance += int(row["amount_cents"]) * (1 if row["direction"] == "credit" else -1)
        assert balance > 0, (row["date"], balance)
    assert opening >= 25000


def test_seeded_sampling_is_reproducible():
    a = sampler.sample_rows(_plan(), months=_months(2), state="CA", seed="same")
    b = sampler.sample_rows(_plan(), months=_months(2), state="CA", seed="same")
    assert [row["amount_cents"] for row in a] == [row["amount_cents"] for row in b]
    assert [row["date"] for row in a] == [row["date"] for row in b]


def test_unknown_state_falls_back_to_generic_brands():
    rows = sampler.sample_rows(_plan(state="ZZ"), months=["2026-09"], state="ZZ", seed="zz")
    assert rows
    assert any(row["type"] == "DEPOSIT" for row in rows)


def test_parse_hint_accepts_three_shapes():
    assert sampler._parse_hint("9000-14000") == (900_000, 1_400_000)
    assert sampler._parse_hint("$2,600-$3,200") == (260_000, 320_000)
    assert sampler._parse_hint(2600) == (260_000, 260_000)
    assert sampler._parse_hint("") is None


def test_brand_carries_no_store_number_of_its_own():
    """门店号只由描述层补一次：采样器自己再补就会印成两串数字。"""
    rows = sampler.sample_rows(_plan(), months=_months(2), state="CA", seed="store")
    pos = [row for row in rows if row["type"] == "POS" and row["category"] != "subscription"]
    assert pos
    for row in pos:
        assert not row["base_description"].split()[-1].isdigit(), row["base_description"]


def test_subscription_rows_are_online_not_local():
    """订阅是线上扣款，不能带持卡人城市。"""
    rows = sampler.sample_rows(_plan(), months=_months(1), state="CA", seed="subs")
    subs = [row for row in rows if row["category"] == "subscription"]
    assert subs
    for row in subs:
        assert row["location"] == "online"
        assert not row["city"]


def test_missing_budget_hints_fall_back_to_state_medians():
    """档案没填收支档时按州的中位收入兜底，房租落在收入的 28–33%。"""
    plan = sampler.plan_from_profile({"name": "NO HINTS"}, state="CA")
    monthly = plan.income_median * (26 / 12 if plan.income_cadence == "biweekly" else 1.0)
    assert 0.8 * sampler.STATE_MONTHLY_INCOME["CA"] <= monthly <= 1.2 * sampler.STATE_MONTHLY_INCOME["CA"]
    assert 0.25 * monthly <= plan.rent_median <= 0.36 * monthly


def test_total_rows_stay_inside_the_statement_row_range():
    """整月行数（工资 + 固定账单 + 订阅 + 消费）必须落在版式行数区间里。"""
    plan = sampler.plan_from_profile({"name": "HENG LI"}, state="CA")
    for seed in ("rows-1", "rows-2", "rows-3"):
        rows = sampler.sample_rows(
            plan, months=["2026-09"], state="CA", seed=seed, row_target=(16, 26)
        )
        assert 16 <= len(rows) <= 26, (seed, len(rows))


def test_no_single_category_dominates_a_month():
    """修剪/补齐不许把某类堆成一个月 10 笔（旧实现的 bug：杂货 10 笔、咖啡 0 笔）。"""
    plan = sampler.plan_from_profile({"name": "HENG LI"}, state="CA")
    rows = sampler.sample_rows(
        plan, months=[f"2026-{month:02d}" for month in range(1, 13)], state="CA", seed="mix"
    )
    counts: dict[str, int] = {}
    for row in rows:
        if row["type"] == "POS" and row["category"] != "subscription":
            counts[row["category"]] = counts.get(row["category"], 0) + 1
    total = sum(counts.values())
    assert counts.get("grocery", 0) / total < 0.45, counts
    assert len([name for name, value in counts.items() if value >= 3]) >= 3, counts
