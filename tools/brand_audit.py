"""品牌残留审计工具。

    python tools/brand_audit.py                     # 用内置样例账本审计全部 11 个模板
    python tools/brand_audit.py --from result.json  # 用真实场景结果审计
    python tools/brand_audit.py --templates chase-personal usbank-personal

文本层残留分两类：`KNOWN_GAPS` 里的是已知的（烙在复刻底稿/法务段落里），
其它命中就是回归，退出码 1。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import fitz  # noqa: E402

from billgen.audit import scan_text, unknown_gaps  # noqa: E402
from billgen.synth import from_game_result, render, supported_ids  # noqa: E402

SAMPLE = {
    "id": "brand-audit",
    "seed": "brand-audit",
    "createdAt": "2027-02-01T00:00:00.000Z",
    "subject": {
        "name": "Maya Okafor",
        "age": 34,
        "address": {"street": "2104 S 1st St", "city": "Austin", "state": "TX", "zip": "78704"},
        "occupation": "Designer",
        "household": "single",
        "hobbies": [],
    },
    "statements": [
        {
            "bankName": "Harborline Bank",
            "accountType": "checking",
            "accountLast4": "1347",
            "periodStart": "2027-01-01",
            "periodEnd": "2027-01-31",
            "months": [
                {
                    "cycleStart": "2027-01-01",
                    "cycleEnd": "2027-01-31",
                    "openingBalanceCents": 100000,
                    "closingBalanceCents": 150000,
                    "totalCreditsCents": 250000,
                    "totalDebitsCents": 200000,
                    "transactionCount": 4,
                }
            ],
            "transactions": [
                {"id": "t0", "transactionDate": "2027-01-04", "postedDate": "2027-01-04",
                 "description": "ACME PAYROLL DIRECT DEP", "memo": "", "type": "ACH",
                 "direction": "credit", "amountCents": 250000},
                {"id": "t1", "transactionDate": "2027-01-06", "postedDate": "2027-01-06",
                 "description": "CAMDEN LIVING RENT PMT 0106", "memo": "", "type": "ACH",
                 "direction": "debit", "amountCents": 120000},
                {"id": "t2", "transactionDate": "2027-01-12", "postedDate": "2027-01-12",
                 "description": "ATM WITHDRAWAL 0112 TERMINAL", "memo": "", "type": "ATM",
                 "direction": "debit", "amountCents": 30000},
                {"id": "t3", "transactionDate": "2027-01-20", "postedDate": "2027-01-20",
                 "description": "CHECK 1042", "memo": "", "type": "CHECK",
                 "direction": "debit", "amountCents": 50000},
            ],
        }
    ],
    "audit": {"rules": [], "findings": []},
    "usage": {"llmCalls": 0, "tokens": 0, "tavilyQueries": 0},
    "warnings": [],
    "status": "completed",
}


def _business_copy() -> dict:
    """同一组流水换一份 business_checking 外壳，用来审计商业版式。"""
    payload = json.loads(json.dumps(SAMPLE))
    statement = json.loads(json.dumps(payload["statements"][0]))
    statement["accountType"] = "business_checking"
    statement["accountLast4"] = "8821"
    payload["statements"].append(statement)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="source", type=Path, help="场景结果包 JSON")
    parser.add_argument("--templates", nargs="*", help="只审计这些模板")
    args = parser.parse_args(argv)

    payload = json.loads(args.source.read_text()) if args.source else _business_copy()
    templates = args.templates or list(supported_ids())
    failures = 0
    for template_id in templates:
        account = "business_checking" if "business" in template_id else "checking"
        try:
            ledger = from_game_result(payload, account=account, cycle=0)
            pdf = render(template_id, ledger).pdf
        except Exception as exc:  # 数据里没有该账户类型时跳过，不算失败
            print(f"{template_id:18s} skip  {exc}")
            continue
        with fitz.open(stream=pdf, filetype="pdf") as doc:
            text = "\n".join(page.get_text() for page in doc)
        found = scan_text(text)
        leftover = unknown_gaps(template_id, found)
        known = {token: count for token, count in found.items() if token not in leftover}
        status = "OK" if not leftover else "FAIL"
        print(f"{template_id:18s} {status}  剩余={leftover or '-'}  已知={known or '-'}")
        if leftover:
            failures += 1
    if failures:
        print(f"\n{failures} 个模板出现了未登记的品牌残留；把它改掉，或明确写进 KNOWN_GAPS。")
        return 1
    print("\n全部命中都在 KNOWN_GAPS 内。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
