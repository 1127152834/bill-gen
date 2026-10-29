"""模板绑定表：LedgerDocument → 某个模板的数据字典。

11 个服务端模板共用同一份账本，差异全部收敛在这里的映射函数里。信用卡模板
（amex 之外）不在此列：13 个模板的版式都是存款账户对账单，信用账户数据无法
按存款账户字段表达，所以 ``accounts`` 声明得清清楚楚，越界即报错而不是硬塞。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..ledger import LedgerDocument
from . import boa, chase, simple, usbank, wellsfargo

__all__ = ["BINDINGS", "BINDING_BY_ID", "Binding", "binding_for", "count_rows"]

DEPOSIT = "checking"
BUSINESS = "business_checking"


@dataclass(frozen=True)
class Binding:
    template_id: str
    accounts: frozenset[str]
    build: Callable[[LedgerDocument, dict[str, Any]], dict[str, Any]]
    count: Callable[[dict[str, Any]], int]

    def accepts(self, account_kind: str) -> bool:
        return account_kind in self.accounts


def _b(template_id, accounts, build, count) -> Binding:
    return Binding(template_id, frozenset(accounts), build, count)


BINDINGS: tuple[Binding, ...] = (
    _b("boa-personal", {DEPOSIT}, boa.build, boa.count_rows),
    _b("boa-business", {BUSINESS}, boa.build, boa.count_rows),
    _b("wellsfargo", {DEPOSIT}, wellsfargo.build, wellsfargo.count_rows),
    _b("amex", {DEPOSIT}, simple.build_amex, simple.count_rows),
    _b("mercury", {DEPOSIT, BUSINESS}, simple.build_mercury, simple.count_rows),
    _b("capitalone", {DEPOSIT}, simple.build_capitalone, simple.count_rows),
    _b("sofi", {DEPOSIT}, simple.build_sofi, simple.count_rows),
    _b("chase-personal", {DEPOSIT}, chase.build_personal, chase.count_personal),
    _b("chase-business", {BUSINESS}, chase.build_business, chase.count_business),
    _b("usbank-personal", {DEPOSIT}, usbank.build_personal, usbank.count_rows),
    _b("usbank-business", {BUSINESS}, usbank.build_business, usbank.count_rows),
)

BINDING_BY_ID: dict[str, Binding] = {item.template_id: item for item in BINDINGS}


class BindingError(ValueError):
    """模板与账本类型不匹配，或模板没有服务端版式。"""


def binding_for(template_id: str) -> Binding:
    try:
        return BINDING_BY_ID[template_id]
    except KeyError:
        raise BindingError(f"模板没有账本映射：{template_id}") from None


def count_rows(template_id: str, data: dict[str, Any]) -> int:
    return binding_for(template_id).count(data)
