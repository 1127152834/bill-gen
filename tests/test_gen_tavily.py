"""Tavily 检索客户端：全离线（假传输层），覆盖解析、软配额、重试与 Key 不外泄。"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from billgen.gen import llm as llm_mod
from billgen.gen.config import LlmConfig
from billgen.gen.tavily import ENDPOINT, TavilyClient, TavilyError, TavilyQuotaExceeded

TAVILY_KEY = "tvly-secret-999"


@pytest.fixture
def sleeps(monkeypatch):
    recorded: list[float] = []
    monkeypatch.setattr(llm_mod, "_sleep", recorded.append)
    return recorded


class FakeHttp:
    """按顺序回放响应的假 http；协议同 :mod:`billgen.gen.llm` 的模块文档。"""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests: list[dict] = []

    def request(self, method, url, headers, data, timeout):
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "payload": json.loads(data.decode("utf-8")) if data else None,
                "timeout": timeout,
            }
        )
        if not self.replies:
            raise AssertionError("假 http 的响应已经用完了：代码多请求了一次")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        status, body = reply
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        return status, body


def _cfg(**overrides) -> LlmConfig:
    cfg = LlmConfig(
        base_url="https://llm.example/v1",
        api_key="sk-test-abcdef",
        model="model-x",
        tavily_key=TAVILY_KEY,
        timeout=5.0,
        max_retries=2,
    )
    return replace(cfg, **overrides) if overrides else cfg


def _results(*rows: tuple[str, str, str]) -> dict[str, Any]:
    return {
        "query": "ignored",
        "results": [
            {"url": url, "title": title, "content": content, "score": 0.9}
            for url, title, content in rows
        ],
    }


def _client(http: FakeHttp, **overrides) -> TavilyClient:
    kwargs = {"max_queries": overrides.pop("max_queries", 40)}
    return TavilyClient(_cfg(**overrides), http=http, **kwargs)


# ---- 正常解析 --------------------------------------------------------------
def test_search_posts_key_in_body_and_parses_results():
    http = FakeHttp((200, _results(("https://a.example/x", "A 店", "月费 12.99"))))
    client = _client(http)

    hits = client.search("A 店 月费", max_results=2, depth="advanced")

    assert [(hit.query, hit.url, hit.title, hit.content) for hit in hits] == [
        ("A 店 月费", "https://a.example/x", "A 店", "月费 12.99")
    ]
    request = http.requests[0]
    assert request["method"] == "POST"
    assert request["url"] == ENDPOINT
    assert request["payload"]["api_key"] == TAVILY_KEY  # 既有 Node 实现就是放在 body 里
    assert request["payload"]["query"] == "A 店 月费"
    assert request["payload"]["max_results"] == 2
    assert request["payload"]["search_depth"] == "advanced"
    assert client.calls == 1


def test_search_without_results_returns_empty_list():
    http = FakeHttp((200, {"answer": "没有找到"}), (200, {"results": []}))
    client = _client(http)

    assert client.search("冷门店") == []
    assert client.search("冷门店 2") == []
    assert client.calls == 2


# ---- 软配额 ----------------------------------------------------------------
def test_quota_exceeded_does_not_touch_the_network():
    http = FakeHttp((200, _results()), (200, _results()))
    client = _client(http, max_queries=2)

    assert client.search("一") == []
    assert client.search("二") == []

    with pytest.raises(TavilyQuotaExceeded) as excinfo:
        client.search("三")

    assert len(http.requests) == 2  # 第三次根本没发出去
    assert "上限 2 次" in str(excinfo.value)
    assert client.calls == 2


# ---- 失败与重试 ------------------------------------------------------------
def test_5xx_is_retried_then_succeeds(sleeps):
    http = FakeHttp((503, "upstream busy"), (200, _results(("https://b.example", "B", "内容"))))
    client = _client(http)

    hits = client.search("B 店")

    assert [hit.url for hit in hits] == ["https://b.example"]
    assert len(http.requests) == 2
    assert len(sleeps) == 1 and 0.6 <= sleeps[0] <= 1.0
    assert client.calls == 1  # 一次逻辑检索只花一次额度，重试不重复计


def test_persistent_5xx_error_never_leaks_the_key(sleeps):
    body = f'{{"detail":"bad key {TAVILY_KEY}"}}'
    http = FakeHttp((500, body), (500, body), (500, body))
    client = _client(http)

    with pytest.raises(TavilyError) as excinfo:
        client.search("C 店")

    message = str(excinfo.value)
    assert len(http.requests) == 3  # 1 次 + max_retries(2)
    assert len(sleeps) == 2
    assert "HTTP 500" in message
    assert TAVILY_KEY not in message
    assert "bad key" in message


def test_4xx_is_not_retried(sleeps):
    http = FakeHttp((400, '{"detail":"invalid query"}'))
    client = _client(http)

    with pytest.raises(TavilyError) as excinfo:
        client.search("D 店")

    assert len(http.requests) == 1
    assert sleeps == []
    assert "HTTP 400" in str(excinfo.value)


def test_timeout_is_wrapped():
    http = FakeHttp(TimeoutError("timed out"))
    client = _client(http, max_retries=0)

    with pytest.raises(TavilyError) as excinfo:
        client.search("E 店")

    message = str(excinfo.value)
    assert "超时" in message or "连不上" in message
    assert TAVILY_KEY not in message
    assert len(http.requests) == 1


def test_error_field_in_200_body_is_raised_and_redacted():
    http = FakeHttp((200, {"error": f"usage limit reached for {TAVILY_KEY}"}))
    client = _client(http)

    with pytest.raises(TavilyError) as excinfo:
        client.search("F 店")

    message = str(excinfo.value)
    assert "Tavily 报错" in message
    assert "usage limit" in message
    assert TAVILY_KEY not in message


def test_missing_tavily_key_is_reported_up_front():
    with pytest.raises(TavilyError) as excinfo:
        TavilyClient(_cfg(tavily_key="  "), http=FakeHttp())
    assert "Tavily Key" in str(excinfo.value)
