"""异步任务队列：一人 × 一银行 = 一个任务，后台跑、页面轮询进度、可取消。

设计要点
--------
* 纯标准库：``ThreadPoolExecutor`` + ``threading.Event``，进程内排队，不引外部依赖；
* 每次状态变化都重写 ``<root>/<job_id>/job.json``，页面与 CLI 都能据此看到现场；
* 进程重启时 ``queued``/``running`` 一律降级成 ``failed``（"进程重启，任务中断"）——
  runner 的线程已经随旧进程消失，留着就是永远转圈的幽灵任务；
* 所有 Job 变更都在同一把 ``RLock`` 里完成，进度追加也走它，避免多线程写坏列表；
* 落盘前统一脱敏：``api_key``/``token`` 这类字段只写 ``***``，产物里不留密钥。

产物约定
--------
``ctx.out_dir`` 就是 ``<root>/<job_id>/``，runner 往里写 ``json``/``pdf``/``png``/``txt``，
HTTP 层再把整个目录打成 zip、或单份取出来给浏览器。
"""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = ["JobCancelled", "JobContext", "Job", "JobNotFound", "JobQueue", "Runner"]

LOG = logging.getLogger(__name__)

# 异常文本只留前 500 字：够定位，又不至于把整段 traceback 写进 job.json 和页面。
ERROR_LIMIT = 500
# 重启后给中断任务补的说明，页面直接展示这句话。
RESTART_ERROR = "进程重启，任务中断"
TERMINAL = ("done", "failed", "cancelled")
# 落盘时要抹掉的字段名（归一化后比较）：api_key / apikey / api-key 都算同一个。
_SECRET_KEYS = frozenset(
    {"api_key", "apikey", "secret", "service_key", "token", "authorization", "password"}
)

Runner = Callable[["JobContext", "Job"], dict]


class JobCancelled(Exception):
    """runner 收到取消信号后主动抛出；队列据此把任务落成 ``cancelled``。"""


class JobNotFound(LookupError):
    """未知 job_id。用 LookupError 而不是 KeyError，免得 str(exc) 带引号。"""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"未知任务：{job_id}")
        self.job_id = job_id


def _now() -> str:
    """统一用 UTC ISO8601，跨时区排 created_at 不会错位。"""
    return datetime.now(UTC).isoformat()


