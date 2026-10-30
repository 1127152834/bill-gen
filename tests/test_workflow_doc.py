"""工作流文档与 agent 清单必须一致：阶段表、契约字段、消费者矩阵都别各说各话。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / "docs" / "workflow-v2.md"
MANIFEST = ROOT / "agents" / "manifest.json"


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_every_stage_agent_appears_in_the_workflow_doc() -> None:
    doc = WORKFLOW.read_text(encoding="utf-8")
    for entry in _manifest()["agents"]:
        assert entry["id"] in doc or entry["title"] in doc, f"工作流文档里没有 {entry['id']}"


def test_contract_fields_read_by_agents_are_documented() -> None:
    doc = WORKFLOW.read_text(encoding="utf-8")
    fields = {field for entry in _manifest()["agents"] for field in entry.get("contracts_read", [])}
    fields.discard("*")
    missing = [field for field in fields if field.split(".*")[0].split(".")[0] not in doc]
    assert not missing, f"契约字段没有写进工作流文档：{missing}"


def test_pipeline_matches_declared_stages() -> None:
    manifest = _manifest()
    ids = {entry["id"]: entry for entry in manifest["agents"]}
    stages = [ids[name]["stage"] for name in manifest["pipeline"]]
    assert stages == sorted(stages), "pipeline 顺序与 stage 编号不一致"


def test_single_execution_point_is_documented() -> None:
    """整形只能在投影前发生一次，这条是硬规矩，得写死在文档里。"""
    doc = WORKFLOW.read_text(encoding="utf-8")
    assert "唯一执行点" in doc
    assert "contracts.conform" in doc and "contracts.check" in doc


def test_agents_that_write_numbers_are_only_the_solver() -> None:
    writers = [entry["id"] for entry in _manifest()["agents"] if entry.get("can_write_numbers")]
    assert writers == ["solver"], f"能改数字的只该是求解器，现在是 {writers}"
