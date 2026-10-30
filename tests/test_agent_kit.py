"""接入包（kit/）必须自洽：文件齐全、契约与 skill 一一对应、lint 能抓错也能整形。"""
from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "kit"
LINT = KIT / "tools" / "statement_lint.py"
RENDERABLE = [
    "boa-personal", "boa-business", "chase-personal", "chase-business", "capitalone",
    "mercury", "sofi", "usbank-personal", "usbank-business", "wellsfargo",
]


def _lint(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(LINT), *args], capture_output=True, text=True, check=False)


def test_kit_has_every_required_doc_and_prompt() -> None:
    sys.path.insert(0, str(ROOT / "tools"))
    from export_agent_kit import missing  # noqa: PLC0415

    assert missing() == []


def test_every_renderable_account_has_contract_and_skill() -> None:
    contracts = {p.stem for p in (KIT / "contracts").glob("*.json")}
    skills = {p.stem for p in (KIT / "skills").glob("*.md")} - {"README"}
    assert set(RENDERABLE) <= contracts, f"缺契约：{set(RENDERABLE) - contracts}"
    assert set(RENDERABLE) <= skills, f"缺 skill：{set(RENDERABLE) - skills}"


def test_contracts_satisfy_the_published_schema_shape() -> None:
    schema = json.loads((KIT / "schemas" / "contract.schema.json").read_text(encoding="utf-8"))
    required = set(schema["required"])
    for name in RENDERABLE:
        contract = json.loads((KIT / "contracts" / f"{name}.json").read_text(encoding="utf-8"))
        gaps = required - set(contract)
        assert not gaps, f"{name} 缺字段 {gaps}"
        assert contract["evidence"], f"{name} 没有出处"
        assert isinstance(contract["descriptor"], dict) and contract["descriptor"]


def _mercury_payload() -> dict:
    return {
        "address": ["1119 Freer Avenue", "Temple City, CA 91780"],
        "footer_line2": "Mercury | help@mercury.com | 2261 Market Street, Suite 86807, San Francisco, CA 94114",
        "transactions": [
            {"date": "2026-09-03", "type": "Card", "desc": "VONS 1350 TEMPLE CITY CA", "amount": -99.92},
            # 两个词的商户名 + PAYMENT 尾巴：剥完还剩一个词时规则故意不剥（"CLIENT PAYMENT" → "CLIENT" 更假）
            {"date": "2026-09-04", "type": "ACH", "desc": "NETFLIX SUBSCRIPTION PAYMENT", "amount": -13.35},
        ],
    }


def test_lint_catches_the_three_classic_mistakes(tmp_path: Path) -> None:
    data = tmp_path / "month.json"
    data.write_text(json.dumps(_mercury_payload()), encoding="utf-8")
    result = _lint("--contract", str(KIT / "contracts" / "mercury.json"), "--data", str(data))
    assert result.returncode == 1
    assert "descriptor-city" in result.stdout        # 卡行带城市
    assert "type-enum" in result.stdout              # 裸 ACH
    assert "descriptor-tail" in result.stdout        # PAYMENT 尾巴


def test_conform_fixes_them_and_the_second_pass_is_clean() -> None:
    data = _mercury_payload()
    sys.path.insert(0, str(KIT / "tools"))
    import statement_lint  # noqa: PLC0415

    contract = json.loads((KIT / "contracts" / "mercury.json").read_text(encoding="utf-8"))
    counts = statement_lint.conform(contract, data)
    assert counts["cities"] == 1 and counts["types"] == 1 and counts["tails"] == 1
    assert statement_lint.check_data(contract, data) == []


def test_lint_reads_label_prefixed_rows_for_chase(tmp_path: Path) -> None:
    """Chase 的 desc 里带标签与对齐空档；校验器要能切出描述符本体。"""
    data = {
        "address": ["1 Main St", "Austin, TX 78704"],
        "footers": ["JPMorgan Chase Bank, N.A. | chase.com | 1-800-935-9935"],
        "transactions": [
            {"date": "2026-09-02", "type": "Deposit", "desc": "Deposit          09/02 LONGBOW AGENCY", "amount": 908.98},
            {"date": "2026-09-04", "type": "Card Purchase",
             "desc": "Card Purchase          09/04 KROGER 9274 AUSTIN TX Card 3217", "amount": -88.12},
        ],
    }
    path = tmp_path / "chase.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    result = _lint("--contract", str(KIT / "contracts" / "chase-personal.json"), "--data", str(path))
    assert result.returncode == 0, result.stdout


def test_kit_zip_builds_and_contains_contracts(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "tools"))
    from export_agent_kit import build  # noqa: PLC0415

    zip_path = build(tmp_path / "agent-kit.zip", out_dir=tmp_path / "agent-kit")
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert "agent-kit/README.md" in names
    assert "agent-kit/contracts/chase-personal.json" in names
    assert "agent-kit/tools/statement_lint.py" in names
    assert "agent-kit/prompts/auditors/a6-contract.md" in names
