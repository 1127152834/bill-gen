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
    "real_traits",
    "refresh_references",
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


# 已知真实银行的联系信息。整合版模板印的是真行名，账本这边也得跟上：
# 命中这张表就用真实法务名/域名/客服线/信箱，不再拼 `{slug}.com` + 555 号段。
_REAL_TRAITS: dict[str, dict[str, str]] = {
    "bank of america": {
        "legal": "Bank of America, N.A.", "domain": "bankofamerica.com",
        "phone": "1-800-432-1000", "po_box": "P O Box 25118",
        "po_box_alt": "P O Box 25118", "city_state_zip": "Tampa, FL 33622-5118",
        "phone_service": "1-800-432-1000", "phone_es": "1-800-688-6086",
        "phone_intl": "1-302-683-8950", "phone_metro": "1-800-432-1000",
    },
    "wells fargo bank": {
        "legal": "Wells Fargo Bank, N.A.", "domain": "wellsfargo.com",
        "phone": "1-800-869-3557", "po_box": "P O Box 10335",
        "po_box_alt": "P O Box 10335", "city_state_zip": "Des Moines, IA 50306-0335",
        "phone_service": "1-800-869-3557", "phone_es": "1-800-642-4720",
        "phone_intl": "1-925-875-7745", "phone_metro": "1-800-869-3557",
    },
    "jpmorgan chase bank": {
        "legal": "JPMorgan Chase Bank, N.A.", "domain": "chase.com",
        "phone": "1-800-935-9935", "po_box": "P O Box 659805",
        "po_box_alt": "P O Box 659805", "city_state_zip": "San Antonio, TX 78265-9805",
        "phone_service": "1-800-935-9935", "phone_es": "1-888-609-7805",
        "phone_intl": "1-512-895-5962", "phone_metro": "1-800-935-9935",
    },
    "u.s. bank national association": {
        "legal": "U.S. Bank National Association", "domain": "usbank.com",
        "phone": "1-800-872-2657", "po_box": "P O Box 3528",
        "po_box_alt": "P O Box 3528", "city_state_zip": "Oshkosh, WI 54903-3528",
        "phone_service": "1-800-872-2657", "phone_es": "1-800-685-0575",
        "phone_intl": "1-503-401-9991", "phone_metro": "1-800-872-2657",
    },
    "capital one": {
        "legal": "Capital One, N.A.", "domain": "capitalone.com",
        "phone": "1-800-227-4825", "po_box": "P O Box 60599",
        "po_box_alt": "P O Box 60599", "city_state_zip": "City of Industry, CA 91716-0599",
        "phone_service": "1-800-227-4825", "phone_es": "1-800-442-7922",
        "phone_intl": "1-804-934-2001", "phone_metro": "1-800-227-4825",
    },
    # 参考件 `SoFi-Statement-SAMPLE.pdf` 第 1 页 Contact Information 印的就是这个
    # 街道地址（不是信箱），客服线带字母号 `(855) 456-SOFI (7634)`。
    "sofi bank": {
        "legal": "SoFi Bank, N.A.", "domain": "sofi.com",
        "phone": "1-855-456-7634", "po_box": "2750 East Cottonwood Parkway #300",
        "po_box_alt": "P O Box 4197", "city_state_zip": "Cottonwood Heights, Utah 84121",
        # 法务段落里的客服线是数字号；联系方式块的字母号由主题默认数据提供
        "phone_service": "1-855-456-7634", "phone_es": "(855) 456-SOFI (7634)",
        "phone_intl": "1-855-456-7634", "phone_metro": "1-855-456-7634",
    },
    "mercury": {
        "legal": "Mercury Technologies, Inc.", "domain": "mercury.com",
        "phone": "1-415-767-0250", "po_box": "P O Box 77045",
        "po_box_alt": "P O Box 77045", "city_state_zip": "San Francisco, CA 94107",
        "phone_service": "1-415-767-0250", "phone_es": "1-415-767-0250",
        "phone_intl": "1-415-767-0250", "phone_metro": "1-415-767-0250",
    },
    "alliant credit union": {
        "legal": "Alliant Credit Union", "domain": "alliantcu.org",
        "phone": "1-800-328-1935", "po_box": "P O Box 66999",
        "po_box_alt": "P O Box 66999", "city_state_zip": "Chicago, IL 60666-0999",
        "phone_service": "1-800-328-1935", "phone_es": "1-800-328-1935",
        "phone_intl": "1-773-462-2000", "phone_metro": "1-800-328-1935",
    },
    "ally bank": {
        "legal": "Ally Bank", "domain": "ally.com",
        "phone": "1-877-247-2559", "po_box": "P O Box 951",
        "po_box_alt": "P O Box 951", "city_state_zip": "Horsham, PA 19044-0951",
        "phone_service": "1-877-247-2559", "phone_es": "1-877-247-2559",
        "phone_intl": "1-602-659-5000", "phone_metro": "1-877-247-2559",
    },
}


