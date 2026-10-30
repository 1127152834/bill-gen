"""从参考件提取「账单契约」，并生成每家账户的 skill 文档。

原则（对应 ``docs/statement-contract.md``）：
- 每条规则必须有出处：参考 PDF（文件#页）或引擎里已经据真件实现的转换函数。
- **探不到就写进 open_questions，不许凭记忆补规则。**
- 一家账户一份契约（``contracts/<template>.json``）+ 一份 skill（``agents/skills/<template>.md``）。

用法::

    python tools/contract_extract.py                 # 全部模板
    python tools/contract_extract.py --templates mercury,chase-personal
    python tools/contract_extract.py --diff           # 只打印与现有契约的差异
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import fitz

from billgen import registry
from billgen.synth.bridge import TEMPLATE_BANK  # 模板 → 银行真名
from billgen.synth.ledger import real_traits  # 银行真名 → 域名/电话/邮箱等

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "contracts"
CURATED = CONTRACTS / "curated"
SKILLS = ROOT / "agents" / "skills"

_CARD_TAIL = re.compile(r"Card\s+(\d{4})")
_PLACE = re.compile(r"\b([A-Z][A-Za-z .'-]{2,20})\s+([A-Z]{2})\b")
_MONEY = re.compile(r"-?\$?\d[\d,]*\.\d\d")
_PAGE_NO = re.compile(r"Page\s+(\d+)\s+of\s+(\d+)")
_TYPE_SHAPE = re.compile(r"^[A-Za-z][A-Za-z /&-]{1,20}$")

_DATEISH = re.compile(r"\b\d{1,2}/\d{1,2}\b|^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}\b")
_NOISE = (" you ", " your ", "dispute", "statement period", "balance subject", "annual percentage")

_TYPE_TOKENS = (
    "Card Purchase", "POS Purchase", "Debit Card", "ACH Debit", "ACH Credit", "ACH",
    "Direct Deposit", "Zelle", "Check Paid", "Service Fee", "Monthly Service Fee",
    "Withdrawal", "Deposit", "Transfer", "Wire",
)

# 引擎里已经「据真件实现」的转换函数：作为描述符规则的次级出处
DESCRIPTOR_SOURCES = {
    "chase-personal": "src/billgen/synth/adapters/chase.py::_personal_desc",
    "chase-business": "src/billgen/synth/adapters/chase.py::_business_desc",
    "boa-personal": "src/billgen/synth/adapters/boa.py::_descriptor",
    "boa-business": "src/billgen/synth/adapters/boa.py::_descriptor",
    "usbank-personal": "src/billgen/synth/adapters/usbank.py::_row",
    "usbank-business": "src/billgen/synth/adapters/usbank.py::_row",
    "wellsfargo": "src/billgen/synth/line_styles.py::wellsfargo_desc",
    "capitalone": "src/billgen/synth/line_styles.py::capitalone_desc",
    "sofi": "src/billgen/synth/line_styles.py::sofi_desc",
    "mercury": "src/billgen/synth/line_styles.py::mercury_desc",
}

# 已知的每家卡尾策略（有真件支撑，见 docs/realism-patterns.md §4.4）
CARD_TAIL_POLICY = {
    "chase-personal": "in_row",
    "chase-business": "in_row",
    "boa-personal": "in_row",
    "boa-business": "in_row",
    "usbank-personal": False,
    "usbank-business": False,
    "wellsfargo": False,
    "capitalone": False,
    "sofi": False,
    "mercury": False,
}


@dataclass
class Sample:
    path: Path
    pages: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.pages)


def _samples(theme: str) -> list[Sample]:
    base = ROOT / "src" / "billgen" / "themes" / theme
    out: list[Sample] = []
    for path in sorted(base.rglob("*.pdf")):
        try:
            doc = fitz.open(path)
        except Exception:  # noqa: BLE001 - 参考件损坏不该阻断提取
            continue
        out.append(Sample(path=path, pages=[page.get_text() for page in doc]))
    return out


def _rows(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _detect_columns(rows: Iterable[str]) -> list[str]:
    columns = ["date", "description", "amount"]
    if any("balance" in row.lower() for row in rows):
        columns.append("balance")
    return columns


def _detect_types(rows: list[str]) -> list[str]:
    """Type 列取值的证据式探测：短、重复出现、不是金额/日期/句子。"""
    counter: dict[str, int] = {}
    for row in rows:
        if not (2 <= len(row) <= 24):
            continue
        if _MONEY.search(row) or re.match(r"^\d|^[A-Z][a-z]{2} \d", row):
            continue
        letters = sum(ch.isalpha() or ch == " " for ch in row)
        if letters < len(row) * 0.6:
            continue
        if row.isupper() and len(row.split()) <= 2:  # 大标题（ACCOUNT DETAILS）
            continue
        if not _TYPE_SHAPE.match(row) or row.split()[0].lower() in ("date", "amount", "page", "account", "ref"):
            continue
        counter[row] = counter.get(row, 0) + 1
    ordered = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [name for name, hits in ordered if hits >= 3][:8]


def _stacked_rows(text: str, limit: int = 6) -> list[str]:
    """把竖排的表格行拼回一条：连续短行直到出现金额为止。"""
    out: list[str] = []
    buffer: list[str] = []
    for line in (item.strip() for item in text.splitlines()):
        if not line:
            continue
        buffer.append(line)
        if _MONEY.search(line):
            noisy = ("Banking services", "Account Number", "Routing Number", "Account Type",
                     "Beginning Balance", "Closing Balance", "Statement balance",
                     "Total withdrawals", "Total deposits")
            joined = " ".join(buffer).lower()
            dated = bool(_DATEISH.search(buffer[0]))
            if (
                len(buffer) >= 3
                and not any(word in item for item in buffer for word in noisy)
                and not any(word in f" {joined} " for word in _NOISE)
                and dated
            ):
                out.append(" | ".join(buffer))
            buffer = []
            if len(out) >= limit:
                break
        elif len(buffer) > 8:
            buffer = []
    return out


def _detect_card_rows(rows: list[str]) -> list[str]:
    """能看出「一行交易长什么样」的样例行：带金额、够长、去重。"""
    out: list[str] = []
    for row in rows:
        if len(row) < 12 or len(row) > 110 or not _MONEY.search(row):
            continue
        lowered = f" {row.lower()} "
        if any(word in lowered for word in _NOISE) or "banking services" in lowered:
            continue
        if not _DATEISH.search(row):  # 没有日期的一律当成披露文字，不是交易行
            continue
        if row not in out:
            out.append(row)
        if len(out) == 6:
            break
    return out


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def extract(template_id: str) -> dict[str, Any]:
    spec = next((item for item in registry.SPECS if item.id == template_id), None)
    theme = spec.theme if spec else template_id.split("-")[0]
    samples = _samples(theme)
    rows = [row for sample in samples for row in _rows(sample.text)]
    text = "\n".join(sample.text for sample in samples)
    evidence = [f"{_relative(sample.path)}" for sample in samples]
    open_questions: list[str] = []

    card_rows = _detect_card_rows(rows)
    if not card_rows:
        for sample in samples:
            for page in sample.pages:
                card_rows.extend(_stacked_rows(page))
            if card_rows:
                break
    tail_policy = CARD_TAIL_POLICY.get(template_id, "unknown")
    if tail_policy == "in_row" and samples and not any(_CARD_TAIL.search(row) for row in rows):
        open_questions.append("卡尾 4 位应在行内，但参考件没探到（需人工核对样例行）")
    if tail_policy is False and any(_CARD_TAIL.search(row) for row in rows):
        open_questions.append("参考件行内出现卡号，与「卡号在表头」的结论冲突，需人工裁决")

    curated_file = CURATED / f"{theme}.json"
    types = [] if curated_file.is_file() else _detect_types(rows)
    if not types and not curated_file.is_file():
        open_questions.append("未从参考件探到 Type 列取值，需人工核对（可能该版式没有 Type 列）")
    if not samples:
        open_questions.append("该主题没有参考 PDF：规则只能来自适配器实现，必须拿真实账单核对后才能当契约用")
    elif not any(sample.text.strip() for sample in samples):
        open_questions.append("参考件没有文本层（扫描/图形版）：样例行与类型枚举需要 OCR 或人工抄录")

    root = template_id.split("-")[0]
    traits = real_traits(TEMPLATE_BANK.get(root, "")) or {}
    wanted_tokens = [traits.get(key, "") for key in ("domain", "phone", "legal", "city_state_zip")]
    literals = [tok for tok in wanted_tokens if tok and tok in text]

    identity = {
        key: traits.get(key, "")
        for key in ("legal", "domain", "phone", "city_state_zip")
        if traits.get(key)
    }
    contract: dict[str, Any] = {
        "template_id": template_id,
        "theme": theme,
        "bank_identity": identity,
        "columns": _detect_columns(rows),
        "type_enum": types,
        "type_aliases": {},
        "descriptor": {
            "card": {
                "template": "{merchant}" if tail_policy is not True else "{merchant} Card {last4}",
                "rules": (["has_card_tail"] if tail_policy == "in_row" else [])
                + (["no_city"] if theme == "mercury" else []),
                "max_len": 90,
            }
        },
        "card_tail": tail_policy,
        "row_rules": {
            "amount_decimals": 2,
            "balance_chain": True,
        },
        "forbidden_literals": ["SAMPLE", "billgen", "PyMuPDF", "Bank of America"],
        "required_literals": literals,
        "sample_rows": card_rows,
        "extractor": "tools/contract_extract.py",
        "evidence": evidence + ([DESCRIPTOR_SOURCES[template_id]] if template_id in DESCRIPTOR_SOURCES else []),
        "open_questions": open_questions,
    }
    if any(_PAGE_NO.search(row) for row in rows):
        contract["row_rules"]["page_numbering"] = "statement-pages-only"

    curated_path = CURATED / f"{theme}.json"
    if curated_path.is_file():
        curated = json.loads(curated_path.read_text(encoding="utf-8"))
        kept: list[str] = []
        for key in ("columns", "type_enum", "type_aliases", "descriptor", "row_rules",
                    "forbidden_literals", "desc_has_label", "sections_note"):
            if key in curated:  # 空列表也算人工结论（例如「这一版没有 Type 列」）
                contract[key] = curated[key]
                kept.append(key)
        for extra in ("kind", "type_column", "card_types", "knowledge_refs"):
            if extra in curated:
                contract[extra] = curated[extra]
        contract["why"] = curated.get("why", "")
        contract["evidence"] = sorted({*contract["evidence"], *(curated.get("evidence") or [])})
        contract["curated_keys"] = kept
        # curated 规则覆盖后，自动探测的疑问仍然保留，供人工复核
        if curated.get("type_enum") and types:
            curated_types = [str(item).lower() for item in curated["type_enum"]]
            overlap = any(
                detected.lower() in known or known in detected.lower()
                for detected in types
                for known in curated_types
            )
            if not overlap:
                open_questions.append("自动探测的 Type 与人工规则无交集，需复核参考件是否换了版式")
        if types:
            contract["detected_type_enum"] = types
    return contract


def skill_markdown(contract: dict[str, Any], field_doc: Path | None) -> str:
    tid = contract["template_id"]
    spec = next((item for item in registry.SPECS if item.id == tid), None)
    bank = spec.display_name if spec else tid
    lines = [
        f"# Skill · {bank}（`{tid}`）",
        "",
        "> 这是**这个账户**的填写规范与格式常识。写它的人只做归纳：内容全部来自参考件与引擎里已据真件实现的转换函数。",
        "> 机器可读版：[../../contracts/{tid}.json](../../contracts/{tid}.json)。".format(tid=tid),
        "",
        "## 一句话",
        f"- 版式主题：`{contract['theme']}`；列：{', '.join(contract['columns']) or '未探到'}",
        f"- 卡尾 4 位：{_tail_zh(contract['card_tail'])}",
        f"- 有 Type 列：{'是' if contract.get('type_column', bool(contract['type_enum'])) else '否'}"
        + ("（按打印顺序倒序，最新在上）" if contract.get("row_rules", {}).get("order") == "newest-first" else ""),
        "",
        "## 类型枚举（Type / 行种类）",
        ("- " + "\n- ".join(contract["type_enum"])) if contract["type_enum"] else "- 未从参考件探到，按契约 `open_questions` 处理，**不许猜**",
        "",
        "## 描述符语法",
        "| 行类 | 骨架 | 规则 |",
        "| --- | --- | --- |",
    ]
    for key, spec in contract["descriptor"].items():
        rules = ", ".join(spec.get("rules") or []) or "—"
        lines.append(f"| {key} | `{spec.get('template')}` | {rules} |")
    lines += [
        "",
        "## 必须出现 / 禁止出现",
        f"- 必须：{', '.join(contract['required_literals']) or '（未探到，见 open_questions）'}",
        f"- 禁止：{', '.join(contract['forbidden_literals'])}",
        "",
        "## 参考件里的真实行（照这个写）",
    ]
    if contract["sample_rows"]:
        lines += [f"```\n{row}\n```" for row in contract["sample_rows"][:4]]
    else:
        lines.append("- 未探到样例行（该模板可能只由解码器重放，不含可读文本层）")
    lines += ["", "## 出处", *[f"- `{item}`" for item in contract["evidence"]]]
    if contract["open_questions"]:
        lines += ["", "## 待人工核对（不许凭记忆补）", *[f"- {q}" for q in contract["open_questions"]]]
    identity = contract.get("bank_identity") or {}
    if identity:
        lines += ["", "## 抬头身份（必须原样）", *[f"- {key}: `{value}`" for key, value in identity.items()]]
    if contract.get("why"):
        lines += ["", "## 为什么这样写", f"- {contract['why']}"]
    refs = contract.get("knowledge_refs") or []
    if refs:
        lines += ["", "## 本地依据（knowledge/）", *[f"- [{ref}](../../{ref})" for ref in refs]]
    if field_doc and field_doc.is_file():
        lines += ["", f"## 字段字典", f"- [{field_doc.name}](../../{_relative(field_doc)})（每个顶层键的类型与样例）"]
    lines += [
        "",
        "## 常见错误（本项目真实踩过）",
        "- 描述符里带了持卡人城市（Mercury/线上商户）",
        "- Type 列出现裸 `ACH`/`DEBIT`，或把刷卡消费写成 ACH",
        "- 行首出现对齐用的前导空格",
        "- 同一个固定扣款一个月出现两次",
        "- 金额恰好等于类目上限（`-45.00` 四连）",
        "",
        "## 用法",
        "1. 生成阶段：导演/求解器**不看**这份 skill，它们只产出语义字段。",
        "2. 交付前：`contracts.conform(template_id, data)` 按契约整形，`contracts.check()` 校验。",
        "3. 校对阶段：把这份 skill 当人工复核清单，逐项对照参考件。",
    ]
    return "\n".join(lines) + "\n"


def _tail_zh(policy: Any) -> str:
    return {"in_row": "印在行尾（`… Card 4499`）", False: "不进行内，卡号在表头"}.get(policy, "未确认")


def write_contract(contract: dict[str, Any], *, diff_only: bool = False) -> tuple[Path, str]:
    path = CONTRACTS / f"{contract['template_id']}.json"
    existing = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    if diff_only and existing is not None:
        changed = [key for key in contract if existing.get(key) != contract[key]]
        return path, ("差异：" + ", ".join(changed)) if changed else "无差异"
    path.parent.mkdir(parents=True, exist_ok=True)
    if existing is not None:
        contract["evidence"] = sorted({*existing.get("evidence", []), *contract["evidence"]})
    path.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path, "写入"


def main() -> int:
    parser = argparse.ArgumentParser(description="从参考件提取账单契约并生成 skill")
    parser.add_argument("--templates", default="", help="逗号分隔；默认全部可渲染模板")
    parser.add_argument("--diff", action="store_true", help="只打印差异，不写文件")
    args = parser.parse_args()

    wanted = [t.strip() for t in args.templates.split(",") if t.strip()]
    ids = [spec.id for spec in registry.SPECS if spec.kind != "static-web"]
    if wanted:
        ids = [tid for tid in ids if tid in wanted]

    for tid in ids:
        contract = extract(tid)
        path, action = write_contract(contract, diff_only=args.diff)
        print(f"{tid:18s} {action:6s} {path.relative_to(ROOT)}")
        if args.diff:
            continue
        field_doc = ROOT / "docs" / "templates" / f"{tid}.md"
        skill = SKILLS / f"{tid}.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text(skill_markdown(contract, field_doc), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
