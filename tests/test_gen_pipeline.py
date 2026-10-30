"""账单生成 Python 移植版：离线管线测试。

LLM、Tavily、任务上下文全部用假的，测试全程不联网、不需要 key。
"""

from __future__ import annotations

import json
import re
import socket
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest

from billgen.gen import prompts as P
from billgen.gen.llm import LlmBadJson
from billgen.gen.pipeline import build_ledger, generate, node_normalize
from billgen.gen.tavily import TavilyQuotaExceeded, TavilyResult
from billgen.jobs import JobCancelled

MONTHS = ["2026-07", "2026-08", "2026-09"]
TEMPLATE = "boa-personal"

PROFILE = {
    "name": "Jordan Ellis",
    "age": 34,
    "occupation": "freelance designer",
    "maritalStatus": "married",
    "children": 1,
    "hobbies": ["cycling", "cooking"],
    "address": {
        "street": "1200 S Lamar Blvd",
        "city": "Austin",
        "state": "TX",
        "zip": "78704",
    },
}

# 调研计划的固定回复：顺序决定 f1..f7 的编号（cell phone 与 netflix 同类别，会被去重）。
PLAN = [
    {"id": "q1", "category": "groceries", "query": "grocery prices Austin TX 2026", "scope": "metro"},
    {"id": "q2", "category": "fuel", "query": "gas prices Austin TX", "scope": "metro"},
    {"id": "q3", "category": "rent", "query": "apartments for rent Austin TX monthly", "scope": "metro"},
    {"id": "q4", "category": "dining", "query": "restaurant prices Austin TX", "scope": "metro"},
    {"id": "q5", "category": "utilities", "query": "electricity rate Texas residential", "scope": "state"},
    {"id": "q6", "category": "insurance", "query": "car insurance monthly premium Texas", "scope": "state"},
    {"id": "q7", "category": "subscriptions", "query": "netflix standard plan price", "scope": "national"},
    {"id": "q8", "category": "subscriptions", "query": "monthly cell phone plan unlimited price", "scope": "national"},
]


def _model() -> dict:
    """一份自洽的生活模型：收入 + 3 个账单 + 4 类消费。"""
    return {
        "incomeStreams": [
            {
                "id": "inc1",
                "label": "Client work",
                "payerName": "Ridgeway Studio",
                "channel": "ach_client",
                "cadence": {"kind": "irregular", "perMonth": 4},
                "amountCents": {"min": 85000, "max": 140000},
                "seasonality": [1.0] * 12,
            }
        ],
        "recurring": [
            {
                "id": "rec-rent",
                "label": "Mueller Apartments",
                "kind": "rent",
                "dayRule": {"kind": "dayOfMonth", "day": 1},
                "amount": {"kind": "fixed", "cents": 158000},
                "account": "checking",
                "priority": 1,
                "factId": "f3",
                "txnType": "ACH",
            },
            {
                "id": "rec-util",
                "label": "Bluebonnet Electric",
                "kind": "utility",
                "dayRule": {"kind": "dayOfMonth", "day": 12},
                "amount": {
                    "kind": "seasonal",
                    "minCents": 6200,
                    "maxCents": 15800,
                    "peakMonths": [7, 8, 9],
                },
                "account": "checking",
                "priority": 2,
                "factId": "f5",
                "txnType": "ACH",
            },
            {
                "id": "rec-stream",
                "label": "Netflix",
                "kind": "subscription",
                "dayRule": {"kind": "dayOfMonth", "day": 21},
                "amount": {"kind": "fixed", "cents": 1549},
                "account": "checking",
                "priority": 3,
                "factId": "f7",
                "txnType": "ACH",
            },
        ],
        "discretionaryPool": [
            {
                "factId": "f1",
                "weight": 3.0,
                "perMonth": {"min": 3, "max": 5},
                "ticketCents": {"min": 2400, "max": 8900},
                "pricing": "basket",
                "taxable": True,
                "tip": False,
                "account": "checking",
                "weekendBias": 0.4,
                "location": "local",
            },
            {
                "factId": "f2",
                "weight": 2.0,
                "perMonth": {"min": 2, "max": 4},
                "ticketCents": {"min": 2800, "max": 6800},
                "pricing": "fuel",
                "unitPriceCents": 329,
                "taxable": True,
                "tip": False,
                "account": "checking",
                "weekendBias": 0.5,
                "location": "local",
            },
            {
                "factId": "f4",
                "weight": 2.0,
                "perMonth": {"min": 2, "max": 4},
                "ticketCents": {"min": 1400, "max": 6200},
                "pricing": "basket",
                "taxable": True,
                "tip": True,
                "account": "checking",
                "weekendBias": 0.7,
                "location": "local",
            },
            {
                "factId": "f6",
                "weight": 1.0,
                "perMonth": {"min": 1, "max": 1},
                "ticketCents": {"min": 9600, "max": 9600},
                "pricing": "fixed_menu",
                "taxable": False,
                "tip": False,
                "account": "checking",
                "weekendBias": 0.0,
                "location": "online",
            },
        ],
        "openingBalanceCents": 486213,
    }


