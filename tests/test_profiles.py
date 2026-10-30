"""档案库（``billgen.profiles``）的行为约定。

这些测试盯的是「别静默丢数据」：库文件坏了要报、导入的坏行要标出来、
手改过的账号不能被重新派生覆盖、同一个档案重跑两次必须派生出同一套号。
"""

from __future__ import annotations

import copy
import json
import pathlib
from datetime import date

import pytest

from billgen.profiles import (
    DEFAULT_STORE,
    ImportResult,
    Profile,
    ProfileError,
    ProfileNotFound,
    ProfileStore,
    ensure_accounts,
    import_csv,
    import_json,
    new_business,
    new_person,
    validate_profile,
)

PERSON = {
    "name": "Maya Okafor",
    "street": "4821 Larkspur Dr",
    "city": "Austin",
    "state": "TX",
    "zip_code": "78745",
    "occupation": "Registered Nurse",
    "monthly_income_hint": "4000-6000",
}

BUSINESS = {
    "legal_name": "Northwind Labs LLC",
    "entity_type": "LLC",
    "street": "77 Cedar Ave",
    "city": "Portland",
    "state": "OR",
    "zip_code": "97205",
    "industry": "Software",
    "employee_count": 12,
    "monthly_revenue_hint": "40000-60000",
}


def _store(tmp_path: pathlib.Path, name: str = "profiles.json") -> ProfileStore:
    return ProfileStore(tmp_path / name)


def _aba_ok(number: str) -> bool:
    """ABA 3-7-1 校验位：派生出来的路由号也得能过。"""
    if not (isinstance(number, str) and len(number) == 9 and number.isdigit()):
        return False
    weights = (3, 7, 1, 3, 7, 1, 3, 7)
    total = sum(w * int(d) for w, d in zip(weights, number[:8], strict=True))
    return (-total) % 10 == int(number[8])


# ------------------------------------------------------------------ 读写


def test_default_store_points_at_data_dir():
    assert isinstance(DEFAULT_STORE, pathlib.Path)
    assert DEFAULT_STORE.name == "profiles.json"
    assert ProfileStore().path == DEFAULT_STORE


def test_missing_file_is_empty_store(tmp_path):
    store = _store(tmp_path)
    assert store.all() == []
    assert store.path.exists() is False  # 读不该顺手建库


def test_upsert_assigns_id_and_keeps_created_at(tmp_path):
    store = _store(tmp_path)
    person = new_person(**PERSON)
    saved = store.upsert(person)
    assert len(saved.id) == 12 and saved.id.isalnum()
    assert saved.created_at and saved.updated_at

    created = saved.created_at
    saved.display_name = "Maya O."
    saved.updated_at = ""
    again = store.upsert(saved)
    assert again.id == saved.id
    assert again.created_at == created  # 创建时间只在第一次写入时定下
    assert again.updated_at  # 更新时重新盖上
    assert again.display_name == "Maya O."


def test_save_is_atomic_and_reloadable(tmp_path):
    store = _store(tmp_path)
    person = store.upsert(new_person(**PERSON))
    assert store.path.is_file()
    assert not store.path.with_name(store.path.name + ".tmp").exists()

    raw = json.loads(store.path.read_text(encoding="utf-8"))
    assert raw["profiles"][0]["id"] == person.id

    reloaded = ProfileStore(store.path)
    reloaded.load()
    assert [p.id for p in reloaded.all()] == [person.id]
    assert reloaded.get(person.id).payload["address"]["zip"] == "78745"


def test_get_missing_raises_profile_not_found(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ProfileNotFound):
        store.get("deadbeefcafe")


