"""13 个模板入口的渲染指纹，作为重构的等价性判据。

判据只看 ``pages / size / text_sha256_16 / text_chars / fonts``：
PDF 字节数会因内嵌随机 ID 浮动 1–2 字节，不能作为等价判据；
文本层哈希在两次连续运行中完全一致（引擎是确定性的）。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .. import registry
from ..spec import RenderOptions

__all__ = ["compare", "compare_file", "probe", "record"]

PARITY_KEYS = ("pages", "size", "text_sha256_16", "text_chars", "fonts")

_SUBSET = re.compile(r"^[A-Z]{6}\+")

# 旧仓 probe 用的 id → 新注册表 id（P0 基线是旧目录下记录的）
LEGACY_IDS: dict[str, str] = {
    "wf": "wellsfargo",
    "ae": "amex",
    "merc": "mercury",
    "c1": "capitalone",
    "us-personal": "usbank-personal",
    "us-business": "usbank-business",
}


def probe(spec_id: str, opts: RenderOptions | None = None) -> dict[str, Any]:
    import fitz

    spec = registry.get_spec(spec_id)
    renderer = registry.get_renderer(spec_id)
    if spec.kind == "static-web":
        return {"id": spec_id, "name": spec.display_name, "kind": spec.kind,
                **renderer.inventory()}
    data = renderer.default_data()
    result = renderer.render(data, opts or spec.default_options)
    doc = fitz.open(stream=result.pdf, filetype="pdf")
    try:
        text = "\n".join(page.get_text() for page in doc)
        # 字体子集前缀每次出件都随机（真样本也是这样），判据里只留名字本体。
        fonts = sorted({_SUBSET.sub("", font[3]) for page in doc for font in page.get_fonts()})
        return {
            "id": spec_id,
            "name": spec.display_name,
            "kind": spec.kind,
            "pages": result.pages,
            "size": [round(doc[0].rect.width, 2), round(doc[0].rect.height, 2)],
            "bytes": len(result.pdf),
            "text_sha256_16": hashlib.sha256(text.encode()).hexdigest()[:16],
            "text_chars": len(text),
            "fonts": fonts,
            "secs_render": round(result.elapsed_ms / 1000, 2),
        }
    finally:
        doc.close()


def record(repo: Path | None = None) -> dict[str, Any]:  # noqa: ARG001 - 兼容旧签名
    return {spec.id: probe(spec.id) for spec in registry.SPECS}


def compare(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """返回不一致项描述；空列表 = 完全等价。"""
    diffs: list[str] = []
    for spec_id, before in baseline.items():
        after = current.get(spec_id) or current.get(LEGACY_IDS.get(spec_id, ""), {})
        if not after:
            diffs.append(f"{spec_id}: missing template")
            continue
        if before.get("kind") == "static-web":
            before = dict(before)
            for old, new in (("vendor_pdf_lib", "pdf_lib"), ("vendor_fontkit", "fontkit")):
                if old in before:
                    before[new] = before[old]
            keys = tuple(key for key in ("files", "bytes", "entry", "pdf_lib", "fontkit") if key in before)
        else:
            keys = PARITY_KEYS
        for key in keys:
            if before.get(key) != after.get(key):
                diffs.append(f"{spec_id}.{key}: {before.get(key)!r} -> {after.get(key)!r}")
        for key in ("bytes", "files"):
            b, a = before.get(key), after.get(key)
            if isinstance(b, int) and isinstance(a, int) and b != a:
                diffs.append(f"note {spec_id}.{key}: {b} -> {a} ({a - b:+d})")
    return diffs


def compare_file(baseline_path: Path, repo: Path | None = None) -> bool:
    baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    current = record(repo)
    diffs = compare(baseline, current)
    hard = [d for d in diffs if not d.startswith("note ")]
    for line in diffs:
        print(("[DIFF] " if not line.startswith("note ") else "[note] ") + line)
    print(f"\n结论：{'PASS 基线一致' if not hard else f'{len(hard)} 处与基线不一致'}")
    return not hard


if __name__ == "__main__":  # pragma: no cover
    import sys

    args = sys.argv[1:]
    target = Path(args[0]) if args else Path(__file__).parent / "baseline.json"
    payload = record()
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"基线已写入 {target}（{len(payload)} 条）")
