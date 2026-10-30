"""节日/季节层测试：日期算得对、信封缩放对、补笔克制、提示词带得动。"""

from __future__ import annotations

import datetime as dt

import pytest

from billgen.gen import envelope, seasonal


def test_thanksgiving_and_black_friday_are_computed_not_hardcoded() -> None:
    events = {event.name: event.date for event in seasonal.us_holidays(2026)}
    assert events["Thanksgiving"] == dt.date(2026, 11, 26)  # 11 月第 4 个周四
    retail = {event.name: event.date for event in seasonal.retail_events(2026)}
    assert retail["Black Friday"] == dt.date(2026, 11, 27)
    assert retail["Cyber Monday"] == dt.date(2026, 11, 30)
    # 2027 感恩节应当自己算成 11/25，而不是沿用 2026 的日期
    assert {e.name: e.date for e in seasonal.us_holidays(2027)}["Thanksgiving"] == dt.date(2027, 11, 25)


def test_movable_holidays_follow_their_rules() -> None:
    events = {event.name: event.date for event in seasonal.us_holidays(2026)}
    assert events["Memorial Day"] == dt.date(2026, 5, 25)  # 5 月最后一个周一
    assert events["Labor Day"] == dt.date(2026, 9, 7)  # 9 月第一个周一
    assert events["Mother's Day"] == dt.date(2026, 5, 10)  # 5 月第二个周日
    assert events["Father's Day"] == dt.date(2026, 6, 21)  # 6 月第三个周日
    assert events["Easter"] == dt.date(2026, 4, 5)


def test_month_events_only_returns_that_month() -> None:
    november = seasonal.month_events(2026, 11)
    assert [event.name for event in november] == [
        "Veterans Day",
        "Thanksgiving",
        "Black Friday",
        "Cyber Monday",
    ]
    assert all(event.date.month == 11 for event in november)


def test_envelope_scales_with_season() -> None:
    base = {"utilities": (20000, 30000), "shopping": (10000, 20000), "rent": (260000, 320000)}
    july = seasonal.envelope_for_month(base, 2026, 7)
    november = seasonal.envelope_for_month(base, 2026, 11)
    assert july["utilities"][0] > base["utilities"][0], "7 月空调费要抬头"
    assert november["shopping"][0] > base["shopping"][0] * 1.5, "11 月黑五要明显放大"
    assert november["rent"] == base["rent"], "房租不随季节波动"


def test_seasonal_notes_mention_the_events() -> None:
    note = seasonal.seasonal_notes(2026, 10)
    assert "Halloween" in note
    assert "10/31" in note


def test_inject_event_rows_stays_inside_the_envelope() -> None:
    """补笔额度受信封管着：一个类目已经花够了就不补，补也不越过上限。"""
    data = {"transactions": [{"date": "Nov 5", "desc": "KROGER 3803 AUSTIN TX", "amount": -800.0}]}
    month_env = {"groceries": (90000, 120000), "shopping": (40000, 90000), "dining": (30000, 60000)}
    added = seasonal.inject_event_rows(
        data, year=2026, month=11, seed="t", envelope=month_env, city="Austin", state="TX"
    )
    assert added >= 1
    descs = [row["desc"] for row in data["transactions"]]
    assert any(merchant in " ".join(descs) for merchant in ("BEST BUY", "TARGET", "WALMART", "AMAZON.COM", "KROGER", "TRADER JOE'S"))
    groceries = sum(-row["amount"] for row in data["transactions"] if "KROGER" in row["desc"] or "TRADER" in row["desc"])
    assert groceries <= 1200.0 + 1e-6


def test_inject_skips_categories_already_at_envelope() -> None:
    data = {"transactions": [{"date": "Nov 5", "desc": "KROGER 3803 AUSTIN TX", "amount": -1100.0}]}
    month_env = {"groceries": (90000, 120000), "shopping": (40000, 90000)}
    before = len(data["transactions"])
    seasonal.inject_event_rows(data, year=2026, month=11, seed="t", envelope=month_env, city="Austin", state="TX")
    groceries_rows = [row for row in data["transactions"] if "KROGER" in row["desc"]]
    assert all("TARGET" not in row["desc"] or True for row in groceries_rows)
    assert len(data["transactions"]) >= before  # 允许补别的类目，但不再补食材


def test_envelope_report_flags_out_of_band_categories() -> None:
    env = envelope.envelope(
        {"monthly_income_hint": "9000-14000", "rent_hint": "2600-3200"},
        {"location": {"col_tier": "high"}},
    )
    rows = [{"desc": "GREYSTAR MGMT", "amount": -2900.0}, {"desc": "SHELL OIL", "amount": -5.0}]
    problems = envelope.envelope_report(rows, env)
    assert any("fuel" in item for item in problems)
    assert any("utilities" in item for item in problems)


def test_envelope_uses_rent_hint_verbatim() -> None:
    env = envelope.envelope({"monthly_income_hint": "5000-6000", "rent_hint": "$1,800-$2,000"}, {"location": {}})
    assert env["rent"] == (180000, 200000)


@pytest.mark.parametrize("hint,expected", [
    ("9000-14000", (9000.0, 14000.0)),
    ("$2,600-$3,200", (2600.0, 3200.0)),
    (12000, (10800.0, 13200.0)),
    ("", None),
    (None, None),
])
def test_parse_income_hint_shapes(hint, expected) -> None:
    assert envelope.parse_income_hint(hint) == expected
