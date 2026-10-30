"""把一段账期拆成「每月一份模板 JSON」，并给出三层校验结论。

交付物不再是 PDF，而是**直接能导进模板的 JSON**：一个月一个文件，字段就是
`binding.build()` 出来的那套（每家银行不一样），所以拆月必须在账本层做——
把行按自然月分桶、重算每个月自己的期初期末，再让各模板的适配器照常出数据。
这样每份 JSON 都走的是同一条已经验证过的适配链路，不需要为「拆月」再写一遍各家规则。

三层校验：

1. **结构**（`structure`）：键是否齐、类型是否对、日期/金额能不能解析、数组元素结构是否一致；
2. **单月内容**（`content`）：账期是否包住所有行、期初 + 收支 = 期末、行数没被丢、小计与逐行相加一致、
   没有占位词（复用 `lint.issues`，它本来就是为这件事写的）；
3. **跨月连续**（`continuity`）：上个月期末 = 下个月期初、月份不重不漏、文件月份与账期同月。

`ok` 只在三层全过时为真——「JSON 能解析」不算通过。
"""
from __future__ import annotations

import calendar
import dataclasses
import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .. import registry
from .bridge import build_data
from .ledger import LedgerDocument, Period
from .lint import Issue, placeholder_hits

__all__ = [
    "MonthArtifact",
    "month_artifacts",
    "month_keys",
    "split_ledger",
    "validate_artifact",
    "validate_batch",
]

_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y", "%Y-%m-%d")
_DATE_NO_YEAR = ("%m/%d", "%b %d", "%B %d")
# 只认「一个数」的写法：`1,234.56`、`-1650.00`、`$12.30`。
# 卡号/磁码那种「3 060411196 08」有多组数字，不能当金额，否则会把编号判成坏数据。
_MONEY_SHAPE = re.compile(r"^[+-]?\$?\d[\d,]*\.\d{2}[+-]?$")


# --------------------------------------------------------------------------- 拆月
def month_keys(ledger: LedgerDocument) -> list[str]:
    """账本里出现过的自然月，形如 `2026-07`，按时间升序。"""
    seen: list[str] = []
    for row in ledger.rows:
        key = f"{row.date.year:04d}-{row.date.month:02d}"
        if key not in seen:
            seen.append(key)
    return seen


def _month_bounds(key: str) -> tuple[date, date]:
    year, month = (int(part) for part in key.split("-"))
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def split_ledger(ledger: LedgerDocument, months: list[str] | None = None) -> list[tuple[str, LedgerDocument]]:
    """按自然月把账本切成若干子账本，期初期末首尾相接。

    `months` 可以指定要哪几个月（例如只要 7—9 月）；不传就取账本里出现过的月份。
    空月份（那一个月没有流水）不产出文件——没有交易就没有账单，硬造一份等于编数据。
    """
    wanted = set(months) if months else None
    out: list[tuple[str, LedgerDocument]] = []
    opening = ledger.opening_cents
    # 始终从头扫一遍余额：只要 9 月时，9 月的期初必须是 8 月末的余额，
    # 不能把「跳过的那几个月」的收支凭空抹掉。
    for key in month_keys(ledger):
        rows = tuple(row for row in ledger.rows if f"{row.date.year:04d}-{row.date.month:02d}" == key)
        if not rows:
            continue
        start, end = _month_bounds(key)
        period = Period(start=max(start, min(r.date for r in rows)),
                        end=min(end, max(r.date for r in rows)))
        closing = opening + sum(r.signed_cents for r in rows)
        if wanted is None or key in wanted:
            out.append(
                (
                    key,
                    dataclasses.replace(
                        ledger,
                        period=period,
                        rows=rows,
                        opening_cents=opening,
                        closing_cents=closing,
                    ),
                )
            )
        opening = closing
    return out


@dataclass
class MonthArtifact:
    """一个月的一份交付件：JSON 数据 + 三层校验结论。"""

    month: str
    template: str
    data: dict[str, Any]
    opening_cents: int
    closing_cents: int
    rows: int
    structure: list[dict[str, str]] = field(default_factory=list)
    content: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.structure and not self.content

    def report(self) -> dict[str, Any]:
        return {
            "month": self.month,
            "template": self.template,
            "rows": self.rows,
            "opening": self.opening_cents / 100,
            "closing": self.closing_cents / 100,
            "ok": self.ok,
            "structure": list(self.structure),
            "content": list(self.content),
            "warnings": list(self.warnings),
        }


