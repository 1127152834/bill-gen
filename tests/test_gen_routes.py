"""任务队列的 HTTP 路由：提交、轮询、取消、单份下载、打包下载、未初始化 503。

起真服务（``make_server("127.0.0.1", 0)``），用假 runner 注入，不碰真实渲染。
"""

from __future__ import annotations

import io
import json
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest


def _runner(ctx, job):
    """假生成器：正常模板写两份产物；slow 模板一直挂着等取消。"""
    ctx.log("开始", percent=0)
    if job.template == "slow":
        ctx.cancel_event.wait(10)
        ctx.raise_if_cancelled()
        return {"slow": True}
    (ctx.out_dir / "statement.json").write_text(
        json.dumps({"month": job.months[0], "rows": 3}, ensure_ascii=False), encoding="utf-8"
    )
    (ctx.out_dir / "report.txt").write_text("ok", encoding="utf-8")
    ctx.raise_if_cancelled()
    ctx.log("完成", percent=100, months=len(job.months))
    return {"months": job.months, "files": ["statement.json", "report.txt"]}


@pytest.fixture()
def web(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BILL_GEN_DATA_DIR", str(tmp_path / "data"))
    from billgen.server import http as http_module
    from billgen.server.legacy import LegacyMounts

    queue = http_module.init_jobs(tmp_path / "jobs", _runner, max_workers=2)
    server = http_module.make_server("127.0.0.1", 0, legacy=LegacyMounts())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", queue
    finally:
        server.shutdown()
        server.server_close()
        queue.shutdown(wait=False)
        http_module._JOBS = None


def _request(url: str, method: str = "GET", payload: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def _submit(origin: str, **overrides):
    payload = {"profile_id": "p1", "template": "boa-personal", "months": ["2026-01"]}
    payload.update(overrides)
    return _request(origin + "/api/v1/gen/jobs", "POST", payload)


def _wait_status(origin: str, job_id: str, want: tuple[str, ...], timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, body, _ = _request(f"{origin}/api/v1/gen/jobs/{job_id}")
        assert status == 200
        payload = json.loads(body)
        if payload["status"] in want:
            return payload
        time.sleep(0.01)
    raise AssertionError(f"任务 {job_id} 未在 {timeout}s 内到达 {want}")


# ---- 1) 提交 202 + 跑完 ----------------------------------------------------
def test_submit_returns_202_then_done(web):
    origin, _ = web
    status, body, _ = _submit(origin)
    assert status == 202
    job = json.loads(body)
    assert job["status"] in ("queued", "running", "done")
    assert job["months"] == ["2026-01"] and job["out_dir"]
    done = _wait_status(origin, job["id"], ("done",))
    assert done["result"]["files"] == ["statement.json", "report.txt"]
    assert [entry["message"] for entry in done["progress"]] == ["开始", "完成"]


# ---- 2) 缺字段 / 非法月份 / 多余字段 → 400 --------------------------------
def test_submit_validation_errors(web):
    origin, _ = web
    status, body, _ = _submit(origin, months=[])
    assert status == 400 and json.loads(body)["error"]["code"] == "invalid_request"
    status, body, _ = _submit(origin, months=["2026-13"])
    assert status == 400
    status, body, _ = _submit(origin, template="")
    assert status == 400
    payload = {"profile_id": "p1", "template": "boa-personal", "months": ["2026-01"], "seed": 7}
    status, body, _ = _request(origin + "/api/v1/gen/jobs", "POST", payload)
    assert status == 400
    # 白名单外的字段由 _body() 直接挡掉
    status, body, _ = _submit(origin, nonsense=1)
    assert status == 400 and "error" in json.loads(body)
    # seed 是合法的可选字段
    status, body, _ = _submit(origin, seed="abc")
    assert status == 202


# ---- 3) 列表 + 单查 + 未知 id ---------------------------------------------
def test_list_and_detail(web):
    origin, _ = web
    first = json.loads(_submit(origin)[1])
    second = json.loads(_submit(origin, months=["2026-02"])[1])
    status, body, _ = _request(origin + "/api/v1/gen/jobs")
    assert status == 200
    ids = [job["id"] for job in json.loads(body)["jobs"]]
    assert ids[:2] == [second["id"], first["id"]]
    status, body, _ = _request(f"{origin}/api/v1/gen/jobs/{first['id']}")
    assert status == 200 and json.loads(body)["id"] == first["id"]
    status, body, _ = _request(origin + "/api/v1/gen/jobs/nope00000000")
    assert status == 404 and json.loads(body)["error"]["code"] == "job_not_found"
    status, body, _ = _request(origin + "/api/v1/gen/jobs/nope00000000/package")
    assert status == 404


# ---- 4) 取消 ---------------------------------------------------------------
def test_cancel_running_job(web):
    origin, _ = web
    job = json.loads(_submit(origin, template="slow")[1])
    _wait_status(origin, job["id"], ("running",))
    status, body, _ = _request(f"{origin}/api/v1/gen/jobs/{job['id']}/cancel", "POST", {})
    assert status == 200 and json.loads(body)["id"] == job["id"]
    cancelled = _wait_status(origin, job["id"], ("cancelled",))
    assert cancelled["result"] is None


# ---- 5) 单份下载 + 白名单 --------------------------------------------------
def test_single_file_download_and_rejections(web):
    origin, _ = web
    job = json.loads(_submit(origin)[1])
    _wait_status(origin, job["id"], ("done",))
    base = f"{origin}/api/v1/gen/jobs/{job['id']}/files"
    status, body, headers = _request(base + "/statement.json")
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert json.loads(body)["month"] == "2026-01"
    status, body, _ = _request(base + "/report.txt")
    assert status == 200 and body == b"ok"
    for name in ("statement.exe", "missing.json", ".hidden.json", "..%2Fsecret.json", "sub%2F..%2Fsecret.json"):
        status, body, _ = _request(f"{base}/{name}")
        assert status == 404, name


# ---- 6) 打包下载 -----------------------------------------------------------
def test_package_zip(web):
    origin, _ = web
    job = json.loads(_submit(origin)[1])
    _wait_status(origin, job["id"], ("done",))
    status, body, headers = _request(f"{origin}/api/v1/gen/jobs/{job['id']}/package")
    assert status == 200 and headers["Content-Type"] == "application/zip"
    assert job["id"] in headers["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        names = archive.namelist()
        assert "statement.json" in names and "report.txt" in names
        # 成员名是 basename：解压出来平铺一层，不带服务器路径
        assert all("/" not in name and "\\" not in name for name in names)
        assert not any("api_key" in name.lower() for name in names)
        assert json.loads(archive.read("statement.json"))["rows"] == 3


# ---- 7) 未初始化 → 503 -----------------------------------------------------
def test_jobs_unavailable_returns_503(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BILL_GEN_DATA_DIR", str(tmp_path / "data"))
    from billgen.server import http as http_module
    from billgen.server.legacy import LegacyMounts

    http_module._JOBS = None
    server = http_module.make_server("127.0.0.1", 0, legacy=LegacyMounts())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        status, body, _ = _request(origin + "/api/v1/gen/jobs")
        assert status == 503
        error = json.loads(body)["error"]
        assert error["code"] == "jobs_unavailable" and error["message"] == "任务队列未初始化"
        status, body, _ = _submit(origin)
        assert status == 503
    finally:
        server.shutdown()
        server.server_close()