# ---- 假客户端 ------------------------------------------------------------


class FakeLlm:
    """按 schema 路由的假模型；可注入坏 JSON、审核 finding。"""

    def __init__(self, *, model: dict | None = None, findings: list | None = None,
                 bad_json_once: bool = False) -> None:
        self.model = deepcopy(model or _model())
        self.findings = deepcopy(findings or [])
        self.bad_json_once = bad_json_once
        self.names: list[str] = []
        self.life_calls = 0

    def json_chat(self, messages, *, schema=None, temperature=0.2, max_tokens=4000, timeout=None):
        if schema is P.RESEARCH_PLAN_SCHEMA:
            name = "plan"
        elif schema is P.FINANCIAL_MODEL_SCHEMA:
            name = "lifeModel"
            self.life_calls += 1
        elif schema is P.MODEL_PATCH_SCHEMA:
            name = "lifeModelPatch"
            self.life_calls += 1
        elif schema is P.DESCRIPTORS_SCHEMA:
            name = "descriptors"
        elif schema is P.AUDIT_SCHEMA:
            name = "audit"
        else:  # pragma: no cover - 有新的 schema 就说明测试该更新了
            raise AssertionError(f"未知 schema: {schema}")
        self.names.append(name)
        if self.bad_json_once:
            self.bad_json_once = False
            raise LlmBadJson("假模型：第一次给了坏 JSON")

        if name == "plan":
            return {"queries": deepcopy(PLAN)}
        if name == "lifeModel":
            return deepcopy(self.model)
        if name == "lifeModelPatch":
            return {"patch": {}}
        if name == "descriptors":
            text = "\n".join(str(item.get("content", "")) for item in messages)
            keys = list(dict.fromkeys(re.findall(r'"merchantKey"\s*:\s*"([^"]+)"', text)))
            return {
                "descriptors": [
                    {"merchantKey": key, "template": f"CHECKCARD {{MMDD}} {key.upper()}", "memo": "Card purchase"}
                    for key in keys
                ]
            }
        return {"findings": deepcopy(self.findings)}


# 查询关键字 → 事实。标题和类别对齐 PLAN 的顺序，编号 f1..f7。
_FACT_MAP = [
    (("grocery", "groceries"), ("H-E-B", "groceries", "Milk $3.49, eggs $4.29, bread $2.99")),
    (("gas", "fuel"), ("Shell", "fuel", "Regular $3.29, Premium $3.79")),
    (("apartment", "rent"), ("Mueller Apartments", "rent", "1 bedroom from $1,650")),
    (("restaurant", "dining"), ("Torchy's Tacos", "dining", "Tacos $3.75, queso $4.25")),
    (("electricity", "utility", "utilities"), ("Bluebonnet Electric", "utilities", "Average bill $128")),
    (("insurance",), ("State Farm", "insurance", "Auto from $96 per month")),
    (("netflix", "streaming"), ("Netflix", "subscriptions", "Standard $15.49")),
    (("cell", "phone"), ("T-Mobile", "subscriptions", "Unlimited $70")),
]