def _sanitize(value: Any) -> Any:
    """递归脱敏：密钥类字段一律写 ``***``，其余原样。"""
    if isinstance(value, dict):
        cleaned: dict[Any, Any] = {}
        for key, item in value.items():
            name = str(key).strip().lower().replace("-", "_")
            cleaned[key] = "***" if name in _SECRET_KEYS else _sanitize(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    return value


def _last_percent(progress: list[dict]) -> int | None:
    """取进度里最后一个非空 percent，用来恢复「单调不减」的基准线。"""
    for entry in reversed(progress):
        percent = entry.get("percent")
        if isinstance(percent, int):
            return percent
    return None


@dataclass
class Job:
    """一个任务的全部可序列化状态。runner 拿到的是同一个对象（只读用）。"""

    id: str
    profile_id: str
    template: str
    months: list[str]
    status: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    progress: list[dict] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    out_dir: str = ""

    @property
    def seed(self) -> str | None:
        """submit 传进来的随机种子；不落 job.json（不属于任务状态的公共契约）。"""
        return getattr(self, "_seed", None)

    def to_dict(self) -> dict[str, Any]:
        """全量快照；progress 深拷贝，调用方改返回值不会污染队列。"""
        return {
            "id": self.id,
            "profile_id": self.profile_id,
            "template": self.template,
            "months": list(self.months),
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "progress": copy.deepcopy(self.progress),
            "result": copy.deepcopy(self.result),
            "error": self.error,
            "out_dir": self.out_dir,
        }


@dataclass
class JobContext:
    """交给 runner 的上下文：日志、取消检查、产物目录。

    ``_queue``/``_job`` 是内部回指，让 ``log()`` 能走队列那把锁并顺手落盘；
    单独构造（没有队列）时退化成只往 job.progress 里追加。
    """

    job_id: str
    out_dir: Path
    cancel_event: threading.Event
    _queue: JobQueue | None = field(default=None, repr=False, compare=False)
    _job: Job | None = field(default=None, repr=False, compare=False)

    def log(self, message: str, *, percent: int | None = None, **extra: Any) -> None:
        """追加一条进度：``{"at": iso, "message": str, "percent": int|None, **extra}``。

        ``percent`` 只允许 0–100，且相对上一次只增不减；落后就按上次的算。
        """
        entry: dict[str, Any] = {"at": _now(), "message": str(message), "percent": percent}
        entry.update(extra)
        queue = self._queue
        if queue is None or self._job is None:
            # 没有队列（单测里裸构造）：本地追加，不持久化。
            if self._job is not None:
                self._job.progress.append(entry)
            return
        queue._append_progress(self._job, entry)

    def raise_if_cancelled(self) -> None:
        """runner 在关键节点（每月出片前、渲染前）调一下，取消就立刻退出。"""
        if self.cancel_event.is_set():
            raise JobCancelled(f"任务 {self.job_id} 已取消")

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()


class JobQueue:
    """线程池 + 磁盘持久化的最小任务队列。

    所有对 ``Job`` 的读写都在 ``self._lock`` 内；runner 在自己的线程里跑，
    只通过 ``JobContext`` 回写进度，所以页面轮询永远看到一致的快照。
    """

    def __init__(
        self,
        root: Path | str,
        runner: Runner,
        *,
        max_workers: int = 1,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.runner = runner
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._events: dict[str, threading.Event] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._percents: dict[str, int | None] = {}
        # created_at 精度到微秒，同微秒内的两个任务靠自增序号定先后。
        self._seq: dict[str, int] = {}
        self._next_seq = 0
        self._recover()
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)), thread_name_prefix="billgen-job"
        )

    # ---- 对外 API --------------------------------------------------------
    def submit(
        self,
        profile_id: str,
        template: str,
        months: list[str],
        *,
        seed: str | None = None,
    ) -> Job:
        """建目录、写 job.json、丢进线程池；返回立刻可见的 queued 任务。"""
        job_id = uuid.uuid4().hex[:12]
        out_dir = self.root / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        job = Job(
            id=job_id,
            profile_id=profile_id,
            template=template,
            months=list(months),
            status="queued",
            created_at=_now(),
            out_dir=str(out_dir),
        )
        job._seed = seed  # type: ignore[attr-defined]
        with self._lock:
            self._jobs[job_id] = job
            self._seq[job_id] = self._next_seq
            self._next_seq += 1
            self._events[job_id] = threading.Event()
            self._cancel_events[job_id] = threading.Event()
            self._percents[job_id] = None
            self._persist(job)
        # 提交放到锁外：worker 可能立刻开始跑，别让它抢着等这把锁。
        self._executor.submit(self._run, job)
        return job

    def list(self) -> list[Job]:
        """按 created_at 倒序（新的在前）。"""
        with self._lock:
            jobs = list(self._jobs.values())
            return sorted(
                jobs,
                key=lambda job: (job.created_at, self._seq.get(job.id, 0)),
                reverse=True,
            )

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFound(job_id)
            return job

    def cancel(self, job_id: str) -> Job:
        """排队中直接落 cancelled（不会再启动）；运行中只置位，等 runner 自己抛。"""
        with self._lock:
            job = self.get(job_id)
            if job.status == "queued":
                job.status = "cancelled"
                job.finished_at = _now()
                self._persist(job)
                self._events[job_id].set()
            elif job.status == "running":
                self._cancel_events[job_id].set()
            return job

    def wait(self, job_id: str, timeout: float | None = None) -> Job:
        """等任务落定；超时也返回当前状态（测试与 CLI 用）。"""
        with self._lock:
            self.get(job_id)  # 未知 id 先抛，别让人白等一个 timeout
            event = self._events[job_id]
        event.wait(timeout)
        return self.get(job_id)

    def shutdown(self, wait: bool = True) -> None:
        """测试收尾用：不再接新任务；排队中的会被丢弃。"""
        self._executor.shutdown(wait=wait)

    # ---- worker ----------------------------------------------------------
    def _run(self, job: Job) -> None:
        with self._lock:
            if job.status != "queued":
                # 排队阶段已被取消：不启动 runner，状态与事件都已落好。
                return
            job.status = "running"
            job.started_at = _now()
            self._persist(job)
            cancel_event = self._cancel_events[job.id]
        ctx = JobContext(
            job_id=job.id,
            out_dir=Path(job.out_dir),
            cancel_event=cancel_event,
            _queue=self,
            _job=job,
        )
        result: dict | None = None
        status, error = "done", None
        try:
            result = self.runner(ctx, job)
            if cancel_event.is_set():
                # runner 收尾时才发现被取消：按取消算，别让页面显示「成功」。
                status = "cancelled"
        except JobCancelled:
            status = "cancelled"
        except BaseException as exc:  # noqa: BLE001 - runner 的异常一律落到任务状态上
            LOG.exception("任务 %s 失败", job.id)
            status, error = "failed", str(exc)
        with self._lock:
            job.status = status
            job.finished_at = _now()
            if status == "done":
                if result is None:
                    job.result = {}
                elif isinstance(result, dict):
                    job.result = result
                else:
                    # runner 违反约定返回了非 dict：包一层，别让 job.result 类型漂移。
                    job.result = {"value": result}
            if error:
                job.error = error[:ERROR_LIMIT]
            self._persist(job)
            self._events[job.id].set()

    # ---- 内部：进度与持久化 ----------------------------------------------
    def _append_progress(self, job: Job, entry: dict[str, Any]) -> None:
        """把一条进度写进 job.progress 并落盘（调用方必须已持有锁或由本方法加锁）。"""
        with self._lock:
            percent = entry.get("percent")
            if percent is not None:
                try:
                    value = int(percent)
                except (TypeError, ValueError):
                    value = None
                if value is not None:
                    value = max(0, min(100, value))
                    last = self._percents.get(job.id)
                    if last is not None:
                        value = max(value, last)
                    self._percents[job.id] = value
                    entry["percent"] = value
                else:
                    entry["percent"] = None
            job.progress.append(_sanitize(entry))
            self._persist(job)

    def _persist(self, job: Job) -> None:
        """原子写 ``<out_dir>/job.json``（临时文件 + os.replace）。"""
        payload = _sanitize(job.to_dict())
        target = Path(job.out_dir) / "job.json"
        tmp = target.with_name("job.json.tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, target)
        except OSError:
            # 磁盘满/只读挂载不该把 runner 弄崩，状态留在内存里继续跑。
            LOG.warning("任务 %s 落盘失败：%s", job.id, target, exc_info=True)

    def _from_dict(self, payload: dict[str, Any], out_dir: Path) -> Job:
        job = Job(
            id=str(payload["id"]),
            profile_id=str(payload.get("profile_id", "")),
            template=str(payload.get("template", "")),
            months=[str(month) for month in payload.get("months") or []],
            status=str(payload.get("status", "failed")),
            created_at=str(payload.get("created_at") or _now()),
            started_at=payload.get("started_at"),
            finished_at=payload.get("finished_at"),
            progress=[dict(item) for item in payload.get("progress") or []],
            result=payload.get("result"),
            error=payload.get("error"),
            out_dir=str(out_dir),
        )
        return job

    def _recover(self) -> None:
        """扫 root 下的 job.json：恢复历史任务，把在飞的标成 failed。"""
        for path in sorted(self.root.glob("*/job.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("job.json 不是对象")
                job = self._from_dict(payload, path.parent)
            except (OSError, ValueError, TypeError, KeyError):
                LOG.warning("跳过无法解析的任务文件：%s", path)
                continue
            self._jobs[job.id] = job
            self._seq[job.id] = self._next_seq
            self._next_seq += 1
            self._percents[job.id] = _last_percent(job.progress)
            if job.status in ("queued", "running"):
                # 旧进程的线程已经没了，留着就是永远转圈 —— 直接判死。
                job.status = "failed"
                job.error = RESTART_ERROR
                job.finished_at = _now()
                self._persist(job)
            event = threading.Event()
            if job.status in TERMINAL:
                event.set()
            self._events[job.id] = event
            self._cancel_events[job.id] = threading.Event()
