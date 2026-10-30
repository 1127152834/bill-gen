"""模型与 Tavily 的连通性探测。

对话框里的凭据填完就点「保存并重启」，之前没人验证过它通不通：地址写错、Key 过期、
模型名不对，都要等到真正生成账单才炸，而且报错藏在子进程日志里。这里拿**还没保存**的
值打一次最小请求（模型一次 16 token 的结构化补全、Tavily 一次只取 1 条结果的搜索），
把上游状态码和报错正文原样交给前端；前端两项都通过才允许保存。

探测只用 ``urllib``：容器里没装 requests，探测不该为了一个按钮多一个依赖。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from .settings import HunterSettings, SettingsError, apply_patch

# 单独留一个句柄：测试里替换它就只是「假装上游」，不会把测试自己发的 HTTP 请求也换掉。
_urlopen = urllib.request.urlopen

TIMEOUT_SECONDS = 20.0
USER_AGENT = "billgen-hunter-probe/1.0"
TAVILY_ENDPOINT = "https://api.tavily.com/search"

_JSON_SCHEMA_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "probe",
        "schema": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        "strict": True,
    },
}


class ProbeError(RuntimeError):
    """探测参数本身不合法（缺地址、缺 Key），与上游失败分开报。"""


def _clip(text: str, limit: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", USER_AGENT)
    for name, value in headers.items():
        request.add_header(name, value)
    try:
        with _urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return int(response.status), response.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # 4xx/5xx 也是「通」，只是凭据或参数不对
        detail = ""
        try:
            detail = exc.read(20_000).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - 读不到正文就只报状态码
            detail = ""
        return int(exc.code), detail
    except urllib.error.URLError as exc:
        raise ProbeError(f"连不上 {url}：{exc.reason}") from exc
    except TimeoutError as exc:
        raise ProbeError(f"连接 {url} 超时（{TIMEOUT_SECONDS:.0f}s）") from exc


def _merged(current: HunterSettings, patch: dict[str, Any]) -> HunterSettings:
    """用「已保存值 + 表单里没保存的值」拼出要探测的配置。空串表示沿用。"""
    try:
        return apply_patch(current, patch)
    except SettingsError as exc:
        raise ProbeError(str(exc)) from exc


def _completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _llm_payloads(model: str, structured_mode: str) -> list[dict[str, Any]]:
    """先按用户选的结构化模式试一次，再退一次「什么花活都不带」的纯对话。

    代理/自建网关常常认 ``json_object`` 不认 ``json_schema``（反之亦然），探测的目的是
    确认「地址 + Key + 模型」通不通，不该被 response_format 的方言差异卡住。
    """
    prompt = '连通性测试：只回复 {"ok": true}'
    base: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 16,
        "temperature": 0,
        "stream": False,
    }
    variants: list[dict[str, Any]] = []
    if structured_mode == "json_schema":
        variants.append({**base, "response_format": _JSON_SCHEMA_FORMAT})
        variants.append({**base, "response_format": {"type": "json_object"}})
    elif structured_mode == "json_mode":
        variants.append({**base, "response_format": {"type": "json_object"}})
    else:  # function_calling
        variants.append(
            {
                **base,
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "probe",
                            "description": "连通性测试",
                            "parameters": {
                                "type": "object",
                                "properties": {"ok": {"type": "boolean"}},
                                "required": ["ok"],
                            },
                        },
                    }
                ],
                "tool_choice": {"type": "function", "function": {"name": "probe"}},
            }
        )
    variants.append(base)
    return variants


def probe_llm(current: HunterSettings, patch: dict[str, Any] | None = None) -> dict[str, Any]:
    """打一次最小补全，确认模型接入可用。"""
    merged = _merged(current, patch or {})
    if not merged.base_url:
        raise ProbeError("还没填模型接口地址")
    if not merged.has_api_key:
        raise ProbeError("还没填模型 API Key")
    url = _completions_url(merged.base_url)
    headers = {"Authorization": f"Bearer {merged.api_key}"}
    started = time.monotonic()
    attempts: list[str] = []
    for payload in _llm_payloads(merged.model, merged.structured_mode):
        status, body = _post_json(url, payload, headers)
        elapsed = int((time.monotonic() - started) * 1000)
        if status == 200:
            sample = _reply_text(body)
            return {
                "ok": True,
                "latency_ms": elapsed,
                "endpoint": url,
                "model": merged.model,
                "detail": f"{merged.model} 已连通，回复：{sample or '（空）'}",
            }
        attempts.append(_upstream_error(status, body))
        if status not in (400, 422):
            break
    return {
        "ok": False,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "endpoint": url,
        "model": merged.model,
        "error": attempts[-1] if attempts else "模型没有返回可用的响应",
        "attempts": attempts,
    }


def _reply_text(body: str) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return _clip(body, 120)
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return _clip(body, 120)
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return _clip(content, 120)
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        arguments = (tool_calls[0].get("function") or {}).get("arguments")
        if isinstance(arguments, str):
            return _clip(arguments, 120)
    return "（回复里没有正文）"


def _upstream_error(status: int, body: str) -> str:
    hint = ""
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            hint = str(error.get("message") or error.get("type") or "")
        elif isinstance(error, str):
            hint = error
        hint = hint or str(data.get("message") or "")
    hint = hint or _clip(body, 200)
    if status in (401, 403):
        return f"鉴权失败（HTTP {status}）：{hint}"
    if status == 404:
        return f"地址不对（HTTP 404）：{hint or '检查接口地址是不是漏了 /v1'}"
    return f"上游返回 HTTP {status}：{hint}"


def probe_tavily(current: HunterSettings, patch: dict[str, Any] | None = None) -> dict[str, Any]:
    """打一次只取 1 条结果的搜索，确认 Tavily Key 可用。"""
    merged = _merged(current, patch or {})
    if not merged.has_tavily_key:
        raise ProbeError("还没填 Tavily Key")
    payload = {
        "api_key": merged.tavily_api_key,
        "query": "billgen connectivity probe",
        "max_results": 1,
        "search_depth": "basic",
        "include_answer": False,
    }
    headers = {"Authorization": f"Bearer {merged.tavily_api_key}"}
    started = time.monotonic()
    status, body = _post_json(TAVILY_ENDPOINT, payload, headers)
    elapsed = int((time.monotonic() - started) * 1000)
    if status == 200:
        count = 0
        try:
            results = json.loads(body).get("results")
            count = len(results) if isinstance(results, list) else 0
        except ValueError:
            count = 0
        return {
            "ok": True,
            "latency_ms": elapsed,
            "endpoint": TAVILY_ENDPOINT,
            "detail": f"Tavily 已连通，返回 {count} 条结果",
        }
    return {
        "ok": False,
        "latency_ms": elapsed,
        "endpoint": TAVILY_ENDPOINT,
        "error": _upstream_error(status, body),
    }
