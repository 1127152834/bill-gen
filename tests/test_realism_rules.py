"""把「真账单长什么样」钉成回归：11 套模板都必须过 realism_rules 的逐家检查。

工具在 `tools/realism_rules.py`：通用反模式（占位词 / 省略号 / 符号写法 / 全零路由…）
＋ 逐家行文（Chase/BoA 卡尾号、C1 品类列、SoFi Direct Payment、Mercury ACH In…）。
任何一条不满足，这里就红——这是「模板不会退回千篇一律」的闸门。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_template_satisfies_its_bank_rules():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "realism_rules.py"), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"规则不满足：\n{proc.stdout}\n{proc.stderr}"


def test_rules_cover_every_supported_template():
    """新增模板时如果忘了写规则，这里会提醒——否则那套版式没人看着。"""
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "tools"))
    from billgen.synth import supported_ids  # noqa: PLC0415

    import realism_rules  # noqa: PLC0415

    known = set(realism_rules.BANK_RULES)
    missing = [tid for tid in supported_ids() if tid not in known]
    assert not missing, f"这些模板还没有逐家规则：{missing}"
