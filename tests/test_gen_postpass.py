"""交付前收尾（postpass）的离线测试：不改账、只改一眼假的地理与档位。"""

from __future__ import annotations

import json

import pytest

from billgen.gen import postpass


def _mercury_like(rows):
    return {
        "beginning_balance": 1000.00,
        "transactions": rows,
    }


def test_utilities_follow_the_profile_city():
    """Temple City（LA 县卫星城）的电是 SCE，不是 LADWP/Con Edison。"""
    data = _mercury_like([
        {"date": "Aug 2", "desc": "LADWP", "amount": -120.0},
        {"date": "Aug 3", "desc": "CON EDISON", "amount": -80.0},
        {"date": "Aug 4", "desc": "SO CAL GAS", "amount": -40.0},
    ])
    changed = postpass.fix_utilities(data, state="CA", city="Temple City")
    assert changed == 2
    descs = [row["desc"] for row in data["transactions"]]
    assert descs[0] == "SOUTHERN CALIFORNIA EDISON"
    assert descs[1] == "SOUTHERN CALIFORNIA EDISON"
    assert descs[2] == "SO CAL GAS"  # 本来就是对的，不动


def test_utilities_for_austin_use_local_vendors():
    data = _mercury_like([{"date": "Sep 2", "desc": "CON EDISON", "amount": -95.0}])
    postpass.fix_utilities(data, state="TX", city="Austin")
    assert data["transactions"][0]["desc"] == "AUSTIN ENERGY"


def test_duplicate_utilities_keep_only_the_biggest_bill():
    """一个月只会有一张电费单：两笔就只留金额最大的那张。"""
    data = _mercury_like([
        {"date": "Sep 2", "desc": "SOUTHERN CALIFORNIA EDISON", "amount": -60.0},
        {"date": "Sep 9", "desc": "SOUTHERN CALIFORNIA EDISON", "amount": -180.0},
    ])
    dropped = postpass.dedupe_utilities(data, state="CA", city="Temple City")
    assert dropped == 1
    assert [row["amount"] for row in data["transactions"]] == [-180.0]


def test_online_merchants_drop_the_cardholder_city():
    """`NETFLIX.COM AUSTIN TX` 不是真账单的写法。"""
    data = _mercury_like([
        {"date": "Sep 3", "desc": "NETFLIX.COM AUSTIN TX", "amount": -19.0},
        {"date": "Sep 4", "desc": "SPOTIFY USA AUSTIN, TX", "amount": -11.99},
        {"date": "Sep 5", "desc": "TRADER JOE'S 123 AUSTIN TX", "amount": -52.0},
    ])
    assert postpass.fix_online_places(data) == 2
    descs = [row["desc"] for row in data["transactions"]]
    assert descs[0] == "NETFLIX.COM"
    assert descs[1] == "SPOTIFY USA"
    assert descs[2] == "TRADER JOE'S 123 AUSTIN TX"  # 实体店保留城市


def test_out_of_region_chain_is_swapped_for_a_local_one():
    data = _mercury_like([{"date": "Sep 6", "desc": "WHATABURGER 9053 TEMPLE CITY CA", "amount": -12.4}])
    assert postpass.fix_chains(data, state="CA") == 1
    swapped = data["transactions"][0]["desc"]
    assert "WHATABURGER" not in swapped
    assert "CA" in swapped


def test_absurd_amounts_are_pulled_into_category_range():
    """加油 $4.56 是「刷卡买水」的量级，不是加油。"""
    data = _mercury_like([{"date": "Sep 7", "desc": "SHELL OIL 4999 AUSTIN TX", "amount": -4.56}])
    changed = postpass.fix_amounts(data)
    assert changed == 1
    amount = abs(data["transactions"][0]["amount"])
    lo, hi = postpass.CATEGORY_RANGES["fuel"]
    assert lo <= amount <= hi


def test_plausible_amounts_are_left_alone():
    data = _mercury_like([{"date": "Sep 8", "desc": "STARBUCKS 123", "amount": -6.25}])
    assert postpass.fix_amounts(data) == 0


