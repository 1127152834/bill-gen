"""账单猎手的配置、子进程状态与同源转发。"""

from __future__ import annotations

import io
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from billgen.hunter import HunterProcess, HunterSettings, SettingsError, SettingsStore, apply_patch
from billgen.hunter.settings import to_env
from billgen.hunter import probe
from billgen.server.http import make_server


# ---- 配置 ----------------------------------------------------------------
def _store(tmp_path: Path, **env: str) -> SettingsStore:
    return SettingsStore(tmp_path / "hunter" / "settings.json", environ=env)


def test_defaults_come_from_environment(tmp_path: Path):
    store = _store(
        tmp_path,
        LLM_FAKE="0",
        LLM_BASE_URL="https://proxy.example/v1",
        LLM_API_KEY="sk-test",
        LLM_MODEL="model-x",
        TAVILY_API_KEY="tvly-test",
        BANK_DISPLAY_NAME="Harborline Bank",
        BILLGEN_TEMPLATES="auto,boa-personal",
        BILLGEN_AUTO_LIMIT="3",
        API_KEY="svc",
    )
    settings = store.load()
    assert settings.llm_fake is False
    assert settings.model_ready is True
    assert settings.model == "model-x"
    assert settings.template_list == ["auto", "boa-personal"]
    assert settings.auto_limit == 3
    assert settings.service_key == "svc"


def test_missing_file_falls_back_and_bad_json_is_ignored(tmp_path: Path):
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_text("{ 这不是 json", encoding="utf-8")
    assert store.load().model == "gpt-6.1-sol"


def test_public_view_hides_secrets(tmp_path: Path):
    store = _store(tmp_path)
    store.save(apply_patch(store.load(), {"api_key": "sk-secret", "tavily_api_key": "tvly-1"}))
    public = store.load().public()
    assert "api_key" not in public and "tavily_api_key" not in public
    assert public["has_api_key"] is True
    assert public["has_tavily_key"] is True


def test_patch_keeps_existing_secret_when_blank(tmp_path: Path):
    store = _store(tmp_path)
    store.save(apply_patch(store.load(), {"api_key": "sk-keep"}))
    store.patch({"api_key": "", "model": "model-y"})
    saved = store.load()
    assert saved.api_key == "sk-keep"
    assert saved.model == "model-y"


def test_patch_rejects_unknown_fields_and_bad_values(tmp_path: Path):
    store = _store(tmp_path)
    with pytest.raises(SettingsError):
        apply_patch(store.load(), {"nonsense": 1})
    with pytest.raises(SettingsError):
        apply_patch(store.load(), {"auto_limit": 99})
    with pytest.raises(SettingsError):
        apply_patch(store.load(), {"structured_mode": "yaml"})
    with pytest.raises(SettingsError):
        apply_patch(store.load(), {"templates": "not-a-template"})
    with pytest.raises(SettingsError):
        apply_patch(store.load(), {"base_url": "proxy.example"})
    with pytest.raises(SettingsError):
        apply_patch(store.load(), {"llm_fake": "yes"})


def test_llm_fake_follows_the_credentials_instead_of_a_switch():
    """页面上不再有「离线夹具模式」开关：三项凭据齐就走真模型，掉一项自动回落。"""
    fresh = HunterSettings()
    assert fresh.llm_fake is True
    assert fresh.model_ready is False

    full = apply_patch(
        fresh,
        {"base_url": "https://proxy.example/v1", "api_key": "sk-1", "tavily_api_key": "tvly-1"},
    )
    assert full.llm_fake is False
    assert full.model_ready is True

    # 少一项就不再是真实链路，自动回到本地夹具（不用人再点开关）。
    assert apply_patch(full, {"base_url": ""}).llm_fake is True
    # 显式传值仍然优先，便于运维脚本钉死一种模式。
    assert apply_patch(full, {"llm_fake": True}).llm_fake is True


def test_patch_strips_trailing_slash(tmp_path: Path):
    settings = apply_patch(HunterSettings(), {"base_url": "https://proxy.example/v1/"})
    assert settings.base_url == "https://proxy.example/v1"


