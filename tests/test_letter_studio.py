"""`tools/letter_studio.py` 的接口测试：起真服务、用 urllib 打真请求。

只验三件用户会碰到的事：页面能不能打开、改字段能不能出片、字段超限时给不给得出人话的报错。
"""
from __future__ import annotations

import importlib.util
import json
import urllib.error
import urllib.request
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _studio() -> ModuleType:
    spec = importlib.util.spec_from_file_location("letter_studio", ROOT / "tools" / "letter_studio.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def base_url():
    module = _studio()
    server = module.serve(0)          # 0 = 让系统挑空闲端口，避免和线上服务撞车
    port = server.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


def _post(url: str, payload: dict) -> tuple[int, bytes, str]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as err:
        return err.code, err.read(), err.headers.get("Content-Type", "")


def test_index_page_serves_the_editor(base_url: str) -> None:
    with urllib.request.urlopen(f"{base_url}/", timeout=10) as resp:
        body = resp.read().decode()
    assert resp.status == 200
    assert "Mercury 验证信" in body
    assert "BASE + 'api/render'" in body and "BASE + 'api/png'" in body


def test_defaults_endpoint_returns_letter_fields(base_url: str) -> None:
    with urllib.request.urlopen(f"{base_url}/api/defaults", timeout=10) as resp:
        data = json.loads(resp.read())
    assert data["company"]["name"] and data["account"]["routing"]
    assert isinstance(data["company"]["address"], list)


def test_render_returns_a_pdf_and_reflects_edits(base_url: str) -> None:
    payload = {
        "date": "2026-12-01",
        "company": {"name": "Aurora Labs LLC", "signatory": "Wei Chen"},
        "account": {"number": "7712039485"},
    }
    status, body, content_type = _post(f"{base_url}/api/render", payload)
    assert status == 200
    assert content_type.startswith("application/pdf")
    assert body.startswith(b"%PDF-")

    import fitz

    with fitz.open(stream=body, filetype="pdf") as doc:
        text = doc[0].get_text()
    assert "Aurora Labs LLC" in text and "Wei Chen" in text and "7712039485" in text
    assert "Liroa Commerce" not in text      # 默认值必须被换掉，不是叠印


def test_png_endpoint_returns_an_image(base_url: str) -> None:
    status, body, content_type = _post(f"{base_url}/api/png", {})
    assert status == 200
    assert content_type == "image/png"
    assert body[:8] == b"\x89PNG\r\n\x1a\n"


def test_too_many_address_lines_is_a_readable_400(base_url: str) -> None:
    status, body, _ = _post(f"{base_url}/api/render", {"company": {"address": ["a", "b", "c"]}})
    assert status == 400
    error = json.loads(body)["error"]
    assert "2 行" in error and "公司地址" in error


def test_unknown_route_is_404(base_url: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(f"{base_url}/nope", timeout=10)
    assert err.value.code == 404
