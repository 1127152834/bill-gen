"""校验 agents/ 清单与文件是否自洽：文件存在、schema 能解析、提示词有必需小节。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"
REQUIRED_SECTIONS = ("## 角色", "## 输入", "## 硬规则")


def check(root: Path = ROOT) -> list[str]:
    agents_dir = root / "agents"
    problems: list[str] = []
    manifest_path = agents_dir / "manifest.json"
    if not manifest_path.is_file():
        return ["缺少 agents/manifest.json"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"manifest.json 不是合法 JSON：{exc}"]

    ids = set()
    for entry in manifest.get("agents", []):
        agent_id = entry.get("id")
        if not agent_id:
            problems.append("有 agent 缺少 id")
            continue
        if agent_id in ids:
            problems.append(f"{agent_id}: id 重复")
        ids.add(agent_id)
        prompt = entry.get("prompt")
        if not prompt:
            problems.append(f"{agent_id}: 缺少 prompt")
            continue
        path = agents_dir / prompt
        if not path.is_file():
            problems.append(f"{agent_id}: 提示词不存在 {prompt}")
            continue
        text = path.read_text(encoding="utf-8")
        if len(text.strip()) < 200:
            problems.append(f"{agent_id}: 提示词太短，像是在占位")
        for section in REQUIRED_SECTIONS:
            if section not in text:
                problems.append(f"{agent_id}: 缺少小节 {section}")
        for schema in entry.get("inputs", []):
            if schema.endswith(".schema.json") and not (agents_dir / "schemas" / schema).is_file():
                problems.append(f"{agent_id}: 缺少输入 schema {schema}")

    for schema_file in sorted((agents_dir / "schemas").glob("*.schema.json")):
        try:
            json.loads(schema_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{schema_file.name}: schema 不是合法 JSON：{exc}")
    return problems


def main() -> int:
    problems = check()
    if problems:
        for item in problems:
            print(f"✗ {item}")
        return 1
    manifest = json.loads((AGENTS / "manifest.json").read_text(encoding="utf-8"))
    print(f"✓ {len(manifest['agents'])} 个 agent、{len(list((AGENTS / 'schemas').glob('*.schema.json')))} 份 schema 全部自洽")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