def test_budget_pulls_rent_and_income_into_the_profile_range():
    """档案写月入 9000–14000、房租 2600–3200，账单上就不能是 $275。"""
    data = _mercury_like([
        {"date": "Sep 1", "desc": "AVALON MGMT", "amount": -60.0},
        {"date": "Sep 1", "desc": "AVALON MGMT", "amount": -275.0},
        {"date": "Sep 5", "desc": "RIDGEWAY DESIGN", "amount": 275.0},
        {"date": "Sep 12", "desc": "RIDGEWAY DESIGN", "amount": 275.0},
        {"date": "Sep 19", "desc": "RIDGEWAY DESIGN", "amount": 275.0},
        {"date": "Sep 26", "desc": "RIDGEWAY DESIGN", "amount": 275.0},
    ])
    changed = postpass.fix_budget(data, income_hint="9000-14000", rent_hint="2600-3200")
    assert changed > 0
    rows = data["transactions"]
    rent = [row for row in rows if "MGMT" in row["desc"]]
    assert len(rent) == 1, "房租只该留一笔"
    assert 2600 <= abs(rent[0]["amount"]) <= 3200
    income = sum(row["amount"] for row in rows if row["amount"] > 0)
    assert 9000 <= income <= 14000


def test_parse_range_accepts_the_shapes_users_type():
    assert postpass.parse_range("9000-14000") == (9000.0, 14000.0)
    assert postpass.parse_range("$2,600-$3,200") == (2600.0, 3200.0)
    low, high = postpass.parse_range(12000)
    assert low < 12000 < high
    assert postpass.parse_range("") is None
    assert postpass.parse_range(None) is None


def test_repair_month_reports_each_category():
    data = _mercury_like([
        {"date": "Sep 2", "desc": "LADWP", "amount": -60.0},
        {"date": "Sep 9", "desc": "LADWP", "amount": -10.0},
        {"date": "Sep 5", "desc": "NETFLIX.COM AUSTIN TX", "amount": -19.0},
    ])
    report = postpass.repair_month(data, state="CA", city="Temple City")
    assert report["utilities"] == 2
    assert report["duplicate_utilities"] == 1
    assert report["online_places"] == 1
    assert set(report) == {
        "utilities",
        "duplicate_utilities",
        "online_places",
        "chains",
        "budget",
        "amounts",
    }


def test_repair_month_keeps_the_sum_consistent():
    """改完金额必须让「期初 + 流水 = 期末」仍然成立。"""
    data = {
        "beginning_balance": 500.0,
        "current_balance": 379.0,
        "transactions": [
            {"date": "Sep 2", "desc": "SHELL OIL 4999 AUSTIN TX", "amount": -4.56},
            {"date": "Sep 9", "desc": "STARBUCKS 123 AUSTIN TX", "amount": -6.25},
            {"date": "Sep 20", "desc": "TRADER JOE'S 9854 AUSTIN TX", "amount": -110.19},
        ],
    }
    postpass.repair_month(data, state="TX", city="Austin")
    total = sum(row["amount"] for row in data["transactions"])
    assert round(data["beginning_balance"] + total, 2) == data["current_balance"]


def test_rechain_links_next_month_to_previous_closing() -> None:
    """收尾改过金额后，下个月期初必须等于上个月重算出来的期末。"""
    july = {"beginning_balance": 1000.0, "transactions": [{"date": "Jul 2", "desc": "X", "amount": -100.0}]}
    august = {"beginning_balance": 900.0, "transactions": [{"date": "Aug 3", "desc": "Y", "amount": 250.0}]}
    # 模拟收尾把 7 月那笔从 -100 改成 -50：7 月期末变 950，8 月期初还停在 900。
    july["transactions"][0]["amount"] = -50.0
    assert postpass.month_totals(july) == (1000.0, 950.0)

    assert postpass.rechain([july, august]) == 1
    assert postpass.month_totals(august)[0] == 950.0
    assert postpass.month_totals(august)[1] == 1200.0


def test_rechain_is_a_noop_when_the_chain_already_lines_up() -> None:
    months = [
        {"beginning_balance": 500.0, "transactions": [{"date": "Sep 1", "desc": "A", "amount": -50.0}]},
        {"beginning_balance": 450.0, "transactions": [{"date": "Oct 1", "desc": "B", "amount": 100.0}]},
    ]
    assert postpass.rechain(months) == 0
    assert postpass.month_totals(months[1]) == (450.0, 550.0)
