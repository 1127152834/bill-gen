#!/usr/bin/env python3
"""Dependency-free HTTP contract shared by installed-wheel and container smokes."""
from __future__ import annotations

import hashlib
import json
import math
import struct
import time
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


class SmokeError(RuntimeError):
    """A reproducible smoke contract or lifecycle failure."""


def positive_timeout(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("timeout must be positive and finite")
    return number


class Client:
    def __init__(self, base_url: str, timeout: float):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}))

    def get(self, path: str, *, payload=None, status=200, content_type=None):
        if not path.startswith("/") or path.startswith("//") or urlsplit(path).netloc:
            raise SmokeError(f"Expected same-service absolute path, got {path!r}")
        data = None if payload is None else json.dumps(payload).encode()
        request = Request(self.base + path, data=data,
                          headers={} if data is None else {"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read()
                if response.status != status:
                    raise SmokeError(f"{path}: expected HTTP {status}, got {response.status}")
                if content_type and response.headers.get_content_type() != content_type:
                    raise SmokeError(f"{path}: unexpected Content-Type {response.headers.get('Content-Type')}")
                return body, response.headers
        except HTTPError as exc:
            raise SmokeError(f"{path}: HTTP {exc.code}: {exc.read(2048)!r}") from exc

    def json(self, path, **kwargs):
        body, _ = self.get(path, **kwargs)
        return json.loads(body)


def require(condition, message):
    if not condition:
        raise SmokeError(message)


def wait_ready(base_url: str, *, startup_timeout=30.0, alive: Callable[[], bool] | None = None):
    deadline = time.monotonic() + startup_timeout
    last = "service has not responded"
    while time.monotonic() < deadline:
        if alive is not None and not alive():
            raise SmokeError("service exited before readiness")
        try:
            health = Client(base_url, min(1.0, max(0.01, deadline - time.monotonic()))).json("/healthz")
            if health.get("status") == "ok" and health.get("service") == "billgen":
                return
            last = repr(health)
        except (OSError, URLError, SmokeError, ValueError) as exc:
            last = str(exc)
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    raise SmokeError(f"readiness timed out after {startup_timeout}s: {last}")


def run_smoke(base_url: str, *, timeout=60.0, template_id="boa-personal") -> dict:
    """Check health, registry, legacy data, defaults, preview and every PDF/PNG artifact."""
    client = Client(base_url, timeout)
    health = client.json("/healthz", content_type="application/json")
    require(health.get("status") == "ok" and health.get("service") == "billgen", "bad health")
    templates = client.json("/api/v1/templates", content_type="application/json")["templates"]
    ids = [item["id"] for item in templates]
    require(len(ids) == 13 and len(set(ids)) == 13, f"expected 13 distinct templates: {ids}")
    require(template_id in ids, f"missing template: {template_id}")
    html, _ = client.get("/boa/", content_type="text/html")
    require(b"<html" in html.lower() or b"<!doctype" in html.lower(), "legacy editor HTML missing")
    legacy = client.json("/boa/api/data")
    require(bool(legacy.get("personal")) and bool(legacy.get("business")), "legacy data missing")
    data = client.json(f"/api/v1/templates/{template_id}/data", content_type="application/json")
    require(isinstance(data, dict) and bool(data), "empty template defaults")
    job = client.json(f"/api/v1/templates/{template_id}/preview", payload={
        "data": data, "options": {"page_dpi": 36, "encrypt": False}},
        status=201, content_type="application/json")
    require(job.get("pages", 0) > 0 and job.get("template_id") == template_id, "bad preview job")
    links = job["links"]
    persisted = client.json(links["self"], content_type="application/json")
    require(persisted["job_id"] == job["job_id"], "job lookup mismatch")
    pdf, headers = client.get(links["pdf"], content_type="application/pdf")
    require(pdf.startswith(b"%PDF-") and b"%%EOF" in pdf[-1024:], "invalid PDF framing")
    require("attachment" in headers.get("Content-Disposition", ""), "PDF download disposition missing")
    require(len(pdf) == job["pdf"]["bytes"] and hashlib.sha256(pdf).hexdigest() == job["pdf"]["sha256"],
            "PDF length/hash mismatch")
    require(len(links["pages"]) == job["pages"], "page link count mismatch")
    dimensions = []
    for index, link in enumerate(links["pages"]):
        png, _ = client.get(link, content_type="image/png")
        require(png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR", "invalid PNG header")
        width, height = struct.unpack(">II", png[16:24])
        info = job["page_info"][index]
        require(width > 0 and height > 0 and (width, height) == (info["pixel_width"], info["pixel_height"]),
                "PNG dimensions mismatch")
        require(hashlib.sha256(png).hexdigest() == job["hashes"][index], "PNG hash mismatch")
        dimensions.append([width, height])
    return {"status": "ok", "templates": len(ids), "template": template_id,
            "pages": job["pages"], "pdf_bytes": len(pdf), "png_dimensions": dimensions}
