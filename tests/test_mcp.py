"""MCP 契约层与工具函数的测试（不经过传输层，直接调注册到的函数）。"""
from __future__ import annotations

import asyncio
import json

import pytest

from billgen import registry
from billgen.mcp import contract as contracts
from billgen.mcp import server as mcp_server

RENDERABLE = [spec.id for spec in registry.SPECS if spec.kind != "static-web"]


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    return mcp_server.build(out_dir=str(tmp_path_factory.mktemp("mcp")))


def call(server, name: str, arguments: dict):
    result = asyncio.run(server.call_tool(name, arguments))
    if isinstance(result, tuple):
        content = result[0]
        if content and hasattr(content[0], "text"):
            return json.loads(content[0].text)
        return content
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    return json.loads(result.content[0].text)


def test_every_template_has_a_contract():
    for template_id in RENDERABLE:
        data = contracts.contract(template_id)
        assert data["contract_version"] == 1
        assert data["template"]["id"] == template_id
        assert data["data"], template_id
        assert data["types"], template_id
        assert data["rules"], template_id
        assert any("SAMPLE" in rule for rule in data["rules"]), "规则里必须写明 SAMPLE 约束"


def test_contract_scaffolds_what_the_engine_cannot_invent():
    boa = contracts.contract("boa-personal")["data"]
    assert boa["promo"]["award_source"], "BOA 促销文案必须给占位，否则会印出真实商品名"
    assert boa["promo"]["overdraft_product"].endswith("Balance Guard\u2122")
    chase = contracts.contract("chase-personal")["data"]
    assert chase["product"] == "Everyday Checking"


def test_list_templates_covers_every_renderable_template(srv):
    payload = call(srv, "list_templates", {})
    assert payload["count"] == len(RENDERABLE)
    ids = {item["id"] for item in payload["templates"]}
    assert ids == set(RENDERABLE)
    assert "alliant" not in ids, "纯浏览器端模板不该出现在服务端渲染清单里"


@pytest.mark.parametrize("template_id", RENDERABLE)
def test_contract_defaults_render_after_bank_swap(srv, template_id):
    """把行名换成对应银行后，契约给的骨架必须能直接出片。"""
    data = call(srv, "template_contract", {"template_id": template_id})["data"]
    bank = data.get("bank")
    if isinstance(bank, dict):
        bank.update({"name": "Bank of America", "legal": "Bank of America, N.A.",
                     "domain": "bankofamerica.com"})
    result = call(srv, "validate", {"template_id": template_id, "data": data})
    assert result["ok"], result["errors"]
    assert result["pages"] >= 1


def test_validate_normalizes_formatted_money(srv):
    """展示格式的金额被「还原」而不是「拒绝」：早先它会让整套账单出不了片。"""
    data = call(srv, "template_contract", {"template_id": "boa-personal"})["data"]
    data["begin_balance"] = "$1,000.00"
    result = call(srv, "validate", {"template_id": "boa-personal", "data": data})
    assert result["ok"], result["errors"]
    assert any("还原成裸数字" in w for w in result["warnings"])


def test_validate_flags_placeholder_bank_identity(srv):
    """占位行名（Sample Bank / Harborline Bank）现在算缺陷，真行名才是期望。"""
    data = call(srv, "template_contract", {"template_id": "boa-personal"})["data"]
    data["bank"] = {"name": "Harborline Bank", "legal": "Harborline Bank, N.A.",
                    "domain": "harborline-bank.com"}
    result = call(srv, "validate", {"template_id": "boa-personal", "data": data})
    assert not result["ok"]
    assert any("虚构占位行名" in problem for problem in result["errors"])


def test_render_writes_pdf_and_reports_fingerprint(srv, tmp_path):
    data = call(srv, "template_contract", {"template_id": "chase-personal"})["data"]
    data["bank"] = {"name": "JPMorgan Chase Bank", "legal": "JPMorgan Chase Bank, N.A.",
                    "domain": "chase.com"}
    out = call(srv, "render", {"template_id": "chase-personal", "data": data,
                               "filename": "mcp-test"})
    assert out["ok"], out
    assert out["pages"] >= 1
    assert out["bytes"] > 10_000
    assert out["brand_leftovers"] == {}
    with open(out["path"], "rb") as handle:
        head = handle.read(5)
    assert head == b"%PDF-"


def test_preview_makes_png(srv):
    data = call(srv, "template_contract", {"template_id": "wellsfargo"})["data"]
    out = call(srv, "preview", {"template_id": "wellsfargo", "data": data, "pages": [1]})
    assert out["ok"], out
    assert out["files"] and out["files"][0].endswith(".png")


