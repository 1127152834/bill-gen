"""Single HTTP entry point: registry API and legacy editors, without proxy ports."""

from __future__ import annotations

import io
import json
import logging
import mimetypes
import os
import re
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .. import delivery, synth
from ..jobs import Job, JobNotFound, JobQueue
from ..profiles import (
    Profile,
    ProfileError,
    ProfileNotFound,
    ProfileStore,
    ensure_accounts,
    import_csv,
    import_json,
    validate_profile,
)
from ..themes.mercury import letter_web
from . import generate_page
from .application import ApplicationError, ApplicationService
from .legacy import LegacyMounts

LOG = logging.getLogger(__name__)
MAX_BODY = 4 * 1024 * 1024
# 出片目录（compose 里 BILLGEN_OUT=/data/renders），出片的 PDF 就在这里面。
RENDERS_ENV = "BILLGEN_OUT"

EDITOR_LINKS = (
    ("/boa/", "BOA"),
    ("/wf/", "Wells Fargo"),
    ("/chase/", "Chase Personal"),
    ("/chasebiz/", "Chase Business"),
    ("/us/", "US Bank Personal"),
    ("/usbiz/", "US Bank Business"),
    ("/merc/", "Mercury / Capital One / SoFi"),
    ("/alliant/", "Alliant"),
    ("/ally/", "Ally"),
    ("/letters/mercury/", "Mercury 验证信（信件编辑器）"),
    ("/generate/", "账单 JSON 生成器"),
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


# ---- 异步任务队列的进程级单例 ------------------------------------------------
# 懒建：测试可以先 `init_jobs(tmp_path, fake_runner)` 注入，再起真服务；
# 启动路径（serve / CLI）在装配阶段调一次，路由层只读这个变量。
_JOBS: JobQueue | None = None


def init_jobs(root: Path | str, runner, *, max_workers: int = 1) -> JobQueue:
    """建（或替换）任务队列。重复调用时旧队列停止接活，跑着的任务会自己收尾。"""
    global _JOBS
    previous, _JOBS = _JOBS, JobQueue(root, runner, max_workers=max_workers)
    if previous is not None:
        previous.shutdown(wait=False)
    return _JOBS


def _build_profile(payload) -> Profile:
    """把外面塞进来的 dict 变成能入库的档案：校验 + 补账户事实。"""
    profile = _finalize_profile(Profile.from_dict(payload))
    return profile


def _finalize_profile(profile: Profile) -> Profile:
    problems = validate_profile(profile)
    if problems:
        raise ProfileError("；".join(problems))
    ensure_accounts(profile)
    return profile


# ---- 人物档案库的进程级单例 --------------------------------------------------
# 路径优先级：BILLGEN_PROFILES > (BILL_GEN_DATA_DIR 或 ".data")/billgen/profiles.json。
# 容器里 compose 会给 BILLGEN_PROFILES=/data/billgen/profiles.json（落在挂载卷上）。
_PROFILES: ProfileStore | None = None


def profiles_store(path: Path | str | None = None) -> ProfileStore:
    """取档案库（首次调用时按环境变量定位并读盘）。"""
    global _PROFILES
    if _PROFILES is None or path is not None:
        target = path or os.environ.get("BILLGEN_PROFILES") or (
            Path(os.environ.get("BILL_GEN_DATA_DIR") or ".data") / "billgen" / "profiles.json"
        )
        store = ProfileStore(target)
        store.load()
        _PROFILES = store
    return _PROFILES


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

    def _renders_dir(self) -> Path:
        """出片目录：compose 里 BILLGEN_OUT=/data/renders，任务产物也落在这下面。"""
        return Path(os.environ.get(RENDERS_ENV) or "renders").expanduser().resolve()

    def _render_file(self, raw_name: str):
        """把一份出片发给浏览器：PDF、月模板 JSON、预览 PNG 都走这里。

        只认文件名，路径穿越一律当 404。后缀决定 content-type —— 交付物已经改成
        「每月一份 JSON」，只放 PDF 会让页面上的下载按钮全部 404。
        """
        from urllib.parse import unquote

        name = unquote(raw_name).strip()
        suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        types = {"pdf": "application/pdf", "json": "application/json; charset=utf-8", "png": "image/png"}
        if not name or "/" in name or "\\" in name or ".." in name or suffix not in types:
            return self._error("not_found", "Render not found", 404)
        root = self._renders_dir()
        target = (root / name).resolve()
        if root not in target.parents or not target.is_file():
            return self._error("not_found", "Render not found", 404)
        disposition = "attachment" if suffix == "json" else "inline"
        return self._send(
            target.read_bytes(),
            types[suffix],
            headers={
                # inline：点「预览」直接在标签页里看；JSON 用 attachment，别让浏览器当页面渲染。
                "Content-Disposition": f'{disposition}; filename="{name}"',
            },
        )

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

    def _job_links(self, job):
        job = dict(job)
        base = f"/api/v1/jobs/{job['job_id']}"
        job["links"] = {
            "self": base,
            "pdf": base + "/document.pdf",
            "pages": [base + f"/pages/{i}.png" for i in range(job["pages"])],
        }
        return job

    # ---- 异步任务队列路由（/api/v1/gen/jobs）---------------------------------
    _GEN_JOBS = "/api/v1/gen/jobs"
    _GEN_JOB = re.compile(r"/api/v1/gen/jobs/([A-Za-z0-9_-]+)\Z")
    _GEN_JOB_ACTION = re.compile(r"/api/v1/gen/jobs/([A-Za-z0-9_-]+)/(cancel|package)\Z")
    _GEN_JOB_FILE = re.compile(r"/api/v1/gen/jobs/([A-Za-z0-9_-]+)/files/([^/]+)\Z")
    # 单份下载只放这四种后缀：PDF 出片、月模板 JSON、预览 PNG、日志 TXT。
    _JOB_FILE_TYPES = {
        "json": "application/json; charset=utf-8",
        "pdf": "application/pdf",
        "png": "image/png",
        "txt": "text/plain; charset=utf-8",
    }
    _MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])\Z")

    _PROFILE_ROUTE = re.compile(r"/api/v1/profiles/([0-9a-zA-Z_-]{1,64})\Z")

    def _profiles_api(self, path: str):
        """`/api/v1/profiles*`：档案库的增删改查 + 批量导入。"""
        store = profiles_store()
        if path == "/api/v1/profiles":
            if self.command in ("GET", "HEAD"):
                return self._json({"profiles": [p.to_dict() for p in store.all()]})
            if self.command != "POST":
                return self._error("method_not_allowed", "Method not allowed", 405)
            payload = self._raw_json()
            try:
                profile = _build_profile(payload)
            except ProfileError as exc:
                return self._error("invalid_profile", str(exc), 400)
            return self._json(store.upsert(profile).to_dict(), 201)
        if path == "/api/v1/profiles/import":
            if self.command != "POST":
                return self._error("method_not_allowed", "Method not allowed", 405)
            payload = self._raw_json()
            kind = str(payload.get("kind") or "json").lower()
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                return self._error("invalid_request", "缺少要导入的内容（text）", 400)
            try:
                result = import_csv(text) if kind == "csv" else import_json(text)
            except ProfileError as exc:
                return self._error("invalid_import", str(exc), 400)
            saved = []
            for candidate in result.profiles:
                try:
                    saved.append(store.upsert(_finalize_profile(candidate)).to_dict())
                except ProfileError as exc:
                    result.errors.append({"row": -1, "error": str(exc)})
            return self._json({"profiles": saved, "errors": list(result.errors)})
        match = self._PROFILE_ROUTE.match(path)
        if not match:
            return self._error("not_found", "Profile endpoint not found", 404)
        pid = match.group(1)
        if self.command in ("GET", "HEAD"):
            try:
                return self._json(store.get(pid).to_dict())
            except ProfileNotFound:
                return self._error("not_found", "档案不存在", 404)
        if self.command == "DELETE":
            try:
                store.delete(pid)
            except ProfileNotFound:
                return self._error("not_found", "档案不存在", 404)
            return self._json({"deleted": True, "id": pid})
        if self.command in ("PUT", "PATCH"):
            payload = self._raw_json()
            try:
                current = store.get(pid)
                merged = {**current.to_dict(), **payload, "id": pid}
                return self._json(store.upsert(_build_profile(merged)).to_dict())
            except ProfileNotFound:
                return self._error("not_found", "档案不存在", 404)
            except ProfileError as exc:
                return self._error("invalid_profile", str(exc), 400)
        return self._error("method_not_allowed", "Method not allowed", 405)

    def _gen_jobs(self, path: str):
        """`/api/v1/gen/jobs*` 的总入口：未初始化先回 503，再按形状分派。"""
        queue = _JOBS
        if queue is None:
            return self._error("jobs_unavailable", "任务队列未初始化", 503)
        if path == self._GEN_JOBS:
            if self.command in ("GET", "HEAD"):
                return self._json({"jobs": [job.to_dict() for job in queue.list()]})
            if self.command == "POST":
                return self._submit_job(queue)
            return self._error("method_not_allowed", "Method not allowed", 405)
        match = self._GEN_JOB.match(path)
        if match:
            job = self._job_or_404(queue, match.group(1))
            if self.command in ("GET", "HEAD"):
                return self._json(job.to_dict())
            return self._error("method_not_allowed", "Method not allowed", 405)
        match = self._GEN_JOB_ACTION.match(path)
        if match:
            job_id, action = match.groups()
            if action == "cancel":
                if self.command != "POST":
                    return self._error("method_not_allowed", "Method not allowed", 405)
                self._job_or_404(queue, job_id)
                return self._json(queue.cancel(job_id).to_dict())
            if self.command not in ("GET", "HEAD"):
                return self._error("method_not_allowed", "Method not allowed", 405)
            return self._job_package(self._job_or_404(queue, job_id))
        match = self._GEN_JOB_FILE.match(path)
        if match:
            if self.command not in ("GET", "HEAD"):
                return self._error("method_not_allowed", "Method not allowed", 405)
            return self._job_file(self._job_or_404(queue, match.group(1)), match.group(2))
        return self._error("not_found", "Job endpoint not found", 404)

    def _submit_job(self, queue: JobQueue):
        """POST body：``{profile_id, template, months, seed?}`` → 202 + job dict。"""
        payload = self._body(("profile_id", "template", "months", "seed"))
        profile_id = payload.get("profile_id")
        template = payload.get("template")
        months = payload.get("months")
        seed = payload.get("seed")
        if not isinstance(profile_id, str) or not profile_id.strip():
            raise ApplicationError("invalid_request", "profile_id is required", 400)
        if not isinstance(template, str) or not template.strip():
            raise ApplicationError("invalid_request", "template is required", 400)
        if not isinstance(months, list) or not months:
            raise ApplicationError("invalid_request", "months 必须是非空数组", 400)
        for month in months:
            if not isinstance(month, str) or not self._MONTH.match(month):
                raise ApplicationError("invalid_request", f"months 里的 {month!r} 不是 YYYY-MM", 400)
        if seed is not None and not isinstance(seed, str):
            raise ApplicationError("invalid_request", "seed 必须是字符串", 400)
        job = queue.submit(profile_id.strip(), template.strip(), list(months), seed=seed)
        return self._json(job.to_dict(), 202)

    def _job_or_404(self, queue: JobQueue, job_id: str) -> Job:
        """未知 id 统一 404，别让 LookupError 掉到 500 分支。"""
        try:
            return queue.get(job_id)
        except JobNotFound as exc:
            raise ApplicationError("job_not_found", f"任务不存在：{job_id}", 404) from exc

    def _job_package(self, job: Job):
        """把 out_dir 打成 zip 流回；目录没了就是 404（任务被清过）。"""
        out_dir = Path(job.out_dir)
        if not out_dir.is_dir():
            raise ApplicationError("not_found", "任务产物目录不存在", 404)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for item in sorted(out_dir.iterdir()):
                if not item.is_file() or item.name.startswith(".") or item.name.endswith(".tmp"):
                    continue
                # 密钥类文件不进包：交付物里不该出现任何 *_api_key* 的东西。
                if "api_key" in item.name.lower():
                    continue
                # 成员名用 basename，解压出来是平铺的一层，不会带出服务器绝对路径。
                archive.write(item, arcname=os.path.basename(item.name))
        return self._send(
            buffer.getvalue(),
            "application/zip",
            headers={"Content-Disposition": f'attachment; filename="{job.id}-package.zip"'},
        )

    def _job_file(self, job: Job, raw_name: str):
        """单份下载：只认 basename、不认点开头、后缀白名单。"""
        name = unquote(raw_name).strip()
        suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if (
            not name
            or name != os.path.basename(name)
            or ".." in name
            or "\\" in name
            or name.startswith(".")
            or suffix not in self._JOB_FILE_TYPES
        ):
            return self._error("not_found", "Job artifact not found", 404)
        out_dir = Path(job.out_dir).resolve()
        target = (out_dir / name).resolve()
        if out_dir not in target.parents or not target.is_file():
            return self._error("not_found", "Job artifact not found", 404)
        disposition = "attachment" if suffix in ("json", "txt") else "inline"
        return self._send(
            target.read_bytes(),
            self._JOB_FILE_TYPES[suffix],
            headers={"Content-Disposition": f'{disposition}; filename="{name}"'},
        )

    # ---- Mercury 验证信编辑器 -------------------------------------------------
    _LETTER_BASE = "/letters/mercury"

    def _letter(self, path: str):
        base = self._LETTER_BASE
        tail = path[len(base):].rstrip("/")
        # 页面里的 fetch 写的是 `<BASE>api/render`（独立编辑器 letter_studio 的路由是
        # `/api/*`），挂到主服务后少了一层前缀，这里把可选的 `api` 吃掉，两种写法都
        # 落到同一处——否则前端 404 只会在错误条里印一句 [object Object]。
        if tail.startswith("/api/"):
            tail = tail[len("/api"):]
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
            if path in ("/generate", "/generate/"):
                if self.command not in ("GET", "HEAD"):
                    return self._error("method_not_allowed", "Method not allowed", 405)
                return self._send(generate_page.page_bytes(), "text/html; charset=utf-8")
            if path.startswith("/api/v1/renders/") and self.command in ("GET", "HEAD"):
                return self._render_file(path.removeprefix("/api/v1/renders/"))
            if path.startswith("/api/v1/"):
                # 异步任务队列：/api/v1/gen/jobs 系列，未初始化走 503。
                if path == self._GEN_JOBS or path.startswith(self._GEN_JOBS + "/"):
                    return self._gen_jobs(path)
                if path.startswith("/api/v1/profiles"):
                    return self._profiles_api(path)
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
                if self.command == "POST" and path == "/api/v1/synth/months":
                    payload = self._body(("template", "result", "account", "cycle", "bank_name", "months"))
                    template = payload.pop("template", None)
                    if not isinstance(template, str) or not template:
                        raise ApplicationError("invalid_request", "template is required", 400)
                    if not isinstance(payload.get("result"), dict):
                        raise ApplicationError("invalid_request", "result is required", 400)
                    try:
                        body, manifest = delivery.build_package(
                            template, payload.pop("result"), **payload
                        )
                    except (KeyError, ValueError, synth.SynthLintError) as err:
                        return self._json({"error": str(err)}, 400)
                    return self._send(
                        body,
                        "application/zip",
                        headers={
                            "Content-Disposition": f'attachment; filename="{template}-months.zip"',
                            "X-Month-Count": str(manifest["count"]),
                            "X-Month-OK": "1" if manifest["ok"] else "0",
                        },
                    )
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


def make_server(host="127.0.0.1", port=8800, *, application=None, legacy=None):
    server = BillGenHTTPServer((host, port), Handler)
    server.application = application or ApplicationService()
    server.legacy = legacy or LegacyMounts()
    return server


def serve(host="127.0.0.1", port=8800):
    from .bootstrap import prepare_workspace

    prepare_workspace()
    # 任务队列：产物落 BILLGEN_JOBS_DIR（容器里是 /data/jobs），生成链路走 Python 版 worklow。
    from ..gen.runner import make_runner

    jobs_root = Path(
        os.environ.get("BILLGEN_JOBS_DIR")
        or (Path(os.environ.get("BILL_GEN_DATA_DIR") or "data") / "jobs")
    )
    init_jobs(
        jobs_root,
        make_runner(),
        max_workers=int(os.environ.get("BILLGEN_JOB_WORKERS") or 2),
    )
    with make_server(host, port) as server:
        print(f"BillGen: http://{host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0
