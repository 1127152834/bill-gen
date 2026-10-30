"""异步任务队列：状态机、取消、重启恢复、进度单调、落盘脱敏。"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from billgen.jobs import ERROR_LIMIT, RESTART_ERROR, JobNotFound, JobQueue


def _ok_runner(ctx, job):
    """正常跑完：写一份产物、打两条进度、回一个 dict。"""
    (ctx.out_dir / f"{job.months[0]}.json").write_text('{"ok": true}', encoding="utf-8")
    ctx.log("开始", percent=0)
    ctx.raise_if_cancelled()
    ctx.log("写盘完成", percent=100, files=1, api_key="sk-secret")
    return {"files": [f"{job.months[0]}.json"], "count": 1, "api_key": "sk-secret"}


# ---- 1) 跑通 ---------------------------------------------------------------
def test_submit_runs_to_done(tmp_path: Path):
    queue = JobQueue(tmp_path / "jobs", _ok_runner)
    try:
        job = queue.submit("p1", "boa-personal", ["2026-01", "2026-02"])
        # worker 线程可能已经抢跑，submit 只保证任务一定进了队列
        assert job.out_dir == str(tmp_path / "jobs" / job.id)
        assert job.status in ("queued", "running", "done")
        done = queue.wait(job.id, timeout=10)
        assert done.status == "done"
        assert done.started_at and done.finished_at
        assert done.result == {"files": ["2026-01.json"], "count": 1, "api_key": "sk-secret"}
        assert done.error is None
        assert [entry["message"] for entry in done.progress] == ["开始", "写盘完成"]
        assert Path(done.out_dir).joinpath("2026-01.json").is_file()
    finally:
        queue.shutdown()


# ---- 2) runner 抛错 → failed + 截断 ---------------------------------------
def test_runner_error_fails_and_truncates(tmp_path: Path):
    message = "x" * (ERROR_LIMIT + 400)

    def runner(ctx, job):
        raise RuntimeError(message)

    queue = JobQueue(tmp_path / "jobs", runner)
    try:
        job = queue.submit("p1", "boa-personal", ["2026-01"])
        failed = queue.wait(job.id, timeout=10)
        assert failed.status == "failed"
        assert failed.error is not None
        assert len(failed.error) == ERROR_LIMIT and failed.error == message[:ERROR_LIMIT]
    finally:
        queue.shutdown()


# ---- 3) 运行中取消 --------------------------------------------------------
def test_cancel_running_marks_cancelled(tmp_path: Path):
    def runner(ctx, job):
        ctx.log("等取消", percent=10)
        ctx.cancel_event.wait(10)       # 不等到取消也不往下走
        ctx.raise_if_cancelled()
        return {"should": "not happen"}

    queue = JobQueue(tmp_path / "jobs", runner)
    try:
        job = queue.submit("p1", "boa-personal", ["2026-01"])
        deadline = time.monotonic() + 10
        while job.status != "running" and time.monotonic() < deadline:
            time.sleep(0.01)
        running = queue.cancel(job.id)
        assert running.status == "running"          # 状态由 runner 自己落定
        cancelled = queue.wait(job.id, timeout=10)
        assert cancelled.status == "cancelled"
        assert cancelled.result is None
        assert cancelled.finished_at
    finally:
        queue.shutdown()


# ---- 4) 排队中被取消：不启动 runner ---------------------------------------
def test_cancel_queued_never_starts(tmp_path: Path):
    gate, started, calls = threading.Event(), threading.Event(), []

    def runner(ctx, job):
        calls.append(job.id)
        started.set()
        gate.wait(10)
        return {"ok": True}

    queue = JobQueue(tmp_path / "jobs", runner, max_workers=1)
    try:
        first = queue.submit("p1", "boa-personal", ["2026-01"])
        assert started.wait(10)                     # 第一个占住唯一的 worker
        second = queue.submit("p2", "boa-personal", ["2026-02"])
        assert second.status == "queued"
        assert queue.cancel(second.id).status == "cancelled"
        gate.set()
        assert queue.wait(first.id, timeout=10).status == "done"
        settled = queue.wait(second.id, timeout=10)
        assert settled.status == "cancelled"
        assert calls == [first.id]                  # 被取消的任务从未进 runner
    finally:
        queue.shutdown()


# ---- 5) wait 超时返回当前状态 ---------------------------------------------
def test_wait_timeout_returns_current_state(tmp_path: Path):
    gate = threading.Event()

    def runner(ctx, job):
        gate.wait(10)
        return {"ok": True}

    queue = JobQueue(tmp_path / "jobs", runner)
    try:
        job = queue.submit("p1", "boa-personal", ["2026-01"])
        current = queue.wait(job.id, timeout=0.05)
        assert current.status == "running" and current.finished_at is None
        gate.set()
        assert queue.wait(job.id, timeout=10).status == "done"
    finally:
        queue.shutdown()


# ---- 6) 重启恢复：in-flight 一律标 failed ---------------------------------
def test_restart_marks_inflight_failed(tmp_path: Path):
    root = tmp_path / "jobs"
    for job_id, status in (("alive0000001", "running"), ("queue0000001", "queued"), ("done00000001", "done")):
        folder = root / job_id
        folder.mkdir(parents=True)
        (folder / "job.json").write_text(
            json.dumps(
                {
                    "id": job_id,
                    "profile_id": "p1",
                    "template": "boa-personal",
                    "months": ["2026-01"],
                    "status": status,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "progress": [],
                    "result": {"ok": True} if status == "done" else None,
                    "out_dir": str(folder),
                }
            ),
            encoding="utf-8",
        )
    queue = JobQueue(root, _ok_runner)
    try:
        for job_id in ("alive0000001", "queue0000001"):
            recovered = queue.get(job_id)
            assert recovered.status == "failed"
            assert recovered.error == RESTART_ERROR
            assert recovered.finished_at
            # 恢复后立刻重写盘上的 job.json，重启两次也不会重新变成幽灵任务
            assert json.loads((Path(recovered.out_dir) / "job.json").read_text(encoding="utf-8"))["status"] == "failed"
        assert queue.get("done00000001").status == "done"
        assert queue.wait("done00000001", timeout=0.01).status == "done"
    finally:
        queue.shutdown()


# ---- 7) progress percent 只增不减、夹在 0–100 -----------------------------
def test_progress_percent_is_monotonic(tmp_path: Path):
    def runner(ctx, job):
        for percent in (10, 60, 40, 150, -5, None, 60):
            ctx.log(f"p{percent}", percent=percent)
        return {"ok": True}

    queue = JobQueue(tmp_path / "jobs", runner)
    try:
        job = queue.wait(queue.submit("p1", "boa-personal", ["2026-01"]).id, timeout=10)
        assert [entry["percent"] for entry in job.progress] == [10, 60, 60, 100, 100, None, 100]
        assert all("at" in entry and "message" in entry for entry in job.progress)
    finally:
        queue.shutdown()


# ---- 8) job.json 落盘可重载 + 不含 api_key --------------------------------
def test_job_json_is_reloadable_and_redacted(tmp_path: Path):
    root = tmp_path / "jobs"
    queue = JobQueue(root, _ok_runner)
    try:
        job = queue.wait(queue.submit("p1", "boa-personal", ["2026-07"]).id, timeout=10)
        path = Path(job.out_dir) / "job.json"
        assert path.is_file()
        raw = path.read_text(encoding="utf-8")
        assert "sk-secret" not in raw and '"api_key": "***"' in raw
        payload = json.loads(raw)
        assert payload["id"] == job.id and payload["status"] == "done"
        assert payload["months"] == ["2026-07"] and payload["result"]["count"] == 1
    finally:
        queue.shutdown()
    # 新队列只靠文件就能把历史任务捞回来
    reloaded = JobQueue(root, _ok_runner)
    try:
        again = reloaded.get(job.id)
        assert again.status == "done" and again.result["count"] == 1
        assert reloaded.wait(job.id, timeout=0.01).status == "done"
    finally:
        reloaded.shutdown()


# ---- 9) list 新的在前 -----------------------------------------------------
def test_list_is_newest_first(tmp_path: Path):
    queue = JobQueue(tmp_path / "jobs", _ok_runner)
    try:
        first = queue.wait(queue.submit("p1", "boa-personal", ["2026-01"]).id, timeout=10)
        second = queue.wait(queue.submit("p2", "boa-personal", ["2026-02"]).id, timeout=10)
        third = queue.wait(queue.submit("p3", "boa-personal", ["2026-03"]).id, timeout=10)
        assert [job.id for job in queue.list()] == [third.id, second.id, first.id]
    finally:
        queue.shutdown()


# ---- 10) 未知 id / to_dict 快照 ------------------------------------------
def test_unknown_job_and_snapshot_copy(tmp_path: Path):
    queue = JobQueue(tmp_path / "jobs", _ok_runner)
    try:
        with pytest.raises(JobNotFound):
            queue.get("nope")
        with pytest.raises(JobNotFound):
            queue.wait("nope", timeout=0.1)
        job = queue.wait(queue.submit("p1", "boa-personal", ["2026-01"]).id, timeout=10)
        snapshot = job.to_dict()
        snapshot["progress"].clear()
        snapshot["months"].append("2026-09")
        assert job.progress and job.months == ["2026-01"]
    finally:
        queue.shutdown()
