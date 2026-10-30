"""账单规则检查：把「真账单长什么样」变成可执行的断言。

    python tools/realism_rules.py                    # 全部模板
    python tools/realism_rules.py --templates chase-personal capitalone
    python tools/realism_rules.py --json             # 机器可读，供 CI / 测试用

检查分两类：

1. **通用反模式**（任何模板都不许出现）：占位词、省略号、`- $` / `+ $` 写法、
   全零路由号、全零 ACH trace。
2. **逐家行文**（只有这家才该出现）：Chase/BoA 的卡尾号、Capital One 的品类列、
   SoFi 的 `Direct Payment`、Mercury 的 `ACH In` / `End of Day Balance`、
   U.S. Bank 的 `Ref Number`、Amex 的 `DEBIT CARD PURCHASE` + ID 行。

退出码 1 表示有规则不满足。新增模板/改行文时先跑这个，再跑 pytest。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import fitz  # noqa: E402

from billgen.synth import from_game_result, render, supported_ids  # noqa: E402

PERSONAL = {
    "id": "realism-rules",
    "seed": "realism-rules",
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
            "bankName": "JPMorgan Chase Bank, N.A.",
            "accountType": "checking",
            "accountLast4": "1347",
            "periodStart": "2027-01-01",
            "periodEnd": "2027-01-31",
            "months": [
                {
                    "cycleStart": "2027-01-01",
                    "cycleEnd": "2027-01-31",
                    "openingBalanceCents": 480000,
                    "closingBalanceCents": 512345,
                    "totalCreditsCents": 320000,
                    "totalDebitsCents": 287655,
                    "transactionCount": 8,
                }
            ],
            "transactions": [
                {"id": "t0", "transactionDate": "2027-01-04", "postedDate": "2027-01-04",
                 "description": "ACME CORP PAYROLL PPD", "memo": "", "type": "ACH",
                 "direction": "credit", "amountCents": 312580},
                {"id": "t1", "transactionDate": "2027-01-05", "postedDate": "2027-01-05",
                 "description": "WHOLE FOODS MKT 105 AUSTIN TX", "memo": "", "type": "POS",
                 "direction": "debit", "amountCents": 8642},
                {"id": "t2", "transactionDate": "2027-01-07", "postedDate": "2027-01-07",
                 "description": "CAMDEN PROPERTY MGMT RENT", "memo": "", "type": "ACH",
                 "direction": "debit", "amountCents": 215000},
                {"id": "t3", "transactionDate": "2027-01-09", "postedDate": "2027-01-09",
                 "description": "ZELLE PAYMENT TO JORDAN R", "memo": "", "type": "ZELLE",
                 "direction": "debit", "amountCents": 12000},
                {"id": "t4", "transactionDate": "2027-01-12", "postedDate": "2027-01-12",
                 "description": "NETFLIX.COM", "memo": "", "type": "POS",
                 "direction": "debit", "amountCents": 2299},
                {"id": "t5", "transactionDate": "2027-01-15", "postedDate": "2027-01-15",
                 "description": "ATM WITHDRAWAL 0115 TERMINAL 7014", "memo": "", "type": "ATM",
                 "direction": "debit", "amountCents": 20000},
                {"id": "t6", "transactionDate": "2027-01-18", "postedDate": "2027-01-18",
                 "description": "CHECK 1042", "memo": "", "type": "CHECK",
                 "direction": "debit", "amountCents": 45000},
                {"id": "t7", "transactionDate": "2027-01-31", "postedDate": "2027-01-31",
                 "description": "INTEREST PAYMENT", "memo": "", "type": "INTEREST",
                 "direction": "credit", "amountCents": 82},
            ],
        }
    ],
    "audit": {"rules": [], "findings": []},
    "usage": {"llmCalls": 0, "tokens": 0, "tavilyQueries": 0},
    "warnings": [],
    "status": "completed",
}

#: 通用反模式：任何模板出现即是假件特征。
COMMON_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    ("placeholder", "占位词", re.compile(r"\b(SAMPLE|EXAMPLE|DEMO|DUMMY|LOREM IPSUM)\b")),
    ("ellipsis", "省略号（真账单是硬切）", re.compile(r"…")),
    ("plus-sign", "金额带正号 `+ $1.00`", re.compile(r"\+[^\S\n]*\$\d")),
    ("spaced-minus", "符号与金额之间有空格 `- $1.00`", re.compile(r"(?<![\w$])-[^\S\n]+\$\d")),
    ("zero-routing", "全零路由号", re.compile(r"\b0{9}\b")),
    ("zero-trace", "全零 ACH trace", re.compile(r"\b0{8}\d")),
]

#: 逐家行文：只有这家才该出现的东西（以及别家不该有的）。
BANK_RULES: dict[str, list[tuple[str, str, re.Pattern[str]]]] = {
    "chase-personal": [
        ("card-tail", "卡行带 `Card 1347`", re.compile(r"Card \d{4}")),
        ("zelle-to", "Zelle 行有 `to`", re.compile(r"Zelle Payment to ")),
        ("ach-label", "ACH 行有类型前缀", re.compile(r"ACH (Credit|Debit)")),
    ],
    "chase-business": [
        ("card-tail", "卡行带 `Card 1347`", re.compile(r"Card \d{4}")),
        ("check-paid", "支票行有 `Check Paid`", re.compile(r"Check Paid")),
    ],
    "boa-personal": [
        ("card-tail", "卡行带 `Card 1347`", re.compile(r"Card \d{4}")),
        ("co-id", "ACH 行有 `CO ID:XXXXX#####`", re.compile(r"CO ID:XXXXX\d{5}")),
        ("des", "ACH 行是 `DES:…` 结构", re.compile(r"DES:[A-Z]")),
    ],
    "boa-business": [
        ("co-id", "ACH 行有 `CO ID:XXXXX#####`", re.compile(r"CO ID:XXXXX\d{5}")),
    ],
    "capitalone": [
        ("category", "CATEGORY 列是品类词", re.compile(r"\b(Groceries|Dining|Bills & Utilities|Income|Transfers|Cash|Merchandise)\b")),
    ],
    "sofi": [
        ("direct-payment", "TYPE 列有 `Direct Payment`", re.compile(r"Direct Payment")),
        ("txid", "有 `Transaction ID:` 续行", re.compile(r"Transaction ID:")),
    ],
    "mercury": [
        ("ach-in", "Type 列有 `ACH In`", re.compile(r"ACH In")),
        ("eod", "有 `End of Day Balance` 列", re.compile(r"End of Day Balance")),
    ],
    "amex": [
        ("card-purchase", "有 `DEBIT CARD PURCHASE`", re.compile(r"DEBIT CARD PURCHASE")),
        ("id-line", "有 `ID ####` 续行", re.compile(r"ID \d{10,}")),
    ],
    "usbank-personal": [
        ("ref-number", "有 `Ref Number` 列", re.compile(r"Ref Number")),
    ],
    "usbank-business": [
        ("ref-number", "有 `Ref Number` 列", re.compile(r"Ref Number")),
    ],
    "wellsfargo": [
        ("card-purchase", "有 `Card Purchase` 行文", re.compile(r"Card Purchase")),
        ("daily-balance", "有 `Ending daily balance` 列", re.compile(r"Ending daily")),
    ],
}

#: 别家不该出现的标记（避免「全都一样」）。
FORBIDDEN_PER_BANK: dict[str, list[tuple[str, str, re.Pattern[str]]]] = {
    "capitalone": [("card-tail", "Capital One 行内不印卡尾号", re.compile(r"Card \d{4}"))],
    "sofi": [("card-tail", "SoFi 行内不印卡尾号", re.compile(r"Card \d{4}"))],
    "mercury": [("card-tail", "Mercury 行内不印卡尾号", re.compile(r"Card \d{4}"))],
    "amex": [("card-tail", "Amex 行内不印卡尾号", re.compile(r"Card \d{4}"))],
    "wellsfargo": [("card-tail", "Wells Fargo 行内不印卡尾号", re.compile(r"Card \d{4}"))],
    "usbank-personal": [("card-tail", "U.S. Bank 行内不印卡尾号", re.compile(r"Card \d{4}"))],
    "usbank-business": [("card-tail", "U.S. Bank 行内不印卡尾号", re.compile(r"Card \d{4}"))],
}


def _payload() -> dict:
    payload = json.loads(json.dumps(PERSONAL))
    business = json.loads(json.dumps(payload["statements"][0]))
    business["accountType"] = "business_checking"
    business["accountLast4"] = "8821"
    payload["statements"].append(business)
    return payload



#: 日期写法 → (月, 日)。各家版式各不相同（`Aug 29` / `08/29` / `August 29, 2026`）。
_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}
_DATE_PATTERNS = (
    re.compile(r"^([A-Z][a-z]{2})[a-z]*\s+(\d{1,2})(?:,\s*\d{4})?$"),
    re.compile(r"^(\d{1,2})/(\d{1,2})(?:/\d{2,4})?$"),
)


def _to_month_day(value: str) -> tuple[int, int] | None:
    text = str(value or "").strip()
    for idx, pattern in enumerate(_DATE_PATTERNS):
        hit = pattern.match(text)
        if not hit:
            continue
        if idx == 0:
            month = _MONTHS.get(hit.group(1).lower()[:3])
            if month:
                return (month, int(hit.group(2)))
        else:
            return (int(hit.group(1)), int(hit.group(2)))
    return None


def _row_lists(node, path=""):
    """把所有「带 date 字段的 dict 列表」挖出来（它们就是版面上的一列流水）。"""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _row_lists(value, f"{path}.{key}")
    elif isinstance(node, list):
        if node and all(isinstance(item, dict) and "date" in item for item in node):
            yield path, node
        else:
            for i, item in enumerate(node):
                yield from _row_lists(item, f"{path}[{i}]")


def date_order_problems(template: str, data: dict) -> list[dict[str, str]]:
    """流水必须按日期升序印：分区拼接最容易漏排（实测 USB 印出 Sep 6 → Aug 29 → Sep 4）。

    跨年账期（12 月 → 1 月）允许一次回绕，别的一律算错序。
    """
    problems: list[dict[str, str]] = []
    for path, rows in _row_lists(data):
        if len(rows) < 2:
            continue
        prev = None
        wrap_seen = False
        for item in rows:
            cur = _to_month_day(item.get("date", ""))
            if cur is None:
                prev = None
                continue
            if prev is not None and cur < prev:
                wraps = prev[0] >= 11 and cur[0] <= 2
                if wraps and not wrap_seen:
                    wrap_seen = True
                else:
                    problems.append(
                        {
                            "template": template,
                            "rule": "date-order",
                            "label": "流水日期必须升序",
                            "hit": f"{path}: {prev[0]:02d}/{prev[1]:02d} → {cur[0]:02d}/{cur[1]:02d}",
                        }
                    )
                    break
            prev = cur
    return problems


# PDF 里绝不能残留的生成器指纹：真出件不会出现这些字样，出现一次就一眼假。
# 注意别把参考件自带的 producer 也算进来（C1 的底稿 producer 本来就是 openhtmltopdf）。
FINGERPRINTS = [
    ("generator", "生成器指纹", re.compile(r"billgen|PyMuPDF|pymupdf|MuPDF|mupdf|pdf-poster|pdfpost", re.IGNORECASE)),
]

# 每家出件该带的 producer（与真实账单一致；改版式时顺手核对）
EXPECTED_PRODUCER = {
    "chase-personal": "OpenText",
    "chase-business": "OpenText",
    "usbank-personal": "AFP2PDF",
    "usbank-business": "AFP2PDF",
    "capitalone": "openhtmltopdf",
    "mercury": "react-pdf",
    "sofi": "LibreOffice",
    "wellsfargo": "iText",
    "amex": "Smart Communications",
    "boa-personal": "TargetStream",
    "boa-business": "TargetStream",
}


#: 有真参考件的版式：参考件里带路径的网址，我们出片必须一字不差地留着。
#: 银行把「查披露页 / 查账单说明」的路径印在正文里，剪掉只留域名是常见的手滑，
#: 但它一眼能看出来（真账单不会把自家路径省了）。
REFERENCE_PDF = {
    "chase-personal": "src/billgen/themes/chase/assets/chase_static_personal.pdf",
    "chase-business": "src/billgen/themes/chase/assets/chase_static_business.pdf",
    "usbank-personal": "src/billgen/themes/usbank/assets/us_static_personal.pdf",
    "usbank-business": "src/billgen/themes/usbank/assets/usbiz_static.pdf",
    "capitalone": "src/billgen/themes/capitalone/C1-Personal-Statement-SAMPLE.pdf",
    "mercury": "src/billgen/themes/mercury/Demo-Bank-Statement-SAMPLE.pdf",
    "sofi": "src/billgen/themes/sofi/SoFi-Statement-SAMPLE.pdf",
}

_URL_WITH_PATH = re.compile(r"([a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org)/[A-Za-z0-9/_.-]+)")


def url_path_problems(template: str, text: str) -> list[dict[str, str]]:
    """参考件里带路径的网址，出片里必须一个不少（只换域名、丢掉 /path 是最常见的手滑）。"""
    rel = REFERENCE_PDF.get(template)
    if not rel:
        return []
    try:
        with fitz.open(str(ROOT / rel)) as ref:
            ref_text = " ".join(page.get_text() for page in ref)
    except Exception:  # noqa: BLE001 - 参考件读不到时不阻断规则
        return []
    want = {u.rstrip(".") for u in _URL_WITH_PATH.findall(ref_text)}
    have = {u.rstrip(".") for u in _URL_WITH_PATH.findall(text)}
    return [
        {"template": template, "rule": "url-path", "label": "参考件里的网址路径被剪掉了", "hit": u}
        for u in sorted(want - have)
    ]


_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")


def _font_names(source) -> set[str]:
    """文档里出现过的字体名（去掉子集前缀 `ABCDEF+`）。"""
    names: set[str] = set()
    opener = (lambda: fitz.open(stream=source, filetype="pdf")) if isinstance(source, bytes) else (lambda: fitz.open(str(source)))
    with opener() as doc:
        for page in doc:
            for entry in page.get_fonts():
                names.add(_SUBSET_PREFIX.sub("", entry[3]))
    return names


#: 参考件里的字体名本身就残缺时，给一个等价表（我们的名字 → 参考件里的名字）。
#: Capital One 条款页的 medium 子集在官方 PDF 里 BaseFont 就是一个小数点
#: （`AAAAZO+.`），PyMuPDF 读出来叫 `.`；我们插的是同一款字，只是名字正常。
FONT_EQUIVALENTS: dict[str, dict[str, str]] = {"capitalone": {"MyFontMedium": "."}}


#: 已知、且有意保留的字体差异：这些版式的品牌文字（行址、客服号）目前是用 base-14
#: Helvetica 另行绘制的 —— 主题自带的是 CID 子集，直接写内容流字号能对上、但文字层
#: 会变成一串读不出来的 CID 码（比多一个未嵌入字体糟得多）。等主题能提供可复用的
#: 字体句柄再关掉这条豁免。
FONT_PARITY_EXEMPT = {
    "usbank-personal": {"Helvetica"},
    "usbank-business": {"Helvetica"},
}


def font_parity_problems(template: str, pdf: bytes) -> list[dict[str, str]]:
    """出片的字体清单不能比参考件多。

    换掉正文是最容易漏的地方：我们用 base-14 Helvetica 往一份「所有字体都嵌入
    子集」的账单上写字，`pdffonts` 里立刻多一个 `no`（未嵌入）。这种差异不会
    出现在肉眼里，但只要有人查字体就露馅。
    """
    rel = REFERENCE_PDF.get(template)
    if not rel:
        return []
    try:
        want = _font_names(ROOT / rel)
    except Exception:  # noqa: BLE001 - 参考件读不到时不阻断规则
        return []
    if not want:
        return []
    ours = _font_names(pdf)
    for mine, theirs in FONT_EQUIVALENTS.get(template, {}).items():
        if mine in ours and theirs in want:
            ours.discard(mine)
    ours -= FONT_PARITY_EXEMPT.get(template, set())
    extra = sorted(ours - want)
    return [
        {"template": template, "rule": "font-parity", "label": "多出参考件没有的字体", "hit": name}
        for name in extra
    ]


def pdf_fingerprint_problems(template: str, pdf: bytes) -> list[dict[str, str]]:
    """扫原始字节 + 文档信息：生成器指纹残留、producer 与真实账单不符。"""
    problems: list[dict[str, str]] = []
    raw = pdf.decode("latin-1", errors="ignore")
    for key, label, pattern in FINGERPRINTS:
        hit = pattern.search(raw)
        if hit:
            problems.append({"template": template, "rule": key, "label": label, "hit": hit.group(0)[:40]})
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        producer = (doc.metadata or {}).get("producer") or ""
    want = EXPECTED_PRODUCER.get(template)
    if want and want.lower() not in producer.lower():
        problems.append(
            {"template": template, "rule": "producer", "label": "producer 与真实账单不符", "hit": f"{producer or '（空）'} ≠ {want}"}
        )
    return problems


import re as _re_page

_PAGE_RE = _re_page.compile(r"Page\s+(\d+)\s*of\s*(\d+)")


def page_number_problems(template: str, pdf: bytes) -> list[dict[str, str]]:
    """页码必须自洽：`Page k of N` 里 k 按 1..N 递增，N 等于**带页码的页数**。

    真实账单里披露页、尾空白页是不编号的（U.S. Bank 4 页只印 `Page 1 of 2`、
    `Page 2 of 2`），所以 N 不等于物理页数是正常的；但 N 与「实际带页码的页数」
    不一致、或 k 跳号/重号，就是露馅。
    """
    problems: list[dict[str, str]] = []
    stamps: list[tuple[int, int, int]] = []          # (物理页, k, N)
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        for page in doc:
            hit = _PAGE_RE.search(page.get_text())
            if hit:
                stamps.append((page.number + 1, int(hit.group(1)), int(hit.group(2))))
        pages = doc.page_count
    if not stamps:
        return problems
    totals = {n for _, _, n in stamps}
    if len(totals) > 1:
        problems.append({"template": template, "rule": "page-number",
                         "label": "同一份账单里页码总数不一致", "hit": str(sorted(totals))})
    total = stamps[0][2]
    seq = [k for _, k, _ in stamps]
    if seq != list(range(1, len(stamps) + 1)):
        problems.append({"template": template, "rule": "page-number",
                         "label": "页码不连续或重号", "hit": f"{seq}（物理页 {[x for x,_,_ in stamps]}）"})
    if total != len(stamps):
        problems.append({"template": template, "rule": "page-number",
                         "label": "页码总数与实际带页码页数不符",
                         "hit": f"of {total}，实际带页码 {len(stamps)} 页 / 全件 {pages} 页"})
    return problems


_MONEY_TOKEN = _re_page.compile(r"^[-+]?\$?[\d,]+\.\d\d[-+]?$")
# 金额常被拆成几格：`$` 单独一格、负号单独一格、`-  5,154.53` 中间还带空格。
_AMOUNT_FRAGMENT = _re_page.compile(r"^[-+$]?[\d,]*\.?\d*[-+]?$")


def _money_of(*parts: str) -> int | None:
    joined = "".join(part.replace(" ", "") for part in parts)
    return _money_cents(joined) if _MONEY_TOKEN.match(joined) else None
_DATE_LABEL = _re_page.compile(r"^\d{1,2}/\d{1,2}|^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b", _re_page.I)
_BEGIN_LABEL = _re_page.compile(r"beginning balance", _re_page.I)
_END_LABEL = _re_page.compile(r"(ending|closing|current) balance", _re_page.I)


def _money_cents(token: str) -> int | None:
    """`$1,234.56` / `-1,234.56` / `1,234.56-`（U.S. Bank 尾随减号）→ 分。"""
    text = token.replace("$", "").replace(",", "").replace(" ", "")
    negative = text.startswith("-") or text.endswith("-")
    text = text.strip("-")
    if not text:
        return None
    try:
        value = int(round(float(text) * 100))
    except ValueError:
        return None
    return -value if negative else value


def _text_rows(pdf: bytes, tol: float = 2.0, gap: float = 18.0) -> list[tuple[float, float, str, int | None]]:
    """把文字按基线聚成行，再把「标签 → 它右边那一笔金额」配成 `(物理页, 基线, 标签, 金额)`。

    三件事非做不可，否则规则会「假通过」：
    * 金额和标签通常不是同一个 span（右对齐单独绘制），按基线 ±tol 抓同行；
    * 金额常被拆成几格（U.S. Bank 的 `$` 与 `11,249.73`、Wells Fargo 的 `-` 与 `5,154.53`），
      要把紧随其后的金额碎片拼成一个数；
    * 摘要区可能是**双列**（Amex 左边 `Statement Summary`、右边 `Interest Summary` 共用同一条
      基线），所以「取本行最后一个金额」会拿到右列的数——只取标签之后、下一个标签之前的金额。
    """
    rows: list[tuple[float, float, str, int | None]] = []
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        for page in doc:
            spans: list[tuple[float, float, float, str]] = []      # x0, x1, 基线, 文本
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        text = span["text"].strip()
                        if text:
                            spans.append((span["bbox"][0], span["bbox"][2], span["origin"][1], text))
            spans.sort(key=lambda item: (round(item[2], 1), item[0]))

            def segments(baseline: float, items: list[tuple[float, float, str]]) -> None:
                items.sort()
                chunks: list[list[tuple[float, float, str]]] = []
                prev_x1 = None
                for item in items:
                    if chunks and item[0] - (prev_x1 or 0) <= gap:
                        chunks[-1].append(item)
                    else:
                        chunks.append([item])
                    prev_x1 = item[1]

                def is_money(chunk: list[tuple[float, float, str]]) -> bool:
                    # 金额碎片里可能夹空格（Wells Fargo 印 `-  5,154.53`），先抹掉再判。
                    return all(_AMOUNT_FRAGMENT.match(text.replace(" ", "")) for _, _, text in chunk)

                index = 0
                while index < len(chunks):
                    chunk = chunks[index]
                    if is_money(chunk):
                        index += 1
                        continue
                    label = " ".join(text for _, _, text in chunk).strip()
                    amount = None
                    look = index + 1
                    gathered: list[str] = []
                    prev_x1 = chunk[-1][1]
                    while look < len(chunks) and is_money(chunks[look]):
                        parts = [text for _, _, text in chunks[look]]
                        adjacent = chunks[look][0][0] - prev_x1 <= 12.0
                        candidate = gathered + parts if adjacent else parts
                        value = _money_of(*candidate)
                        if value is not None:
                            amount = value
                            look += 1
                            break
                        if adjacent:
                            gathered = candidate
                        else:
                            gathered = parts
                        prev_x1 = chunks[look][-1][1]
                        look += 1
                    rows.append((page.number + 1, baseline, label, amount))
                    index = look if amount is not None else index + 1

            group: list[tuple[float, float, float, str]] = []
            base = None
            for span in spans:
                if base is None or abs(span[2] - base) <= tol:
                    group.append(span)
                    base = span[2] if base is None else base
                    continue
                segments(base, [(x0, x1, text) for x0, x1, _, text in group])
                group = [span]
                base = span[2]
            if group:
                segments(base or 0.0, [(x0, x1, text) for x0, x1, _, text in group])
    return rows


def summary_arithmetic_problems(template: str, pdf: bytes) -> list[dict[str, str]]:
    """摘要区必须自己算得平：`期初 + 各分项（带符号）= 期末`。

    只取第一对 `Beginning Balance … Ending Balance`，且跳过以日期开头的行——
    Chase / Amex 的流水区也会出现 `Beginning Balance`，把流水金额算进来必然算不平。
    """
    rows = [r for r in _text_rows(pdf) if r[3] is not None and r[2] and not _DATE_LABEL.match(r[2])]
    begin = next((i for i, r in enumerate(rows) if _BEGIN_LABEL.search(r[2])), None)
    if begin is None:
        return []
    end = next((i for i, r in enumerate(rows) if i > begin and _END_LABEL.search(r[2])), None)
    if end is None or end - begin < 2:
        return []
    opening = rows[begin][3]
    closing = rows[end][3]
    middle = sum(r[3] for r in rows[begin + 1 : end])
    if opening + middle == closing:
        return []
    return [{
        "template": template,
        "rule": "summary-arithmetic",
        "label": "摘要区加减法不平（期初 + 分项 ≠ 期末）",
        "hit": f"期初 {opening} + 分项 {middle} = {opening + middle}，期末印的是 {closing}"
               f"（差 {closing - opening - middle} 分）",
    }]


# ---------------------------------------------------------------------------
# 换行名出片：别家银行名不许留在纸上
# ---------------------------------------------------------------------------
# 生产上默认就走这条路（比如 BoA 的账本落在 chase/usbank 版式上），所以每套版式都得
# 能换成账本那家银行的名字。这里用一个虚构行名把所有版式跑一遍：原行名的任何痕迹
# 都不许留，新行名必须印出来。
CROSS_BANK_NAME = "Cascade Trust Bank"
CROSS_BANK_TOKENS = {
    "boa": (r"bank of america",),
    "chase": (r"jpmorgan", r"\bchase\b"),
    "usbank": (r"u\.s\. bank", r"usbank"),
    "wellsfargo": (r"wells fargo", r"wellsfargo"),
    "capitalone": (r"capital\s*one", r"capitalone\.com", r"1-888-464-0727", r"richmond"),
    "mercury": (r"\bmercury\b", r"help@mercury\.com", r"market street", r"column n\.a\."),
    "sofi": (r"\bsofi\b",),
    "amex": (r"american express", r"\bamex\b"),
}


# 磁码行：0.24pt 的微缩行，纸上几乎看不见，放大或抽文字层就能读。
# 底稿形态是「账号前 8 位 / DRE 段 / 周期段 / on-us 段」，其中 DRE 是印厂号、不是路由号 ——
# 早先我们把 9 位路由号塞在第一段，把 DRE 段换成了账号分段，整行跟真件对不上。
_MICRO_SIZE = 1.5  # pt；底稿那行是 0.24pt，留点余量


def micro_line_problems(template: str, pdf: bytes) -> list[dict[str, str]]:
    if not template.startswith("chase"):
        return []
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        spans = []
        for page in doc[:1]:
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        if span["size"] <= _MICRO_SIZE and span["text"].strip():
                            spans.append(span["text"])
    micro = " ".join(spans)
    problems: list[dict[str, str]] = []
    if "DRE " not in micro:
        problems.append({"template": template, "rule": "micr-dre",
                         "label": "磁码行缺 DRE 段", "hit": micro[:40]})
    # 个人版底稿最后一段是 `0000      AT 00`，企业版只有 `0000` —— 都算有 on-us 段
    if not ("AT 00" in micro or "0000" in micro):
        problems.append({"template": template, "rule": "micr-on-us",
                         "label": "磁码行缺 on-us 段", "hit": micro[:40]})
    return problems


def cross_bank_problems(template: str) -> list[dict[str, str]]:
    """把这套版式换个行名印一遍，原行名残留 / 新行名没印出来都算问题。"""
    root = template.split("-")[0]
    patterns = CROSS_BANK_TOKENS.get(root, ())
    if not patterns:
        return []
    payload = _payload()
    want = "business_checking" if template.endswith("-business") else "checking"
    payload["statements"] = [s for s in payload["statements"] if s["accountType"] == want]
    ledger = from_game_result(payload, watermark=False, bank_name=CROSS_BANK_NAME)
    with fitz.open(stream=render(template, ledger).pdf, filetype="pdf") as doc:
        text = " ".join(page.get_text() for page in doc)
    problems: list[dict[str, str]] = []
    flat = " ".join(text.split())
    for pattern in patterns:
        hit = re.search(pattern, flat, re.IGNORECASE)
        if hit:
            problems.append({
                "template": template,
                "rule": "cross-bank-residue",
                "label": "换成别家行名出片后，纸上还留着原行名",
                "hit": flat[max(0, hit.start() - 40):hit.end() + 40],
            })
    if CROSS_BANK_NAME.lower() not in flat.lower():
        problems.append({
            "template": template,
            "rule": "cross-bank-missing",
            "label": "换成别家行名出片后，新行名一次都没印出来",
            "hit": CROSS_BANK_NAME,
        })
    return problems


def statement_text(template: str, ledger, *, watermark: bool = False) -> str:
    """渲染并取正文文本。

    默认**不画水印**：水印是我们给样张加的标记，真实出件没有；规则检查看的是出件形态。
    """
    with fitz.open(stream=render(template, ledger).pdf, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def check_template(template: str, ledger) -> list[dict[str, str]]:
    pdf = render(template, ledger).pdf
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        text = "\n".join(page.get_text() for page in doc)
    problems: list[dict[str, str]] = pdf_fingerprint_problems(template, pdf)
    problems.extend(page_number_problems(template, pdf))
    problems.extend(summary_arithmetic_problems(template, pdf))
    problems.extend(url_path_problems(template, text))
    problems.extend(font_parity_problems(template, pdf))
    problems.extend(micro_line_problems(template, pdf))
    for key, label, pattern in COMMON_RULES:
        hit = pattern.search(text)
        if hit:
            problems.append({"template": template, "rule": key, "label": label, "hit": hit.group(0)})
    for key, label, pattern in BANK_RULES.get(template, []):
        if not pattern.search(text):
            problems.append({"template": template, "rule": key, "label": label, "hit": "（缺失）"})
    try:
        from billgen.synth.bridge import build_data

        problems.extend(date_order_problems(template, build_data(template, ledger)))
    except Exception as exc:  # noqa: BLE001 - 拿不到装配数据就当规则不满足
        problems.append({"template": template, "rule": "date-order", "label": "无法检查日期顺序", "hit": str(exc)[:120]})
    for key, label, pattern in FORBIDDEN_PER_BANK.get(template, []):
        hit = pattern.search(text)
        if hit:
            problems.append({"template": template, "rule": key, "label": label, "hit": hit.group(0)})
    return problems


def _ledger_for(template: str):
    """business 版式要 business_checking 账本，个人版式要 checking。"""
    payload = _payload()
    want = "business_checking" if template.endswith("-business") else "checking"
    payload["statements"] = [s for s in payload["statements"] if s["accountType"] == want]
    return from_game_result(payload, watermark=False)


def run(templates: list[str] | None = None) -> list[dict[str, str]]:
    ids = templates or list(supported_ids())
    problems: list[dict[str, str]] = []
    for template in ids:
        try:
            problems.extend(check_template(template, _ledger_for(template)))
            problems.extend(cross_bank_problems(template))
        except Exception as exc:  # noqa: BLE001 - 渲染失败也是规则不满足
            problems.append({"template": template, "rule": "render", "label": "渲染失败", "hit": str(exc)[:120]})
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--templates", nargs="*", help="只检查这些模板（默认全部）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    problems = run(args.templates)
    if args.json:
        print(json.dumps(problems, ensure_ascii=False, indent=2))
    else:
        ids = args.templates or list(supported_ids())
        for template in ids:
            mine = [p for p in problems if p["template"] == template]
            mark = "✔" if not mine else "✘"
            print(f"{mark} {template}")
            for p in mine:
                print(f"    - [{p['rule']}] {p['label']}：{p['hit']}")
        print(f"\n{len(problems)} 条规则不满足" if problems else "\n全部规则通过")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