class FakeTavily:
    """假检索：按查询关键词返回一条事实，可模拟额度耗尽。"""

    def __init__(self, *, max_calls: int | None = None) -> None:
        self.max_calls = max_calls
        self.calls = 0

    def search(self, query, *, max_results=3):
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise TavilyQuotaExceeded("本月检索额度用尽")
        self.calls += 1
        lowered = str(query).lower()
        title, category, content = "Local Merchant", "shopping", "Price $12.99"
        for needles, payload in _FACT_MAP:
            if any(needle in lowered for needle in needles):
                title, category, content = payload
                break
        return [
            TavilyResult(
                query=str(query),
                url=f"https://example.test/fact-{self.calls}",
                title=f"{title} | example.test",
                content=content,
            )
        ]


class FakeCtx:
    """假的任务上下文：记录日志，并在第 N 次日志时取消。"""

    def __init__(self, *, cancel_after: int | None = None, cancelled: bool = False) -> None:
        self.cancel_after = cancel_after
        self.cancelled = cancelled
        self.logs: list[str] = []

    def log(self, message, percent=None):
        self.logs.append(str(message))
        if self.cancel_after is not None and len(self.logs) > self.cancel_after:
            raise JobCancelled("假上下文：任务已取消")

    def raise_if_cancelled(self):
        if self.cancelled:
            raise JobCancelled("假上下文：任务已取消")


def _gen(tmp_path: Path, *, llm=None, tavily=None, ctx=None, profile=None,
         template: str = TEMPLATE, months=None, seed: str = "test-seed", out_dir=None):
    return generate(
        ctx,
        profile=profile or PROFILE,
        template=template,
        months=MONTHS if months is None else months,
        llm=llm or FakeLlm(),
        tavily=tavily or FakeTavily(),
        seed=seed,
        out_dir=out_dir or (tmp_path / "out"),
    )


# ---- 测试 ----------------------------------------------------------------


