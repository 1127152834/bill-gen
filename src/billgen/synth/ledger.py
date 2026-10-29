"""中间规范 :class:`LedgerDocument`：一个账户、一个账期、一条自洽的余额链。

上游（场景生成器）给的是「多账户 × 多账期」的结果包；这里只把它切成**一期一份**的
文档，并把所有能由账本自己算出来的东西算准：

* 余额链由期初 + 逐笔累加重建，不信任上游给的 runningBalance（跨期时它只对第一期成立）；
* 日终余额与全期日历对齐（非交易日结转），日均余额由它算出；
* 账号/路由号由种子稳定派生，同种子重跑结果一致。
"""
from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any

from .money import aba_routing, account_number, digits

__all__ = [
    "AccountRef",
    "BankIdentity",
    "CreditTerms",
    "Holder",
    "LedgerDocument",
    "LedgerError",
    "LedgerRow",
    "Period",
    "from_game_result",
]


class LedgerError(ValueError):
    """上游结果包与账本假设不符（缺账户、期次越界、金额符号错……）。"""


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:  # pragma: no cover - 上游 schema 已保证格式
        raise LedgerError(f"不是合法日期：{value!r}") from exc


def _slug(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in text]
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "sample-bank"


def _fiction_phone(seed: str) -> str:
    """555-01xx 是北美保留的虚构号段，看着正常但保证不存在。"""
    n = int(hashlib.sha256(f"phone:{seed}".encode()).hexdigest()[:4], 16) % 100
    return f"(555) 010-{n:04d}"


def _fiction_toll_free(seed: str, tag: str) -> str:
    """1-800-555-01xx：555-0100..0199 是北美保留虚构号段，形制正常但打不通。"""
    n = int(hashlib.sha256(f"{tag}:{seed}".encode()).hexdigest()[:4], 16) % 100
    return f"1-800-555-{n:04d}"


# 行址只取中立城市（不是任何真银行的实际地址）
_HQ_CITIES = (
    ("Columbus, OH", "43219", "614"),
    ("Wilmington, DE", "19801", "302"),
    ("Minneapolis, MN", "55402", "612"),
    ("Charlotte, NC", "28202", "704"),
    ("Salt Lake City, UT", "84111", "801"),
)


@dataclass(frozen=True, slots=True)
class BankIdentity:
    name: str
    legal: str
    domain: str
    phone: str
    po_box: str = ""
    po_box_alt: str = ""
    city_state_zip: str = ""
    phone_service: str = ""
    phone_es: str = ""
    phone_intl: str = ""
    phone_metro: str = ""

    @classmethod
    def derive(cls, name: str, seed: str) -> BankIdentity:
        legal = name if name.endswith((".", "N.A.", "Bank")) and "," in name else f"{name}, N.A."
        digest = int(hashlib.sha256(f"hq:{seed}".encode()).hexdigest()[:8], 16)
        city, zip5, area = _HQ_CITIES[digest % len(_HQ_CITIES)]
        return cls(
            name=name,
            legal=legal,
            domain=f"{_slug(name)}.example",
            phone=_fiction_phone(seed),
            po_box=f"P O Box {40000 + digest % 20000}",
            po_box_alt=f"P O Box {40000 + (digest // 7) % 20000}",
            city_state_zip=f"{city} {zip5}",
            phone_service=_fiction_toll_free(seed, "service"),
            phone_es=_fiction_toll_free(seed, "spanish"),
            phone_intl=_fiction_toll_free(seed, "intl"),
            phone_metro=f"{area}-555-01{int(hashlib.sha256(f'metro:{seed}'.encode()).hexdigest()[:2], 16) % 100:02d}",
        )


@dataclass(frozen=True, slots=True)
class Holder:
    name: str
    address_lines: tuple[str, ...]
    is_business: bool = False

    @property
    def city_state_zip(self) -> str:
        return self.address_lines[-1] if self.address_lines else ""

    @property
    def zip5(self) -> str:
        """末行末尾的五位邮编（模板常把邮编单独放进一列）。"""
        tail = self.city_state_zip.split()
        return tail[-1][:5] if tail and tail[-1][:5].isdigit() else ""

    @property
    def city_state(self) -> str:
        """``Austin TX``：去掉邮编后的城市行。"""
        parts = self.city_state_zip.split()
        if parts and self.zip5 and parts[-1][:5] == self.zip5:
            parts = parts[:-1]
        return " ".join(parts)

    @property
    def street_lines(self) -> tuple[str, ...]:
        """不含姓名的地址行（收件人区块单独给姓名的模板用）。"""
        return self.address_lines[1:] if len(self.address_lines) > 1 else self.address_lines


@dataclass(frozen=True, slots=True)
class AccountRef:
    kind: str            # checking | business_checking | credit
    last4: str
    number: str
    routing: str
    product: str

    @property
    def is_credit(self) -> bool:
        return self.kind == "credit"

    @property
    def display(self) -> str:
        return self.number


