"""账本自检：渲染前先证明「这份账单是闭合的」，再证明「一行都没被丢掉」。

静默丢行是这类适配层最典型的翻车方式（列宽不够就少画一行、类型不认识就跳过），
而且丢完之后 PDF 看起来毫无异常。所以适配器必须能被逐条对账。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .adapters import BindingError, binding_for
from .ledger import LedgerDocument

__all__ = ["Issue", "SynthLintError", "issues", "placeholder_hits", "verify"]


@dataclass(frozen=True)
class Issue:
    """一条自检发现。

    ``advisory=True`` 的不阻断出片：真实账单里余额短期为负（透支）是正常现象，
    拦住它只会逼着人去改数字——那才是真的在造账。
    """

    code: str
    detail: str
    advisory: bool = False

    def __str__(self) -> str:
        prefix = "warning " if self.advisory else ""
        return f"{prefix}{self.code}: {self.detail}"


class SynthLintError(ValueError):
    def __init__(self, template_id: str, found: list[Issue]) -> None:
        self.template_id = template_id
        self.issues = tuple(found)
        listed = "；".join(str(item) for item in found[:6])
        super().__init__(f"{template_id} 账本自检未通过（{len(found)} 项）：{listed}")


# 占位词：真账单上永远不会出现，出现就说明这份数据是「没填完的示例」。
_PLACEHOLDER_WORDS = (
    "sample",
    "example",
    "demo",
    "dummy",
    "test data",
    "lorem ipsum",
    "john doe",
    "jane doe",
    "your name",
    "somebody",
    "nobody",
    "placeholder",
)
# 这些词单独出现不算错（"Test" 可能是商户名的一部分），所以只在整词/整字段命中时判。
_PLACEHOLDER_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in _PLACEHOLDER_WORDS) + r")\b",
    re.IGNORECASE,
)
# 全 0 的账号/路由号（`000000000001`、`000000000`）同样是没填。
_ALL_ZERO_RE = re.compile(r"0{9,}")
_ACCOUNT_KEYS = ("account", "routing", "iban", "check", "reference", "id")


def _walk_strings(value, path: str = ""):
    """把数据树里的 (路径, 字符串) 摊平，用来扫占位词。"""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_strings(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _walk_strings(item, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


def placeholder_hits(data: dict) -> list[str]:
    """扫出这份数据里的占位痕迹（路径 + 命中词），给 `issues()` 和外部调用方共用。"""
    hits: list[str] = []
    for path, text in _walk_strings(data):
        if not text.strip():
            continue
        match = _PLACEHOLDER_RE.search(text)
        if match:
            hits.append(f"{path}={text.strip()[:40]!r}（{match.group(0)}）")
            continue
        lowered = path.lower()
        if any(word in lowered for word in _ACCOUNT_KEYS) and _ALL_ZERO_RE.search(text):
            hits.append(f"{path}={text.strip()[:40]!r}（全 0 号码）")
    return hits


def issues(ledger: LedgerDocument, data: dict, template_id: str) -> list[Issue]:
    found: list[Issue] = []

    for hit in placeholder_hits(data):
        found.append(Issue("placeholder_text", f"{template_id} 里还有占位内容：{hit}"))

    expected = ledger.opening_cents + ledger.credits_cents - ledger.debits_cents
    if expected != ledger.closing_cents:
        found.append(
            Issue(
                "balance_not_closed",
                f"期初 {ledger.opening_cents} + 贷 {ledger.credits_cents} "
                f"- 借 {ledger.debits_cents} = {expected}，期末却记 {ledger.closing_cents}",
            )
        )

    for row in ledger.rows:
        if row.amount_cents <= 0:
            found.append(Issue("amount_not_positive", f"{row.date} {row.description} 金额 {row.amount_cents}"))
        if not (ledger.period.start <= row.date <= ledger.period.end):
            found.append(Issue("row_out_of_period", f"{row.date} 不在 {ledger.period.start}..{ledger.period.end}"))
        if row.balance_cents < 0:
            # 透支在真账单上会出现；记为提醒，不拦渲染。
            found.append(
                Issue("negative_balance", f"{row.date} 余额 {row.balance_cents}", advisory=True)
            )

    ordered = [row.date for row in ledger.rows]
    if ordered != sorted(ordered):
        found.append(Issue("rows_unsorted", "流水未按日期升序"))

    try:
        binding = binding_for(template_id)
    except BindingError as exc:
        found.append(Issue("no_binding", str(exc)))
        return found

    carried = binding.count(data)
    if carried != len(ledger.rows):
        found.append(
            Issue("rows_dropped", f"适配器只承载 {carried} 行，账本有 {len(ledger.rows)} 行")
        )

    # 账单上印的「合计」必须和逐行相加对得上。模板各自维护 total 字段，
    # 一旦某家把行过滤了而没重算，读者一眼就能发现（真账单从不结不上）。
    found.extend(_sum_mismatches(ledger, data, template_id))
    if not binding.accepts(ledger.account.kind):
        found.append(
            Issue(
                "account_mismatch",
                f"{template_id} 只接受 {sorted(binding.accounts)}，账本是 {ledger.account.kind}",
            )
        )
    return found



def _cents(text) -> int | None:
    """把 "$1,234.56" / "-12.34" / "" 解析成分，解析不了返回 None。"""
    raw = str(text or "").strip().replace("$", "").replace(",", "").replace("(", "-").replace(")", "")
    if not raw or raw in {"-", "."}:
        return None
    try:
        return int(round(float(raw) * 100))
    except ValueError:
        return None


def _sum_mismatches(ledger: LedgerDocument, data: dict, template_id: str) -> list[Issue]:
    """逐行合计 vs 账单上的小计行。只比能明确配对的字段。"""
    found: list[Issue] = []
    totals = data.get("totals")
    if isinstance(totals, dict):
        for key, rows_key, direction in (
            ("deposits", "deposits", "credit"),
            ("withdrawals", "withdrawals", "debit"),
            ("additions", "deposits", "credit"),
            ("subtractions", "withdrawals", "debit"),
        ):
            if key not in totals or rows_key not in data:
                continue
            rows = [r for r in data.get(rows_key) or [] if isinstance(r, dict)]
            if not rows:
                continue
            amount = _cents(totals[key])
            if amount is None:
                continue
            summed = 0
            for row in rows:
                value = _cents(row.get("amount") or row.get("value"))
                if value is not None:
                    summed += abs(value)
            if abs(summed - abs(amount)) > 1:
                found.append(
                    Issue(
                        "sum_mismatch",
                        f"{template_id} 的 {key} 小计 {totals[key]} 与逐行合计 "
                        f"{summed / 100:.2f} 对不上",
                    )
                )
            break
    return found


def verify(ledger: LedgerDocument, data: dict, template_id: str) -> list[Issue]:
    """拦下阻断项；提醒项原样返回，交给调用方决定怎么展示。"""
    found = issues(ledger, data, template_id)
    blocking = [issue for issue in found if not issue.advisory]
    if blocking:
        raise SynthLintError(template_id, blocking)
    return [issue for issue in found if issue.advisory]
