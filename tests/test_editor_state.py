"""Legacy editors keep workspace snapshots atomic; only AE output is per browser."""
import copy
import importlib.util
import json
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import fitz
import pytest

from billgen.server.state import BrowserSessions, EditorState, serialized

ROOT = Path(__file__).resolve().parents[1]
THEMES = ROOT / "src/billgen/themes"


def load_editor(monkeypatch, path, generator_name):
    generator = types.SimpleNamespace(DATA={"sample": "original"})
    # Editors now import their own theme explicitly, never a process-wide alias.
    import importlib
    theme = path.relative_to(THEMES).parts[0]
    package = importlib.import_module(f"billgen.themes.{theme}")
    monkeypatch.setattr(package, generator_name, generator, raising=False)
    spec = importlib.util.spec_from_file_location("state_test_" + path.parent.name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, generator


def write_pdf(data, path, **kwargs):
    with fitz.open() as doc:
        doc.new_page().insert_text((20, 20), data.get("name", "SAMPLE"))
        doc.save(path)


@pytest.mark.parametrize("relative,generator", [
    ("chase/editor_personal/server.py", "generate_personal"),
    ("chase/editor_business/server.py", "generate_business"),
    ("usbank/editor_us/server.py", "generate_us"),
    ("usbank/editor_biz/server.py", "generate_biz"),
    ("wellsfargo/editor/server.py", "generate"),
])
def test_regenerate_owns_input_and_publishes_options(monkeypatch, tmp_path, relative, generator):
    module, gen = load_editor(monkeypatch, THEMES / relative, generator)
    gen.build_pdf = write_pdf
    module.DATA_FILE = str(tmp_path / "data.json")
    module.PREVIEW = str(tmp_path / "preview.pdf")
    module.PV_DIR = str(tmp_path / "pages")
    data = {"name": "FIRST"}
    before = copy.deepcopy(data)
    args = (data, False, False, "declared") if "wellsfargo" in relative else (data, False)
    module.regenerate(*args)
    assert data == before
    assert module.STATE.watermark is False
    assert module.STATE.pages == 1
    gen.build_pdf = lambda *a, **kw: (_ for _ in ()).throw(ValueError("failed build"))
    with pytest.raises(ValueError, match="failed build"):
        module.regenerate(data, True, True) if "wellsfargo" in relative else module.regenerate(data, True)
    assert module.STATE.watermark is False


def test_boa_watermark_is_per_template(monkeypatch, tmp_path):
    module, gen = load_editor(monkeypatch, THEMES / "boa/editor/server.py", "generate")
    gen.build_pdf = write_pdf
    module.DATA_FILES = {t: str(tmp_path / (t + ".json")) for t in module.STATES}
    module.PREVIEW = {t: str(tmp_path / (t + ".pdf")) for t in module.STATES}
    module.PV_DIR = str(tmp_path / "pages")
    data = {"name": "PERSONAL"}
    module.regenerate("personal", data, False)
    module.regenerate("business", {"name": "BUSINESS"}, True)
    assert module.STATES["personal"].watermark is False
    assert module.STATES["business"].watermark is True
    assert data == {"name": "PERSONAL"}


def test_serialized_covers_read_modify_publish_and_reentry():
    state = EditorState()
    @serialized
    def nested():
        state.pages += 1
    @serialized
    def operation():
        nested()
        state.hashes.append(str(state.pages))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: operation(), range(100)))
    assert state.hashes == [str(i) for i in range(1, 101)]
    snapshot = state.snapshot()
    snapshot["hashes"].clear()
    assert len(state.hashes) == 100


def test_session_ids_are_server_issued_and_expire():
    sessions = BrowserSessions()
    a, header = sessions.resolve("billgen_ae_session=attacker")
    assert "=attacker;" not in header
    a.pdf = b"A"
    again, header2 = sessions.resolve(header.split(";", 1)[0])
    assert again is a and header2 is None
    b, _ = sessions.resolve(None)
    assert b.pdf is None
    a.touched -= sessions.ttl + 1
    expired, _ = sessions.resolve(header.split(";", 1)[0])
    assert expired.pdf is None