def month_artifacts(
    template_id: str, ledger: LedgerDocument, months: list[str] | None = None
) -> list[MonthArtifact]:
    """按模板逐月出 JSON 并逐月校验；单月出问题不影响其他月份出件。"""
    artifacts: list[MonthArtifact] = []
    for key, sub in split_ledger(ledger, months):
        artifact = MonthArtifact(
            month=key,
            template=template_id,
            data={},
            opening_cents=sub.opening_cents,
            closing_cents=sub.closing_cents,
            rows=len(sub.rows),
        )
        try:
            artifact.data = build_data(template_id, sub)
        except Exception as err:            # noqa: BLE001 适配器/自检的报错都要落到报告里
            artifact.content.append({"rule": "build_failed", "path": "", "detail": f"{type(err).__name__}: {err}"})
            artifacts.append(artifact)
            continue
        validate_artifact(artifact, sub)
        artifacts.append(artifact)
    return artifacts


# ------------------------------------------------------------------- 结构校验
def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def parse_money(value: Any) -> int | None:
    """`"1,234.56"` / `-1650.0` / `"$12.30"` → 分；解析不了返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(float(value) * 100))
    raw = str(value or "").strip().replace("$", "").replace(",", "").replace("(", "-").replace(")", "")
    if not raw or raw in {"-", ".", "—"}:
        return None
    try:
        return int(round(float(raw) * 100))
    except ValueError:
        return None


def parse_date(value: Any) -> date | None:
    """认得各家模板的日期写法；只有「月/日」没有年份时返回 None（无法判断年份，不算错）。"""
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            from datetime import datetime

            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _date_shape_ok(value: Any) -> bool:
    """没有年份的 `8/25`、`Aug 25` 也要能通过形状检查。"""
    text = str(value or "").strip()
    if not text:
        return False
    if parse_date(text):
        return True
    from datetime import datetime

    for fmt in _DATE_NO_YEAR:
        try:
            datetime.strptime(text, fmt)
            return True
        except ValueError:
            continue
    return False


def _structure_issues(template_id: str, data: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """对着该模板自己的默认数据比结构与类型；返回 (错误, 提醒)。"""
    defaults = registry.get_renderer(template_id).default_data()
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    for key, sample in defaults.items():
        path = key
        if key not in data:
            # 有的键是模板选填（默认值为空列表/空串），缺了不影响导入
            if sample in ("", None, [], {}):
                warnings.append({"rule": "missing_optional", "path": path, "detail": f"{path} 未提供（默认值为空）"})
            else:
                errors.append({"rule": "missing_key", "path": path, "detail": f"缺少必填字段 {path}"})
            continue
        value = data[key]
        want, got = _json_type(sample), _json_type(value)
        if sample is None:
            continue
        if want == "number" and got == "string" and parse_money(value) is not None:
            continue
        if want != got:
            errors.append({"rule": "type_mismatch", "path": path, "detail": f"{path} 应为 {want}，实际 {got}"})
            continue
        if isinstance(sample, list) and sample and isinstance(sample[0], dict):
            want_keys = set(sample[0])
            for index, row in enumerate(value):
                if not isinstance(row, dict):
                    errors.append({"rule": "type_mismatch", "path": f"{path}[{index}]", "detail": "数组元素应为对象"})
                    continue
                missing = want_keys - set(row)
                if missing:
                    # 模板默认行里有、适配器没给的字段，多半是模板用 `.get()` 取的选填项
                    # （例如 mercury 默认行的 `txid`，实际数据用 `id`）；只提醒，不当错误。
                    warnings.append(
                        {"rule": "row_field_absent", "path": f"{path}[{index}]", "detail": f"默认行有而此处没有 {sorted(missing)}"}
                    )
                for field_name in sorted(want_keys & set(row)):
                    sample_value = sample[0][field_name]
                    want_t, got_t = _json_type(sample_value), _json_type(row[field_name])
                    if sample_value is None or want_t == got_t:
                        continue
                    if want_t == "number" and got_t == "string" and parse_money(row[field_name]) is not None:
                        continue
                    errors.append(
                        {
                            "rule": "row_type_mismatch",
                            "path": f"{path}[{index}].{field_name}",
                            "detail": f"{field_name} 应为 {want_t}，实际 {got_t}（{row[field_name]!r}）",
                        }
                    )
                extra = set(row) - want_keys
                if extra:
                    warnings.append(
                        {"rule": "unknown_row_field", "path": f"{path}[{index}]", "detail": f"模板默认行里没有的字段 {sorted(extra)}"}
                    )
            if value:
                shared = set.intersection(*(set(row) for row in value if isinstance(row, dict))) if value else set()
                for index, row in enumerate(value):
                    if not isinstance(row, dict):
                        continue
                    ragged = set(row) - shared
                    if ragged:
                        warnings.append(
                            {
                                "rule": "row_field_inconsistent",
                                "path": f"{path}[{index}]",
                                "detail": f"同一数组里这行多出/缺少 {sorted(ragged)}，字段要一致才能导入",
                            }
                        )
    return errors, warnings


def _leaf_kind(value: Any) -> str | None:
    """从一个默认值字符串反推它是什么：金额 / 日期 / 普通文本。

    不用字段名猜（`micr.cycle` 里也有 `cycle`、`days: "15"` 也像金额），
    直接拿模板自己的默认值当标尺——默认值长什么样，交付的 JSON 就必须长什么样。
    """
    if not isinstance(value, str) or not value.strip():
        return None
    # 金额必须带两位小数：`1,500.00` 是金额，`1`（micr.cycle 的期数）、`0000`
    # （on-us 段）、`3 020 6041 1196`（分组账号）都不是，不能按金额去要求。
    if _MONEY_SHAPE.match(value.strip()) and parse_money(value) is not None:
        return "money"
    if _date_shape_ok(value):
        return "date"
    return None


def _value_format_issues(
    sample: Any, value: Any, path: str = ""
) -> list[dict[str, str]]:
    """按默认值的形状检查金额/日期能不能解析；解析不了模板导入后画不出来。

    嵌套数组要**按列**对齐：`daily = [["08/15", "10,713.56"], …]` 里第 0 列是日期、
    第 1 列是金额；直接拿 `sample[0]` 去比第 1 列就会把金额当日期报错。
    """
    found: list[dict[str, str]] = []
    if isinstance(sample, dict) and isinstance(value, dict):
        for key, item in value.items():
            found.extend(_value_format_issues(sample.get(key), item, f"{path}.{key}" if path else str(key)))
        return found
    if isinstance(sample, list) and isinstance(value, list):
        if not sample:
            return found
        for index, item in enumerate(value):
            if isinstance(sample[0], dict):
                probe = sample[0]                              # 行对象：拿默认首行当样板
            else:
                # 数组（含 `[[日期, 金额], …]` 这种二维表）逐元素对齐，
                # 元素不够就借最后一条：`daily[i][1]` 必须是金额，`daily[i][0]` 必须是日期。
                probe = sample[index] if index < len(sample) else sample[-1]
            found.extend(_value_format_issues(probe, item, f"{path}[{index}]"))
        return found
    kind = _leaf_kind(sample)
    text = value.strip() if isinstance(value, str) else ""
    if kind == "money" and text and parse_money(value) is None:
        found.append({"rule": "money_unparseable", "path": path, "detail": f"{value!r} 解析不出金额"})
    elif kind == "date" and text and not _date_shape_ok(value):
        found.append({"rule": "date_unparseable", "path": path, "detail": f"{value!r} 不是可识别的日期"})
    return found


# --------------------------------------------------- 交付件自身的算术（不信 lint）
_ROW_MONEY_FIELDS = ("amount", "withdrawal", "deposit", "credit", "debit")
# 分栏式模板（boa/usbank/chase-business）把「支出」单独放一个数组，数组里的 `amount`
# 也是正数；所以符号要看**数组名**，不能只看正负。
_OUTFLOW_KEYS = ("withdraw", "fee", "check", "debit", "atm", "payment", "charge", "electronic")
_INFLOW_KEYS = ("deposit", "credit", "interest")


def json_rows(data: dict[str, Any]) -> list[tuple[str, int]]:
    """从交付 JSON 里认出「流水行」，返回 [(所在数组, 带符号分)]。

    不按模板逐家写死字段，而是看**结构**：数组里每个元素都是对象、且带
    `amount` / `withdrawal` / `deposit` 之类金额字段，就是流水。
    `withdrawal`/`debit` 记支出（减），`deposit`/`credit` 记收入（加），`amount` 按正负。
    """
    rows: list[tuple[str, int]] = []
    for key, value in data.items():
        if not isinstance(value, list) or not value or not all(isinstance(item, dict) for item in value):
            continue
        if not any(field in value[0] for field in _ROW_MONEY_FIELDS):
            continue
        lowered = key.lower()
        outflow = any(hint in lowered for hint in _OUTFLOW_KEYS) and not any(
            hint in lowered for hint in _INFLOW_KEYS
        )
        for item in value:
            if "amount" in item:
                cents = parse_money(item.get("amount"))
                if cents is not None:
                    # 已带负号就照负号；正数就看所在数组是收入还是支出
                    if cents > 0 and outflow:
                        cents = -cents
                    rows.append((key, cents))
                continue
            debit = parse_money(item.get("withdrawal", item.get("debit")))
            credit = parse_money(item.get("deposit", item.get("credit")))
            if debit is not None:
                rows.append((key, -abs(debit)))
            if credit is not None:
                rows.append((key, abs(credit)))
    return rows


def json_total_issues(artifact: MonthArtifact) -> list[dict[str, str]]:
    """交付件自己的加减法：流水合计必须等于期末 − 期初，行数必须等于账本行数。

    这条不依赖 `lint`（lint 校验的是「账本 vs 模板数据」的对应关系，
    一旦有人手改 JSON，只有这里的自相加会立刻不平）。
    """
    found: list[dict[str, str]] = []
    if not artifact.data:
        return found
    rows = json_rows(artifact.data)
    if not rows:
        found.append({"rule": "no_transactions_found", "path": "", "detail": "交付件里找不到流水行，模板导入后会是空账单"})
        return found
    total = sum(cents for _, cents in rows)
    expected = artifact.closing_cents - artifact.opening_cents
    if total != expected:
        found.append(
            {
                "rule": "json_total_mismatch",
                "path": "",
                "detail": f"流水合计 {total / 100:,.2f} ≠ 期末−期初 {expected / 100:,.2f}（差 {(total - expected) / 100:,.2f}）",
            }
        )
    if len(rows) != artifact.rows:
        found.append(
            {"rule": "json_row_count_mismatch", "path": "", "detail": f"交付 {len(rows)} 行，账本 {artifact.rows} 行"}
        )
    return found


# ------------------------------------------------------------------- 内容校验
def validate_artifact(artifact: MonthArtifact, ledger: LedgerDocument) -> None:
    """给一份已出好的 JSON 补上结构与内容两层结论（原地写进 artifact）。"""
    if not artifact.data:
        return
    defaults = registry.get_renderer(artifact.template).default_data()
    structure, warnings = _structure_issues(artifact.template, artifact.data)
    structure.extend(_value_format_issues(defaults, artifact.data))
    artifact.structure.extend(structure)
    artifact.warnings.extend(warnings)

    for issue in _content_issues(artifact, ledger):
        artifact.content.append({"rule": issue.code, "path": "", "detail": issue.detail})
    artifact.content.extend(json_total_issues(artifact))


def _content_issues(artifact: MonthArtifact, ledger: LedgerDocument) -> list[Issue]:
    from .lint import issues as lint_issues

    found: list[Issue] = []
    key_start, key_end = _month_bounds(artifact.month)
    if not (key_start <= ledger.period.start <= ledger.period.end <= key_end):
        found.append(Issue("period_out_of_month", f"账期 {ledger.period.start}..{ledger.period.end} 不在 {artifact.month} 内"))
    expected = ledger.opening_cents + ledger.credits_cents - ledger.debits_cents
    if expected != ledger.closing_cents:
        found.append(Issue("balance_not_closed", f"期初 + 收支 = {expected}，期末记 {ledger.closing_cents}"))
    if not ledger.rows:
        found.append(Issue("empty_month", "这个月没有任何流水"))
    for hit in placeholder_hits(artifact.data):
        found.append(Issue("placeholder_text", f"还有占位内容：{hit}"))
    try:
        found.extend(lint_issues(ledger, artifact.data, artifact.template))
    except Exception as err:               # noqa: BLE001
        found.append(Issue("lint_failed", f"{type(err).__name__}: {err}"))
    # 去重：同一个问题只报一条
    seen: set[tuple[str, str]] = set()
    unique: list[Issue] = []
    for issue in found:
        key = (issue.code, issue.detail)
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique


# ------------------------------------------------------------------- 跨月校验
def validate_batch(artifacts: list[MonthArtifact]) -> dict[str, Any]:
    """整批结论：文件数量、月份连续性、上期末=下期初。"""
    problems: list[dict[str, str]] = []
    ordered = sorted(artifacts, key=lambda item: item.month)
    if not ordered:
        problems.append({"rule": "no_months", "detail": "一份都没出：这个账期里没有可交付的月份"})
    for previous, current in zip(ordered, ordered[1:]):
        if previous.template != current.template:
            problems.append({"rule": "template_mix", "detail": f"{previous.month} 与 {current.month} 模板不一致"})
        if previous.closing_cents != current.opening_cents:
            problems.append(
                {
                    "rule": "balance_gap",
                    "detail": f"{previous.month} 期末 {previous.closing_cents / 100:.2f} ≠ "
                    f"{current.month} 期初 {current.opening_cents / 100:.2f}",
                }
            )
        gap_year, gap_month = (int(part) for part in previous.month.split("-"))
        expected = (gap_year + 1, 1) if gap_month == 12 else (gap_year, gap_month + 1)
        if current.month != f"{expected[0]:04d}-{expected[1]:02d}":
            problems.append({"rule": "month_gap", "detail": f"{previous.month} 之后应是 {expected[0]}-{expected[1]:02d}，实际 {current.month}"})
    failed = [item.month for item in ordered if not item.ok]
    return {
        "ok": not problems and not failed,
        "months": [item.month for item in ordered],
        "failed": failed,
        "problems": problems,
    }


def dumps(artifact: MonthArtifact) -> str:
    """导出的 JSON 文本：`ensure_ascii=False`、缩进两格，方便人核对与 diff。"""
    return json.dumps(artifact.data, ensure_ascii=False, indent=2)
