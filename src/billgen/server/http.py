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

from ..hunter import HunterProcess, SettingsError, SettingsStore
from ..hunter.proxy import forward
from ..themes.mercury import letter_web
from .application import ApplicationError, ApplicationService
from .legacy import LegacyMounts

LOG = logging.getLogger(__name__)
MAX_BODY = 4 * 1024 * 1024

# 猎手的 React 产物与页面路由（相对站点根，而不是 /hunter/ 前缀）
HUNTER_ASSETS = ("/assets/", "/favicon.svg", "/vite.svg")
HUNTER_PAGES = ("/new", "/history")
_HUNTER_DETAIL = re.compile(r"/g/[A-Za-z0-9-]+")

EDITOR_LINKS = (
    ("/boa/", "BOA"),
    ("/wf/", "Wells Fargo"),
    ("/ae/", "American Express"),
    ("/chase/", "Chase Personal"),
    ("/chasebiz/", "Chase Business"),
    ("/us/", "US Bank Personal"),
    ("/usbiz/", "US Bank Business"),
    ("/merc/", "Mercury / Capital One / SoFi"),
    ("/alliant/", "Alliant"),
    ("/ally/", "Ally"),
    ("/letters/mercury/", "Mercury 验证信（信件编辑器）"),
)


def _home() -> bytes:
    entry = Path(__file__).parent / "web" / "index.html"
    if entry.is_file():
        return entry.read_bytes()
    links = "".join(f'<li><a href="{url}">{name}</a></li>' for url, name in EDITOR_LINKS)
    return (
        "<!doctype html><html lang=zh-CN><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>BillGen</title><style>body{font:16px system-ui;max-width:850px;"
        "margin:64px auto;padding:24px;background:#f5f6f8;color:#17202b}"
        "li{margin:14px 0}a{color:#155eaa}code{background:#e4e8ef;padding:4px}</style>"
        "<h1>BillGen</h1><p>合成账单模板编辑器 · 单一服务</p><ul>"
        + links
        + "</ul><p>模板注册表：<a href='/api/v1/templates'>/api/v1/templates</a>"
        " · <a href='/healthz'>运行状态</a></p></html>"
    ).encode()


class BillGenHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = True

    def server_close(self):
        super().server_close()
        self.application.close()
        hunter = getattr(self, "hunter", None)
        if hunter is not None:
            hunter.close()


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
        self._send(
            json.dumps(value, ensure_ascii=False).encode(),
            "application/json; charset=utf-8",
            status,
        )

    def _error(self, code, message, status):
        self._json(
            {"error": {"code": code, "message": message}, "request_id": self.request_id}, status
        )

    def _body(self, allowed=("data", "options"), *, required: bool = True):
        if len(self.headers.get_all("Content-Length", [])) != 1:
            self.close_connection = True
            raise ApplicationError("invalid_request", "Expected one Content-Length header", 400)
        if self.headers.get("Transfer-Encoding"):
            raise ApplicationError("invalid_request", "Transfer-Encoding is unsupported", 400)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ApplicationError("invalid_request", "Invalid Content-Length", 400) from exc
        if not required and length == 0:
            return {}
        if length < 1 or length > MAX_BODY:
            self.close_connection = True
            raise ApplicationError(
                "invalid_body_size", "JSON body must be between 1 byte and 4 MiB", 413
            )
        if self.headers.get_content_type() != "application/json":
            raise ApplicationError("unsupported_media_type", "Expected application/json", 415)
        try:
            value = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError) as exc:
            raise ApplicationError("invalid_json", "Malformed JSON body", 400) from exc
        if not isinstance(value, dict) or set(value) - set(allowed):
            raise ApplicationError(
                "invalid_request", "Expected object with " + "/".join(allowed) + " fields", 400
            )
        return value

    def _hunter_settings(self, hunter):
        payload = hunter.store.load().public()
        payload["status"] = hunter.status()
        return payload

    def _hunter_patch(self, hunter):
        patch = self._body(
            (
                "llm_fake",
                "base_url",
                "api_key",
                "model",
                "structured_mode",
                "tavily_api_key",
                "bank_name",
                "templates",
                "auto_limit",
                "ai_fill",
                "service_key",
            )
        )
        try:
            hunter.store.patch(patch)
        except SettingsError as exc:
            raise ApplicationError("invalid_settings", str(exc), 400) from exc
        status = hunter.restart() if patch.get("restart", True) is not False else hunter.status()
        return self._json({**self._hunter_settings(hunter), "status": status})

    def _job_links(self, job):
        job = dict(job)
        base = f"/api/v1/jobs/{job['job_id']}"
        job["links"] = {
            "self": base,
            "pdf": base + "/document.pdf",
            "pages": [base + f"/pages/{i}.png" for i in range(job["pages"])],
        }
        return job

    # ---- Mercury 验证信编辑器 -------------------------------------------------
    _LETTER_BASE = "/letters/mercury"

    def _letter(self, path: str):
        base = self._LETTER_BASE
        tail = path[len(base):].rstrip("/")
        if tail == "" and self.command in ("GET", "HEAD"):
            return self._send(letter_web.PAGE.encode(), "text/html; charset=utf-8")
        if tail == "/defaults" and self.command == "GET":
            return self._json(letter_web.defaults())
        if tail in ("/render", "/png"):
            if self.command != "POST":
                return self._error("method_not_allowed", "Method not allowed", 405)
            payload = self._raw_json()
            try:
                body = (
                    letter_web.render_pdf(payload)
                    if tail == "/render"
                    else letter_web.render_png(payload)
                )
            except ValueError as err:      # 行数/长度超限：把话说清楚
                return self._json({"error": str(err)}, 400)
            if tail == "/render":
                return self._send(
                    body,
                    "application/pdf",
                    headers={"Content-Disposition": 'inline; filename="mercury-letter.pdf"'},
                )
            return self._send(body, "image/png")
        return self._error("not_found", "Letter endpoint not found", 404)

    def _raw_json(self) -> dict:
        """信件字段是自由对象的 JSON，不走 `_body()` 的白名单。"""
        if len(self.headers.get_all("Content-Length", [])) != 1:
            self.close_connection = True
            raise ApplicationError("invalid_request", "Expected one Content-Length header", 400)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ApplicationError("invalid_request", "Invalid Content-Length", 400) from exc
        if length < 1 or length > MAX_BODY:
            self.close_connection = True
            raise ApplicationError("invalid_body_size", "JSON body must be 1 byte … 4 MiB", 413)
        try:
            value = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError) as exc:
            raise ApplicationError("invalid_json", "Malformed JSON body", 400) from exc
        if not isinstance(value, dict):
            raise ApplicationError("invalid_request", "Expected a JSON object", 400)
        return value

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
            if path.startswith("/letters/mercury"):
                return self._letter(path)
            hunter = getattr(self.server, "hunter", None)
            if hunter is not None:
                if path == "/api/v1/hunter/status" and self.command == "GET":
                    return self._json(hunter.status())
                if path == "/api/v1/hunter/settings":
                    if self.command == "GET":
                        return self._json(self._hunter_settings(hunter))
                    if self.command in ("PUT", "POST"):
                        return self._hunter_patch(hunter)
                    return self._error("method_not_allowed", "Method not allowed", 405)
                if path == "/api/v1/hunter/restart" and self.command in ("POST", "PUT"):
                    self._body((), required=False)
                    return self._json(hunter.restart())
                if path == "/hunter" or path.startswith("/hunter/"):
                    return forward(
                        self,
                        hunter.target,
                        strip_prefix="/hunter",
                        api_key=hunter.store.load().service_key,
                    )
                if path == "/api/generations" or path.startswith("/api/generations/"):
                    return forward(self, hunter.target, api_key=hunter.store.load().service_key)
                if path == "/api/health":
                    return forward(self, hunter.target)
                # 猎手前端用的是绝对路径（/assets、/new、/g/:id），同源直通
                if path in HUNTER_PAGES or path.startswith(HUNTER_ASSETS):
                    return forward(self, hunter.target)
                if _HUNTER_DETAIL.fullmatch(path):
                    return forward(self, hunter.target)
            if path.startswith("/api/v1/"):
                if self.command == "GET" and path == "/api/v1/templates":
                    return self._json({"templates": app.list_templates()})
                if self.command == "GET" and path == "/api/v1/synth/capabilities":
                    return self._json(app.synth_capabilities())
                if self.command == "POST" and path == "/api/v1/synth/render":
                    payload = self._body(
                        (
                            "template",
                            "result",
                            "account",
                            "cycle",
                            "bank_name",
                            "watermark",
                            "brand",
                            "docinfo",
                            "options",
                        )
                    )
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
                match = re.fullmatch(
                    r"/api/v1/jobs/([a-zA-Z0-9_-]+)(?:/(document\.pdf|pages/[0-9]+\.png))?", path
                )
                if match and self.command == "GET":
                    job_id, resource = match.groups()
                    if resource is None:
                        return self._json(self._job_links(app.get_job(job_id)))
                    if resource == "document.pdf":
                        return self._send(
                            app.get_pdf(job_id),
                            "application/pdf",
                            headers={"Content-Disposition": 'attachment; filename="statement.pdf"'},
                        )
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
            self._error(
                "internal_error", "Request failed; consult server logs with request_id", 500
            )

    do_GET = _dispatch
    do_POST = _dispatch
    do_PUT = _dispatch
    do_DELETE = _dispatch
    do_HEAD = _dispatch

    def log_message(self, fmt, *args):
        LOG.info("%s %s", self.address_string(), fmt % args)


def make_server(host="127.0.0.1", port=8800, *, application=None, legacy=None, hunter=None):
    server = BillGenHTTPServer((host, port), Handler)
    server.application = application or ApplicationService()
    server.legacy = legacy or LegacyMounts()
    server.hunter = hunter
    return server


def serve(host="127.0.0.1", port=8800, *, hunter: bool = False, hunter_port: int = 8797):
    from .bootstrap import prepare_workspace

    prepare_workspace()
    instance = None
    if hunter:
        instance = HunterProcess(SettingsStore(), port=hunter_port)
        instance.start()
    with make_server(host, port, hunter=instance) as server:
        print(f"BillGen: http://{host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0
