"""`/letters/mercury/` 挂在 billgen 主服务上的接口测试。

同一个页面/同一组渲染函数既由 `tools/letter_studio.py` 独立服务，也挂在主服务下；
这里验主服务那条路：页面能开、默认值能给、改字段能出片、超限能出人话的 400。
"""
from __future__ import annotations

import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import fitz
import pytest

from billgen.server.http import make_server


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv("BILL_GEN_DATA_DIR", str(tmp_path))
    server = make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def get(origin: str, path: str):
    with urlopen(origin + path, timeout=30) as response:
        return response.status, response.headers, response.read()


def post(origin: str, path: str, payload: dict):
    request = Request(origin + path, json.dumps(payload).encode(), {"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=60) as response:
            return response.status, response.headers, response.read()
    except HTTPError as err:
        return err.code, err.headers, err.read()


def test_letter_page_is_mounted_with_and_without_trailing_slash(web):
    for path in ("/letters/mercury", "/letters/mercury/"):
        status, headers, body = get(web, path)
        assert status == 200
        assert headers.get_content_type() == "text/html"
        assert "Mercury 验证信" in body.decode()


def test_letter_defaults_comes_from_the_template(web):
    status, _, body = get(web, "/letters/mercury/defaults")
    assert status == 200
    data = json.loads(body)
    assert data["company"]["name"] and data["account"]["routing"]
    assert isinstance(data["company"]["address"], list)


def test_letter_render_returns_pdf_with_edited_fields(web):
    payload = {"date": "2026-12-24", "company": {"name": "Aurora Labs LLC", "signatory": "Wei Chen"}}
    status, headers, body = post(web, "/letters/mercury/render", payload)
    assert status == 200
    assert headers.get_content_type() == "application/pdf"
    assert body.startswith(b"%PDF-")

    with fitz.open(stream=body, filetype="pdf") as doc:
        text = doc[0].get_text()
    assert "Aurora Labs LLC" in text and "December 24, 2026" in text
    assert "Liroa Commerce" not in text          # 默认值被换掉，而不是叠印


def test_letter_png_endpoint(web):
    status, headers, body = post(web, "/letters/mercury/png", {})
    assert status == 200
    assert headers.get_content_type() == "image/png"
    assert body[:8] == b"\x89PNG\r\n\x1a\n"


def test_letter_overlong_field_is_a_readable_400(web):
    status, _, body = post(web, "/letters/mercury/render", {"bank": {"address": ["a", "b", "c"]}})
    assert status == 400
    assert "银行地址" in json.loads(body)["error"]


def test_letter_rejects_get_on_render(web):
    with pytest.raises(HTTPError) as err:
        urlopen(web + "/letters/mercury/render", timeout=30)
    assert err.value.code == 405


def test_letter_unknown_subpath_is_404(web):
    with pytest.raises(HTTPError) as err:
        urlopen(web + "/letters/mercury/nope", timeout=30)
    assert err.value.code == 404


def test_home_page_links_to_the_letter_editor(web):
    assert "/letters/mercury/" in get(web, "/")[2].decode()
