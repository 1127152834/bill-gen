"""Single HTTP entry point: registry API and legacy editors, without proxy ports."""
from __future__ import annotations

import json
import logging
import mimetypes
import re
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .application import ApplicationError, ApplicationService
from .legacy import LegacyMounts

LOG = logging.getLogger(__name__)
MAX_BODY = 4 * 1024 * 1024

EDITOR_LINKS = (
    ("/boa/", "BOA"), ("/wf/", "Wells Fargo"), ("/ae/", "American Express"),
    ("/chase/", "Chase Personal"), ("/chasebiz/", "Chase Business"),
    ("/us/", "US Bank Personal"), ("/usbiz/", "US Bank Business"),
    ("/merc/", "Mercury / Capital One / SoFi"),
    ("/alliant/", "Alliant"), ("/ally/", "Ally"),
)


def _home() -> bytes:
    entry = Path(__file__).parent / "web" / "index.html"
    if entry.is_file():
        return entry.read_bytes()
    links = "".join(f'<li><a href="{url}">{name}</a></li>' for url, name in EDITOR_LINKS)
    return ("<!doctype html><html lang=zh-CN><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>BillGen</title><style>body{font:16px system-ui;max-width:850px;"
            "margin:64px auto;padding:24px;background:#f5f6f8;color:#17202b}"
            "li{margin:14px 0}a{color:#155eaa}code{background:#e4e8ef;padding:4px}</style>"
            "<h1>BillGen</h1><p>合成账单模板编辑器 · 单一服务</p><ul>" + links +
            "</ul><p>模板注册表：<a href='/api/v1/templates'>/api/v1/templates</a>"
            " · <a href='/healthz'>运行状态</a></p></html>").encode()


class BillGenHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = True

    def server_close(self):
        super().server_close()
        self.application.close()


class Handler(BaseHTTPRequestHandler):
    server: BillGenHTTPServer

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def _send(self, body: bytes, content_type: str, status=200, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Request-ID", self.request_id)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, value, status=200):
        self._send(json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

    def _error(self, code, message, status):
        self._json({"error": {"code": code, "message": message}, "request_id": self.request_id}, status)

    def _body(self, allowed=("data", "options")):
        if len(self.headers.get_all("Content-Length", [])) != 1:
            self.close_connection = True
            raise ApplicationError("invalid_request", "Expected one Content-Length header", 400)
        if self.headers.get("Transfer-Encoding"):
            raise ApplicationError("invalid_request", "Transfer-Encoding is unsupported", 400)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ApplicationError("invalid_request", "Invalid Content-Length", 400) from exc
        if length < 1 or length > MAX_BODY:
            self.close_connection = True
            raise ApplicationError("invalid_body_size", "JSON body must be between 1 byte and 4 MiB", 413)
        if self.headers.get_content_type() != "application/json":
            raise ApplicationError("unsupported_media_type", "Expected application/json", 415)
        try:
            value = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError) as exc:
            raise ApplicationError("invalid_json", "Malformed JSON body", 400) from exc
        if not isinstance(value, dict) or set(value) - set(allowed):
            raise ApplicationError(
                "invalid_request",
                "Expected object with " + "/".join(allowed) + " fields", 400)
        return value

    def _job_links(self, job):
        job = dict(job)
        base = f"/api/v1/jobs/{job['job_id']}"
        job["links"] = {"self": base, "pdf": base + "/document.pdf",
                        "pages": [base + f"/pages/{i}.png" for i in range(job["pages"])]}
        return job

    def _dispatch(self):
        self.request_id = uuid.uuid4().hex
        path = urlsplit(self.path).path
        app = self.server.application
        try:
            if path == "/healthz" and self.command == "GET":
                return self._json({"status": "ok", "service": "billgen", "api_version": "v1"})
            if path == "/" and self.command == "GET":
                return self._send(_home(), "text/html; charset=utf-8")
            if path.startswith("/web-assets/") and self.command in ("GET", "HEAD"):
                name = path.removeprefix("/web-assets/")
                if name not in {"app.css", "app.js", "legacy.css", "legacy.js"}:
                    return self._error("not_found", "Asset not found", 404)
                asset = Path(__file__).parent / "web" / name
                if not asset.is_file():
                    return self._error("not_found", "Asset not found", 404)
                content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
                return self._send(asset.read_bytes(), content_type + "; charset=utf-8")
            if path.startswith("/api/v1/"):
                if self.command == "GET" and path == "/api/v1/templates":
                    return self._json({"templates": app.list_templates()})
                if self.command == "GET" and path == "/api/v1/synth/capabilities":
                    return self._json(app.synth_capabilities())
                if self.command == "POST" and path == "/api/v1/synth/render":
                    payload = self._body(("template", "result", "account", "cycle",
                                          "bank_name", "watermark", "brand", "docinfo",
                                          "options"))
                    template = payload.pop("template", None)
                    if not isinstance(template, str) or not template:
                        raise ApplicationError("invalid_request", "template is required", 400)
                    job = app.render_synth(template, request_id=self.request_id, **payload)
                    return self._json(self._job_links(job), 201)
                if path.startswith("/api/v1/synth/"):
                    return self._error("method_not_allowed", "Method not allowed", 405)
                match = re.fullmatch(r"/api/v1/templates/([a-z0-9-]+)/(data|preview|export)", path)
                if match:
                    template, action = match.groups()
                    if action == "data" and self.command == "GET":
                        return self._json(app.default_data(template))
                    if action in ("preview", "export") and self.command == "POST":
                        payload = self._body()
                        fn = app.preview if action == "preview" else app.export_pdf
                        job = fn(template, request_id=self.request_id, **payload)
                        return self._json(self._job_links(job), 201)
                    return self._error("method_not_allowed", "Method not allowed", 405)
                match = re.fullmatch(r"/api/v1/jobs/([a-zA-Z0-9_-]+)(?:/(document\.pdf|pages/[0-9]+\.png))?", path)
                if match and self.command == "GET":
                    job_id, resource = match.groups()
                    if resource is None:
                        return self._json(self._job_links(app.get_job(job_id)))
                    if resource == "document.pdf":
                        return self._send(app.get_pdf(job_id), "application/pdf", headers={
                            "Content-Disposition": 'attachment; filename="statement.pdf"'})
                    index = int(resource.split("/")[1].split(".")[0])
                    return self._send(app.get_page_png(job_id, index), "image/png")
                return self._error("not_found", "API endpoint not found", 404)
            if self.server.legacy.dispatch(self):
                return
            self._error("not_found", "Page not found", 404)
        except ApplicationError as exc:
            self._error(exc.code, str(exc), exc.status)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            LOG.exception("Request failed: %s", self.request_id)
            self._error("internal_error", "Request failed; consult server logs with request_id", 500)

    do_GET = _dispatch
    do_POST = _dispatch
    do_HEAD = _dispatch

    def log_message(self, fmt, *args):
        LOG.info("%s %s", self.address_string(), fmt % args)


def make_server(host="127.0.0.1", port=8800, *, application=None, legacy=None):
    server = BillGenHTTPServer((host, port), Handler)
    server.application = application or ApplicationService()
    server.legacy = legacy or LegacyMounts()
    return server


def serve(host="127.0.0.1", port=8800):
    from .bootstrap import prepare_workspace

    prepare_workspace()
    with make_server(host, port) as server:
        print(f"BillGen: http://{host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0
