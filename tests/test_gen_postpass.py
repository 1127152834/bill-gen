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
        "sorted",
        "recurring",
        "stores",
        "decap",
        "varied",
        "types",
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


def test_top_up_lifts_under_spent_categories_into_the_envelope():
    """加油只有几笔小额时，抬进信封区间，但不越过单笔上限。"""
    data = _mercury_like([
        {"date": "Nov 5", "desc": "ARCO 6261 TEMPLE CITY CA", "amount": -25.08},
        {"date": "Nov 11", "desc": "ARCO 8521 TEMPLE CITY CA", "amount": -34.26},
    ])
    env = {"fuel": (25200, 50400)}
    counts = postpass.top_up_to_envelope(data, env, state="CA", city="Temple City")
    total = sum(-row["amount"] for row in data["transactions"])
    assert counts["scaled"] >= 1
    assert total >= 252.0 * 0.9
    hi = postpass.range_for("fuel")[1]
    assert all(abs(row["amount"]) <= hi for row in data["transactions"])


def test_top_up_leaves_healthy_categories_alone():
    data = _mercury_like([{"date": "Nov 5", "desc": "VONS 1392 TEMPLE CITY CA", "amount": -950.0}])
    before = data["transactions"][0]["amount"]
    counts = postpass.top_up_to_envelope(data, {"groceries": (90800, 136200)}, state="CA", city="Temple City")
    assert counts == {"scaled": 0, "added": 0}
    assert data["transactions"][0]["amount"] == before


def test_top_up_adds_the_missing_utility_when_only_one_bill_exists():
    """真账单电、气各一张：只有电费时把燃气补上。"""
    data = _mercury_like([{"date": "Nov 14", "desc": "SOUTHERN CALIFORNIA EDISON", "amount": -60.0}])
    counts = postpass.top_up_to_envelope(
        data, {"utilities": (25200, 50400)}, state="CA", city="Temple City"
    )
    descs = [row["desc"] for row in data["transactions"]]
    assert counts["added"] == 1
    assert any("GAS" in desc for desc in descs), descs
    assert any("EDISON" in desc for desc in descs)


def test_range_for_accepts_plural_envelope_names():
    assert postpass.range_for("utilities") == postpass.CATEGORY_RANGES["utility"]
    assert postpass.range_for("groceries") == postpass.CATEGORY_RANGES["grocery"]


def test_recurring_charges_are_deduped_to_one_a_month():
    """会员费/保费/话费一个月就一笔，模型多开几笔要收掉。"""
    data = _mercury_like([
        {"date": "Nov 2", "desc": "EQUINOX TEMPLE CITY CA", "amount": -46.67},
        {"date": "Nov 15", "desc": "EQUINOX TEMPLE CITY CA", "amount": -41.73},
        {"date": "Nov 25", "desc": "EQUINOX TEMPLE CITY CA", "amount": -41.53},
        {"date": "Nov 6", "desc": "NETFLIX.COM", "amount": -23.4},
        {"date": "Nov 20", "desc": "NETFLIX.COM", "amount": -23.4},
    ])
    removed = postpass.dedupe_recurring(data)
    assert removed == 3
    assert len([r for r in data["transactions"] if "EQUINOX" in r["desc"]]) == 1
    assert len([r for r in data["transactions"] if "NETFLIX" in r["desc"]]) == 1


def test_same_store_number_is_renumbered_after_two_visits():
    data = _mercury_like([
        {"date": "Nov 1", "desc": "CHEVRON 7087", "amount": -41.83},
        {"date": "Nov 3", "desc": "CHEVRON 7087", "amount": -42.84},
        {"date": "Nov 5", "desc": "CHEVRON 7087", "amount": -28.8},
        {"date": "Nov 7", "desc": "CHEVRON 7087", "amount": -95.0},
    ])
    changed = postpass.vary_chain_stores(data, seed="t")
    stores = [row["desc"] for row in data["transactions"]]
    assert changed == 2
    assert len({desc for desc in stores}) == 3


def test_repair_month_reports_recurring_and_store_fixes():
    data = _mercury_like([
        {"date": "Nov 2", "desc": "NETFLIX.COM", "amount": -23.4},
        {"date": "Nov 20", "desc": "NETFLIX.COM", "amount": -23.4},
    ])
    report = postpass.repair_month(data, state="CA", city="Temple City")
    assert report["recurring"] == 1
    assert set(report) >= {"utilities", "duplicate_utilities", "online_places", "chains", "budget", "amounts", "sorted", "recurring", "stores"}


def test_decap_nudges_amounts_that_sit_exactly_on_the_cap():
    data = _mercury_like([
        {"date": "Nov 7", "desc": "PANERA BREAD 4774 TEMPLE CITY CA", "amount": -45.0},
        {"date": "Nov 20", "desc": "PANERA BREAD 5554 TEMPLE CITY CA", "amount": -20.0},
    ])
    changed = postpass.decap_amounts(data, seed="t")
    assert changed == 1
    assert abs(data["transactions"][0]["amount"]) < 45.0
    assert data["transactions"][1]["amount"] == -20.0


