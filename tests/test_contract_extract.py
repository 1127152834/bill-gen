"""账户契约与 skill 的完整性闸门。

这些测试守的是「一家银行一个账户 = 一份契约 + 一份 skill」这件事本身：
少了、空了、没有出处、或者规则与引擎行为打架，都得在这里红。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from billgen import contracts, registry

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "contracts"
SKILLS = ROOT / "agents" / "skills"
CURATED = CONTRACTS / "curated"


def _renderable() -> list[str]:
    return [spec.id for spec in registry.SPECS if spec.kind != "static-web"]


@pytest.mark.parametrize("template_id", _renderable())
def test_every_account_has_a_contract_with_evidence(template_id: str) -> None:
    path = CONTRACTS / f"{template_id}.json"
    assert path.is_file(), f"{template_id} 没有契约"
    contract = json.loads(path.read_text(encoding="utf-8"))
    assert contract["template_id"] == template_id
    assert contract["evidence"], "契约必须带出处"
    assert contract["forbidden_literals"], "禁止字面量不能空（至少 SAMPLE/billgen）"
    assert contract.get("descriptor"), "描述符语法不能空"


@pytest.mark.parametrize("template_id", _renderable())
def test_every_account_has_a_skill_doc(template_id: str) -> None:
    path = SKILLS / f"{template_id}.md"
    assert path.is_file(), f"{template_id} 没有 skill 文档"
    text = path.read_text(encoding="utf-8")
    for heading in ("## 类型枚举", "## 描述符语法", "## 出处", "## 用法"):
        assert heading in text, f"{template_id} 的 skill 缺小节 {heading}"
    assert f"contracts/{template_id}.json" in text, "skill 必须链到自己的契约"


@pytest.mark.parametrize("theme_path", sorted(CURATED.glob("*.json")), ids=lambda p: p.stem)
def test_curated_rules_carry_evidence(theme_path: Path) -> None:
    data = json.loads(theme_path.read_text(encoding="utf-8"))
    assert data.get("evidence"), f"{theme_path.stem} 人工规则没有出处"
    assert data.get("why"), f"{theme_path.stem} 没写为什么这样写"


def test_contract_resolves_for_every_renderable_template() -> None:
    for template_id in _renderable():
        assert contracts.resolve(template_id) == template_id
        assert contracts.load(template_id)["template_id"] == template_id


def test_mercury_card_rows_never_carry_the_holders_city() -> None:
    data = {
        "address": ["1119 Freer Avenue", "Temple City, CA 91780"],
        "transactions": [{"date": "Sep 9", "desc": "NETFLIX.COM TEMPLE CITY CA", "type": "Card", "amount": -12.99}],
    }
    counts = contracts.conform("mercury", data)
    assert counts["cities"] >= 1
    assert data["transactions"][0]["desc"] == "NETFLIX.COM"
    assert contracts.check("mercury", data) == []


def test_chase_card_rows_keep_the_card_tail_but_deposits_do_not() -> None:
    data = {
        "address": ["2104 S 1st St", "Austin, TX 78704"],
        "other_withdrawals": [{"date": "09/04", "desc": "Card Purchase 09/04 KROGER 9274 AUSTIN TX", "amount": "5.52"}],
        "deposits": [{"date": "09/02", "desc": "Deposit 09/02 LONGBOW AGENCY", "amount": "908.98"}],
        "electronic_withdrawals": [{"date": "09/01", "desc": "ACH Debit 09/01 GREYSTAR MGMT", "amount": "96.00"}],
    }
    problems = contracts.check("chase-personal", data)
    # 只有刷卡行必须带卡尾；存款/ACH 行不许被要求
    assert [item for item in problems if "GREYSTAR" in item or "LONGBOW" in item] == []
    assert any("KROGER" in item for item in problems)


def test_deposit_and_ach_debit_are_not_card_rows() -> None:
    """``DEPOSIT`` 里含 ``POS``、``ACH Debit`` 里有 ``DEBIT`` —— 早期用子串判断都误判成卡行。"""
    from billgen.contracts import _looks_like_card

    assert not _looks_like_card({"desc": "Deposit 09/02 LONGBOW AGENCY"})
    assert not _looks_like_card({"desc": "ACH Debit 09/01 GREYSTAR MGMT"})
    assert _looks_like_card({"desc": "Card Purchase 09/04 KROGER 9274 AUSTIN TX Card 3217"})
    assert _looks_like_card({"type": "Card", "desc": "AWS EMEA"}, {"card_types": ["Card"]})


def test_extractor_is_deterministic() -> None:
    """同一批参考件跑两遍，提取结果必须一致（否则契约会随渲染时间漂移）。"""
    code = (
        "import json;"
        "from tools.contract_extract import extract;"
        "print(json.dumps(extract('mercury'), ensure_ascii=False, sort_keys=True))"
    )
    first = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                           env={"PYTHONPATH": f"{ROOT}:{ROOT / 'src'}", "PATH": "/usr/bin:/bin:/usr/local/bin"})
    second = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                            env={"PYTHONPATH": f"{ROOT}:{ROOT / 'src'}", "PATH": "/usr/bin:/bin:/usr/local/bin"})
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout


def test_skills_index_lists_every_account() -> None:
    index = (SKILLS / "README.md").read_text(encoding="utf-8")
    for template_id in _renderable():
        assert f"`{template_id}`" in index, f"索引里少了 {template_id}"
