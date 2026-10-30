"""账单生成器的配置与模型客户端：全离线（假传输层），不连任何网络。

假 ``http`` 的协议见 :mod:`billgen.gen.llm` 模块文档：
``request(method, url, headers, data, timeout) -> (status, body_bytes)``。
"""

from __future__ import annotations

import json
import random
from dataclasses import replace
from pathlib import Path

import pytest

from billgen.gen import llm as llm_mod
from billgen.gen.config import ConfigError, LlmConfig, load_config, save_config
from billgen.gen.llm import (
    LlmBadJson,
    LlmClient,
    LlmError,
    LlmRefused,
    _completions_url,
    drop_code_fence,
)

ENV_NAMES = (
    "BILLGEN_LLM_CONFIG",
    "BILLGEN_LLM_BASE_URL",
    "BILLGEN_LLM_API_KEY",
    "BILLGEN_LLM_MODEL",
    "TAVILY_API_KEY",
)

FILE_FIELDS = {
    "base_url": "https://file.example/v1",
    "api_key": "sk-file-123456",
    "model": "file-model",
    "tavily_key": "tvly-file-123456",
}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """真实环境的 Key 不能漏进断言（尤其「环境变量覆盖」那条）。"""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def sleeps(monkeypatch):
    """重试的等待换成记账：测试不该真的睡 0.8 秒。"""
    recorded: list[float] = []
    monkeypatch.setattr(llm_mod, "_sleep", recorded.append)
    return recorded


class FakeHttp:
    """按顺序回放响应的假 http；请求多过响应就报错（免得重试逻辑偷偷多打）。"""

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


def _completion(content, *, usage=None, finish_reason="stop"):
    data = {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}
    if usage is not None:
        data["usage"] = usage
    return 200, data


def _cfg(**overrides) -> LlmConfig:
    cfg = LlmConfig(
        base_url="https://llm.example/v1",
        api_key="sk-test-abcdef",
        model="model-x",
        tavily_key="tvly-test-abcdef",
        timeout=5.0,
    )
    return replace(cfg, **overrides) if overrides else cfg


def _write_config(path: Path, **overrides) -> Path:
    data = dict(FILE_FIELDS)
    data.update(overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ---- config ----------------------------------------------------------------
def test_load_config_missing_file_names_the_fields(tmp_path: Path):
    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path / "nope.json")
    message = str(excinfo.value)
    assert "缺少模型配置" in message
    assert "base_url/api_key/model/tavily_key" in message
    assert "nope.json" in message


def test_load_config_lists_missing_and_blank_fields(tmp_path: Path):
    path = _write_config(tmp_path / "llm.json", api_key="   ", tavily_key="")
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    message = str(excinfo.value)
    assert "api_key、tavily_key" in message  # 只报真正缺的，不把四个字段都列一遍
    assert "base_url/api_key/model/tavily_key" in message


def test_environment_overrides_file_field_by_field(tmp_path: Path, monkeypatch):
    path = _write_config(tmp_path / "llm.json")
    monkeypatch.setenv("BILLGEN_LLM_MODEL", "env-model")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-env-123456")

    cfg = load_config(path)

    assert cfg.model == "env-model"
    assert cfg.tavily_key == "tvly-env-123456"
    assert cfg.base_url == FILE_FIELDS["base_url"]  # 环境变量没给的字段仍由文件补
    assert cfg.api_key == FILE_FIELDS["api_key"]