def test_top_up_rows_keep_the_template_row_shape():
    """新补的行必须带上同组行的固定字段（mercury 的 dir/icon），否则渲染器 KeyError。"""
    data = {
        "company": "HENG LI",
        "transactions": [
            {"date": "Sep 1", "desc": "AVALON MGMT", "amount": -2600.0, "dir": "out", "icon": "achOut"},
            {"date": "Sep 6", "desc": "CHIPOTLE 1234 TEMPLE CITY CA", "amount": -14.38, "dir": "out", "icon": "card"},
        ],
    }
    counts = postpass.top_up_to_envelope(data, {"dining": (14000, 36000)}, state="CA", city="Temple City")
    assert counts["added"] >= 1
    for row in data["transactions"]:
        assert "dir" in row and row["dir"] in {"in", "out"}
        assert "icon" in row


def test_every_template_can_render_after_top_up_adds_a_row():
    """补行不能破坏任何一家版式的行形状：补完必须还能出片。"""
    import json as _json

    from billgen import registry
    from billgen.spec import RenderOptions

    for spec in registry.SPECS:
        if getattr(spec, "kind", "") == "static-web":
            continue  # 浏览器端 pdf-lib 模板，Python 侧没有出片路径
        renderer = registry.get_renderer(spec.id)
        data = _json.loads(_json.dumps(renderer.default_data()))
        arrays = [
            value
            for value in data.values()
            if isinstance(value, list) and value and isinstance(value[0], dict) and "date" in value[0]
        ]
        if not arrays:
            continue
        postpass.top_up_to_envelope(data, {"dining": (40000, 90000)}, state="CA", city="Temple City")
        renderer.render(data, RenderOptions(watermark=False))  # 不抛就算过


def test_injected_rows_carry_the_keys_the_theme_needs():
    """补出来的行必须带 dir/icon/memo，否则版式渲染直接 KeyError: 'dir'。"""
    data = _mercury_like([
        {"date": "Nov 5", "desc": "ARCO 6261 TEMPLE CITY CA", "amount": -25.08,
         "type": "Card", "dir": "out", "icon": "card", "id": "1", "memo": ""},
    ])
    postpass.top_up_to_envelope(data, {"fuel": (25200, 50400)}, seed="t", state="CA", city="Temple City")
    assert len(data["transactions"]) > 1
    for row in data["transactions"]:
        assert row.get("dir") in {"in", "out"}
        assert "amount" in row and "date" in row and "desc" in row


def test_personal_accounts_do_not_print_an_ein():
    """Mercury 底稿是企业样例，个人账单上不该有 EIN 那一行。"""
    data = {"company": "HENG LI", "ein": "EIN ••3170", "transactions": []}
    postpass.repair_month(data, state="CA", city="Temple City", business=False)
    assert data["ein"] == ""

    data2 = {"company": "LUMEN CREATIVE LLC", "ein": "EIN ••3170", "transactions": []}
    postpass.repair_month(data2, state="CA", city="Temple City", business=True)
    assert data2["ein"] == "EIN ••3170"


def test_repeated_amounts_within_a_category_get_spread():
    """$43.65 连着出现三次最像机器：同类目同额要各挪一点。"""
    data = _mercury_like([
        {"date": "Dec 1", "desc": "CHIPOTLE 7008 TEMPLE CITY CA", "amount": -43.65},
        {"date": "Dec 3", "desc": "PANERA BREAD 8229 TEMPLE CITY CA", "amount": -43.65},
        {"date": "Dec 5", "desc": "IN-N-OUT 3069 TEMPLE CITY CA", "amount": -43.65},
    ])
    changed = postpass.diversify_amounts(data, seed="t")
    amounts = [abs(row["amount"]) for row in data["transactions"]]
    assert changed == 2
    assert len(set(amounts)) == 3
    lo, hi = postpass.range_for("dining")
    assert all(lo <= value <= hi for value in amounts)


def test_type_column_only_uses_card_and_ach_forms():
    data = _mercury_like([
        {"date": "Dec 1", "desc": "SO CAL GAS", "amount": -74.51, "type": "ACH"},
        {"date": "Dec 3", "desc": "CHIPOTLE 7008 TEMPLE CITY CA", "amount": -16.43, "type": "DEBIT"},
        {"date": "Dec 5", "desc": "VONS 4865 TEMPLE CITY CA", "amount": -14.4, "type": "Card"},
    ])
    changed = postpass.normalize_types(data)
    assert changed == 2
    assert [row["type"] for row in data["transactions"]] == ["ACH Out", "Card", "Card"]


def test_merchant_purchases_never_stay_ach():
    data = _mercury_like([
        {"date": "Dec 16", "desc": "TARGET", "amount": -152.56, "type": "ACH Out"},
        {"date": "Dec 18", "desc": "PANERA BREAD 8229 TEMPLE CITY CA", "amount": -43.2, "type": "ACH Out"},
        {"date": "Dec 14", "desc": "SOUTHERN CALIFORNIA EDISON", "amount": -195.0, "type": "ACH Out"},
    ])
    assert postpass.normalize_types(data) == 2
    types = [row["type"] for row in data["transactions"]]
    assert types[0] == "Card" and types[1] == "Card" and types[2] == "ACH Out"