def test_broken_json_raises_profile_error(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text("{ 这不是 JSON", encoding="utf-8")
    store = ProfileStore(path)
    with pytest.raises(ProfileError) as err:
        store.load()
    assert "档案库文件损坏" in str(err.value)

    # 懒加载路径（直接查）也要报，不是只在显式 load() 时才炸
    with pytest.raises(ProfileError):
        ProfileStore(path).all()


def test_delete_removes_and_persists(tmp_path):
    store = _store(tmp_path)
    person = store.upsert(new_person(**PERSON))
    store.delete(person.id)
    assert store.all() == []
    store.delete("nonexistent00")  # 重复删除不报错
    assert ProfileStore(store.path).all() == []


def test_from_dict_tolerates_missing_fields():
    blank = Profile.from_dict({})
    assert blank.id == "" and blank.kind == "" and blank.payload == {}
    assert blank.accounts == {}
    person = new_person(**PERSON)
    assert Profile.from_dict(person.to_dict()) == person


# ------------------------------------------------------------------ 校验


def test_validate_flags_state_and_zip():
    person = new_person(**{**PERSON, "state": "tx", "zip_code": "7874"})
    errors = validate_profile(person)
    assert any("state" in message for message in errors)
    assert any("zip" in message for message in errors)


def test_validate_flags_unknown_bank_and_kind():
    person = new_person(**{**PERSON, "bank": "nope-personal"})
    person.kind = "vehicle"
    errors = validate_profile(person)
    assert any("未知模板" in message for message in errors)
    assert any("kind" in message for message in errors)


def test_validate_rejects_static_web_template():
    person = new_person(**{**PERSON, "bank": "alliant"})
    errors = validate_profile(person)
    assert any("static-web" in message for message in errors)


def test_validate_accepts_person_and_business():
    assert validate_profile(new_person(**PERSON)) == []
    assert validate_profile(new_business(**BUSINESS)) == []


def test_validate_flags_business_entity_type_and_income_hint():
    business = new_business(**{**BUSINESS, "entity_type": "PLLC"})
    assert any("entityType" in message for message in validate_profile(business))
    person = new_person(**{**PERSON, "monthly_income_hint": "四千左右"})
    assert any("monthly_income_hint" in message for message in validate_profile(person))


# -------------------------------------------------------------- 账户派生


def test_ensure_accounts_derives_and_is_stable(tmp_path):
    store = _store(tmp_path)
    person = store.upsert(new_person(**PERSON))  # 派生种子用档案 id
    accounts = ensure_accounts(person)
    assert set(accounts) == {"checking", "credit"}
    checking = accounts["checking"]
    number = checking["number"]
    assert number.isdigit() and 10 <= len(number) <= 12
    assert set(number) != {"0"}  # 不许全零
    assert checking["last4"] == number[-4:]
    assert _aba_ok(checking["routing"])
    days = (date.today() - date.fromisoformat(checking["opened"])).days
    assert 365 <= days <= 365 * 6 + 30

    first = copy.deepcopy(accounts)
    assert ensure_accounts(person) == first  # 两次调用完全一致
    assert person.accounts == first  # 派生结果写回了档案


def test_ensure_accounts_never_overwrites_manual_values(tmp_path):
    store = _store(tmp_path)
    person = store.upsert(new_person(**PERSON))
    manual = {
        "number": "998877665544",
        "last4": "5544",
        "routing": "111000025",
        "opened": "2019-03-01",
    }
    person.accounts["checking"] = dict(manual)
    person.accounts["credit"] = {"last4": "0002"}  # 只手填了尾号

    ensure_accounts(person)
    assert person.accounts["checking"] == manual
    credit = person.accounts["credit"]
    assert credit["last4"] == "0002"  # 手改的尾号不被覆盖
    assert len(credit["number"]) >= 10 and credit["number"] != "0002"
    assert set(credit) == {"number", "last4", "routing", "opened"}

    ensure_accounts(person)
    assert person.accounts["checking"] == manual


def test_ensure_accounts_fills_missing_fields_of_partial_account(tmp_path):
    store = _store(tmp_path)
    person = store.upsert(new_person(**PERSON))
    person.accounts["checking"] = {"number": "123456789012"}
    ensure_accounts(person)
    checking = person.accounts["checking"]
    assert checking["number"] == "123456789012"
    assert checking["last4"] == "9012"  # 尾号跟着已有的号走
    assert _aba_ok(checking["routing"])
    assert checking["opened"]


def test_ensure_accounts_uses_business_keys(tmp_path):
    store = _store(tmp_path)
    business = store.upsert(new_business(**BUSINESS))
    accounts = ensure_accounts(business)
    assert set(accounts) == {"business_checking", "credit"}
    assert accounts["business_checking"]["last4"] == accounts["business_checking"]["number"][-4:]


# ------------------------------------------------------------------ 导入


def test_import_json_reports_bad_rows():
    text = json.dumps(
        {
            "profiles": [
                {
                    "kind": "person",
                    "bank": "chase-personal",
                    "name": "Maya Okafor",
                    "address": {
                        "street": "4821 Larkspur Dr",
                        "city": "Austin",
                        "state": "TX",
                        "zip": "78745",
                    },
                    "occupation": "Nurse",
                },
                {
                    "kind": "person",
                    "bank": "boa-personal",
                    "name": "Rui Chen",
                    "address": {
                        "street": "118 Alder Way",
                        "city": "Tampa",
                        "state": "FL",
                        "zip": "33602",
                    },
                },
                {
                    "kind": "business",
                    "bank": "chase-business",
                    "legalName": "Harborline Co",
                    "entityType": "PLLC",
                    "address": {
                        "street": "9 Pier Rd",
                        "city": "Seattle",
                        "state": "WA",
                        "zip": "98101",
                    },
                },
            ]
        }
    )
    result = import_json(text)
    assert isinstance(result, ImportResult)
    assert [p.display_name for p in result.profiles] == ["Maya Okafor", "Rui Chen"]
    assert [error["row"] for error in result.errors] == [2]
    assert "entityType" in result.errors[0]["error"]
    assert result.ok is False


def test_import_json_accepts_bare_array_and_keeps_id():
    entry = {
        "id": "abc123def456",
        "kind": "person",
        "bank": "mercury",
        "display_name": "Maya Okafor",
        "name": "Maya Okafor",
        "street": "4821 Larkspur Dr",
        "city": "Austin",
        "state": "TX",
        "zip": "78745",
        "accounts": {"checking": {"number": "123456789012"}},
    }
    result = import_json(json.dumps([entry]))
    assert result.ok is True
    profile = result.profiles[0]
    assert profile.id == "abc123def456"
    assert profile.accounts["checking"]["number"] == "123456789012"
    assert profile.payload["address"]["city"] == "Austin"


def test_import_json_raises_on_broken_text():
    with pytest.raises(ProfileError):
        import_json("{ not json")


CSV_PERSON = (
    "kind,bank,display_name,name,street,city,state,zip,occupation,monthly_income_hint\n"
    "person,chase-personal,Maya Okafor,Maya Okafor,4821 Larkspur Dr,Austin,TX,78745,Nurse,4000-6000\n"
    "person,chase-personal,Bad State,Bad State,9 Elm St,Dallas,tx,75201,,\n"
    "business,mercury,Northwind Labs,Northwind Labs,77 Cedar Ave,Portland,OR,97205,,\n"
    "person,chase-personal,Bad Hint,Bad Hint,5 Pine Rd,Denver,CO,80202,,4k\n"
)


def test_import_csv_reports_bad_rows():
    result = import_csv(CSV_PERSON)
    assert [p.display_name for p in result.profiles] == ["Maya Okafor"]
    assert [error["row"] for error in result.errors] == [3, 4, 5]
    assert "state" in result.errors[0]["error"]
    assert "legalName" in result.errors[1]["error"]
    assert "monthly_income_hint" in result.errors[2]["error"]


def test_import_csv_accepts_business_columns():
    text = (
        "kind,bank,display_name,legalName,entityType,street,city,state,zip,"
        "industry,employeeCount,monthly_revenue_hint\n"
        "business,mercury,,Northwind Labs LLC,LLC,77 Cedar Ave,Portland,OR,97205,"
        "Software,12,40000-60000\n"
        "\n"  # 尾部空行不算一条档案
    )
    result = import_csv(text)
    assert result.ok is True
    business = result.profiles[0]
    assert business.display_name == "Northwind Labs LLC"
    assert business.payload["entityType"] == "LLC"
    assert business.payload["employeeCount"] == 12
    assert business.payload["address"]["zip"] == "97205"


def test_import_csv_without_header_raises():
    with pytest.raises(ProfileError):
        import_csv("")


def test_entity_type_is_normalized_case_insensitively():
    text = (
        "kind,bank,display_name,legalName,entityType,street,city,state,zip,industry\n"
        "business,mercury,,Northwind Labs,llc,77 Cedar Ave,Portland,OR,97205,Software\n"
    )
    result = import_csv(text)
    assert result.ok is True
    assert result.profiles[0].payload["entityType"] == "LLC"
    # 不认识的写法仍然要报，别把 PLLC 当成 LLC 混过去
    assert validate_profile(new_business(**{**BUSINESS, "entity_type": "PLLC"}))


def test_new_business_normalizes_entity_type():
    sole = new_business(**{**BUSINESS, "entity_type": "sole proprietor"})
    assert sole.payload["entityType"] == "Sole Prop"
    assert validate_profile(new_business(**{**BUSINESS, "entity_type": "S Corp"})) == []


def test_derivation_without_id_is_still_stable():
    person = new_person(**PERSON)  # 还没入库：id 为空
    first = copy.deepcopy(ensure_accounts(person))
    person.accounts = {}
    assert ensure_accounts(person) == first  # 用 bank+display_name 兜底也要稳定
