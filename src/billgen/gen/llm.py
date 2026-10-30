"""OpenAI 兼容的 Chat Completions 客户端，带 JSON 修复重试与 token 记账。

这是生成引擎自己的模型客户端（早期 Node 版走 LangChain 的 ``withStructuredOutput``，
有 json_schema / function_calling / json_mode 三条路）；这边只留一条：

1. 把 JSON Schema 以**文本**塞进 system 提示（端点未必支持 ``response_format``，
   也未必支持 ``strict`` 的 json_schema，见 :func:`json_chat` 的说明）；
2. 模型输出交本地 ``json.loads`` 校验；
3. 不合法就把「上一次原始输出 + 解析错误」回粘成一条 user 消息再要一次（最多 2 次）。

HTTP 只用 ``urllib``（容器里没有 requests，标准库够用）。
测试通过 ``http`` 注入假传输层，协议是一个方法：

    http.request(method: str, url: str, headers: dict[str, str],
                 data: bytes, timeout: float) -> tuple[int, bytes]

* 返回 ``(状态码, 响应体)``，4xx/5xx 也照常返回，不抛异常；
* 网络层失败请抛 ``TimeoutError``（超时）或 ``OSError``（连不上、连接被重置）。

重试：429 / 500 / 502 / 503 / 504、超时、连接错误按 0.8s、1.6s、3.2s 指数退避（带 jitter），
其它 4xx 立即抛——那是请求本身写错了，再试一百次也一样。

错误信息面向用户：带状态码与前 200 字响应，**永远不带 api_key**（见 :func:`_redact`）。
"""

from __future__ import annotations

import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import LlmConfig

__all__ = [
    "RETRYABLE_STATUS",
    "LlmBadJson",
    "LlmClient",
    "LlmError",
    "LlmRefused",
    "drop_code_fence",
]

#: 值得重试的上游状态码；其余非 200 一律直接失败。
RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})

#: 退避基数与上限（秒）：0.8 → 1.6 → 3.2，之后封顶 3.2。
BACKOFF_BASE = 0.8
BACKOFF_CAP = 3.2

USER_AGENT = "billgen-gen/1.0"

#: ``json_chat`` 解析失败后再要几次。
JSON_REPAIR_ROUNDS = 2

_FENCE_OPEN = re.compile(r"^```[A-Za-z0-9_+-]*[ \t]*\r?\n?")

# 单独留句柄：测试里替换它就只是「不睡觉」，不影响别的模块。
_sleep = time.sleep


class LlmError(RuntimeError):
    """模型调用失败（HTTP 错误、超时、连不上、响应不是 JSON）。"""


class LlmRefused(LlmError):
    """模型明确拒绝，或回了空内容——重试没有意义，调用方该换提示词。"""


class LlmBadJson(LlmError):
    """模型反复给不出合法 JSON 对象（修复重试用完）。"""


class _UrllibHttp:
    """默认传输层：urllib。4xx/5xx 转成 ``(状态码, 正文)`` 返回，交给上层决定重试。"""

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        data: bytes,
        timeout: float,
    ) -> tuple[int, bytes]:
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("User-Agent", USER_AGENT)
        for name, value in headers.items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return int(response.status), response.read()
        except urllib.error.HTTPError as exc:  # 4xx/5xx 也是「有响应」
            try:
                body = exc.read()
            except OSError:
                body = b""
            return int(exc.code), body


def _clip(text: Any, limit: int = 200) -> str:
    """压成一行并截断：报错里的响应体不该把日志刷屏。"""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _redact(text: Any, api_key: str) -> str:
    """把 Key 及其常见形状抹成 ``***``：错误信息会进页面和日志。"""
    out = str(text)
    if api_key:
        out = out.replace(api_key, "***")
    out = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer ***", out)
    out = re.sub(r"\bsk-[A-Za-z0-9_-]{6,}", "sk-***", out)
    out = re.sub(r"\btvly-[A-Za-z0-9_-]{6,}", "tvly-***", out)
    return out


def _host(url: str) -> str:
    """报错里只留 scheme://host，不把可能带 token 的路径抄进日志。"""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.netloc else url


