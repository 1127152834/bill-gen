"""把 kit/ 打成一个可搬走的接入包：dist/agent-kit/ + dist/agent-kit.zip。

    PYTHONPATH=.:src .venv/bin/python tools/export_agent_kit.py
    PYTHONPATH=.:src .venv/bin/python tools/export_agent_kit.py --out /tmp/agent-kit.zip

包里放什么：
- docs/     接入手册、流水书写规范、知识库骨架、字段清单、验收方法
- prompts/  编排/选角/导演/求解/审计（含 6 个审计员）
- schemas/  persona / plan / ledger / findings / contract
- contracts/ 每账户一份契约（从仓库 contracts/ 同步）
- skills/   每账户一份人读规范（从 agents/skills/ 同步）
- tools/    独立 lint（零依赖）
"""
from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "kit"
CONTRACTS = ROOT / "contracts"
SKILLS = ROOT / "agents" / "skills"

#: 这些目录整个同步过去（源 → 包内相对路径）
SYNC = [
    (CONTRACTS, "contracts"),
    (SKILLS, "skills"),
]

#: 这些必须存在，缺一个就不许打包
REQUIRED = [
    "README.md",
    "docs/01-integration-playbook.md",
    "docs/02-statement-writing-norms.md",
    "docs/03-knowledge-base.md",
    "docs/04-field-checklist.md",
    "docs/05-evaluation.md",
    "schemas/contract.schema.json",
    "schemas/plan.schema.json",
    "schemas/ledger.schema.json",
    "schemas/persona.schema.json",
    "schemas/findings.schema.json",
    "prompts/00-orchestrator.md",
    "prompts/01-account-picker.md",
    "prompts/02-director.md",
    "prompts/03-solver.md",
    "prompts/04-cast.md",
    "prompts/06-integrator.md",
    "prompts/auditors/a1-accounting.md",
    "prompts/auditors/a2-geography.md",
    "prompts/auditors/a3-statistics.md",
    "prompts/auditors/a4-adversarial.md",
    "prompts/auditors/a5-continuity.md",
    "prompts/auditors/a6-contract.md",
    "tools/statement_lint.py",
]


def missing() -> list[str]:
    return [name for name in REQUIRED if not (KIT / name).is_file()]


def build(out_zip: Path | None = None, *, out_dir: Path | None = None, clean: bool = True) -> Path:
    """组装接入包，返回 zip 路径。"""
    gaps = missing()
    if gaps:
        raise SystemExit(f"kit/ 缺文件：{gaps}")

    target = out_dir or (ROOT / "dist" / "agent-kit")
    if clean and target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    for item in KIT.iterdir():
        if item.name == "__pycache__":
            continue
        dest = target / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    for source, name in SYNC:
        dest = target / name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest)
        # agents/skills 里的 README 是仓库内索引，包内也留着无妨

    zip_path = out_zip or (ROOT / "dist" / "agent-kit.zip")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(target.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(target.parent))
    return zip_path


def main() -> int:
    parser = argparse.ArgumentParser(description="打包 Agent 接入包")
    parser.add_argument("--out", type=Path, help="zip 输出路径")
    parser.add_argument("--dir", type=Path, help="解包目录（默认 dist/agent-kit）")
    args = parser.parse_args()
    path = build(args.out, out_dir=args.dir)
    size = path.stat().st_size
    with zipfile.ZipFile(path) as zf:
        count = len(zf.namelist())
    print(f"✓ {path}（{count} 个文件，{size / 1024:.0f} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
