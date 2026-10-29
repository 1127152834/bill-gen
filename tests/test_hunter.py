"""账单猎手的配置、子进程状态与同源转发。"""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from billgen.hunter import HunterProcess, HunterSettings, SettingsError, SettingsStore, apply_patch
from billgen.hunter.settings import to_env
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