def test_three_months_all_ok(tmp_path):
    llm = FakeLlm()
    res = _gen(tmp_path, llm=llm)

    assert [item.month for item in res.months] == MONTHS
    assert all(item.ok for item in res.months), [item.report for item in res.months]
    assert all(item.data for item in res.months)
    assert res.audit["batch"]["ok"] is True
    assert res.audit["rules"] and all(rule["passed"] for rule in res.audit["rules"])
    # 模型自洽时不该触发兜底或回退
    assert not any("自检未过" in warning for warning in res.warnings)
    assert llm.life_calls == 1
    assert res.usage["llmCalls"] >= 4
    assert res.usage["tavilyQueries"] == 7
    report = json.loads((Path(res.out_dir) / "validation-report.json").read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert [entry["month"] for entry in report["months"]] == MONTHS
    for month in MONTHS:
        assert (Path(res.out_dir) / f"{TEMPLATE}-{month}.json").exists()


def test_build_ledger_closes_by_arithmetic():
    norm = node_normalize(PROFILE, template=TEMPLATE, months=MONTHS, seed="test-seed")
    rows = [
        {"date": date(2026, 7, 3), "posted": date(2026, 7, 3), "type": "ACH", "direction": "debit",
         "amount_cents": 158000, "description": "MUELLER APARTMENTS RENT", "memo": "Rent",
         "merchant_key": "rec-rent", "fact_id": "f3", "category": "rent"},
        {"date": date(2026, 7, 15), "posted": date(2026, 7, 15), "type": "DEPOSIT", "direction": "credit",
         "amount_cents": 220000, "description": "CLIENT PAYMENT", "memo": "Client payment",
         "merchant_key": "inc1", "fact_id": "", "category": "income"},
        {"date": date(2026, 8, 2), "posted": date(2026, 8, 2), "type": "POS", "direction": "debit",
         "amount_cents": 4387, "description": "H-E-B #221 AUSTIN TX", "memo": "Groceries",
         "merchant_key": "f1", "fact_id": "f1", "category": "groceries",
         "city": "Austin", "state": "TX"},
    ]
    ledger = build_ledger(norm, PROFILE, {"openingBalanceCents": 486213}, rows, template=TEMPLATE, seed="test-seed")

    assert ledger.opening_cents == 486213
    assert [row.balance_cents for row in ledger.rows] == [328213, 548213, 543826]
    assert ledger.closing_cents == ledger.opening_cents + sum(row.signed_cents for row in ledger.rows)
    assert ledger.closing_cents == 543826
    assert ledger.period.start == date(2026, 7, 1) and ledger.period.end == date(2026, 9, 30)


def test_arithmetic_gate_is_per_month(tmp_path):
    model = _model()
    model["monthClosings"] = {"2026-08": 999999}  # 8 月故意不闭合，7/9 月应不受连坐
    res = _gen(tmp_path, llm=FakeLlm(model=model))

    assert [item.ok for item in res.months] == [True, False, True]
    august = res.months[1].report
    assert any(item["rule"] == "balance_closure" for item in august["content"])
    assert res.audit["batch"]["ok"] is False
    assert "2026-08" in res.audit["batch"]["failed"]
    assert any("partial" in warning for warning in res.warnings)
    # 硬卡不抛异常：结果照常返回，其它月份的数据可用
    assert res.months[0].data and res.months[2].data


def test_audit_high_only_warns(tmp_path):
    finding = {
        "severity": "high",
        "transactionIds": ["CHK2"],
        "problem": "两笔消费金额完全一样，像是复制出来的",
        "suggestedTarget": "lifeModel",
    }
    llm = FakeLlm(findings=[finding])
    res = _gen(tmp_path, llm=llm)

    assert all(item.ok for item in res.months)
    assert res.audit["findings"][0]["severity"] == "high"
    assert any("audit (high" in warning for warning in res.warnings)
    # 高严重度只允许回退 1 轮，之后照常交付
    assert llm.life_calls == 2


def test_research_quota_degrades(tmp_path):
    res = _gen(tmp_path, tavily=FakeTavily(max_calls=2))

    assert len(res.months) == 3
    assert any("额度" in warning for warning in res.warnings)
    assert res.usage["tavilyQueries"] == 2
    assert all(item.data for item in res.months)


def test_llm_bad_json_retried(tmp_path):
    llm = FakeLlm(bad_json_once=True)
    res = _gen(tmp_path, llm=llm)

    assert llm.names[:2] == ["plan", "plan"]
    assert all(item.ok for item in res.months)


def test_raise_if_cancelled_stops_immediately(tmp_path):
    ctx = FakeCtx(cancelled=True)
    llm = FakeLlm()
    with pytest.raises(JobCancelled):
        _gen(tmp_path, ctx=ctx, llm=llm)
    assert llm.names == []  # 取消发生在任何 LLM 调用之前
    assert ctx.logs == []


def test_log_cancel_propagates(tmp_path):
    with pytest.raises(JobCancelled):
        _gen(tmp_path, ctx=FakeCtx(cancel_after=0))


class KeywordOnlyCtx(FakeCtx):
    """对齐真实 JobContext：percent 只允许关键字传。"""

    def log(self, message, *, percent=None):
        self.logs.append(str(message))


def test_ctx_keyword_only_log(tmp_path):
    ctx = KeywordOnlyCtx()
    res = _gen(tmp_path, ctx=ctx)
    assert all(item.ok for item in res.months)
    assert any("finalize" in message for message in ctx.logs)


def test_ctx_logs_each_node(tmp_path):
    ctx = FakeCtx()
    _gen(tmp_path, ctx=ctx)
    joined = " ".join(ctx.logs)
    for marker in ("normalize", "planResearch", "research", "lifeModel", "schedule",
                   "descriptors", "validate", "audit", "finalize"):
        assert marker in joined


@pytest.mark.parametrize("template", ["static-web", "alliant", "no-such-template"])
def test_invalid_template_raises(tmp_path, template):
    with pytest.raises(ValueError):
        _gen(tmp_path, template=template)


def test_empty_months_raises(tmp_path):
    with pytest.raises(ValueError):
        _gen(tmp_path, months=[])


def test_cross_month_chain(tmp_path):
    res = _gen(tmp_path)

    for previous, following in zip(res.months, res.months[1:], strict=False):
        assert previous.ok and following.ok
        assert abs(float(previous.report["closing"]) - float(following.report["opening"])) < 1e-6
    assert all(item.report["rows"] > 0 for item in res.months)


def test_same_seed_is_deterministic(tmp_path):
    first = _gen(tmp_path / "a", seed="same-seed")
    second = _gen(tmp_path / "b", seed="same-seed")

    for left, right in zip(first.months, second.months, strict=False):
        assert json.dumps(left.data, sort_keys=True) == json.dumps(right.data, sort_keys=True)


def test_runs_offline(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("测试不允许联网")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)

    res = _gen(tmp_path)
    assert all(item.ok for item in res.months)