def test_ae_browser_download_save_and_previews_are_isolated(monkeypatch, tmp_path):
    module, gen = load_editor(monkeypatch, THEMES / "amex/editor.py", "generate_sample_statement")
    module.SAVE_DIR, module.PV_DIR = str(tmp_path), str(tmp_path / "pages")
    monkeypatch.setattr(module, "parse_payload", lambda body: body)
    monkeypatch.setattr(module, "build_warnings", lambda data: ([], {}))
    monkeypatch.setattr(module, "compute_totals", lambda data: {})
    monkeypatch.setattr(module, "parse_metadata", lambda data: ({}, ([], {})))
    monkeypatch.setattr(module, "parse_xmp", lambda data: ({}, ([], {})))
    def build(data, *a, **kw):
        doc = fitz.open()
        doc.new_page().insert_text((20, 20), data["name"])
        return doc
    gen.build_pdf = build
    gen.pdf_bytes = lambda doc, **kw: doc.tobytes()
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    clients = [build_opener(HTTPCookieProcessor(CookieJar())) for _ in range(2)]
    def request(client, path, data=None):
        req = Request(origin + path, data=json.dumps(data).encode() if data is not None else None)
        with client.open(req, timeout=5) as response:
            return response.read()
    try:
        for client in clients:
            request(client, "/")
        first = json.loads(request(clients[0], "/api/build", {"name": "FIRST"}))
        with pytest.raises(HTTPError) as error:
            request(clients[1], "/api/download")
        assert error.value.code == 400
        error.value.close()
        second = json.loads(request(clients[1], "/api/build", {"name": "SECOND"}))
        for client, name in zip(clients, ("FIRST", "SECOND"), strict=True):
            pdf = request(client, "/api/download")
            with fitz.open(stream=pdf, filetype="pdf") as doc:
                assert name in doc[0].get_text()
        for hashes in (first["hashes"], second["hashes"]):
            assert request(clients[0], "/pv/" + hashes[0] + ".png").startswith(b"\x89PNG")
        request(clients[0], "/api/save", {"filename": "first.pdf"})
        with pytest.raises(HTTPError) as error:
            request(clients[1], "/api/reveal", {"action": "finder"})
        assert error.value.code == 400
        error.value.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_portal_explicit_build_and_cross_template_preview_retention(monkeypatch, tmp_path):
    generators = {}
    for name in ("generate_statement", "c1_generate", "sofi_generate"):
        gen = types.SimpleNamespace(DATA={"sample": name}, WATERMARK=True, _fallback_used=set())
        generators[name] = gen
        monkeypatch.setitem(sys.modules, name, gen)
    path = ROOT / "src/billgen/server/portal/editor_server.py"
    spec = importlib.util.spec_from_file_location("state_test_portal", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PV_DIR = str(tmp_path)
    monkeypatch.setattr(module, "mod", lambda tpl: generators[module.TEMPLATES[tpl]["module"]])
    monkeypatch.setattr(module, "normalize", lambda tpl, data: data)
    monkeypatch.setattr(module, "sanitize", lambda tpl, data: (data, {"accent": set(), "missing": set()}))
    monkeypatch.setattr(module, "_set_cjk", lambda tpl, data: None)
    calls = []
    def build(*, meta, data, watermark):
        calls.append((meta, data, watermark))
        doc = fitz.open()
        doc.new_page().insert_text((20, 20), data["name"])
        return doc
    for gen in generators.values():
        gen.build = build
    first = module.render_previews("mercury", {"name": "FIRST"}, False, {"title": "one"})
    module.render_previews("c1", {"name": "SECOND"}, True)
    assert (tmp_path / (first["pages"][0] + ".png")).exists()
    assert calls[0] == ({"title": "one"}, {"name": "FIRST"}, False)
    assert generators["generate_statement"].DATA == {"sample": "generate_statement"}
    assert generators["generate_statement"].WATERMARK is True
