"""交付包：把一段账期打成「每月一份模板 JSON + 校验报告 + 清单」的 zip。

工作流的终点不再是 PDF——用户拿到的是能直接导进模板的 JSON。所以一个交付包必须
自带三样东西，缺一样用户就没法判断这份文件能不能用：

* `<模板>-<YYYY-MM>.json` —— 每月一份，字段就是该模板的导入格式；
* `validation-report.json` —— 每月的结构/内容/跨月结论，哪条不对要能指到字段；
* `manifest.json` —— 文件清单（sha256、行数、期初期末、是否通过），用来核对有没有漏月。
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from typing import Any

from . import synth
from .synth import monthpack

__all__ = ["build_package", "manifest_for"]


def manifest_for(template_id: str, artifacts: list[monthpack.MonthArtifact]) -> dict[str, Any]:
    files = []
    for artifact in artifacts:
        blob = monthpack.dumps(artifact).encode()
        files.append(
            {
                "month": artifact.month,
                "filename": f"{template_id}-{artifact.month}.json",
                "sha256": hashlib.sha256(blob).hexdigest(),
                "bytes": len(blob),
                "rows": artifact.rows,
                "opening": round(artifact.opening_cents / 100, 2),
                "closing": round(artifact.closing_cents / 100, 2),
                "ok": artifact.ok,
            }
        )
    return {
        "template": template_id,
        "months": [artifact.month for artifact in artifacts],
        "count": len(artifacts),
        "files": files,
        "ok": monthpack.validate_batch(artifacts)["ok"],
    }


def build_package(
    template_id: str,
    result: dict[str, Any],
    *,
    account: str = "",
    cycle: int = -1,
    bank_name: str = "",
    months: list[str] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """出 zip 交付包，返回 (zip 字节, manifest)。打包失败只影响这一份，不抛给调用方。"""
    ledger = synth.from_game_result(
        result, account=account or None, cycle=cycle, template=template_id, bank_name=bank_name or None
    )
    artifacts = monthpack.month_artifacts(template_id, ledger, months or None)
    manifest = manifest_for(template_id, artifacts)
    report = {
        "ok": monthpack.validate_batch(artifacts)["ok"],
        "template": template_id,
        "batch": monthpack.validate_batch(artifacts),
        "months": [artifact.report() for artifact in artifacts],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as pack:
        for artifact in artifacts:
            pack.writestr(f"{template_id}-{artifact.month}.json", monthpack.dumps(artifact))
        pack.writestr("validation-report.json", json.dumps(report, ensure_ascii=False, indent=2))
        pack.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return buffer.getvalue(), manifest