def test_to_env_maps_every_field(tmp_path: Path):
    settings = apply_patch(
        HunterSettings(),
        {
            "llm_fake": False,
            "base_url": "https://proxy.example/v1",
            "api_key": "sk-1",
            "tavily_api_key": "tvly-1",
            "templates": "boa-personal",
            "auto_limit": 4,
            "ai_fill": False,
        },
    )
    env = to_env(settings, port=8899, flow_root=tmp_path)
    assert env["PORT"] == "8899"
    assert env["HOST"] == "127.0.0.1"
    assert env["LLM_FAKE"] == "0"
    assert env["LLM_BASE_URL"] == "https://proxy.example/v1"
    assert env["LLM_API_KEY"] == "sk-1"
    assert env["TAVILY_API_KEY"] == "tvly-1"
    assert env["BILLGEN_AUTO_LIMIT"] == "4"
    assert env["BILLGEN_AI_FILL"] == "0"
    assert env["FLOW_ROOT"] == tmp_path.as_posix()


def test_mcp_command_is_a_single_executable():
    from billgen.hunter.settings import mcp_command

    command = mcp_command()
    assert " " not in command
    assert Path(command).exists()


def test_save_is_atomic_and_repeatable(tmp_path: Path):
    store = _store(tmp_path)
    first = store.patch({"model": "m1"})
    second = store.patch({"model": "m2"})
    assert first.model == "m1" and second.model == "m2"
    assert json.loads(store.path.read_text())["model"] == "m2"
    assert not store.path.with_suffix(".tmp").exists()


# ---- 进程状态 ------------------------------------------------------------
class _FakeHunter:
    """替身：不发子进程，只提供 forward 需要的 target 与状态。"""

    def __init__(self, store: SettingsStore, target):
        self.store = store
        self.target = target
        self.restarted = 0
        self.closed = 0

    def status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "running": True,
            "ready": True,
            "pid": 4242,
            "port": self.target.port,
            "url": "/hunter/",
            "error": "",
            "logs": [],
            "settings": self.store.load().public(),
            "environment": "",
        }

    def restart(self):
        self.restarted += 1
        return self.status()

    def close(self):
        self.closed += 1


def test_process_status_without_environment(tmp_path: Path):
    process = HunterProcess(_store(tmp_path), flow_root=tmp_path / "missing", port=8898)
    status = process.status()
    assert status["enabled"] is False
    assert "flow" in status["environment"]
    assert status["running"] is False


