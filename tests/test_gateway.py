"""Real single-service HTTP contracts, replacing the P0 mock proxy tests."""
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


def get(origin, path):
    with urlopen(origin + path, timeout=30) as response:
        return response.status, response.headers, response.read()


def post(origin, path, data):
    request = Request(origin + path, json.dumps(data).encode(), {"Content-Type": "application/json"})
    with urlopen(request, timeout=60) as response:
        return response.status, response.headers, json.loads(response.read())


def test_index_health_registry(web):
    assert b"BillGen" in get(web, "/")[2]
    assert json.loads(get(web, "/healthz")[2])["status"] == "ok"
    specs = json.loads(get(web, "/api/v1/templates")[2])["templates"]
    assert len(specs) == 12
    assert all(any("\u4e00" <= char <= "\u9fff" for char in row["display_name"]) for row in specs)
    assert sum(row["server_render"] for row in specs) == 10


def test_workbench_assets_and_traversal(web):
    html = get(web, "/")[2]
    assert b"/web-assets/app.js" in html
    for name, kind in [("app.js", "javascript"), ("app.css", "text/css"),
                       ("legacy.css", "text/css"), ("legacy.js", "javascript")]:
        status, headers, body = get(web, "/web-assets/" + name)
        assert status == 200 and body
        assert kind in headers["Content-Type"]
    with pytest.raises(HTTPError) as caught:
        get(web, "/web-assets/../http.py")
    assert caught.value.code == 404


def test_preview_pdf_png_and_metadata(web):
    status, headers, job = post(web, "/api/v1/templates/mercury/preview", {"options": {"page_dpi": 72}})
    assert status == 201
    assert job["request_id"] == headers["X-Request-ID"]
    assert job["pages"] >= 1
    _, headers, pdf = get(web, job["links"]["pdf"])
    assert headers["Content-Type"] == "application/pdf"
    with fitz.open(stream=pdf, filetype="pdf") as document:
        assert len(document) == job["pages"]
    _, headers, image = get(web, job["links"]["pages"][0])
    assert headers["Content-Type"] == "image/png"
    assert image.startswith(b"\x89PNG")
    assert json.loads(get(web, job["links"]["self"])[2])["job_id"] == job["job_id"]


def test_export_and_invalid_body(web):
    _, _, job = post(web, "/api/v1/templates/wellsfargo/export", {})
    assert get(web, job["links"]["pdf"])[2].startswith(b"%PDF")
    request = Request(web + "/api/v1/templates/wellsfargo/preview", b"{", {"Content-Type": "application/json"})
    with pytest.raises(HTTPError) as caught:
        urlopen(request, timeout=30)
    with caught.value as error:
        assert error.code == 400
        assert json.loads(error.read())["error"]["code"] == "invalid_json"


@pytest.mark.parametrize("path,status", [
    ("/api/v1/templates/unknown/data", 404),
    ("/api/v1/jobs/missing", 404),
    ("/api/v1/templates/alliant/data", 422),
    ("/api/v1/unknown", 404),
])
def test_structured_errors(web, path, status):
    with pytest.raises(HTTPError) as caught:
        get(web, path)
    with caught.value as error:
        assert error.code == status
        result = json.loads(error.read())
        assert result["request_id"]
        assert "Traceback" not in result["error"]["message"]