@dataclass(frozen=True, slots=True)
class Period:
    start: date
    end: date

    @property
    def label_long(self) -> str:
        from .money import fmt_long

        return f"{fmt_long(self.start)} to {fmt_long(self.end)}"

    @property
    def label_through(self) -> str:
        from .money import fmt_long

        return f"{fmt_long(self.start)} through {fmt_long(self.end)}"

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def days_iter(self) -> Iterable[date]:
        for offset in range(self.days):
            yield self.start + timedelta(days=offset)


@dataclass(frozen=True, slots=True)
class LedgerRow:
    date: dt.date
    posted: dt.date
    description: str
    memo: str
    type: str
    direction: str          # credit | debit
    amount_cents: int       # 恒正
    balance_cents: int      # 该笔入账后的余额（已重建）
    merchant: dict[str, Any] | None = None
    check_no: str | None = None
    reference: str = ""                                  # 该行对外可见的号（trace/conf/id）

    @property
    def signed_cents(self) -> int:
        return self.amount_cents if self.direction == "credit" else -self.amount_cents


@dataclass(frozen=True, slots=True)
class CreditTerms:
    limit_cents: int
    apr: float
    statement_cents: int
    minimum_due_cents: int
    due_date: date
    interest_cents: int


@dataclass(frozen=True, slots=True)
class LedgerDocument:
    bank: BankIdentity
    holder: Holder
    account: AccountRef
    period: Period
    opening_cents: int
    closing_cents: int
    rows: tuple[LedgerRow, ...]
    seed: str
    credit: CreditTerms | None = None
    watermark: bool = True
    extras: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------ 汇总

    @property
    def credits_cents(self) -> int:
        return sum(r.amount_cents for r in self.rows if r.direction == "credit")

    @property
    def debits_cents(self) -> int:
        return sum(r.amount_cents for r in self.rows if r.direction == "debit")

    def rows_of(self, *types: str) -> tuple[LedgerRow, ...]:
        wanted = set(types)
        return tuple(r for r in self.rows if r.type in wanted)

    @property
    def checks(self) -> tuple[LedgerRow, ...]:
        return self.rows_of("CHECK")

    @property
    def fees(self) -> tuple[LedgerRow, ...]:
        return self.rows_of("FEE")

    @property
    def atm(self) -> tuple[LedgerRow, ...]:
        return self.rows_of("ATM")

    def daily_activity(self) -> list[tuple[date, int]]:
        """有交易的日子及其日终余额（模板的 daily 表就吃这个）。"""
        out: list[tuple[date, int]] = []
        for row in self.rows:
            if out and out[-1][0] == row.date:
                out[-1] = (row.date, row.balance_cents)
            else:
                out.append((row.date, row.balance_cents))
        return out

    def daily_full(self) -> list[tuple[date, int]]:
        """全期每日余额，非交易日结转（算日均用）。"""
        by_day: dict[date, int] = {}
        for row in self.rows:
            by_day[row.date] = row.balance_cents
        out: list[tuple[date, int]] = []
        balance = self.opening_cents
        for day in self.period.days_iter():
            balance = by_day.get(day, balance)
            out.append((day, balance))
        return out

    def average_daily_cents(self) -> int:
        days = self.daily_full()
        return round(sum(b for _, b in days) / len(days)) if days else self.opening_cents

    def lowest_daily_cents(self) -> int:
        return min((b for _, b in self.daily_full()), default=self.opening_cents)


# ---------------------------------------------------------------- 构建

def _account_kind(statement: dict[str, Any]) -> str:
    return str(statement.get("accountType", "checking"))


def _holder(payload: dict[str, Any]) -> Holder:
    subject = payload.get("subject") or {}
    if "legalName" in subject:
        addr = subject.get("address") or {}
        lines = [subject["legalName"], addr.get("street", "")]
        city = " ".join(part for part in (addr.get("city"), addr.get("state"), addr.get("zip")) if part)
        lines.append(city)
        return Holder(subject["legalName"], tuple(x for x in lines if x), True)
    addr = subject.get("address") or {}
    city = " ".join(part for part in (addr.get("city"), addr.get("state"), addr.get("zip")) if part)
    lines = [subject.get("name", ""), addr.get("street", ""), city]
    return Holder(subject.get("name", ""), tuple(x for x in lines if x))


def _product(kind: str) -> str:
    return {
        "checking": "Everyday Checking",
        "business_checking": "Business Checking",
        "credit": "Everyday Credit Card",
    }.get(kind, "Checking")


def _credit_terms(statement: dict[str, Any], month: dict[str, Any]) -> CreditTerms | None:
    credit = statement.get("credit")
    cycle = month.get("credit")
    if not credit or not cycle:
        return None
    return CreditTerms(
        limit_cents=int(credit.get("creditLimitCents", 0)),
        apr=float(credit.get("apr", 0.0)),
        statement_cents=int(cycle.get("statementBalanceCents", 0)),
        minimum_due_cents=int(cycle.get("minimumDueCents", 0)),
        due_date=_parse_date(cycle["dueDate"]),
        interest_cents=int(cycle.get("interestChargedCents", 0)),
    )


