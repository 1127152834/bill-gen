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


def test_summary_arithmetic_rule_catches_broken_total():
    """摘要区加减法必须自洽：改一位数就该被抓到（这是拿账单去办事时第一个被查的东西）。"""
    import importlib.util

    import fitz

    spec = importlib.util.spec_from_file_location("realism_rules", ROOT / "tools" / "realism_rules.py")
    rr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rr)

    def page(begin: str, credits: str, debits: str, end: str) -> bytes:
        doc = fitz.open()
        p = doc.new_page()
        for i, (label, amount) in enumerate(
            (
                ("Beginning Balance", begin),
                ("Deposits and Additions", credits),
                ("Electronic Withdrawals", debits),
                ("Ending Balance", end),
            )
        ):
            y = 100 + i * 14
            p.insert_text((72, y), label)
            p.insert_text((420, y), amount)
        return doc.tobytes()

    ok = rr.summary_arithmetic_problems("chase-personal", page("1,000.00", "500.00", "-250.00", "1,250.00"))
    assert ok == []

    bad = rr.summary_arithmetic_problems("chase-personal", page("1,000.00", "500.00", "-250.00", "1,350.00"))
    assert [p["rule"] for p in bad] == ["summary-arithmetic"]
    assert "1,250.00" in bad[0]["hit"] or "125000" in bad[0]["hit"]


def test_font_parity_rule_catches_stray_base14():
    """往一份全嵌入字体的账单上写一笔 Helvetica，字体闸门必须报。"""
    import fitz

    from billgen.synth.bridge import from_game_result, render
    from tools.realism_rules import font_parity_problems

    import json

    payload = json.loads((ROOT / "tests/fixtures/synth_result.json").read_text())
    # 用 Capital One 做样本：它的参考件全是 MyFont 子集，凭空多一个既非嵌入、
    # 也不在参考件里的 Helvetica 就是硬破绽（U.S. Bank 有一条已声明的豁免，见
    # `FONT_PARITY_EXEMPT`）。
    ledger = from_game_result(payload, watermark=False, template="capitalone")
    clean = render("capitalone", ledger).pdf
    assert font_parity_problems("capitalone", clean) == []

    doc = fitz.open(stream=clean, filetype="pdf")
    doc[0].insert_text(fitz.Point(72, 700), "HELLO", fontsize=12, fontname="helv")
    dirty = doc.tobytes()
    doc.close()

    hits = font_parity_problems("capitalone", dirty)
    assert [h["hit"] for h in hits] == ["Helvetica"]


def test_micro_line_rule_catches_routing_in_the_magnetic_line(tmp_path):
    """磁码行第一段是账号前 8 位，不是路由号；DRE 段必须留着。

    早先 `_micr()` 把 9 位路由号塞进第一段、把 DRE 段换成账号分段，整行跟底稿对不上
    （0.24pt 的字，纸上几乎看不见，但放大或抽文字层一眼就露）。
    """
    import fitz

    from tools.realism_rules import micro_line_problems

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((55, 130), "021000021 2810 2158 1888", fontsize=0.24)
    page.insert_text((210, 130), "  1 000000000 09", fontsize=0.24)
    bad = doc.tobytes()

    doc2 = fitz.open()
    page2 = doc2.new_page(width=612, height=792)
    page2.insert_text((55, 130), "28012158", fontsize=0.24)
    page2.insert_text((88, 130), "DRE 703 219 28826 NNNNNNNNNNN", fontsize=0.24)
    page2.insert_text((265, 130), "0000      AT 00", fontsize=0.24)
    good = doc2.tobytes()

    assert [p["rule"] for p in micro_line_problems("chase-personal", bad)] == ["micr-dre"]
    assert micro_line_problems("chase-personal", good) == []
    # 别家版式没有这条微缩行，不该被这条规则管
    assert micro_line_problems("boa-personal", bad) == []

def test_fake_contact_rule_catches_555_and_double_brand():
    import importlib.util, pathlib, sys

    path = pathlib.Path(__file__).resolve().parents[1] / "tools" / "realism_rules.py"
    spec = importlib.util.spec_from_file_location("realism_rules_probe", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["realism_rules_probe"] = mod
    spec.loader.exec_module(mod)

    assert mod.fake_contact_problems("chase-personal", "Service Center: 1-800-935-9935") == []
    bad = mod.fake_contact_problems(
        "chase-personal", "Service Center: 1-800-555-0004\nchase.com")
    assert [p["rule"] for p in bad] == ["fake-contact"]
    dup = mod.fake_contact_problems(
        "chase-personal", "Web site: jpmorgan-jpmorgan-chase.com")
    assert [p["rule"] for p in dup] == ["double-brand"]

