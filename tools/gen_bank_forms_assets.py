"""把每套模板的「可填空表单样例」导出到 `docs/templates/samples/`，字段字典导出到 `docs/templates/`。

    python tools/gen_bank_forms_assets.py            # 覆盖写
    python tools/gen_bank_forms_assets.py --check    # 只比对，不写；有漂移退出码 1

样例必须和引擎的默认数据同源，否则照着旧样例填、字段名早就改了。
生成器同时给每套版式一份字段字典（路径 / 类型 / 样例 / 说明），手改 JSON 时照着填。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from billgen import registry  # noqa: E402
from billgen.mcp import contract as contracts  # noqa: E402

OUT = ROOT / "docs/templates/samples"
FIELDS = ROOT / "docs/templates"
SKIP = {"metadata"}  # 输出用，不是表单字段


def payload(template_id: str) -> str:
    data = registry.get_renderer(template_id).default_data()
    trimmed = {k: v for k, v in data.items() if k not in SKIP}
    return json.dumps(trimmed, ensure_ascii=False, indent=2) + "\n"


def _samples(node: object, path: str, out: dict[str, object]) -> None:
    """把骨架摊平成「路径 → 样例值」；数组只取第一行的元素，路径写成 `rows[].field`。"""
    if isinstance(node, dict):
        for key, value in node.items():
            _samples(value, f"{path}.{key}" if path else str(key), out)
    elif isinstance(node, list):
        if node and isinstance(node[0], (dict, list)):
            _samples(node[0], f"{path}[]", out)
        elif node:
            out[path] = node[0]
        else:
            out[path] = []
    else:
        out[path] = node


def _cell(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text.replace("|", "\\|").replace("\n", " ")[:60] or "—"


def field_payload(template_id: str) -> str:
    """这套版式的字段字典：路径 / 类型 / 样例 / 说明，给模型逐条对照。"""
    types = contracts.field_types(template_id)
    notes = contracts.field_notes(template_id)
    samples: dict[str, object] = {}
    try:
        _samples(contracts.skeleton(template_id), "", samples)
    except (ValueError, KeyError):
        samples = {}
    display = registry.SPEC_BY_ID[template_id].display_name
    lines = [
        f"# {display}（`{template_id}`）字段字典",
        "",
        "由 `tools/gen_bank_forms_assets.py` 从引擎的默认数据生成，别手改：改模板的默认数据再重跑。",
        "「必填」= 引擎会读的顶层键；空着的字段会回落成模板自带样例，所以拿不准也**不要**留空。",
        "",
        "| 字段路径 | 类型 | 样例 | 说明 |",
        "| --- | --- | --- | --- |",
    ]
    for path in sorted(types):
        note = notes.get(path) or notes.get(path.split("[]")[0]) or ""
        lines.append(f"| `{path}` | {types[path]} | {_cell(samples.get(path, '—'))} | {note or '—'} |")
    row_notes = {k: v for k, v in notes.items() if k.startswith("#row.")}
    if row_notes:
        lines += ["", "## 流水行内字段", "", "| 键 | 说明 |", "| --- | --- |"]
        for key, note in sorted(row_notes.items()):
            lines.append(f"| `{key[len('#row.'):]}` | {note} |")
    limits = contracts.limits_for(template_id)
    lines += [
        "",
        f"描述字数上限 `{limits.get('desc_chars')}`，每页行数 {limits.get('rows_per_page')}。",
        "",
        "## 交付形态",
        "",
        "这份字段字典描述的是**一个月**的数据对象。选 3 个月就产出 3 份 JSON（`"
        f"{template_id}-YYYY-MM.json`），每份只带自己那个月的期初/期末与流水；"
        "跨月规则（上期末 = 下期初、月份连续）由 `monthpack` 校验，见 `docs/month-json.md`。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="只检查是否与引擎默认数据一致")
    args = parser.parse_args(argv)

    OUT.mkdir(parents=True, exist_ok=True)
    FIELDS.mkdir(parents=True, exist_ok=True)
    drifted: list[str] = []
    for template_id in registry.SPEC_BY_ID:
        try:
            text = payload(template_id)
        except AssertionError:
            continue  # web 模板没有模块表单，跳过
        docs = field_payload(template_id)
        pairs = [(OUT / f"{template_id}.json", text),
                 (FIELDS / f"{template_id}.md", docs)]
        if args.check:
            for path, want in pairs:
                if not path.exists() or path.read_text() != want:
                    drifted.append(str(path.relative_to(ROOT)))
            continue
        for path, want in pairs:
            path.write_text(want)
        print(f"{template_id:18} -> {pairs[0][0].relative_to(ROOT)} + {pairs[1][0].relative_to(ROOT)}")
    if args.check:
        for name in drifted:
            print(f"drifted: {name}")
        print("assets 与字段字典都与引擎默认数据一致" if not drifted else f"{len(drifted)} 份需要重生成")
        return 1 if drifted else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