def test_draft_from_ledger_returns_template_data(srv):
    result = {
        "statements": [{
            "bankName": "Bank of America", "accountType": "checking", "accountLast4": "0042",
            "periodStart": "2027-01-01", "periodEnd": "2027-01-31",
            "months": [{"cycleStart": "2027-01-01", "cycleEnd": "2027-01-31",
                        "openingBalanceCents": 100000, "closingBalanceCents": 150000}],
            "transactions": [
                {"id": "t1", "transactionDate": "2027-01-04", "postedDate": "2027-01-04",
                 "description": "ACME CORP PAYROLL PPD", "type": "ACH", "direction": "credit",
                 "amountCents": 250000},
                {"id": "t2", "transactionDate": "2027-01-06", "postedDate": "2027-01-06",
                 "description": "CAMDEN PROPERTY MGMT RENT", "type": "ACH", "direction": "debit",
                 "amountCents": 200000},
            ],
        }],
    }
    out = call(srv, "draft_from_ledger", {"template_id": "boa-personal", "result": result})
    assert out["ok"], out
    assert out["data"]["begin_balance"] == "1000.00"
    assert out["ledger"]["rows"] == 2


def test_recommend_templates_runs_and_ranks(srv):
    """推荐工具曾经把不存在的 `template_id` 传进 from_game_result，一调就 NameError。

    这条测试只保证「工具能跑、返回打分」——版式适配度的具体分数由引擎自带测试覆盖。
    """
    payload = {
        "id": "recommend-probe",
        "seed": "recommend-probe",
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
                    {
                        "transactionDate": "2027-01-06",
                        "description": "ACME CORP PAYROLL DIRECT DEP",
                        "amountCents": 320000,
                        "direction": "credit",
                        "category": "payroll",
                    }
                ],
            }
        ],
        "audit": {"rules": [], "findings": []},
    }
    out = call(srv, "recommend_templates", {"result": payload, "limit": 3})
    assert out["ok"] is True, out
    assert out["candidates"], out
    assert out["rows"] >= 1


def test_contract_skeleton_carries_each_template_own_bank():
    """骨架里的行名必须是**这套版式自己的银行**。

    早先 `_scaffold` 一律写死成 Bank of America：模型读 contract 时会拿到
    `bank.name = Bank of America`，于是把它填进 Mercury / U.S. Bank / SoFi 的信头，
    品牌化再照着重画 logo —— 一张假账单上印着别家银行名，是最扎眼的一种假。
    """
    from billgen.mcp.contract import skeleton
    from billgen.synth.bridge import TEMPLATE_BANK

    for template_id in (
        "boa-personal", "boa-business", "chase-personal", "chase-business",
        "usbank-personal", "usbank-business", "wellsfargo", "capitalone",
        "mercury", "sofi", "amex",
    ):
        root = template_id.split("-")[0]
        bank = skeleton(template_id)["bank"]
        # 版式自带的写法（chase 印 `JPMorgan Chase Bank`）优先，只要不跑到别家去
        assert bank["name"].lower().startswith(TEMPLATE_BANK[root].lower()) \
            or TEMPLATE_BANK[root].lower().startswith(bank["name"].lower()), (template_id, bank["name"])
        assert bank["domain"], template_id
        assert bank["legal"], template_id
        if root != "boa":
            assert "Bank of America" not in str(bank), template_id
    # Mercury 的信头是自己，页脚发卡行是 Column N.A.（参考件原文）
    assert skeleton("mercury")["bank"]["legal"] == "Column N.A."


def test_validate_normalizes_formatted_amounts_instead_of_rejecting(srv):
    """模型给 `$2,400.00` 这种展示格式时，别判死整份账单：先还原成裸数字再校验。

    历史：`electronic_withdrawals[2].amount = '2,400.00'` 直接让 chase-business
    整套出不了片（warning 里只剩一句「金额不能带 $ 或千分位」）。
    """
    data = json.loads(json.dumps(contracts.skeleton("chase-business")))
    data["deposits"] = [{"date": "08/18", "description": "ACH Credit", "amount": "$3,035.78"}]
    data["electronic_withdrawals"] = [
        {"date": "08/20", "description": "ACH Debit", "amount": "2,400.00"}
    ]
    out = call(srv, "validate", {"template_id": "chase-business", "data": data})
    assert out["ok"], out
    assert not [e for e in out["errors"] if "千分位" in e]
    assert any("还原成裸数字" in w for w in out["warnings"])
    # 归一化过后的数据能真的出片
    rendered = call(srv, "render", {"template_id": "chase-business", "data": data})
    assert rendered["ok"], rendered
    assert rendered["pages"] >= 1
