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

from billgen.gen import pipeline
from billgen.gen import prompts as P
from billgen.gen.prompts import MERCHANT_EXAMPLES, merchant_pool
from billgen.gen.llm import LlmBadJson
from billgen.gen.pipeline import build_ledger, generate, node_normalize, node_schedule
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


def test_three_months_all_ok(tmp_path, monkeypatch):

    monkeypatch.setenv("BILLGEN_SCHEDULER", "model")  # 这条测的是模型逐行生成的老链路
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


def test_arithmetic_gate_is_per_month(tmp_path, monkeypatch):

    monkeypatch.setenv("BILLGEN_SCHEDULER", "model")  # 这条测的是模型逐行生成的老链路
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


def test_audit_high_only_warns(tmp_path, monkeypatch):

    monkeypatch.setenv("BILLGEN_SCHEDULER", "model")  # 这条测的是模型逐行生成的老链路
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


def test_ctx_logs_each_node(tmp_path, monkeypatch):

    monkeypatch.setenv("BILLGEN_SCHEDULER", "model")  # 这条测的是模型逐行生成的老链路
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


# ---- 商户名 / 水印 / 填充（回归：真实产物里出现过搜索标题当商户名）-------


def _walk_strings(node) -> list[str]:
    """递归取出交付 JSON 里所有字符串（各模板结构不同，不逐字段硬编码）。"""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        out: list[str] = []
        for value in node.values():
            out.extend(_walk_strings(value))
        return out
    if isinstance(node, (list, tuple)):
        out = []
        for value in node:
            out.extend(_walk_strings(value))
        return out
    return []


def _brands() -> set[str]:
    return {brand for pool in P.MERCHANT_EXAMPLES.values() for brand in pool}


def _card_merchants(data: dict) -> list[str]:
    """Chase 卡行里的商户短语：``Card Purchase          07/04 SPROUTS MKT 7243 AUSTIN TX Card 8326``。"""
    out: list[str] = []
    for text in _walk_strings(data):
        match = re.search(r"Card Purchase\s+\d{2}/\d{2}\s+(.+)", text)
        if not match:
            continue
        body = re.sub(r"\s+Card \d{4}$", "", match.group(1)).strip()
        body = re.sub(r"\s+[A-Z][A-Z .]+ [A-Z]{2}$", "", body).strip()  # 去掉尾部城市州
        out.append(body)
    return out


def test_no_doubled_card_prefix(tmp_path):
    """适配器已经印 ``Card Purchase``/``POS PURCHASE``，pipeline 不许再拼一层。"""
    res = _gen(tmp_path, template="chase-personal")

    for month in res.months:
        for text in _walk_strings(month.data):
            assert "POS PURCHASE POS PURCHASE" not in text.upper()
            assert "POS PURCHASE" not in text.upper(), text
            assert "CHECKCARD" not in text.upper(), text


def test_card_merchant_is_a_brand_name(tmp_path, monkeypatch):
    """卡行商户名必须来自品牌池，且不含调研标题词（average/prices/summary/restaurants）。"""
    monkeypatch.setenv("BILLGEN_SCHEDULER", "model")  # 这条测的是模型逐行生成的老链路

    res = _gen(tmp_path, template="chase-personal")
    brands = _brands()
    noise = re.compile(
        r"\b(AVERAGE|AVG|PRICES|PRICE|SUMMARY|RESTAURANTS|RESTAURANT|CHEAPEST|"
        r"COST OF LIVING|HOW MUCH|DEALS|COUPONS|NEAR ME|REVIEWS)\b"
    )
    merchants = [
        name for month in res.months for name in _card_merchants(month.data)
    ]
    assert merchants, "本测试依赖至少一条卡行"
    for name in merchants:
        assert not noise.search(name.upper()), name
        assert any(name.upper().startswith(brand) for brand in brands), name
    # 同一 seed 下同一商户 key 只对应一个品牌（不能一单一个店名）
    assert len(set(merchants)) < len(merchants), merchants


def test_delivered_data_has_no_sample_watermark(tmp_path):
    """交付 JSON 的 watermark 必须是 false，否则出片会印 S A M P L E。"""
    res = _gen(tmp_path, template="chase-personal")

    for month in res.months:
        assert month.data["watermark"] is False, month.month
        assert not month.data.get("encrypt"), month.month
    assert not any("SAMPLE" in text.upper() for month in res.months for text in _walk_strings(month.data))


