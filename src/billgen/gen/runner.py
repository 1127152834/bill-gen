"""任务队列与生成链路之间的胶水层。

`JobQueue` 只认识「一个 `runner(ctx, job) -> dict`」，这个模块提供那个 runner：
按 `job.profile_id` 去档案库取人/企业，跑 `pipeline.generate()`，把每月的模板 JSON
与 `validation-report.json` / `manifest.json` 写进任务目录，最后返回给页面用的摘要。

产物布局（`ctx.out_dir`）::

    <template>-<YYYY-MM>.json     每月一份模板 JSON（页面导入后自己出账单）
    validation-report.json        三层校验报告（structure / content / batch）
    manifest.json                 文件清单（sha256、行数、期初期末、ok）
    job-summary.json              本次生成的摘要（模板/月份/用量/警告）
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from ..jobs import JobCancelled, JobContext
from ..synth import monthpack
from ..profiles import ProfileStore
from . import envelope as envelope_mod
from . import postpass, seasonal
from .config import load_config
from .llm import LlmClient
from .pipeline import GenResult, generate
from .tavily import TavilyClient


def store_path() -> Path:
    """档案库路径，与 HTTP 层同一套优先级（BILLGEN_PROFILES > BILL_GEN_DATA_DIR）。"""
    env = os.environ.get("BILLGEN_PROFILES")
    if env:
        return Path(env)
    return Path(os.environ.get("BILL_GEN_DATA_DIR") or ".data") / "billgen" / "profiles.json"


def _write(path: Path, payload: Any) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _row_count(report: Mapping[str, Any]) -> int:
    """报告里的行数既可能是列表（逐行校验）也可能是整数（账本行数），统一成整数。"""
    raw = report.get("rows")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, (list, tuple)):
        return len(raw)
    return 0


def _month_files(result: GenResult, out_dir: Path) -> list[dict[str, Any]]:
    """每月写一份 `<template>-<month>.json`，返回给页面用的列表。"""
    files: list[dict[str, Any]] = []
    for outcome in result.months:
        name = f"{result.template}-{outcome.month}.json"
        _write(out_dir / name, outcome.data)
        files.append(
            {
                "name": name,
                "month": outcome.month,
                "ok": bool(outcome.ok),
                "rows": _row_count(outcome.report),
                "warnings": list(outcome.warnings),
            }
        )
    return files


def _batch_report(result: GenResult) -> dict[str, Any]:
    """整批报告：单月校验 + 跨月一致性，页面与 zip 里都放这一份。"""
    months = [
        {
            "month": outcome.month,
            "ok": bool(outcome.ok),
            "report": outcome.report,
            "warnings": list(outcome.warnings),
        }
        for outcome in result.months
    ]
    failed = [m["month"] for m in months if not m["ok"]]
    return {
        "template": result.template,
        "ok": not failed,
        "partial": bool(failed) and len(failed) < len(months),
        "failed_months": failed,
        "months": months,
        "warnings": list(result.warnings),
        "audit": result.audit,
    }


def _manifest(result: GenResult, files: list[dict[str, Any]], out_dir: Path) -> dict[str, Any]:
    import hashlib

    entries = []
    for item in files:
        target = out_dir / item["name"]
        blob = target.read_bytes()
        entries.append(
            {
                "name": item["name"],
                "month": item["month"],
                "ok": item["ok"],
                "bytes": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(),
                "rows": item["rows"],
            }
        )
    return {
        "template": result.template,
        "months": [o.month for o in result.months],
        "ok": all(item["ok"] for item in entries),
        "files": entries,
        "usage": result.usage,
    }


def _flatten_profile(record: dict[str, Any]) -> dict[str, Any]:
    """把档案记录摊平成流水线认的形状。

    档案是 ``{id, kind, bank, display_name, payload: {...}, accounts: {...}}``，
    而 ``payload`` 里才是 ``name`` / ``address`` / ``legalName`` 这些真人信息。
    流水线（``node_normalize`` / ``_holder_name`` / ``_address_lines``）读的是顶层键，
    不摊平就会拿不到地址，退回到模型瞎猜的城市——账单上就会出现「1 Market Street /
    Austin, TX」这种跟档案无关的行址。
    """
    flat = dict(record)
    payload = record.get("payload")
    if isinstance(payload, Mapping):
        flat.update(payload)
    flat["profile_id"] = record.get("id") or ""
    flat["bank"] = record.get("bank") or flat.get("bank") or ""
    flat["display_name"] = record.get("display_name") or flat.get("display_name") or ""
    accounts = record.get("accounts")
    if isinstance(accounts, Mapping):
        flat["accounts"] = dict(accounts)
    return flat


def make_runner(*, profiles: Path | str | None = None):
    """造一个给 `JobQueue` 用的 runner；档案库路径可注入（测试用）。"""
    target = Path(profiles) if profiles is not None else store_path()

    def runner(ctx: JobContext, job) -> dict[str, Any]:
        store = ProfileStore(target)
        store.load()
        profile = store.get(job.profile_id)
        out_dir = Path(ctx.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        cfg = load_config()
        llm = LlmClient(cfg)
        tavily = TavilyClient(cfg, max_queries=int(os.environ.get("BILLGEN_MAX_QUERIES") or 40))

        ctx.log(f"任务开始：{job.template} · {', '.join(job.months)}", percent=1)
        flat_profile = _flatten_profile(profile.to_dict())
        is_business = bool(str(flat_profile.get("legalName") or "").strip())
        try:
            result = generate(
                ctx,
                profile=flat_profile,
                template=job.template,
                months=job.months,
                llm=llm,
                tavily=tavily,
                seed=getattr(job, "seed", "") or "",
                out_dir=out_dir,
            )
        except JobCancelled:
            raise
        except Exception as exc:  # 生成失败要把人话留给页面
            ctx.log(f"生成失败：{exc}", percent=100)
            raise

        # 交付前过一遍确定性收尾：跨区公用事业/越区连锁/离谱金额。
        payload = profile.to_dict().get("payload") or {}
        address = payload.get("address") or {}
        state = str(address.get("state") or "")
        city = str(address.get("city") or "")
        income_hint = payload.get("monthly_income_hint")
        rent_hint = payload.get("rent_hint")
        # 预算信封（按月做季节缩放）：先给节日补几笔，再统一收尾、重接余额链。
        base_env = envelope_mod.envelope(
            payload, {"location": {"col_tier": "mid"}}, rent_hint=payload.get("rent_hint")
        )
        seasonal_added: dict[str, int] = {}
        for outcome in result.months:
            data = getattr(outcome, "data", None)
            if not isinstance(data, dict):
                continue
            try:
                year, month = (int(part) for part in str(outcome.month).split("-"))
            except ValueError:
                continue
            month_env = seasonal.envelope_for_month(base_env, year, month)
            added = seasonal.inject_event_rows(
                data, year=year, month=month, seed=f"{job.seed or ''}|{outcome.month}",
                envelope=month_env, city=city, state=state,
            )
            if added:
                seasonal_added[outcome.month] = added
        if seasonal_added:
            ctx.log("收尾：按节日/季节补了本地消费（感恩节采购、黑五、礼品等）", percent=93)

        repaired: dict[str, dict[str, int]] = {}
        topped: dict[str, dict[str, int]] = {}
        for outcome in result.months:
            data = getattr(outcome, "data", None)
            if isinstance(data, dict):
                repaired[outcome.month] = postpass.repair_month(
                    data,
                    state=state,
                    city=city,
                    income_hint=income_hint,
                    rent_hint=rent_hint,
                    business=is_business,
                )
                # 信封补齐：低于下限的类目把已有行抬进区间，公用事业缺一家就补一张
                try:
                    year, month = (int(part) for part in str(outcome.month).split("-"))
                except ValueError:
                    continue
                month_env = seasonal.envelope_for_month(base_env, year, month)
                counts = postpass.top_up_to_envelope(
                    data, month_env, seed=str(job.seed or ""), state=state, city=city
                )
                if any(counts.values()):
                    topped[outcome.month] = counts
        if topped:
            ctx.log("收尾：低于预算信封的类目已补齐（加油、公用事业、医疗等）", percent=94)
        if any(sum(v.values()) for v in repaired.values()):
            ctx.log("收尾：已按档案所在地修正公用事业/连锁/金额档位", percent=94)

        # 收尾改过金额，跨月余额链得重新接一遍；然后按最终数据复核每月算术。
        months_data = [o.data for o in result.months if isinstance(o.data, dict)]
        adjusted = postpass.rechain(months_data)
        if adjusted:
            ctx.log(f"收尾：{adjusted} 个月的期初按上月末重新接续", percent=95)
        for outcome in result.months:
            if isinstance(outcome.data, dict):
                outcome.report = monthpack.check_delivered(job.template, outcome.month, outcome.data)
                outcome.ok = bool(outcome.report.get("ok"))

        # 信封对照：实际花销 vs 收入档算出的区间，偏差只进 warnings（不拦交付）。
        envelope_problems: dict[str, list[str]] = {}
        for outcome in result.months:
            data = getattr(outcome, "data", None)
            if not isinstance(data, dict):
                continue
            try:
                year, month = (int(part) for part in str(outcome.month).split("-"))
            except ValueError:
                continue
            month_env = seasonal.envelope_for_month(base_env, year, month)
            rows = [
                row
                for array in postpass._iter_arrays(data)
                for row in array
                if isinstance(row, dict)
            ]
            problems = envelope_mod.envelope_report(rows, month_env)
            if problems:
                envelope_problems[outcome.month] = problems
                result.warnings.extend(f"信封偏差（{outcome.month}）：{item}" for item in problems)

        files = _month_files(result, out_dir)
        report = _batch_report(result)
        _write(out_dir / "validation-report.json", report)
        manifest = _manifest(result, files, out_dir)
        _write(out_dir / "manifest.json", manifest)
        summary = {
            "template": result.template,
            "months": [o.month for o in result.months],
            "ok": report["ok"],
            "partial": report["partial"],
            "failed_months": report["failed_months"],
            "usage": result.usage,
            "warnings": list(result.warnings),
            "postpass": repaired,
            "seasonal": seasonal_added,
            "envelope": envelope_problems,
            "topped_up": topped,
        }
        _write(out_dir / "job-summary.json", summary)

        files.append({"name": "validation-report.json", "month": "", "ok": report["ok"], "rows": 0})
        files.append({"name": "manifest.json", "month": "", "ok": manifest["ok"], "rows": 0})
        files.append({"name": "job-summary.json", "month": "", "ok": summary["ok"], "rows": 0})

        ctx.log(
            "完成：" + ("全部月份校验通过" if report["ok"] else f"{len(report['failed_months'])} 个月份有问题"),
            percent=100,
        )
        return {
            "files": files,
            "warnings": list(result.warnings),
            "months": summary["months"],
            "usage": result.usage,
            "ok": report["ok"],
            "partial": report["partial"],
        }

    return runner


def run_once(
    profile: Mapping[str, Any],
    *,
    template: str,
    months: list[str],
    out_dir: str | Path,
    seed: str = "",
) -> GenResult:
    """不走队列、同步跑一次（CLI 与调试用）。"""
    cfg = load_config()
    return generate(
        None,
        profile=dict(profile),
        template=template,
        months=list(months),
        llm=LlmClient(cfg),
        tavily=TavilyClient(cfg, max_queries=int(os.environ.get("BILLGEN_MAX_QUERIES") or 40)),
        seed=seed,
        out_dir=out_dir,
    )
