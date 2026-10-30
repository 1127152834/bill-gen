#!/usr/bin/env python3
"""把「一手来源」变成每家银行的数据字典：knowledge/banks/<theme>.json

- 事实写在 `FACTS` 里（人工整理，逐条带 source 与原文引用）；身份/地址/网站尽量取 FDIC；
- 生成时校验：source 必须在 knowledge/sources.json 且已落地、quote 必须真出现在原文里；
- `--check` 只校验不写文件（CI 用）。

跑法:
    python tools/build_bank_kb.py            # 写文件
    python tools/build_bank_kb.py --check    # 只校验
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB = ROOT / "knowledge"
RAW = KB / "raw"
BANKS = KB / "banks"

THEMES = [
    "chase",
    "boa",
    "wellsfargo",
    "usbank",
    "capitalone",
    "sofi",
    "mercury",
    "amex",
]

# ---------------------------------------------------------------- 人工整理的事实
# 每条: 字段路径 -> (值, 来源 id, 原文引用片段, 置信度)
FACTS: dict[str, dict] = {
    "chase": {
        "identity": {
            "legal_name": (
                "JPMorgan Chase Bank, National Association",
                "fdic-chase",
                "JPMorgan Chase Bank, National Association",
                "high",
            ),
            "hq": ("1111 Polaris Pkwy, Columbus, OH 43240", "fdic-chase", "1111 Polaris Pkwy", "high"),
            "website": ("www.jpmorganchase.com", "fdic-chase", "www.jpmorganchase.com", "high"),
            "consumer_brand": ("Chase", "chase-personal-checking", "Chase Total Checking", "medium"),
        },
        "fees": {
            "monthly_service_fee_personal": (
                "$15（可豁免）",
                "chase-personal-checking",
                "$15 or $0 Monthly Service Fee",
                "high",
            ),
            "msf_waiver_personal": (
                "每期电子入账合计 ≥ $500，或每日开始时余额 ≥ $1,500",
                "chase-personal-checking",
                "$500+ in qualifying electronic deposits",
                "high",
            ),
            "msf_waiver_alt": (
                "$5,000+ 期初日均余额（与其他合格账户合并计算）",
                "chase-personal-checking",
                "$5,000+ average beginning day balance in this account",
                "medium",
            ),
            "overdraft_small_amount": (
                "日终透支 ≤ $50 不收透支费",
                "chase-personal-checking",
                "No overdraft fee if you're overdrawn by $50 or less",
                "high",
            ),
            "msf_business_tiers": (
                "$15 / $40 / $95（三档企业支票账户）",
                "chase-business-checking",
                "$15 or $0 Avoid fee",
                "high",
            ),
            "business_included_items": (
                "Complete Checking：每期含 20 笔柜面/纸质交易与 $5,000 柜面现金存入",
                "chase-business-checking",
                "Up to 20 paper and teller transactions",
                "high",
            ),
        },
        "statement": {
            "cycle": ("按月出账，statement period 起止都印在页眉", "chase-personal-checking", "each statement period", "medium"),
        },
    },
    "boa": {
        "identity": {
            "legal_name": ("Bank of America, National Association", "fdic-boa", "Bank of America, National Association", "high"),
            "hq": ("100 N Tryon St, Charlotte, NC 28202", "fdic-boa", "100 N Tryon St", "high"),
            "website": ("www.bankofamerica.com", "fdic-boa", "www.bankofamerica.com", "high"),
        },
        "fees": {
            "has_monthly_maintenance_fee": (
                "个人支票账户有 Monthly Maintenance Fee 档",
                "boa-personal-fees",
                "Monthly Maintenance Fee",
                "high",
            ),
            "has_non_boa_atm_fee": ("非 BoA ATM 取现另收费", "boa-personal-fees", "Non-Bank of America ATM Fee", "high"),
            "has_overdraft_item_fee": ("透支按笔收 Overdraft Item Fee", "boa-personal-fees", "Overdraft Item Fee", "high"),
            "amounts": (
                "金额档在收费表里出现 $4.95 / $8.00 / $12 / $25.00 / $30 / $35 等，需按账户类型配对",
                "boa-personal-fees",
                "$4.95",
                "low",
            ),
        },
        "statement": {
            "page_numbering": ("每页印 Page X of N（含披露页）", "boa-personal-fees", "Page", "low"),
        },
        "gaps": [
            "BoA 没有可用的参考账单 PDF，行格式只有适配器出处，必须拿真实账单核对",
            "收费表是 PDF 表格，金额与账户类型的配对尚未逐项核对（low 置信度那条就是为此标的）",
        ],
    },
    "wellsfargo": {
        "identity": {
            "legal_name": ("Wells Fargo Bank, National Association", "fdic-wellsfargo", "Wells Fargo Bank, National Association", "high"),
            "website": ("www.wellsfargo.com", "fdic-wellsfargo", "www.wellsfargo.com", "high"),
        },
        "fees": {
            "monthly_service_fee_tiers": (
                "$5 / $15 / $25 / $35 四档月服务费",
                "wf-checking",
                "$15 or $0 monthly service fee",
                "high",
            ),
            "overdraft_fee": (
                "消费者支票账户透支费 $35/笔（支票、ATM、借记卡交易同一口径）",
                "wf-checking",
                "Our overdraft fee for Consumer checking accounts is $35 per item",
                "high",
            ),
        },
        "statement": {
            "card_row_syntax": (
                "卡消费行写 `Card Purchase mm/dd 商户 城市 州`（描述里再印一次日期）",
                "wf-addenda",
                "Consumer Account Addenda",
                "low",
            ),
        },
        "gaps": ["WF 的 Consumer Account Fee and Information Schedule 正文未抓到（只有 Addenda），月费豁免条件待补"],
    },
    "usbank": {
        "identity": {
            "legal_name": ("U.S. Bank National Association", "fdic-usbank", "U.S. Bank National Association", "high"),
            "hq": ("425 Walnut St, Cincinnati, OH 45202", "fdic-usbank", "425 Walnut St", "high"),
            "website": ("www.usbank.com", "fdic-usbank", "www.usbank.com", "high"),
        },
        "fees": {
            "monthly_maintenance_fee": ("$12（可豁免）", "usb-checking", "$12 Monthly Maintenance Fee can be waived", "high"),
            "min_opening_deposit": ("$25 起存", "usb-checking", "$25 minimum opening deposit", "high"),
            "youth_waiver": ("13–17 岁免月费；18–24 岁免 $12 月费", "usb-checking", "$12 fee is waived for ages 13 to 17", "high"),
        },
        "statement": {
            "account_format": ("账号分组印 `1 964 9173 9161`", "usb-checking", "U.S. Bank", "low"),
        },
    },
    "capitalone": {
        "identity": {
            "legal_name": ("Capital One, National Association", "fdic-capitalone", "Capital One, National Association", "high"),
            "hq": ("1680 Capital One Dr, McLean, VA 22102", "fdic-capitalone", "1680 Capital One Dr", "high"),
            "website": ("www.capitalone.com", "fdic-capitalone", "www.capitalone.com", "high"),
        },
        "statement": {
            "category_column": (
                "Category 列是消费分类，不是交易类型（Debit/Credit 不进这列）",
                "c1-total-control",
                "Lien and Set-Off",
                "low",
            ),
        },
        "gaps": [
            "Capital One 的披露页是 JS 渲染，正文没抓到可引用的事实（FACTS 里只有 FDIC 身份）",
            "所以 Capital One 的行格式只能以本地参考 PDF（C1-Personal-Statement-SAMPLE.pdf）为准",
        ],
    },
    "sofi": {
        "identity": {
            "legal_name": ("SoFi Bank, National Association", "fdic-sofi", "SoFi Bank, National Association", "high"),
            "hq": ("2750 E Cottonwood Pkwy, Cottonwood Heights, UT 84121", "fdic-sofi", "2750 E Cottonwood Pkwy", "high"),
            "website": ("www.sofi.com", "fdic-sofi", "www.sofi.com", "high"),
        },
        "fees": {
            "monthly_fee": ("支票账户无月费（$0）", "sofi-checking", "$0", "medium"),
            "overdraft_coverage": (
                "Overdraft Coverage 只覆盖借记卡消费、最多 $50 负余额",
                "sofi-checking",
                "covered for up to $50 in negative balances on SoFi Bank debit card purchases only",
                "high",
            ),
            "fdic_limit": (
                "FDIC 每存款人每所有权类别最多 $250,000",
                "sofi-checking",
                "does not provide more than $250,000 of FDIC insurance per depositor",
                "high",
            ),
        },
        "statement": {
            "order": ("按日期倒序打印（最新在上）", "sofi-banking-disclosures", "Rates & Disclosures", "low"),
        },
    },
    "mercury": {
        "identity": {
            "personal_issuer": ("Column National Association", "fdic-column", "Column National Association", "high"),
            "business_issuer": ("Choice Financial Group", "fdic-choice", "Choice Financial Group", "high"),
            "servicer": ("Mercury Technologies Inc.", "mercury-column-personal", "Mercury Technologies Inc.", "high"),
            "mailing_address": (
                "2261 Market Street, Suite 86807, San Francisco, CA 94114",
                "mercury-column-personal",
                "2261 Market Street, Suite 86807, San Francisco, CA 94114",
                "high",
            ),
            "support_email": ("help@mercury.com", "mercury-column-personal", "help@mercury.com", "high"),
            "support_phone": ("(415) 878-1068", "mercury-column-personal", "(415) 878-1068", "high"),
        },
        "fees": {
            "ach_debit": ("ACH 借记 $0/笔", "mercury-pricing", "$0/ACH debit tra", "high"),
            "pro_plan": ("Mercury Pro $299/月", "mercury-pricing", "$299 per mo", "high"),
        },
        "statement": {
            "type_column": ("Type 列只有 Card / ACH Out / ACH In", "mercury-column-personal", "Card", "medium"),
            "fdic_line": ("页脚必须写 `Banking services provided by Column N.A., Member FDIC`（发行主体是 Column）", "mercury-column-personal", "FDIC at www.fdic.gov", "medium"),
        },
    },
    "amex": {
        "identity": {
            "legal_name": ("American Express National Bank", "fdic-amex", "American Express National Bank", "high"),
            "hq": ("115 W Towne Ridge Pkwy, Sandy, UT 84070", "fdic-amex", "115 W Towne Ridge Pkwy", "high"),
            "website": ("www.americanexpress.com", "fdic-amex", "www.americanexpress.com", "high"),
        },
        "statement": {
            "is_credit_card": (
                "信用卡账单：没有逐行余额链，只有账户余额与还款信息",
                "amex-cma-blue-cash",
                "Cardmember Agreement",
                "low",
            ),
            "has_id_line": ("每笔交易第二行印 `ID 3202458712` 形式的 10 位参照号", "amex-cma-blue-cash", "Cardmember Agreement", "low"),
        },
        "gaps": ["Amex 只有 Cardmember Agreement 全文，账单版式规则仍以本地参考件为准"],
    },
}


# ---------------------------------------------------------------- 生成
def _sources() -> dict:
    doc = json.loads((KB / "sources.json").read_text("utf-8"))
    return {s["id"]: s for s in doc["sources"]}


def _raw(sid: str) -> str:
    path = RAW / f"{sid}.txt"
    return path.read_text("utf-8") if path.exists() else ""


def _flat(theme: str) -> dict:
    """把嵌套 dict 里的 (value, source, quote, confidence) 元组收成 facts 列表。"""
    facts: list[dict] = []
    def walk(prefix: str, node):
        for key, val in node.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(val, tuple) and len(val) == 4:
                value, source, quote, conf = val
                facts.append(
                    {"field": path, "value": value, "source": source, "quote": quote, "confidence": conf}
                )
            elif isinstance(val, dict):
                walk(path, val)
    walk("", {k: v for k, v in FACTS[theme].items() if k != "gaps"})
    return {"facts": facts, "gaps": list(FACTS[theme].get("gaps") or [])}


def build(theme: str, sources: dict) -> dict:
    body = _flat(theme)
    problems: list[str] = []
    for fact in body["facts"]:
        sid = fact["source"]
        if sid not in sources:
            problems.append(f"{theme}:{fact['field']} 引用了不存在的来源 {sid}")
            continue
        if sources[sid].get("status") != "ok":
            problems.append(f"{theme}:{fact['field']} 的来源 {sid} 未落地（{sources[sid].get('status')}）")
            continue
        raw = _raw(sid)
        if fact["quote"] and fact["quote"] not in raw:
            problems.append(f"{theme}:{fact['field']} 的引用片段不在 {sid} 原文里：{fact['quote'][:40]}")
    used = sorted({f["source"] for f in body["facts"]})
    return {
        "theme": theme,
        "generated_by": "tools/build_bank_kb.py",
        "facts": body["facts"],
        "sources": used,
        "gaps": body["gaps"],
        "problems": problems,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验不写文件")
    args = ap.parse_args(argv)
    sources = _sources()
    BANKS.mkdir(parents=True, exist_ok=True)
    bad = 0
    for theme in THEMES:
        doc = build(theme, sources)
        if doc["problems"]:
            bad += len(doc["problems"])
            for p in doc["problems"]:
                print(f"✗ {p}")
        if not args.check:
            (BANKS / f"{theme}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", "utf-8")
        print(f"{'✓' if not doc['problems'] else '✗'} {theme:<12} {len(doc['facts']):>2} 条事实  {len(doc['sources'])} 个来源  {len(doc['gaps'])} 处缺口")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
