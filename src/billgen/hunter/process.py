"""账单猎手（Node/LangGraph 服务）的子进程管理。

billgen 是唯一入口：它把猎手当成受管子进程拉起，转发 ``/hunter/`` 与 ``/api/generations``，
前端改完模型配置后调 ``restart()`` 让新配置生效。进程起不来时状态里给原因，不抛给请求层。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import paths
from .settings import HunterSettings, SettingsStore, to_env

LOG = logging.getLogger(__name__)


class HunterUnavailable(RuntimeError):
    """猎手没有可用的运行环境（源码树里没有 node_modules / 没有 node）。"""


@dataclass(frozen=True, slots=True)
class HunterTarget:
    host: str
    port: int

    @property
    def base(self) -> str:
        return f"http://{self.host}:{self.port}"


def default_flow_root() -> Path:
    explicit = os.environ.get("BILLGEN_FLOW_ROOT", "").strip()
    if explicit:
        return Path(explicit)
    return Path(paths.ROOT) / "flow"


class HunterProcess:
    """受管子进程 + 状态快照。所有公开方法都可并发调用。"""

    LOG_LINES = 120

    def __init__(
        self,
        store: SettingsStore,
        *,
        flow_root: Path | None = None,
        port: int = 8797,
        startup_timeout: float = 60.0,
    ) -> None:
        self.store = store
        self.flow_root = Path(flow_root) if flow_root else default_flow_root()
        self.target = HunterTarget("127.0.0.1", port)
        self.startup_timeout = startup_timeout
        self._lock = threading.RLock()
        self._child: subprocess.Popen[bytes] | None = None
        self._logs: deque[str] = deque(maxlen=self.LOG_LINES)
        self._error = ""
        self._ready = False
        self._enabled = self._probe_environment()[0]

    # ---- 环境 -----------------------------------------------------------
    def _probe_environment(self) -> tuple[bool, str]:
        if not self.flow_root.is_dir():
            return False, f"未找到 flow 目录：{self.flow_root}"
        entry = self.flow_root / "apps" / "server" / "src" / "index.ts"
        if not entry.is_file():
            return False, "flow 源码缺失（apps/server/src/index.ts）"
        if not (self.flow_root / "node_modules").is_dir():
            return False, "flow 依赖未安装（node_modules 缺失，先跑 pnpm install）"
        if not self._runner():
            return False, "node 运行时不可用"
        return True, ""

    def _runner(self) -> list[str] | None:
        explicit = os.environ.get("BILLGEN_FLOW_CMD", "").strip()
        if explicit:
            return explicit.split()
        tsx = self.flow_root / "node_modules" / ".bin" / "tsx"
        if tsx.is_file():
            return [tsx.as_posix(), "apps/server/src/index.ts"]
        node = shutil.which("node")
        if node and (self.flow_root / "apps" / "server" / "dist" / "index.js").is_file():
            return [node, "apps/server/dist/index.js"]
        return None

    # ---- 生命周期 -------------------------------------------------------
    def start(self) -> dict[str, Any]:
        with self._lock:
            if not self._enabled:
                return self.status()
            if self._child is not None and self._child.poll() is None:
                return self.status()
            runner = self._runner()
            if runner is None:
                self._enabled = False
                self._error = "缺少运行命令（node_modules/.bin/tsx 或 dist）"
                return self.status()
            settings = self.store.load()
            env = dict(os.environ)
            env.update(to_env(settings, port=self.target.port, flow_root=self.flow_root))
            Path(env["DATABASE_PATH"]).parent.mkdir(parents=True, exist_ok=True)
            Path(env["BILLGEN_OUT"]).mkdir(parents=True, exist_ok=True)
            self._logs.clear()
            self._error = ""
            self._ready = False
            try:
                self._child = subprocess.Popen(  # noqa: S603 - 命令来自配置，不是请求体
                    runner,
                    cwd=self.flow_root.as_posix(),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError as exc:
                self._error = f"启动失败：{exc.strerror or exc}"
                self._child = None
                return self.status()
            thread = threading.Thread(target=self._drain, args=(self._child,), daemon=True)
            thread.start()
            LOG.info("hunter started pid=%s port=%s", self._child.pid, self.target.port)
        self._wait_ready()
        return self.status()

    def _drain(self, child: subprocess.Popen[bytes]) -> None:
        stream = child.stdout
        if stream is None:
            return
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self._logs.append(line)
        with contextlib.suppress(OSError):
            stream.close()

    def _wait_ready(self, timeout: float | None = None) -> bool:
        deadline = time.monotonic() + (timeout if timeout is not None else self.startup_timeout)
        while time.monotonic() < deadline:
            child = self._child
            if child is not None and child.poll() is not None:
                self._error = f"子进程退出（code {child.returncode}）"
                self._ready = False
                return False
            if self._health():
                self._ready = True
                self._error = ""
                return True
            time.sleep(0.25)
        self._error = self._error or "启动超时：健康检查未通过"
        return False

    def _health(self) -> bool:
        try:
            with urllib.request.urlopen(self.target.base + "/api/health", timeout=2) as response:
                return response.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def stop(self, *, timeout: float = 10.0) -> None:
        with self._lock:
            child = self._child
            self._child = None
            self._ready = False
            if child is None or child.poll() is not None:
                return
            try:
                os.killpg(os.getpgid(child.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                child.terminate()
            try:
                child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(child.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    child.kill()
                child.wait(timeout=5)
            LOG.info("hunter stopped")

    def restart(self) -> dict[str, Any]:
        self.stop()
        return self.start()

    # ---- 状态 -----------------------------------------------------------
    def status(self) -> dict[str, Any]:
        with self._lock:
            child = self._child
            running = child is not None and child.poll() is None
            settings = self.store.load()
            return {
                "enabled": self._enabled,
                "running": running,
                "ready": running and self._ready,
                "pid": child.pid if running and child else None,
                "port": self.target.port,
                "url": "/hunter/",
                "error": self._error,
                "logs": list(self._logs)[-40:],
                "settings": settings.public(),
                "environment": self._probe_environment()[1] if not self._enabled else "",
            }

    def close(self) -> None:
        self.stop(timeout=6.0)


def load_settings(store: SettingsStore) -> HunterSettings:
    """便捷读取（给测试与 CLI 用）。"""
    return store.load()


def dump_status(process: HunterProcess) -> str:
    return json.dumps(process.status(), ensure_ascii=False)
