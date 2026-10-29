"""账本自检：渲染前先证明「这份账单是闭合的」，再证明「一行都没被丢掉」。

静默丢行是这类适配层最典型的翻车方式（列宽不够就少画一行、类型不认识就跳过），
而且丢完之后 PDF 看起来毫无异常。所以适配器必须能被逐条对账。
"""
from __future__ import annotations

from dataclasses import dataclass

from .adapters import BindingError, binding_for
from .ledger import LedgerDocument

__all__ = ["Issue", "SynthLintError", "issues", "verify"]


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


def issues(ledger: LedgerDocument, data: dict, template_id: str) -> list[Issue]:
    found: list[Issue] = []

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
    if not binding.accepts(ledger.account.kind):
        found.append(
            Issue(
                "account_mismatch",
                f"{template_id} 只接受 {sorted(binding.accounts)}，账本是 {ledger.account.kind}",
            )
        )
    return found


def verify(ledger: LedgerDocument, data: dict, template_id: str) -> list[Issue]:
    """拦下阻断项；提醒项原样返回，交给调用方决定怎么展示。"""
    found = issues(ledger, data, template_id)
    blocking = [issue for issue in found if not issue.advisory]
    if blocking:
        raise SynthLintError(template_id, blocking)
    return [issue for issue in found if issue.advisory]