def _assign_check_numbers(rows: list[LedgerRow], seed: str) -> list[LedgerRow]:
    start = 1000 + int(hashlib.sha256(f"check-start:{seed}".encode()).hexdigest()[:3], 16) % 8000
    out: list[LedgerRow] = []
    number = start
    for row in rows:
        if row.type == "CHECK":
            out.append(replace(row, check_no=str(number)))
            number += 1
        else:
            out.append(row)
    return out


def _attach_references(rows: list[LedgerRow], seed: str,
                       *, routing: str, last4: str) -> list[LedgerRow]:
    """给每行配一个「银行侧形态」的号，供适配器摆进各自的槽位。

    ACH 走 15 位 trace（前 8 位路由号），Zelle 走 9 位确认号，卡消费走 6 位授权号，
    支票直接沿用票号，其余（利息/手续费/转账）用 10~14 位内部号。
    """
    from . import refs

    out: list[LedgerRow] = []
    for row in rows:
        key = f"{seed}:{row.date.isoformat()}:{row.amount_cents}:{row.description}"
        if row.type == "CHECK" and row.check_no:
            ref = row.check_no
        elif row.type == "ACH":
            ref = refs.ach_trace(routing, key)
        elif row.type == "ZELLE":
            ref = refs.zelle_conf(key)
        elif row.type == "POS":
            ref = f"{last4}:{refs.card_auth(key)}"
        elif row.type in {"FEE", "INTEREST"}:
            ref = digits(f"fee:{key}", 10)
        else:
            ref = refs.internal_id(key)
        out.append(replace(row, reference=ref))
    return out


def from_game_result(
    payload: dict[str, Any],
    *,
    account: str | None = None,
    cycle: int = -1,
    bank_name: str | None = None,
    watermark: bool = True,
    realism: bool = True,
) -> LedgerDocument:
    """把一个生成结果切成「一个账户 × 一个账期」的账本文档。

    :param account: ``checking`` / ``business_checking`` / ``credit``；缺省取第一个账户。
    :param cycle: 账期下标，``-1`` 表示最近一期。
    :param realism: 是否做「入账日 / 金额尾数 / 收单方前缀」的真实化处理。
    """
    statements = payload.get("statements") or []
    if not statements:
        raise LedgerError("结果包里没有 statements")
    statement = next((s for s in statements if _account_kind(s) == account), None) if account else statements[0]
    if statement is None:
        raise LedgerError(f"结果包里没有账户 {account!r}，现有：{[_account_kind(s) for s in statements]}")

    months = statement.get("months") or []
    if not months:
        raise LedgerError("账户没有账期汇总")
    try:
        month = months[cycle]
    except IndexError as exc:
        raise LedgerError(f"账期下标 {cycle} 越界，共 {len(months)} 期") from exc

    period = Period(_parse_date(month["cycleStart"]), _parse_date(month["cycleEnd"]))
    opening = int(month["openingBalanceCents"])
    closing = int(month["closingBalanceCents"])

    selected = [
        t
        for t in statement.get("transactions", [])
        if period.start <= _parse_date(t["transactionDate"]) <= period.end
    ]
    selected.sort(key=lambda t: (t["transactionDate"], t.get("postedDate", ""), t.get("id", "")))

    kind = _account_kind(statement)
    seed = str(payload.get("seed") or payload.get("id") or "seed")
    last4 = str(statement.get("accountLast4", "0000"))
    routing = aba_routing(seed)

    rows: list[LedgerRow] = []
    balance = opening
    for txn in selected:
        direction = "credit" if txn.get("direction") == "credit" else "debit"
        amount = int(txn["amountCents"])
        balance += amount if direction == "credit" else -amount
        rows.append(
            LedgerRow(
                date=_parse_date(txn["transactionDate"]),
                posted=_parse_date(txn.get("postedDate") or txn["transactionDate"]),
                description=str(txn.get("description", "")),
                memo=str(txn.get("memo", "")),
                type=str(txn.get("type", "POS")),
                direction=direction,
                amount_cents=amount,
                balance_cents=balance,
                merchant=txn.get("merchant"),
            )
        )
    rows = _assign_check_numbers(rows, seed)
    rows = _attach_references(rows, seed, routing=routing, last4=last4)

    name = bank_name or str(statement.get("bankName") or "Sample Bank")
    ledger = LedgerDocument(
        bank=BankIdentity.derive(name, seed),
        holder=_holder(payload),
        account=AccountRef(
            kind=kind,
            last4=last4,
            number=account_number(seed, last4),
            routing=routing,
            product=_product(kind),
        ),
        period=period,
        opening_cents=opening,
        closing_cents=closing,
        rows=tuple(rows),
        seed=seed,
        credit=_credit_terms(statement, month),
        watermark=watermark,
        extras={
            "cycle_index": months.index(month),
            "cycle_count": len(months),
            "statement_date": period.end,
            "generated_at": payload.get("createdAt"),
            "subject_type": payload.get("subjectType") or ("business" if "legalName" in (payload.get("subject") or {}) else "personal"),
        },
    )
    if realism:
        from . import realism as _realism

        ledger, _stats = _realism.apply_all(ledger)
    return ledger
