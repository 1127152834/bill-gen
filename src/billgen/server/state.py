"""State and serialization for legacy editors sharing a project workspace.

This is deliberately not per-user workspace isolation. A process-wide reentrant
lock protects font globals and the complete read/build/publish window. Only AE's
last downloadable PDF and saved path belong to browser sessions.
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from functools import wraps
from http.cookies import CookieError, SimpleCookie

RENDER_LOCK = threading.RLock()


def serialized(function):
    """Serialize nested HTTP/workspace/render operations, including publication."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with RENDER_LOCK:
            return function(*args, **kwargs)
    return wrapped


@dataclass
class EditorState:
    watermark: bool = True
    mark: bool = True
    fonts: str = "declared"
    pages: int = 0
    hashes: list[str] = field(default_factory=list)

    def snapshot(self):
        return {"watermark": self.watermark, "mark": self.mark,
                "fonts": self.fonts, "pages": self.pages,
                "hashes": list(self.hashes)}


@dataclass
class BrowserState:
    pdf: bytes | None = None
    path: str | None = None
    hashes: list[str] = field(default_factory=list)
    touched: float = field(default_factory=time.monotonic)


class BrowserSessions:
    """Opaque, server-issued cookie IDs; no caller-selected session fixation."""
    cookie_name = "billgen_ae_session"

    def __init__(self, ttl=86400):
        self.ttl = ttl
        self._sessions: dict[str, BrowserState] = {}

    @serialized
    def resolve(self, cookie_header):
        now = time.monotonic()
        self._sessions = {key: value for key, value in self._sessions.items()
                          if now - value.touched < self.ttl}
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header or "")
        except CookieError:
            pass
        morsel = cookie.get(self.cookie_name)
        token = morsel.value if morsel else None
        fresh = token not in self._sessions
        if fresh:
            token = secrets.token_urlsafe(32)
            self._sessions[token] = BrowserState()
        state = self._sessions[token]
        state.touched = now
        header = (f"{self.cookie_name}={token}; Path=/; HttpOnly; SameSite=Lax"
                  if fresh else None)
        return state, header

    @serialized
    def referenced_hashes(self):
        return {h for state in self._sessions.values() for h in state.hashes}


def browser_session(function):
    """Bind one browser state per request (handlers can serve keepalive requests)."""
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        with RENDER_LOCK:
            self.session, self._session_cookie = self.sessions.resolve(
                self.headers.get("Cookie"))
            return function(self, *args, **kwargs)
    return wrapped
