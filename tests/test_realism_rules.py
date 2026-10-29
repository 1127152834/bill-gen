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


def test_date_order_rule_catches_unsorted_rows() -> None:
    """分区拼接最容易漏排：这条规则必须能抓住倒序流水，也要容忍跨年回绕。"""
    sys.path.insert(0, str(ROOT / "tools"))
    from realism_rules import date_order_problems

    bad = {"withdrawals": [{"date": "Sep 6"}, {"date": "Aug 29"}, {"date": "Sep 4"}]}
    assert date_order_problems("usbank-personal", bad), "倒序流水没被抓出来"
    good = {"withdrawals": [{"date": "Aug 29"}, {"date": "Sep 4"}, {"date": "Sep 6"}]}
    assert not date_order_problems("usbank-personal", good)
    assert not date_order_problems("x", {"rows": [{"date": "Dec 30"}, {"date": "Jan 2"}]}), "跨年回绕被误判"


def test_pdf_fingerprint_rule_catches_injected_producer():
    """生成器指纹与 producer 必须能被抓到：这是「一眼假」里最容易漏的一层（元数据不印在纸面上）。"""
    import importlib.util

    import fitz

    spec = importlib.util.spec_from_file_location("realism_rules", ROOT / "tools" / "realism_rules.py")
    rr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rr)

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "x")
    doc.set_metadata({"producer": "billgen 0.9 / PyMuPDF 1.24"})
    problems = rr.pdf_fingerprint_problems("mercury", doc.tobytes())
    assert {p["rule"] for p in problems} == {"generator", "producer"}

    clean = fitz.open()
    clean.new_page().insert_text((72, 72), "x")
    clean.set_metadata({"producer": "react-pdf"})
    assert rr.pdf_fingerprint_problems("mercury", clean.tobytes()) == []


def test_url_path_rule_catches_stripped_path():
    """参考件里 `chase.com/disclosures` 被剪成 `chase.com` 时必须报出来。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("realism_rules", ROOT / "tools" / "realism_rules.py")
    rr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rr)

    assert [p["hit"] for p in rr.url_path_problems("chase-personal", "web site: chase.com")] == [
        "chase.com/disclosures"
    ]
    assert rr.url_path_problems("chase-personal", "at chase.com/disclosures or call us") == []
