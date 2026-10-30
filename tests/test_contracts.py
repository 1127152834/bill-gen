"""账单契约：格式常识必须是数据 + 一个执行点，而不是散落的 if-else。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from billgen import contracts


def _mercury(**overrides):
    data = {
        "address": ["1119 Freer Avenue", "Temple City, CA 91780"],
        "transactions": [
            {"date": "Nov 26", "desc": "RALPHS 7585 TEMPLE CITY CA", "type": "Card", "amount": -169.76},
            {"date": "Nov 28", "desc": "CLIENT PAYMENT", "type": "ACH In", "amount": 2693.93},
        ],
    }
    data.update(overrides)
    return data


def test_every_shipped_contract_has_evidence():
    for name in contracts.available():
        contract = contracts.load(name)
        assert contract["evidence"], f"{name} 没有出处，等于凭记忆写常识"


def test_mercury_card_rows_drop_city_and_payment_tail():
    data = _mercury()
    shaped = contracts.conform("mercury-personal", data)
    assert shaped["cities"] == 1
    assert data["transactions"][0]["desc"] == "RALPHS 7585"
    # 剥完只剩一个词就不剥（"CLIENT PAYMENT" → "CLIENT" 更假），留给收入方命名规则去治
    assert data["transactions"][1]["desc"] == "CLIENT PAYMENT"
    assert contracts.check("mercury-personal", data) == []


def test_mercury_rejects_unknown_type_and_leading_padding():
    data = _mercury(transactions=[
        {"date": "Nov 26", "desc": "  RALPHS 7585", "type": "ACH", "amount": -169.76},
    ])
    problems = contracts.check("mercury-personal", data)
    assert any("forbidden" in p or "type-enum" in p or "row-format" in p for p in problems)


def test_chase_contract_requires_card_tail_in_row():
    data = {
        "address": ["2104 S 1st St", "Austin, TX 78704"],
        "transactions": [{"date": "09/12", "desc": "Card Purchase 09/12 ARCTERYX AUSTIN TX Card 4499", "type": "Card Purchase", "amount": -210.5}],
    }
    assert contracts.check("chase-personal", data) == []
    data["transactions"][0]["desc"] = "Card Purchase 09/12 ARCTERYX AUSTIN TX"
    assert any("card-tail" in p for p in contracts.check("chase-personal", data))


def test_resolve_maps_renderer_id_to_contract_id():
    assert contracts.resolve("mercury") == "mercury-personal"
    assert contracts.resolve("chase") == "chase-personal"
    assert contracts.resolve("wellsfargo") == "wellsfargo"