def test_full_environment_needs_no_file(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BILLGEN_LLM_BASE_URL", "https://env.example/v1/")
    monkeypatch.setenv("BILLGEN_LLM_API_KEY", "sk-env-123456")
    monkeypatch.setenv("BILLGEN_LLM_MODEL", "env-model")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-env-123456")

    cfg = load_config(tmp_path / "missing" / "llm.json")

    assert cfg.base_url == "https://env.example/v1"  # 尾斜杠被规整
    assert cfg.model == "env-model"
    assert cfg.timeout == 180.0 and cfg.max_retries == 3


def test_save_config_is_private_and_leaves_no_temp_file(tmp_path: Path):
    target = tmp_path / "nested" / "llm.json"
    cfg = _cfg()

    saved = save_config(cfg, target)
    save_config(cfg, target)  # 覆盖一次：原子替换不该留下临时文件

    assert saved == target
    assert target.stat().st_mode & 0o777 == 0o600
    assert [entry.name for entry in target.parent.iterdir()] == ["llm.json"]
    assert load_config(target) == cfg


# ---- llm: chat -------------------------------------------------------------
def test_chat_returns_content_and_posts_openai_shape():
    http = FakeHttp(_completion("你好", usage={"prompt_tokens": 11, "completion_tokens": 7}))
    client = LlmClient(_cfg(), http=http, rng=random.Random(0))

    text = client.chat([{"role": "user", "content": "hi"}], temperature=0.1, max_tokens=64)

    assert text == "你好"
    request = http.requests[0]
    assert request["method"] == "POST"
    assert request["url"] == "https://llm.example/v1/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer sk-test-abcdef"
    assert request["payload"]["model"] == "model-x"
    assert request["payload"]["temperature"] == 0.1
    assert request["payload"]["max_tokens"] == 64
    assert request["payload"]["messages"] == [{"role": "user", "content": "hi"}]
    assert request["timeout"] == 5.0
    assert client.usage == {"prompt_tokens": 11, "completion_tokens": 7, "calls": 1}


def test_chat_retries_429_with_backoff(sleeps):
    http = FakeHttp((429, "rate limited"), _completion("ok"))
    client = LlmClient(_cfg(), http=http, rng=random.Random(0))

    assert client.chat([{"role": "user", "content": "hi"}]) == "ok"

    assert len(http.requests) == 2
    assert len(sleeps) == 1
    assert 0.6 <= sleeps[0] <= 1.0  # 0.8s ±15% jitter
    assert client.usage["calls"] == 1  # 429 不算一次计费调用


def test_chat_does_not_retry_4xx_and_hides_the_key(sleeps):
    body = '{"error":"bad model for key sk-test-abcdef"}'
    http = FakeHttp((400, body))
    client = LlmClient(_cfg(), http=http, rng=random.Random(0))

    with pytest.raises(LlmError) as excinfo:
        client.chat([{"role": "user", "content": "hi"}])

    message = str(excinfo.value)
    assert len(http.requests) == 1
    assert sleeps == []
    assert "HTTP 400" in message
    assert "sk-test-abcdef" not in message
    assert "bad model" in message


def test_chat_wraps_timeout_into_llm_error():
    http = FakeHttp(TimeoutError("timed out"))
    client = LlmClient(_cfg(max_retries=0), http=http, rng=random.Random(0))

    with pytest.raises(LlmError) as excinfo:
        client.chat([{"role": "user", "content": "hi"}], timeout=1.5)

    message = str(excinfo.value)
    assert "模型调用超时" in message
    assert "sk-test-abcdef" not in message
    assert len(http.requests) == 1


def test_chat_empty_reply_raises_refused():
    http = FakeHttp(_completion("   "))
    client = LlmClient(_cfg(), http=http, rng=random.Random(0))

    with pytest.raises(LlmRefused):
        client.chat([{"role": "user", "content": "hi"}])


def test_usage_accumulates_across_calls():
    http = FakeHttp(
        _completion("a", usage={"prompt_tokens": 3, "completion_tokens": 2}),
        _completion("b", usage={"prompt_tokens": 5, "completion_tokens": 1}),
    )
    client = LlmClient(_cfg(), http=http, rng=random.Random(0))

    client.chat([{"role": "user", "content": "1"}])
    client.chat([{"role": "user", "content": "2"}])

    assert client.usage == {"prompt_tokens": 8, "completion_tokens": 3, "calls": 2}


# ---- llm: json_chat --------------------------------------------------------
def test_json_chat_strips_code_fence():
    http = FakeHttp(_completion('```json\n{"ok": true, "n": 2}\n```'))
    client = LlmClient(_cfg(), http=http, rng=random.Random(0))

    assert client.json_chat([{"role": "user", "content": "给 JSON"}]) == {"ok": True, "n": 2}


def test_json_chat_puts_schema_in_system_and_never_sends_response_format():
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"],
    }
    http = FakeHttp(_completion('{"n": 3}'))
    client = LlmClient(_cfg(), http=http, rng=random.Random(0))

    assert client.json_chat([{"role": "user", "content": "给 JSON"}], schema=schema) == {"n": 3}

    payload = http.requests[0]["payload"]
    head = payload["messages"][0]
    assert head["role"] == "system"
    assert "JSON Schema" in head["content"]
    assert '"n"' in head["content"]
    assert "response_format" not in payload


def test_json_chat_repairs_once_with_previous_output_and_error():
    http = FakeHttp(_completion("抱歉，这不是 JSON"), _completion('{"ok": true}'))
    client = LlmClient(_cfg(), http=http, rng=random.Random(0))

    assert client.json_chat([{"role": "user", "content": "给 JSON"}]) == {"ok": True}

    assert len(http.requests) == 2
    repair = http.requests[1]["payload"]["messages"][-1]
    assert repair["role"] == "user"
    assert "抱歉，这不是 JSON" in repair["content"]
    assert "不是合法 JSON" in repair["content"]


def test_json_chat_gives_up_after_two_repairs():
    http = FakeHttp(_completion("嗯"), _completion("还是嗯"), _completion("就是不 JSON"))
    client = LlmClient(_cfg(), http=http, rng=random.Random(0))

    with pytest.raises(LlmBadJson) as excinfo:
        client.json_chat([{"role": "user", "content": "给 JSON"}])

    assert len(http.requests) == 3
    assert "3 次" in str(excinfo.value)


def test_json_chat_rejects_non_object_root():
    http = FakeHttp(_completion("[1, 2]"), _completion('"字符串"'), _completion("null"))
    client = LlmClient(_cfg(), http=http, rng=random.Random(0))

    with pytest.raises(LlmBadJson) as excinfo:
        client.json_chat([{"role": "user", "content": "给 JSON"}])

    assert "必须是 JSON 对象" in str(excinfo.value)


# ---- 小工具 ----------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ('```\n{"a": 1}\n```', '{"a": 1}'),
        ('  {"a": 1}  ', '{"a": 1}'),
        ('```JSON {"a": 1} ```', '{"a": 1}'),
    ],
)
def test_drop_code_fence_variants(raw: str, expected: str):
    assert drop_code_fence(raw) == expected


def test_completions_url_never_doubles_the_v1_segment():
    assert _completions_url("https://x.example/v1") == "https://x.example/v1/chat/completions"
    assert _completions_url("https://x.example") == "https://x.example/v1/chat/completions"
    assert (
        _completions_url("https://x.example/v1/chat/completions")
        == "https://x.example/v1/chat/completions"
    )
