"""Transport-free application API; bounded in-memory jobs, no workspace writes.

Page indices are zero-based. Returned dictionaries are JSON-serializable copies.
IDs are opaque server-issued artifact handles; request_id is correlation only,
never a cache key and never reused to overwrite another request's artifacts.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import secrets
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any

from .. import registry
from ..spec import RenderOptions
from ..synth import (
    LedgerError,
    SynthLintError,
    apply_brand,
    apply_docinfo,
    binding_for,
    build_data,
    from_game_result,
    normalize_id,
    supported_ids,
)
from ..synth.adapters import BindingError
from .state import RENDER_LOCK

__all__ = ["ApplicationError", "ApplicationService"]

# Verified against legacy editor export branches and generator save routines.
_ENCRYPTABLE = frozenset({"chase-personal", "chase-business",
                          "usbank-personal", "usbank-business"})
_DEFAULT_ENCRYPT: frozenset[str] = frozenset()


class ApplicationError(Exception):
    """Public transport-neutral error. Internal exceptions never become messages."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


@dataclass
class _Job:
    created: float
    info: dict[str, Any]
    pdf: bytes
    pngs: tuple[bytes, ...]

    @property
    def size(self) -> int:
        return len(self.pdf) + sum(map(len, self.pngs))


class ApplicationService:
    """One shared service per HTTP server; all public operations use RENDER_LOCK.

    preview/export_pdf accept a JSON object or None (template defaults), and
    RenderOptions or a partial options dict. preview defaults to unencrypted;
    export defaults to each template's historical policy. Explicit encrypt is
    respected, or rejected where the legacy generator has no encryption support.
    Cache TTL is absolute since creation, with oldest-accessed eviction by count
    and total PDF+PNG bytes. Missing/expired artifact handles yield job_not_found.
    """

    def __init__(self, *, max_jobs: int = 32, ttl_seconds: float = 900,
                 max_cache_bytes: int = 128 * 1024 * 1024,
                 max_data_bytes: int = 2 * 1024 * 1024,
                 max_pages: int = 100, max_page_pixels: int = 25_000_000):
        for name, value in {"max_jobs": max_jobs, "max_cache_bytes": max_cache_bytes,
                            "max_data_bytes": max_data_bytes, "max_pages": max_pages,
                            "max_page_pixels": max_page_pixels}.items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)) or not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive and finite")
        self.max_jobs, self.ttl_seconds = max_jobs, ttl_seconds
        self.max_cache_bytes, self.max_data_bytes = max_cache_bytes, max_data_bytes
        self.max_pages, self.max_page_pixels = max_pages, max_page_pixels
        self._jobs: OrderedDict[str, _Job] = OrderedDict()

    def _spec(self, template_id):
        if not isinstance(template_id, str):
            raise ApplicationError("invalid_template", "template_id must be a string")
        try:
            return registry.get_spec(template_id)
        except registry.TemplateNotFound:
            raise ApplicationError("template_not_found", "Unknown template", 404) from None

    def list_templates(self) -> list[dict[str, Any]]:
        with RENDER_LOCK:
            return [{"id": spec.id, "display_name": spec.display_name,
                     "kind": spec.kind, "server_render": spec.kind != "static-web",
                     "supports": ([] if spec.kind == "static-web" else
                                  ["preview", "watermark", "page_dpi", "font_policy"] +
                                  (["encrypt"] if spec.id in _ENCRYPTABLE else [])),
                     "default_options": {**asdict(spec.default_options),
                                         "encrypt": spec.id in _DEFAULT_ENCRYPT},
                     "static_entry": (f"/{spec.id}/index.html" if spec.kind == "static-web" else None)}
                    for spec in registry.SPECS]

    def _server_spec(self, template_id):
        spec = self._spec(template_id)
        if spec.kind == "static-web":
            raise ApplicationError("static_template", "Template is browser-only; use its static entry", 422)
        return spec

    def default_data(self, template_id: str) -> dict[str, Any]:
        with RENDER_LOCK:
            self._server_spec(template_id)
            try:
                return copy.deepcopy(registry.get_renderer(template_id).default_data())
            except Exception:
                raise ApplicationError("default_data_failed", "Template default data is unavailable", 500) from None

    def _options(self, spec, options, mode):
        values = asdict(spec.default_options)
        values["encrypt"] = mode == "export" and spec.id in _DEFAULT_ENCRYPT
        if isinstance(options, RenderOptions):
            options = asdict(options)
        if options is not None:
            if not isinstance(options, Mapping) or any(key not in values for key in options):
                raise ApplicationError("invalid_options", "Unknown or malformed rendering options")
            values.update(options)
        if type(values["encrypt"]) is not bool or type(values["watermark"]) is not bool:
            raise ApplicationError("invalid_options", "encrypt and watermark must be booleans")
        if type(values["page_dpi"]) is not int or not 36 <= values["page_dpi"] <= 300:
            raise ApplicationError("invalid_options", "page_dpi must be an integer from 36 to 300")
        if values["font_policy"] not in ("declared", "subset", "embed-all"):
            raise ApplicationError("invalid_options", "Unknown font_policy")
        if values["encrypt"] and spec.id not in _ENCRYPTABLE:
            raise ApplicationError("unsupported_option", "Template does not support encryption", 422)
        return RenderOptions(**values)

    def synth_capabilities(self) -> dict[str, Any]:
        """哪些模板能直接吃场景账本，以及各自接受的账户类型。"""
        with RENDER_LOCK:
            templates = []
            for template_id in supported_ids():
                try:
                    binding = binding_for(template_id)
                except BindingError:
                    continue
                templates.append({"id": template_id,
                                  "display_name": registry.get_spec(template_id).display_name,
                                  "accounts": sorted(binding.accounts)})
            return {"accounts": ["checking", "business_checking"], "templates": templates}

    def _result(self, result):
        if result is None:
            raise ApplicationError("invalid_result", "result is required")
        if not isinstance(result, dict):
            raise ApplicationError("invalid_result", "result must be a JSON object")
        try:
            encoded = json.dumps(result, allow_nan=False, ensure_ascii=False)
        except (TypeError, ValueError, RecursionError):
            raise ApplicationError("invalid_result", "result must contain finite JSON values") from None
        if len(encoded.encode("utf-8")) > self.max_data_bytes:
            raise ApplicationError("result_too_large", "Generation result exceeds size limit", 413)
        return json.loads(encoded)

    def render_synth(self, template_id: str, result=None, *, account=None, cycle=-1,
                     bank_name=None, watermark=True, brand=True, docinfo=True,
                     options=None, request_id=None):
        """场景生成结果 → 模板数据 → PDF，返回与模板导出同形状的任务。"""
        with RENDER_LOCK:
            self._server_spec(template_id)
            try:
                binding = binding_for(template_id)
            except BindingError:
                raise ApplicationError("template_unsupported",
                                       "Template has no ledger mapping", 422) from None
            if account is not None:
                if not isinstance(account, str):
                    raise ApplicationError("invalid_account", "account must be a string")
                if not binding.accepts(account):
                    raise ApplicationError(
                        "account_mismatch",
                        "Template accepts " + ", ".join(sorted(binding.accounts)), 422)
            if type(cycle) is not int or not -60 <= cycle <= 59:
                raise ApplicationError("invalid_cycle", "cycle must be an integer index")
            if bank_name is not None and (not isinstance(bank_name, str) or not 1 <= len(bank_name) <= 80):
                raise ApplicationError("invalid_bank_name", "bank_name must be 1-80 characters")
            if type(watermark) is not bool:
                raise ApplicationError("invalid_options", "watermark must be a boolean")
            if type(brand) is not bool:
                raise ApplicationError("invalid_options", "brand must be a boolean")
            if type(docinfo) is not bool:
                raise ApplicationError("invalid_options", "docinfo must be a boolean")
            payload = self._result(result)
            try:
                ledger = from_game_result(payload, account=account, cycle=cycle,
                                          bank_name=bank_name, watermark=watermark)
                if not binding.accepts(ledger.account.kind):
                    raise ApplicationError(
                        "account_mismatch",
                        "Template accepts " + ", ".join(sorted(binding.accounts))
                        + f"; result has {ledger.account.kind}", 422)
                data = build_data(template_id, ledger)
            except LedgerError as exc:
                raise ApplicationError("invalid_result", str(exc), 422) from None
            except SynthLintError as exc:
                raise ApplicationError("ledger_check_failed", str(exc), 422) from None
            job = self._render(template_id, data, options, request_id, "export",
                               stable=True,
                               brand_ledger=ledger if brand else None,
                               docinfo_ledger=ledger if docinfo else None)
            job["ledger"] = {"account": ledger.account.kind,
                             "bank": ledger.bank.name,
                             "holder": ledger.holder.name,
                             "period": [ledger.period.start.isoformat(), ledger.period.end.isoformat()],
                             "rows": len(ledger.rows),
                             "opening_cents": ledger.opening_cents,
                             "closing_cents": ledger.closing_cents}
            job["data"] = data
            return job

    def _data(self, template_id, data):
        if data is None:
            data = self.default_data(template_id)
        if not isinstance(data, dict):
            raise ApplicationError("invalid_data", "data must be a JSON object")
        try:
            # JSON round-trip both validates and detaches nested request state.
            encoded = json.dumps(data, allow_nan=False, ensure_ascii=False)
            if len(encoded.encode("utf-8")) > self.max_data_bytes:
                raise ApplicationError("data_too_large", "Request data exceeds size limit", 413)
            return json.loads(encoded)
        except (TypeError, ValueError, RecursionError):
            raise ApplicationError("invalid_data", "data must contain finite JSON values") from None

    def preview(self, template_id: str, data=None, options=None, *, request_id=None):
        return self._render(template_id, data, options, request_id, "preview")

    def export_pdf(self, template_id: str, data=None, options=None, *, request_id=None):
        return self._render(template_id, data, options, request_id, "export")

    def _render(self, template_id, data, options, request_id, mode, *, stable=False,
                brand_ledger=None, docinfo_ledger=None):
        with RENDER_LOCK:
            spec = self._server_spec(template_id)
            opts = self._options(spec, options, mode)
            if request_id is None:
                request_id = secrets.token_urlsafe(18)
            if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128 or any(ord(c) < 32 or ord(c) == 127 for c in request_id):
                raise ApplicationError("invalid_request_id", "request_id must be 1-128 printable characters")
            payload = self._data(template_id, data)
            started = time.perf_counter()
            try:
                import fitz
                result = registry.get_renderer(template_id).render(payload, opts)
                if brand_ledger is not None:
                    branded = apply_brand(result.pdf, template_id, brand_ledger)
                    if branded != result.pdf:
                        result = replace(result, pdf=branded)
                if docinfo_ledger is not None:
                    stamped = apply_docinfo(result.pdf, template_id, docinfo_ledger)
                    if stamped != result.pdf:
                        result = replace(result, pdf=stamped)
                pngs, page_info = [], []
                with fitz.open(stream=result.pdf, filetype="pdf") as doc:
                    if doc.needs_pass:
                        raise ApplicationError("pdf_password_required", "Generated PDF requires a password", 422)
                    if not 0 < doc.page_count <= self.max_pages:
                        raise ApplicationError("artifact_too_large", "PDF page count exceeds limit", 413)
                    metadata = dict(doc.metadata)
                    permissions = doc.permissions
                    for index, page in enumerate(doc):
                        if page.rect.width * page.rect.height * (opts.page_dpi / 72) ** 2 > self.max_page_pixels:
                            raise ApplicationError("artifact_too_large", "Preview page exceeds pixel limit", 413)
                        pix = page.get_pixmap(dpi=opts.page_dpi, alpha=False)
                        png = pix.tobytes("png")
                        pngs.append(png)
                        if len(result.pdf) + sum(map(len, pngs)) > self.max_cache_bytes:
                            raise ApplicationError("artifact_too_large", "Artifacts exceed cache byte limit", 413)
                        page_info.append({"index": index, "width": page.rect.width,
                                          "height": page.rect.height, "pixel_width": pix.width,
                                          "pixel_height": pix.height,
                                          "sha256": hashlib.sha256(png).hexdigest()})
                job_id = secrets.token_urlsafe(24)
                info = {"job_id": job_id, "request_id": request_id, "template_id": template_id,
                        "mode": mode, "pages": len(pngs), "options": asdict(opts),
                        "hashes": [page["sha256"] for page in page_info], "page_info": page_info,
                        "pdf": {"bytes": len(result.pdf), "sha256": hashlib.sha256(result.pdf).hexdigest(),
                                "metadata": metadata, "permissions": permissions,
                                "encrypted": bool(metadata.get("encryption")),
                                "content_type": "application/pdf"},
                        "elapsed_ms": int((time.perf_counter() - started) * 1000)}
            except ApplicationError:
                raise
            except Exception:
                raise ApplicationError("render_failed", "Template rendering failed", 422) from None
            pdf = result.pdf
            if stable and not metadata.get("encryption"):
                pdf = normalize_id(pdf)
                info["pdf"]["bytes"] = len(pdf)
                info["pdf"]["sha256"] = hashlib.sha256(pdf).hexdigest()
            self._prune()
            self._jobs[job_id] = _Job(time.monotonic(), info, pdf, tuple(pngs))
            while len(self._jobs) > self.max_jobs or sum(job.size for job in self._jobs.values()) > self.max_cache_bytes:
                self._jobs.popitem(last=False)
            return copy.deepcopy(info)

    def _prune(self):
        now = time.monotonic()
        for key in list(self._jobs):
            if now - self._jobs[key].created >= self.ttl_seconds:
                del self._jobs[key]

    def _job(self, job_id):
        self._prune()
        if not isinstance(job_id, str) or job_id not in self._jobs:
            raise ApplicationError("job_not_found", "Job has expired or does not exist", 404)
        self._jobs.move_to_end(job_id)
        return self._jobs[job_id]

    def get_job(self, job_id: str) -> dict[str, Any]:
        with RENDER_LOCK:
            return copy.deepcopy(self._job(job_id).info)

    def get_pdf(self, job_id: str) -> bytes:
        with RENDER_LOCK:
            return self._job(job_id).pdf

    def get_page_png(self, job_id: str, page_index: int) -> bytes:
        with RENDER_LOCK:
            job = self._job(job_id)
            if type(page_index) is not int or not 0 <= page_index < len(job.pngs):
                raise ApplicationError("page_not_found", "Page does not exist", 404)
            return job.pngs[page_index]

    def close(self) -> None:
        with RENDER_LOCK:
            self._jobs.clear()