def _backoff_delay(attempt: int, rng: random.Random) -> float:
    """第 ``attempt`` 次重试前的等待（attempt 从 0 起）：指数退避 + ±15% jitter。

    jitter 的作用是批量任务同时被限流时别一起醒过来再撞一次。
    """
    base = min(BACKOFF_BASE * (2**attempt), BACKOFF_CAP)
    return round(base * (0.85 + 0.3 * rng.random()), 3)


def drop_code_fence(text: str) -> str:
    """剥掉 ``` / ```json 围栏并去掉首尾空白。

    ``json`` 之外的标签（```JSON、```js）一并剥掉：模型偶尔会写错标签，
    但内容还是 JSON，没必要为此多花一次调用。
    """
    body = (text or "").strip()
    if body.startswith("```"):
        body = _FENCE_OPEN.sub("", body, count=1)
        stripped = body.rstrip()
        if stripped.endswith("```"):
            body = stripped[:-3]
    return body.strip()


def _content_text(content: Any) -> str:
    """兼容 ``content`` 为字符串或「分段数组」（部分网关会回多段）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts)
    return ""


def _completions_url(base_url: str) -> str:
    """拼出补全地址。

    按约定默认是 ``{base_url}/v1/chat/completions``；但 base_url 本身已经写到
    ``/v1``（内部网关常见写法）时不再叠一层，否则会得到 ``/v1/v1/...`` 的 404。
    """
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def _json_object(text: str, what: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise LlmError(f"{what}返回的不是 JSON（{exc}）：{_clip(text)}") from exc
    if not isinstance(data, dict):
        raise LlmError(f"{what}返回的 JSON 顶层是 {type(data).__name__}，应为对象：{_clip(text)}")
    return data


class LlmClient:
    """一个配置对应一个客户端；``usage`` 是跨调用累加的账单。

    ``usage`` 三个键：``prompt_tokens`` / ``completion_tokens`` / ``calls``。
    ``calls`` 只数**真正拿到 200 的请求**（429 重试不算一次计费调用），
    与 Node 版「每次尝试都 chargeLlm」不同——那边 charge 的是任务预算，
    这边记的是上游账单，混在一起会让预算对不上账。
    """

    def __init__(
        self,
        cfg: LlmConfig,
        *,
        http: Any = None,
        rng: random.Random | None = None,
    ) -> None:
        self.cfg = cfg
        self._http = http if http is not None else _UrllibHttp()
        self._rng = rng if rng is not None else random.Random()
        self.usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}

    # ---- 公开接口 -------------------------------------------------------
    def chat(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.2,
        max_tokens: int = 4000,
        timeout: float | None = None,
    ) -> str:
        """一次对话补全，返回正文文本。

        ``timeout`` 为空时用配置里的值。空回复/拒答抛 :class:`LlmRefused`，
        传输或状态码失败抛 :class:`LlmError`。
        """
        if not messages:
            raise LlmError("模型调用的消息列表为空")
        payload = {
            "model": self.cfg.model,
            "messages": [dict(message) for message in messages],
            "temperature": temperature,
            "max_tokens": int(max_tokens),
            "stream": False,
        }
        data = self._request(payload, timeout=timeout, what="模型调用")
        return self._reply_text(data)

    def json_chat(
        self,
        messages: list[dict],
        *,
        schema: dict | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4000,
        timeout: float | None = None,
    ) -> dict:
        """要一个 JSON 对象；解析失败时带着错误再问一次（最多 :data:`JSON_REPAIR_ROUNDS` 次）。

        ``schema`` 只以文本形式进 system 提示，不发 ``response_format``：
        这个端点未必支持它（Node 版因此配了 json_schema/function_calling/json_mode 三条路），
        文本 Schema + 本地校验 + 一次修复，在所有 OpenAI 兼容实现上都能跑。
        """
        base = self._with_json_hint(messages, schema)
        attempt = 0
        raw = ""
        problem = ""
        while True:
            raw = self.chat(
                base,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            body = drop_code_fence(raw)
            try:
                data = json.loads(body)
            except ValueError as exc:
                problem = f"不是合法 JSON（{exc}）"
            else:
                if isinstance(data, dict):
                    return data
                problem = f"顶层是 {type(data).__name__}，必须是 JSON 对象"
            if attempt >= JSON_REPAIR_ROUNDS:
                break
            attempt += 1
            base = [*self._with_json_hint(messages, schema), _repair_message(raw, problem)]
        raise LlmBadJson(
            f"模型连续 {attempt + 1} 次没有给出合法 JSON 对象：{problem}；"
            f"最后一次输出：{_redact(_clip(raw, 300), self.cfg.api_key)}"
        )

    # ---- 内部 -----------------------------------------------------------
    def _request(self, payload: dict[str, Any], *, timeout: float | None, what: str) -> dict:
        """发一次补全请求；可重试的失败按退避重来，最后仍失败则抛 :class:`LlmError`。"""
        url = _completions_url(self.cfg.base_url)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.cfg.api_key}",
        }
        seconds = float(self.cfg.timeout if timeout is None else timeout)
        retries = max(0, int(self.cfg.max_retries))
        attempt = 0

        while True:
            try:
                status, raw = self._http.request("POST", url, headers, body, seconds)
            except TimeoutError:
                error = LlmError(f"{what}超时（{seconds:.0f}s）：{_host(url)} 没有在时限内响应")
                retryable = True
            except OSError as exc:
                error = LlmError(f"{what}连不上 {_host(url)}：{_redact(exc, self.cfg.api_key)}")
                retryable = True
            else:
                text = _decode(raw)
                if status == 200:
                    data = _json_object(text, what)
                    self._record_usage(data)
                    return data
                error = LlmError(
                    f"{what}失败（HTTP {status}）：{_redact(_clip(text), self.cfg.api_key)}"
                )
                retryable = status in RETRYABLE_STATUS

            if retryable and attempt < retries:
                _sleep(_backoff_delay(attempt, self._rng))
                attempt += 1
                continue
            raise error

    def _record_usage(self, data: dict[str, Any]) -> None:
        usage = data.get("usage")
        if isinstance(usage, dict):
            for name in ("prompt_tokens", "completion_tokens"):
                value = usage.get(name)
                if isinstance(value, int) and not isinstance(value, bool):
                    self.usage[name] += value
        self.usage["calls"] += 1

    def _reply_text(self, data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LlmError(f"模型响应里没有 choices：{_redact(_clip(data), self.cfg.api_key)}")
        choice = choices[0]
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        text = _content_text(message.get("content"))
        if text.strip():
            return text
        refusal = _clip(message.get("refusal") or "", 200)
        if refusal:
            raise LlmRefused(f"模型拒绝回答：{_redact(refusal, self.cfg.api_key)}")
        finish = _clip(choice.get("finish_reason") or "未知", 40)
        raise LlmRefused(f"模型返回空回复（finish_reason={finish}）")

    @staticmethod
    def _with_json_hint(messages: list[dict], schema: dict | None) -> list[dict]:
        """在 system 里说清「只要 JSON 对象」；给了 schema 就把 schema 原文附上。"""
        hint = (
            "只输出一个 JSON 对象：不要 Markdown 代码块（包括 ```json 包裹），"
            "不要解释、不要前后缀文字。"
        )
        if schema is not None:
            hint += "\n必须只输出符合该 JSON Schema 的对象，字段名与类型严格一致：\n" + json.dumps(
                schema, ensure_ascii=False, indent=2
            )
        merged = [dict(message) for message in messages]
        if merged and merged[0].get("role") == "system":
            head = str(merged[0].get("content") or "").strip()
            merged[0]["content"] = f"{head}\n\n{hint}" if head else hint
        else:
            merged.insert(0, {"role": "system", "content": hint})
        return merged


def _decode(raw: Any) -> str:
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8", "replace")
    return "" if raw is None else str(raw)


def _repair_message(previous: str, problem: str) -> dict[str, str]:
    """修复轮的用户消息：把上一次输出和错误一起给模型，比只说「再试一次」有效。"""
    return {
        "role": "user",
        "content": (
            f"你上一次的输出无法当作 JSON 对象使用：{problem}。\n"
            f"上一次输出：\n{_clip(previous, 2000)}\n"
            "请重新输出：只给一个符合要求的 JSON 对象，不要代码块、不要解释。"
        ),
    }
