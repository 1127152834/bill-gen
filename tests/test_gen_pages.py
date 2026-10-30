"""`/generate/` 页面与 `/api/v1/profiles` 路由的集成测试（真起服务、不连外网）。"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from billgen.server import http as H


@pytest.fixture()
def server(tmp_path: Path):
    H.profiles_store(tmp_path / "profiles.json")
    instance = H.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{instance.server_port}"
    try:
        yield base
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)
        H._PROFILES = None


def _get(url: str):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.status, resp.read()


def _call(url: str, method: str, payload: dict | None = None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _person_payload(**over) -> dict:
    payload = {
        "kind": "person",
        "bank": "chase-personal",
        "display_name": "王梅",
        "payload": {
            "name": "MEI WANG",
            "address": {"street": "2104 S 1st St", "city": "Austin", "state": "TX", "zip": "78704"},
            "occupation": "freelance graphic designer",
            "monthly_income_hint": "5000-7000",
        },
    }
    payload.update(over)
    return payload


def test_generate_page_is_served(server: str):
    status, body = _get(server + "/generate/")
    assert status == 200
    text = body.decode()
    assert "账单 JSON 生成器" in text
    assert "api('/gen/jobs'" in text, "页面提交任务应当打 /gen/jobs"
    assert "API + '/gen/jobs/'" in text, "下载/取消要拼在 API 前缀后面"


def test_generate_page_without_slash(server: str):
    status, _ = _get(server + "/generate")
    assert status == 200


def test_profile_crud_roundtrip(server: str):
    status, created = _call(server + "/api/v1/profiles", "POST", _person_payload())
    assert status == 201
    pid = created["id"]
    assert created["display_name"] == "王梅"
    # 账户事实应当已派生并存进档案
    assert set(created["accounts"]) >= {"checking", "credit"}
    assert created["accounts"]["checking"]["last4"] == created["accounts"]["checking"]["number"][-4:]

    status, listing = _call(server + "/api/v1/profiles", "GET")
    assert status == 200
    assert [p["id"] for p in listing["profiles"]] == [pid]

    status, updated = _call(
        server + f"/api/v1/profiles/{pid}", "PUT", {"display_name": "王梅 · 改"}
    )
    assert status == 200
    assert updated["display_name"] == "王梅 · 改"
    assert updated["accounts"] == created["accounts"], "手改显示名不该重算账户"

    status, gone = _call(server + f"/api/v1/profiles/{pid}", "DELETE")
    assert status == 200 and gone["deleted"] is True
    status, listing = _call(server + "/api/v1/profiles", "GET")
    assert listing["profiles"] == []


def test_profile_rejects_bad_state_and_zip(server: str):
    bad = _person_payload()
    bad["payload"]["address"] = {"street": "1 A St", "city": "Austin", "state": "Texas", "zip": "787"}
    status, body = _call(server + "/api/v1/profiles", "POST", bad)
    assert status == 400
    assert body["error"]["code"] == "invalid_profile"


def test_profile_rejects_static_web_template(server: str):
    status, body = _call(server + "/api/v1/profiles", "POST", _person_payload(bank="alliant"))
    assert status == 400
    assert "alliant" in json.dumps(body, ensure_ascii=False) or body["error"]["code"] == "invalid_profile"


def test_profile_import_json_reports_bad_rows(server: str):
    text = json.dumps(
        [
            _person_payload(),
            _person_payload(display_name="",
                            payload={**_person_payload()["payload"], "name": ""}),
        ]
    )
    status, body = _call(
        server + "/api/v1/profiles/import", "POST", {"kind": "json", "text": text}
    )
    assert status == 200
    assert len(body["profiles"]) == 1
    assert body["errors"] and body["errors"][0]["row"] == 1


def test_profile_import_requires_text(server: str):
    status, body = _call(server + "/api/v1/profiles/import", "POST", {"kind": "json"})
    assert status == 400


def test_unknown_profile_returns_404(server: str):
    status, body = _call(server + "/api/v1/profiles/deadbeef0000", "GET")
    assert status == 404
    assert body["error"]["code"] == "not_found"


def test_hunter_routes_are_gone():
    """猎手下线后，旧入口必须是 404，而不是挂着一个空转发。"""
    import http.client
    import json as _json

    from billgen.server import http as H

    server = H.make_server("127.0.0.1", 0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for path in ("/hunter/", "/api/v1/hunter/status", "/api/generations"):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", path)
            response = conn.getresponse()
            body = response.read()
            assert response.status == 404, (path, response.status, body[:80])
            conn.close()
    finally:
        server.shutdown()
        server.server_close()