def test_merchant_names_are_stable_across_runs(tmp_path):
    """同一个 seed 两次跑出的商户名一致；换 seed 允许不同。"""
    first = _gen(tmp_path / "a", template="chase-personal", seed="fix-1")
    second = _gen(tmp_path / "b", template="chase-personal", seed="fix-1")
    for left, right in zip(first.months, second.months, strict=False):
        assert _card_merchants(left.data) == _card_merchants(right.data)


def test_build_ledger_watermark_and_check_numbers(tmp_path):
    """账本本身就不带水印；支票行必须有票号（否则 Chase 印不出 ``Check Paid 1110``）。"""
    res = _gen(tmp_path, template="chase-personal")
    norm = node_normalize(PROFILE, template="chase-personal", months=MONTHS, seed="fix-1")
    check_row = {
        "date": date(2026, 9, 2),
        "posted": date(2026, 9, 2),
        "type": "CHECK",
        "direction": "debit",
        "amount_cents": 158000,
        "description": "GREYSTAR MGMT",
        "memo": "Rent payment",
        "merchant": None,
        "reference": "1234567890",
    }
    ledger = build_ledger(norm, PROFILE, _model(), [check_row], template="chase-personal")

    assert ledger.watermark is False
    assert ledger.rows[0].check_no and ledger.rows[0].check_no.isdigit()
    assert len(ledger.rows[0].check_no) == 4
    assert res.months[0].data["watermark"] is False


def test_clean_merchant_scrubs_research_titles():
    """兜底路径：搜索标题片段要洗成人话，且不超过 18 字符。"""
    from billgen.gen.pipeline import MERCHANT_MAX_CHARS, _clean_merchant

    for raw in (
        "GAS PRICES BY STATION NEAR ME",
        "AVERAGE GROCERY PRICES AUSTIN",
        "PRESCRIPTION PRICES SUMMARY",
        "RESTAURANTS IN AUSTIN TX",
        "Shell Station 4412 Austin TX",
    ):
        cleaned = _clean_merchant(raw)
        # 整句都是标题词时洗成空串是对的：调用方随即落回品牌池。
        assert len(cleaned) <= MERCHANT_MAX_CHARS, cleaned
        assert not re.search(r"\b(AVERAGE|PRICES|SUMMARY|RESTAURANTS|COSTS)\b", cleaned), cleaned
        # 洗空了或只剩残片 → 最终商户名来自品牌池；能留下人话时也不许带标题词。
        brand = pipeline._merchant_for("no-such-category", key=raw, seed="fix-1", fallback=raw)
        assert brand in MERCHANT_EXAMPLES["retail"] or not re.search(
            r"\b(AVERAGE|PRICES|SUMMARY|RESTAURANTS|COSTS|STATION)\b", brand
        ), brand
    assert _clean_merchant("Shell Station 4412 Austin TX")


def test_merchant_examples_cover_intents():
    """决策 14 的池子：21 个类别、每类 6–10 个品牌、全部短名大写。"""
    assert len(P.MERCHANT_EXAMPLES) >= 21
    for category, pool in P.MERCHANT_EXAMPLES.items():
        assert 6 <= len(pool) <= 10, category
        for brand in pool:
            assert brand == brand.upper(), brand
            assert 1 < len(brand) <= 18, brand


def test_research_title_never_becomes_merchant():
    """调研标题（``Gas Prices By Station``）洗成商户名时不许留下标题词。"""
    noise_words = ("AVERAGE", "AVG", "PRICES", "PRICE", "SUMMARY", "RESTAURANTS", "CHEAPEST")
    titles = (
        "Gas Prices By Station",
        "Average Grocery Costs In Austin",
        "Prescription Prices At Pharmacies",
        "Restaurants Summary 2026",
    )
    for title in titles:
        cleaned = pipeline._clean_merchant(title)
        assert len(cleaned) <= pipeline.MERCHANT_MAX_CHARS, cleaned
        assert not any(word in cleaned for word in noise_words), cleaned
        assert not cleaned.split()[-1:] or cleaned.split()[-1] not in {"IN", "AT", "A", "AN", "OF"}
        # 洗成残片（"GAS"）或洗空时，最终商户名必须落回真品牌池，绝不上标题。
        brand = pipeline._merchant_for("no-such-category", key=title, seed="fix-1", fallback=title)
        assert brand in MERCHANT_EXAMPLES["retail"], brand
        assert not any(word in brand for word in noise_words), brand


def test_merchant_pool_is_stable_and_branded():
    """同一 seed 挑出的商户名完全一致，且都来自品牌池（大写、不带城市）。"""
    first = pipeline._merchant_for("groceries", key="fact-1", seed="fix-1")
    second = pipeline._merchant_for("groceries", key="fact-1", seed="fix-1")
    other = pipeline._merchant_for("groceries", key="fact-2", seed="fix-1")
    assert first == second
    assert first in MERCHANT_EXAMPLES["groceries"]
    assert other in MERCHANT_EXAMPLES["groceries"]
    assert all(name == name.upper() for name in MERCHANT_EXAMPLES["groceries"])