# 短名别名：模板正文里写的是 "Chase" / "U.S. Bank" / "SoFi"，
# 品牌重绘把原稿短名换成 ledger.bank.name，名字一致才不会叠成 "Chase Chase"。
_REAL_TRAITS.update({
    "chase": _REAL_TRAITS["jpmorgan chase bank"],
    # 模板和模型都爱写「JPMorgan Chase」这个中间形态：剥词剥到这里就断了，
    # 会退化成派生出来的假行址（P O Box 51963 / Charlotte / 1-800-555-0004）。
    "jpmorgan chase": _REAL_TRAITS["jpmorgan chase bank"],
    "u.s. bank": _REAL_TRAITS["u.s. bank national association"],
    "sofi": _REAL_TRAITS["sofi bank"],
    "wells fargo": _REAL_TRAITS["wells fargo bank"],
})

# 行址只取中立城市（不是任何真银行的实际地址）
_HQ_CITIES = (
    ("Columbus, OH", "43219", "614"),
    ("Wilmington, DE", "19801", "302"),
    ("Minneapolis, MN", "55402", "612"),
    ("Charlotte, NC", "28202", "704"),
    ("Salt Lake City, UT", "84111", "801"),
)


#: 各家的真路由号（ACH/ABA）。账单上的汇款信息、直接存款都跟它对得上，
#: 用派生号会露馅：一个「Bank of America」账单配 021000021 以外的路由就是假的。
_REAL_ROUTING: dict[str, tuple[str, ...]] = {
    "bank of america": ("026009593", "111000025", "051000017"),
    "wells fargo bank": ("121000248", "121042882"),
    "jpmorgan chase bank": ("021000021", "322271627", "021202337"),
    "u.s. bank national association": ("091000022", "081000210", "123000220"),
    "sofi bank": ("081000210", "124003116"),
    "capital one": ("051405515", "056073502"),
    "american express national bank": ("124085066", "021000089"),
    "mercury": ("121140399",),
    "alliant credit union": ("271081528",),
    "ally bank": ("124003116",),
}
_REAL_ROUTING.update({
    "chase": _REAL_ROUTING["jpmorgan chase bank"],
    "u.s. bank": _REAL_ROUTING["u.s. bank national association"],
    "sofi": _REAL_ROUTING["sofi bank"],
    "american express": _REAL_ROUTING["american express national bank"],
    "wells fargo": _REAL_ROUTING["wells fargo bank"],
})


#: 行名里可以去掉的法定后缀（"JPMorgan Chase Bank, N.A." → "jpmorgan chase bank"）
_LEGAL_TAIL = {"n", "a", "na", "n a", "national", "association", "bank", "f", "s", "b",
               "fsb", "inc", "llc", "corp", "corporation", "company"}


def _bank_key(name: str) -> str:
    import re as _re

    s = _re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    return _re.sub(r"\s+", " ", s).strip()


_ROUTING_NORM: dict[str, tuple[str, ...]] = {}


