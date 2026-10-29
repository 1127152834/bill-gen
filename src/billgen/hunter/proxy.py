"""把猎手的 HTTP 接口转进 billgen 的同源入口。

只做透明转发：方法、查询串、请求体、``text/event-stream`` 都原样穿过，SSE 不落缓冲，
否则进度条会等到作业结束才刷新。上游不可达回 502，由前端提示「服务未启动」。
"""

from __future__ import annotations

import http.client
import logging
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:  # pragma: no cover - 仅类型
    from http.server import BaseHTTPRequestHandler

    from .process import HunterTarget

LOG = logging.getLogger(__name__)

DEFAULT_PORTS = {80, 443}
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "accept-encoding",
}
STREAM_TIMEOUT = 900.0


def _upstream_path(path: str, *, strip_prefix: str = "") -> str:
    target = urlsplit(path)
    raw = target.path
    if strip_prefix:
        if raw == strip_prefix.rstrip("/"):
            raw = "/"
        elif raw.startswith(strip_prefix):
            raw = "/" + raw[len(strip_prefix) :].lstrip("/")
    if target.query:
        raw += "?" + target.query
    return raw or "/"


def _request_headers(handler: BaseHTTPRequestHandler, *, api_key: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in handler.headers.items():
        if key.lower() in HOP_BY_HOP:
            continue
        headers[key] = value
    if api_key and "X-API-Key" not in headers:
        headers["X-API-Key"] = api_key
    headers.setdefault("Accept-Encoding", "identity")
    return headers


def _read_body(handler: BaseHTTPRequestHandler) -> bytes | None:
    length = handler.headers.get("Content-Length")
    if not length:
        return None
    try:
        size = int(length)
    except ValueError:
        return None
    if size <= 0:
        return None
    return handler.rfile.read(size)


def forward(
    handler: BaseHTTPRequestHandler,
    target: HunterTarget,
    *,
    strip_prefix: str = "",
    api_key: str = "",
) -> bool:
    """把当前请求转发给上游；返回是否已经写过响应。"""
    path = _upstream_path(handler.path, strip_prefix=strip_prefix)
    body = _read_body(handler)
    headers = _request_headers(handler, api_key=api_key)
    connection = http.client.HTTPConnection(target.host, target.port, timeout=STREAM_TIMEOUT)
    try:
        connection.request(handler.command, path, body=body, headers=headers)
        response = connection.getresponse()
    except (OSError, http.client.HTTPException) as exc:
        connection.close()
        LOG.info("hunter proxy failed: %s", exc)
        handler._error("hunter_unavailable", f"账单猎手未响应：{exc.__class__.__name__}", 502)  # noqa: SLF001
        return True

    content_type = response.getheader("Content-Type", "application/octet-stream")
    streaming = content_type.startswith("text/event-stream")
    handler.send_response(response.status, response.reason)
    handler.send_header("Content-Type", content_type)
    for name in ("Cache-Control", "X-Request-ID"):
        value = response.getheader(name)
        if value:
            handler.send_header(name, value)
    if streaming:
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("X-Accel-Buffering", "no")
        handler.end_headers()
        try:
            while True:
                chunk = response.read(1024)
                if not chunk:
                    break
                handler.wfile.write(chunk)
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            handler.close_connection = True
        finally:
            connection.close()
        return True

    payload = response.read()
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    if handler.command != "HEAD" and payload:
        try:
            handler.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            handler.close_connection = True
    connection.close()
    return True
