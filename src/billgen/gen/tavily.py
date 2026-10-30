"""Tavily 检索客户端（Node 侧 ``flow/packages/core/src/research.ts`` 的 ``createTavilyClient``）。

与 Node 版的一致之处：真实检索、不退化成空结果、429/5xx 退避重试、错误里不出现 Key。
不同之处：不经过 ``@langchain/tavily``，直接 POST ``https://api.tavily.com/search``，
``api_key`` 放在请求**体**里（Tavily 的 HTTP 接口就是这么收的，Node 版由 LangChain 代发，
落到线上也是同一形状）。

额度是**软上限**（见 ``docs/DECISIONS.md`` 决策 2 / 13）：调研只给账单找外部依据，
少一个品类不该拖死整单。超过 ``max_queries`` 抛 :class:`TavilyQuotaExceeded`，
由调用方降级（跳过这条查询、继续生成）；这里既不静默返回空列表——「没查到」和
「没额度」在生成逻辑里是两件事——也不中断整单。

HTTP 传输层协议与 :mod:`billgen.gen.llm` 相同：
``http.request(method, url, headers, data, timeout) -> (status, body_bytes)``。
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Any

from . import llm as _llm
from .config import LlmConfig

__all__ = [
    "ENDPOINT",
    "TavilyClient",
    "TavilyError",
    "TavilyQuotaExceeded",
    "TavilyResult",
]

#: Tavily 的搜索端点（官方 HTTP 接口，不随配置变化）。
ENDPOINT = "https://api.tavily.com/search"


class TavilyError(RuntimeError):
    """检索失败（HTTP 错误、超时、响应不是 JSON、上游报错）。"""


class TavilyQuotaExceeded(TavilyError):
    """本地软配额用完：这条查询没发出去，调用方应降级而不是终止任务。

    继承 :class:`TavilyError` 是为了「顺手 catch 全部检索失败」也能兜住；
    要区分降级时请先 ``except TavilyQuotaExceeded``。
    """


@dataclass(slots=True)
class TavilyResult:
    """一条检索结果：``content`` 是 Tavily 给出的摘要正文，够模型抽事实了。"""

    query: str
    url: str
    title: str
    content: str


class TavilyClient:
    """一个任务一个客户端；``calls`` 是本客户端已经花掉的查询数。"""

    def __init__(self, cfg: LlmConfig, *, http: Any = None, max_queries: int = 40) -> None:
        key = (cfg.tavily_key or "").strip()
        if not key:
            raise TavilyError("缺少 Tavily Key：请填 tavily_key 或设置 TAVILY_API_KEY")
        self.cfg = cfg
        self.max_queries = max(0, int(max_queries))
        self.calls = 0
        self._http = http if http is not None else _llm._UrllibHttp()
        self._rng = random.Random()

    # ---- 公开接口 -------------------------------------------------------
    def search(
        self,
        query: str,
        *,
        max_results: int = 3,
        depth: str = "basic",
    ) -> list[TavilyResult]:
        """检索一次，返回若干条结果（上游没有结果时返回空列表）。

        配额在发请求**之前**检查：``calls`` 记的是「花掉的查询」，
        失败重试不重复计数（一次逻辑检索就是一次额度）。
        """
        text = str(query or "").strip()
        if not text:
            raise TavilyError("Tavily 查询为空")
        if self.calls >= self.max_queries:
            raise TavilyQuotaExceeded(
                f"Tavily 查询额度已用完（上限 {self.max_queries} 次），跳过「{_llm._clip(text, 60)}」"
            )
        self.calls += 1
        data = self._request(text, max_results=int(max_results), depth=str(depth))
        return _parse_results(data, text, self.cfg.tavily_key)

    # ---- 内部 -----------------------------------------------------------
    def _request(self, query: str, *, max_results: int, depth: str) -> str:
        payload = {
            "api_key": self.cfg.tavily_key,
            "query": query,
            "max_results": max_results,
            "search_depth": depth,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        seconds = float(self.cfg.timeout)
        retries = max(0, int(self.cfg.max_retries))
        attempt = 0

        while True:
            try:
                status, raw = self._http.request("POST", ENDPOINT, headers, body, seconds)
            except TimeoutError:
                error = TavilyError(f"Tavily 搜索超时（{seconds:.0f}s）")
                retryable = True
            except OSError as exc:
                error = TavilyError(f"Tavily 连不上：{_llm._redact(exc, self.cfg.tavily_key)}")
                retryable = True
            else:
                text = _llm._decode(raw)
                if status == 200:
                    return text
                error = TavilyError(
                    f"Tavily 搜索失败（HTTP {status}）："
                    f"{_llm._redact(_llm._clip(text), self.cfg.tavily_key)}"
                )
                retryable = status == 429 or status >= 500

            if retryable and attempt < retries:
                _llm._sleep(_llm._backoff_delay(attempt, self._rng))
                attempt += 1
                continue
            raise error


def _parse_results(text: str, query: str, api_key: str) -> list[TavilyResult]:
    """解析响应体；``{"error": ...}`` 也在此转成 :class:`TavilyError`。"""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise TavilyError(
            f"Tavily 返回的不是 JSON：{_llm._redact(_llm._clip(text), api_key)}"
        ) from exc
    if not isinstance(data, dict):
        raise TavilyError(f"Tavily 返回的 JSON 顶层是 {type(data).__name__}，应为对象")
    error = data.get("error")
    if isinstance(error, str) and error.strip():
        raise TavilyError(f"Tavily 报错：{_llm._redact(_llm._clip(error), api_key)}")
    rows = data.get("results")
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise TavilyError(f"Tavily 响应里的 results 不是数组：{type(rows).__name__}")
    results: list[TavilyResult] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        results.append(
            TavilyResult(
                query=query,
                url=str(row.get("url") or "").strip(),
                title=str(row.get("title") or "").strip(),
                content=str(row.get("content") or "").strip(),
            )
        )
    return results