def routing_for(name: str, seed: str) -> str:
    """有真路由就用真的（同一行名固定挑一个，别每份账单都换），否则派生一个自洽的。

    行名是模板自带的法定全称（``JPMorgan Chase Bank, N.A.``、``U.S. Bank National
    Association``），直接查表会错过 —— 这里逐级剥掉尾部法定后缀再查。
    """
    from .money import aba_routing

    key = _bank_key(name)
    words = key.split()
    while True:
        pool = _ROUTING_NORM.get(" ".join(words))
        if pool:
            pick = int(hashlib.sha256(f"routing-pick:{seed}".encode()).hexdigest()[:8], 16)
            return pool[pick % len(pool)]
        if len(words) > 1 and words[-1] in _LEGAL_TAIL:
            words.pop()
            continue
        if len(words) > 2 and words[-2] in _LEGAL_TAIL:
            words.pop(-2)
            continue
        break
    return aba_routing(seed)


_ROUTING_NORM.update({_bank_key(k): v for k, v in _REAL_ROUTING.items()})


def real_traits(name: str) -> dict[str, str] | None:
    """按行名查真行联系方式表；`JPMorgan Chase Bank, N.A.` 也能命中 `jpmorgan chase bank`。

    模板里写的是短名（Chase）、法定名带后缀（… Bank, N.A.）、甚至大小写不一，
    所以从右往左逐词剥到命中为止，别让「真行却配 555 假电话」这种破绽漏出去。
    """
    key = " ".join((name or "").replace(",", " ").split()).lower()
    if not key:
        return None
    words = key.split()
    while words:
        hit = _REAL_TRAITS.get(" ".join(words))
        if hit is not None:
            return hit
        words.pop()
    return None


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
        name = (name or "").strip()
        if not name:
            # 未指定行名：中性占位，build_data() 会替换成模板自带的名字。
            return cls(name="", legal="", domain="", phone="", po_box="", po_box_alt="",
                       city_state_zip="", phone_service="", phone_es="", phone_intl="", phone_metro="")
        traits = real_traits(name)
        if traits is not None:
            return cls(name=name, **traits)
        legal = name if name.endswith((".", "N.A.", "Bank")) and "," in name else f"{name}, N.A."
        digest = int(hashlib.sha256(f"hq:{seed}".encode()).hexdigest()[:8], 16)
        city, zip5, area = _HQ_CITIES[digest % len(_HQ_CITIES)]
        return cls(
            name=name,
            legal=legal,
            # 虚构行也要有个看起来像真银行的站点：`.example` 是 RFC 2606 保留域名，
            # 一行 `harborline-bank.example` 出现在账单页脚上就是「这是假的」的自白。
            domain=f"{_slug(name)}.com",
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
        """账单正文里展示的账号。

        样本对过账：Mercury、SoFi、Chase、BoA、U.S. Bank 的正文都是**完整账号**
        （Mercury ``700012345678``、SoFi ``41013344287``），只有 Capital One 的
        页眉截成 ``...5521``。所以默认给全号，需要截断的版式自己在主题里截。
        """
        return self.number

    @property
    def grouped(self) -> str:
        """印在账单页眉的账号，按真账单习惯用空格分组。

        真样本：BOA ``3250 1987 0414``（4-4-4）、U.S. Bank ``1 964 9173 9161``
        （1-3-4-4）、Mercury 15 位连写。这里按位数为 12 的账号给 4-4-4，
        其余原样返回，模板可以自行覆盖。
        """
        digits = "".join(ch for ch in self.number if ch.isdigit())
        if len(digits) == 12:
            return f"{digits[:4]} {digits[4:8]} {digits[8:]}"
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


def refresh_references(ledger: "LedgerDocument") -> "LedgerDocument":
    """银行身份落定后重挂一遍行内号码。

    ACH trace 的前 8 位必须是本行路由号；``from_game_result`` 里银行名可能还是空的
    （等 ``bridge`` 用模板自带银行补齐），那时派生出的路由号与最终印在账单上的对不上。
    所以银行身份一确定就调这里，按 ``ledger.account.routing`` 重算全部 reference。
    """
    rows = _attach_references(list(ledger.rows), ledger.seed,
                              routing=ledger.account.routing,
                              last4=ledger.account.last4)
    return replace(ledger, rows=tuple(rows))


#: 这些版式的参考件是「自然月结单」（Mercury 9 月 1 日–9 月 30 日、SoFi 同样），
#: 平移成开户日账期会和页眉的 `September 2026` 打架。
CALENDAR_MONTH_TEMPLATES = frozenset({"mercury", "capitalone", "sofi"})


def _anchor_delta(period: Period, seed: str) -> int:
    """账期整体要平移的天数（让结单日落在 seed 派生的「开户日」上）。"""
    import hashlib

    if not seed:
        return 0
    digest = hashlib.sha256(f"anchor:{seed}".encode("utf-8")).digest()
    anchor = 1 + digest[0] % 28
    return 0 if period.end.day == anchor else anchor - period.end.day


def _anchored_period(period: Period, seed: str) -> Period:
    """把自然月账期平移成「开户日结单」的账期。

    上游生成器按月产出，账期永远 1 号到月末 —— 真账单不是这样：结单日是
    开户日，15 号开户就每月 15 号结单。这里按 seed 派生一个结单日，把整段
    账期（含交易日期）整体平移，跨度不变、内部仍然闭合。
    """
    delta = _anchor_delta(period, seed)
    return Period(period.start + timedelta(days=delta), period.end + timedelta(days=delta))


def _shift_tx(tx: dict, days: int) -> dict:
    """把交易日期整体平移，保持与平移后的账期对齐。"""
    if not days:
        return tx
    moved = dict(tx)
    for key in ("transactionDate", "postedDate"):
        raw = moved.get(key)
        if raw:
            moved[key] = (_parse_date(raw) + timedelta(days=days)).isoformat()
    return moved


def from_game_result(
    payload: dict[str, Any],
    *,
    account: str | None = None,
    cycle: int = -1,
    bank_name: str | None = None,
    watermark: bool = True,
    realism: bool = True,
    template: str = "",
) -> LedgerDocument:
    """把一个生成结果切成「一个账户 × 一个账期」的账本文档。

    :param account: ``checking`` / ``business_checking`` / ``credit``；缺省取第一个账户。
    :param cycle: 账期下标，``-1`` 表示最近一期。
    :param realism: 是否做「入账日 / 金额尾数 / 收单方前缀」的真实化处理。
    :param template: 目标版式；日历月结单的版式（Mercury / Capital One / SoFi / Amex）
        不参与「结单日按开户日平移」，否则页眉会出现「September 2026 / Aug 29 - Sep 27」这种自相矛盾的账期。
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

    raw_period = Period(_parse_date(month["cycleStart"]), _parse_date(month["cycleEnd"]))
    book_seed = str(payload.get("seed") or payload.get("id") or "seed")
    calendar_month = template in CALENDAR_MONTH_TEMPLATES
    delta = 0 if calendar_month else _anchor_delta(raw_period, book_seed)
    period = raw_period if calendar_month else _anchored_period(raw_period, book_seed)
    opening = int(month["openingBalanceCents"])
    closing = int(month["closingBalanceCents"])

    selected = [
        _shift_tx(t, delta)
        for t in statement.get("transactions", [])
        if raw_period.start <= _parse_date(t["transactionDate"]) <= raw_period.end
    ]
    selected.sort(key=lambda t: (t["transactionDate"], t.get("postedDate", ""), t.get("id", "")))

    kind = _account_kind(statement)
    seed = str(payload.get("seed") or payload.get("id") or "seed")
    last4 = str(statement.get("accountLast4", "0000"))
    # 行名只认调用方显式传的 bank_name。留空时先按种子派生一个自洽路由号（不能是空串，
    # 否则账户路由号与 ACH trace 前 8 位会印成 00000000）；build_data() 换成模板自带的
    # 那家银行后，再用 refresh_references() 按真路由号重挂一次（见 bridge._adopt_template_bank）。
    wanted_bank = (bank_name or "").strip()
    routing = routing_for(wanted_bank, seed)

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

    # 行名只认调用方显式传的 bank_name：不再读结果包里的 bankName，也不再兜一个写死的银行名。
    # 留空时账本用空名占位，build_data() 会换成模板自带的银行名（见 bridge._adopt_template_bank）。
    name = wanted_bank
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
