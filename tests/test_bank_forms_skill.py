"""bank-forms 技能与引擎保持同源：资产（表单样例）和参考文件都不能漂。

- `flow/skills/bank-forms/assets/<template>.json` 必须与 `default_data()` 一致——
  模型照着旧样例填字段名，渲染会整段回落成占位数据。
- 每套版式都要有参考文件（`references/<bank>.md`），且 SKILL.md 的索引里列了它。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from billgen import registry  # noqa: E402

SKILL = ROOT / "flow/skills/bank-forms"
ASSETS = SKILL / "assets"
REFS = SKILL / "references"

SKIP_KEYS = {"metadata"}

#: 版式 id → 参考文件名（与 TS 侧 `REFERENCE_BY_TEMPLATE` 同构）。
REFERENCE_BY_TEMPLATE = {
    "boa-personal": "boa",
    "boa-business": "boa",
    "chase-personal": "chase",
    "chase-business": "chase",
    "usbank-personal": "usbank",
    "usbank-business": "usbank",
    "wellsfargo": "wellsfargo",
    "capitalone": "capitalone",
    "sofi": "sofi",
    "mercury": "mercury",
}


def _form_templates() -> list[str]:
    out = []
    for template_id in registry.SPEC_BY_ID:
        try:
            registry.get_renderer(template_id).default_data()
        except AssertionError:
            continue  # web 模板没有模块表单
        out.append(template_id)
    return out


@pytest.mark.parametrize("template_id", _form_templates())
def test_asset_matches_engine_default_data(template_id: str) -> None:
    data = registry.get_renderer(template_id).default_data()
    expected = json.dumps({k: v for k, v in data.items() if k not in SKIP_KEYS}, ensure_ascii=False, indent=2) + "\n"
    path = ASSETS / f"{template_id}.json"
    assert path.exists(), f"缺少表单样例：{path}"
    assert path.read_text() == expected, f"{template_id} 的样例与引擎默认数据不一致，跑 tools/gen_bank_forms_assets.py"


def test_every_template_has_reference_and_index_entry() -> None:
    index = (SKILL / "SKILL.md").read_text()
    missing_refs = []
    missing_index = []
    for template_id in _form_templates():
        bank = REFERENCE_BY_TEMPLATE.get(template_id)
        if bank is None or not (REFS / f"{bank}.md").exists():
            missing_refs.append(template_id)
        elif f"`{template_id}`" not in index:
            missing_index.append(template_id)
    assert not missing_refs, f"这些版式没有参考文件：{missing_refs}"
    assert not missing_index, f"SKILL.md 索引里没列：{missing_index}"


def test_references_declare_fields_and_anti_patterns() -> None:
    """每份参考都得有「表单字段」表和「反例」节，否则模型拿到的只是散文。"""
    thin = []
    for bank in sorted(set(REFERENCE_BY_TEMPLATE.values())):
        body = (REFS / f"{bank}.md").read_text()
        if "表单字段" not in body or "反例" not in body:
            thin.append(bank)
    common = (REFS / "common.md").read_text()
    assert not thin, f"这些参考缺少字段表或反例：{thin}"
    assert "一律不许出现" in common


def test_skill_assets_have_no_placeholder_words() -> None:
    banned = re.compile(r"\b(SAMPLE|EXAMPLE|DEMO|DUMMY|PLACEHOLDER)\b")
    hits = []
    for path in sorted(ASSETS.glob("*.json")):
        if banned.search(path.read_text()):
            hits.append(path.name)
    assert not hits, f"样例里还有占位词：{hits}"