def test_regional_billers_follow_the_profile_state():
    """Austin 的账户不该在电费里出现 Con Edison / PG&E（跨辖区一眼假）。"""
    texas = merchant_pool("utilities", state="TX")
    assert "AUSTIN ENERGY" in texas
    assert not any(name in texas for name in ("CON EDISON", "PG&E", "SCE"))
    assert "CON EDISON" in merchant_pool("utilities", state="NY")
    assert merchant_pool("utilities", state="ZZ") == merchant_pool("utilities")


def test_bill_entries_drop_invented_labels():
    """ACH 账单描述 = 品牌 + 引用号；模型编的 ``UTIL BILL`` 之类说明不进描述。"""
    row = {
        "type": "ACH",
        "merchant_key": "utility-1",
        "base_description": "CON EDISON",
        "category": "utilities",
        "city": "Austin",
        "state": "TX",
    }
    norm = {"template": "chase-personal", "seed": "fix-1"}
    assert pipeline._final_description(row, "CON EDISON UTIL BILL", norm) == "CON EDISON"
    assert pipeline._final_description(row, "PPD ID:8837", norm).startswith("CON EDISON PPD ID")


def test_pos_rows_never_use_the_research_title_as_merchant():
    """POS 行的商户名只来自品牌池；事实的 name（搜索标题）不参与。"""
    contract = " 2026-10-30T00:00:00Z"
    norm = node_normalize(PROFILE, template="chase-personal", months=["2026-09"], seed="fix-1")
    model = deepcopy(_model())
    model["discretionaryPool"] = [
        {
            "factId": "f9",
            "weight": 1.0,
            "perMonth": {"min": 2, "max": 2},
            "ticketCents": {"min": 2645, "max": 2645},
            "pricing": "basket",
            "taxable": True,
            "tip": False,
            "account": "checking",
            "weekendBias": 0.2,
            "location": "online",
        }
    ]
    facts = [
        {
            "id": "f9",
            "name": "Best Graphic Design Services In Austin",
            "category": "graphic_design",
            "city": "Austin",
            "state": "TX",
            "national": False,
        }
    ]
    rows = node_schedule(norm, model, facts, seed="fix-1")
    pos = [row for row in rows if row["type"] == "POS"]
    assert pos
    pool = {name for names in MERCHANT_EXAMPLES.values() for name in names}
    for row in pos:
        assert row["base_description"] in pool, row["base_description"]
        assert "GRAPHIC" not in row["base_description"]
        assert "DESIGN" not in row["base_description"]


def test_two_electricity_bills_are_flagged_by_the_self_check():
    """同一个月的两笔电费要被自检抓出来，模型才有机会改。"""
    norm = node_normalize(PROFILE, template="chase-personal", months=["2026-09"], seed="dup-1")
    model = deepcopy(_model())
    model["recurring"] = list(model["recurring"]) + [
        {
            "id": "rec-util-2",
            "label": "Pacific Gas and Electric",
            "kind": "utility",
            "dayRule": {"kind": "dayOfMonth", "day": 18},
            "amount": {"kind": "fixed", "cents": 9400},
            "account": "checking",
            "priority": 2,
            "factId": "",
            "txnType": "ACH",
        }
    ]
    issues = pipeline._model_issues(model, [], norm)
    assert any("同月同类只能一笔" in issue for issue in issues), issues


def test_schedule_keeps_only_one_bill_per_family():
    """就算模型坚持给两笔电费，调度层也只落一笔。"""
    norm = node_normalize(PROFILE, template="chase-personal", months=["2026-09"], seed="dup-2")
    model = deepcopy(_model())
    model["recurring"] = list(model["recurring"]) + [
        {
            "id": "rec-util-2",
            "label": "Pacific Gas and Electric",
            "kind": "utility",
            "dayRule": {"kind": "dayOfMonth", "day": 18},
            "amount": {"kind": "fixed", "cents": 9400},
            "account": "checking",
            "priority": 2,
            "factId": "",
            "txnType": "ACH",
        }
    ]
    rows = node_schedule(norm, model, [], seed="dup-2")
    utilities = [row for row in rows if "ELECTRIC" in row["base_description"].upper()]
    assert len(utilities) == 1, [row["base_description"] for row in utilities]
    rents = [row for row in rows if int(row["amount_cents"]) == 158000]
    assert len(rents) == 1, [row["base_description"] for row in rents]