@pytest.fixture()
def web(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BILL_GEN_DATA_DIR", str(tmp_path / "data"))
    from billgen.server.legacy import LegacyMounts

    store = _store(tmp_path)
    upstream = _upstream()
    from billgen.hunter.process import HunterTarget

    target = HunterTarget("127.0.0.1", upstream.server_port)
    hunter = _FakeHunter(store, target)
    server = make_server("127.0.0.1", 0, legacy=LegacyMounts(), hunter=hunter)  # type: ignore[arg-type]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        yield origin, store, hunter
    finally:
        server.shutdown()
        server.server_close()
        upstream.shutdown()


def _upstream() -> ThreadingHTTPServer:
    class Upstream(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path == "/api/health":
                return self._json({"ok": True})
            if self.path == "/api/generations":
                return self._json({"items": [{"id": "g1"}], "path": self.path})
            if self.path.startswith("/events"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for index in range(3):
                    self.wfile.write(f"data: {index}\n\n".encode())
                    self.wfile.flush()
                return
            self._json({"path": self.path, "method": "GET"})

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b""
            self._json(
                {
                    "path": self.path,
                    "method": "POST",
                    "api_key": self.headers.get("X-API-Key", ""),
                    "body": body.decode() or None,
                }
            )

        def _json(self, payload):
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _get(url: str) -> tuple[int, bytes, str]:
    with urllib.request.urlopen(url, timeout=20) as response:
        return response.status, response.read(), response.headers.get("Content-Type", "")


def test_status_endpoint(web):
    origin, _, _ = web
    status, body, _ = _get(origin + "/api/v1/hunter/status")
    assert status == 200
    payload = json.loads(body)
    assert payload["ready"] is True and payload["port"] == payload["port"]


def test_settings_get_and_put(web):
    origin, store, hunter = web
    status, body, _ = _get(origin + "/api/v1/hunter/settings")
    assert status == 200 and json.loads(body)["model"] == "gpt-6.1-sol"

    request = urllib.request.Request(
        origin + "/api/v1/hunter/settings",
        data=json.dumps({"model": "model-z", "ai_fill": False}).encode(),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read())
    assert payload["model"] == "model-z"
    assert payload["ai_fill"] is False
    assert store.load().model == "model-z"
    assert hunter.restarted == 1


def test_settings_put_rejects_bad_field(web):
    origin, _, _ = web
    request = urllib.request.Request(
        origin + "/api/v1/hunter/settings",
        data=json.dumps({"templates": "nope"}).encode(),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=20)
    assert excinfo.value.code == 400
    assert json.loads(excinfo.value.read())["error"]["code"] == "invalid_settings"


def test_hunter_ui_and_api_are_proxied(web):
    origin, _, _ = web
    status, body, content_type = _get(origin + "/hunter/")
    assert status == 200 and json.loads(body)["path"] == "/"
    status, body, _ = _get(origin + "/hunter/some/page?x=1")
    assert json.loads(body)["path"] == "/some/page?x=1"
    status, body, _ = _get(origin + "/api/generations")
    assert json.loads(body)["items"][0]["id"] == "g1"


def test_proxy_forwards_post_body_and_api_key(web):
    origin, _, _ = web
    body = json.dumps({"subjectType": "personal"}).encode()
    request = urllib.request.Request(
        origin + "/api/generations",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read())
    assert payload["method"] == "POST"
    assert payload["body"] == body.decode()
    assert payload["api_key"] == "devlocal"


def test_sse_is_not_buffered(web):
    origin, _, _ = web
    status, body, content_type = _get(origin + "/hunter/events")
    assert status == 200 and content_type.startswith("text/event-stream")
    assert body == b"data: 0\n\ndata: 1\n\ndata: 2\n\n"


def test_restart_endpoint(web):
    origin, _, hunter = web
    request = urllib.request.Request(
        origin + "/api/v1/hunter/restart",
        data=b"",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read())
    assert payload["ready"] is True
    assert hunter.restarted == 1


def test_proxy_reports_upstream_down(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BILL_GEN_DATA_DIR", str(tmp_path / "data"))
    from billgen.hunter.process import HunterTarget
    from billgen.server.legacy import LegacyMounts

    hunter = _FakeHunter(_store(tmp_path), HunterTarget("127.0.0.1", 9))
    server = make_server("127.0.0.1", 0, legacy=LegacyMounts(), hunter=hunter)  # type: ignore[arg-type]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/hunter/", timeout=20)
        assert excinfo.value.code == 502
        assert json.loads(excinfo.value.read())["error"]["code"] == "hunter_unavailable"
    finally:
        server.shutdown()
        server.server_close()


# ---- 模型 / Tavily 连通性探测 -------------------------------------------------


class _FakeResponse:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body.encode()

    def read(self, _limit: int = -1) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _patch_urlopen(monkeypatch, replies):
    """按顺序返回 (status, body)；返回 None 表示连不上。"""
    calls: list[dict] = []

    def fake(request, timeout=None):
        calls.append({"url": request.full_url, "body": request.data, "headers": dict(request.headers)})
        reply = replies.pop(0)
        if reply is None:
            raise urllib.error.URLError("nodename nor servname provided")
        status, body = reply
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "err", {}, None, )
        return _FakeResponse(status, body)

    monkeypatch.setattr(probe, "_urlopen", fake)
    return calls


def _http_error(monkeypatch, status: int, body: str):
    def fake(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, status, "err", {}, io.BytesIO(body.encode())
        )

    monkeypatch.setattr(probe, "_urlopen", fake)


def test_probe_llm_reports_success_with_latency(monkeypatch):
    _patch_urlopen(
        monkeypatch,
        [(200, json.dumps({"choices": [{"message": {"content": '{"ok": true}'}}]}))],
    )
    settings = HunterSettings(base_url="https://llm.test/v1", api_key="sk-live", model="gpt-x")
    result = probe.probe_llm(settings)
    assert result["ok"] is True
    assert result["endpoint"] == "https://llm.test/v1/chat/completions"
    assert "已连通" in result["detail"] and '{"ok": true}' in result["detail"]
    assert isinstance(result["latency_ms"], int)


def test_probe_llm_reports_auth_failure(monkeypatch):
    _http_error(monkeypatch, 401, json.dumps({"error": {"message": "invalid api key"}}))
    settings = HunterSettings(base_url="https://llm.test/v1", api_key="sk-bad")
    result = probe.probe_llm(settings)
    assert result["ok"] is False
    assert "鉴权失败" in result["error"] and "invalid api key" in result["error"]


def test_probe_llm_falls_back_to_plain_payload(monkeypatch):
    """第一次带 response_format 被 400 打回，就退一次纯对话，别把方言差异当故障。"""
    _patch_urlopen(
        monkeypatch,
        [
            (400, json.dumps({"error": {"message": "response_format unsupported"}})),
            (400, json.dumps({"error": {"message": "response_format unsupported"}})),
            (200, json.dumps({"choices": [{"message": {"content": "pong"}}]})),
        ],
    )
    settings = HunterSettings(base_url="https://llm.test/v1", api_key="sk-live")
    result = probe.probe_llm(settings)
    assert result["ok"] is True and "pong" in result["detail"]


def test_probe_llm_names_missing_field(monkeypatch):
    with pytest.raises(probe.ProbeError, match="接口地址"):
        probe.probe_llm(HunterSettings(api_key="sk-live"))
    with pytest.raises(probe.ProbeError, match="API Key"):
        probe.probe_llm(HunterSettings(base_url="https://llm.test/v1"))


def test_probe_llm_uses_the_unsaved_form_values(monkeypatch):
    calls = _patch_urlopen(
        monkeypatch, [(200, json.dumps({"choices": [{"message": {"content": "ok"}}]}))]
    )
    stored = HunterSettings(base_url="", api_key="")
    result = probe.probe_llm(
        stored, {"base_url": "https://form.test/v1", "api_key": "sk-form"}
    )
    assert result["ok"] is True
    assert calls[0]["url"] == "https://form.test/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-form"


def test_probe_tavily_success_and_failure(monkeypatch):
    _patch_urlopen(monkeypatch, [(200, json.dumps({"results": [{"title": "x"}]}))])
    ok = probe.probe_tavily(HunterSettings(tavily_api_key="tvly-live"))
    assert ok["ok"] is True and "1 条结果" in ok["detail"]

    _http_error(monkeypatch, 403, json.dumps({"detail": {"error": "invalid api key"}}))
    bad = probe.probe_tavily(HunterSettings(tavily_api_key="tvly-bad"))
    assert bad["ok"] is False and "鉴权失败" in bad["error"]

    with pytest.raises(probe.ProbeError, match="Tavily Key"):
        probe.probe_tavily(HunterSettings())


def test_probe_endpoints_are_wired(web, monkeypatch):
    origin, store, _ = web
    store.patch({"base_url": "https://llm.test/v1", "api_key": "sk-1", "tavily_api_key": "tvly-1"})
    _patch_urlopen(
        monkeypatch,
        [
            (200, json.dumps({"choices": [{"message": {"content": "ok"}}]})),
            (200, json.dumps({"results": []})),
        ],
    )
    for path in ("test-llm", "test-tavily"):
        request = urllib.request.Request(
            origin + f"/api/v1/hunter/{path}",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read())
        assert payload.get("ok") is True, (path, payload)


def test_probe_endpoint_reports_missing_key_without_500(web):
    origin, _, _ = web
    request = urllib.request.Request(
        origin + "/api/v1/hunter/test-tavily",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        assert response.status == 200
        payload = json.loads(response.read())
    assert payload["ok"] is False and "Tavily Key" in payload["error"]


def test_settings_payload_carries_storage_and_masked_hints(web):
    origin, store, _ = web
    store.patch({"api_key": "sk-abcdef123456", "tavily_api_key": "tvly-zzzz9999"})
    status, body, _ = _get(origin + "/api/v1/hunter/settings")
    payload = json.loads(body)
    storage = payload["storage"]
    assert storage["path"].endswith("hunter/settings.json")
    assert storage["saved_at"] and storage["bytes"] > 0
    assert storage["writable"] is True
    assert storage["key_hint"] == "sk-…3456"
    assert storage["tavily_hint"] == "tvl…9999"


def test_settings_file_is_not_world_readable(tmp_path: Path):
    """文件里放的是 API Key：写完必须是 0600，不能跟着 umask 走。"""
    store = _store(tmp_path)
    path = Path(store.path)
    store.patch({"api_key": "sk-secret"})
    assert path.stat().st_mode & 0o777 == 0o600


def test_every_hunter_api_path_is_reachable_through_the_hub(web):
    """猎手自己的 /api/* 必须整段转发：漏一条前端就只看到「随机生成失败」。"""
    origin, _, _ = web
    for path, method in (
        ("/api/personas/random", "POST"),
        ("/api/settings", "GET"),
        ("/api/settings/test", "POST"),
    ):
        request = urllib.request.Request(
            origin + path,
            data=b"{}" if method == "POST" else None,
            headers={"Content-Type": "application/json"} if method == "POST" else {},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            assert response.status == 200, path
