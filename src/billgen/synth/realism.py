"""把账本往「真实账单的样子」再推一步：入账日、金额尾数、商户描述符。

这些规则在生成侧（``flow/``）已经有一部分，这里做的是**双重保险**：
结果是确定性的，同一账本永远得到同一份账单；任何一步都不改变
「期初 + 贷 − 借 = 期末」这条恒等式（余额链会重算）。

三段处理：

1. :func:`postings` —— ACH / 手续费 / 利息 / 支票只能在工作日入账（周末与联邦
   假日一律提前到上一个工作日）；POS / ATM / Zelle 保持原日（真账单里周末刷卡、
   ATM 取现、即时转账都正常出现）。
2. :func:`amounts` —— 给「太整齐」的消费金额补零头。**目前不接进主流程**：
   改金额会让「期初 + 贷 − 借」对不上上游给的期末余额，而这条恒等式正是
   ``ledger_check_failed`` 的判据（拿它抓坏数据比补零头重要）。要启用就整条链路
   一起改上游汇总，不能只在这里动手。
3. :func:`descriptors` —— POS 行补上收单方前缀（``SQ *``、``TST* ``、``PAYPAL *``、
   ``AMZN Mktp US*``），这是真账单里最好认的一条特征。

顺序：先改金额（影响余额），再改日期（影响排序），最后重算余额链。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from datetime import date, timedelta

from .ledger import LedgerDocument, LedgerRow

__all__ = [
    "HOLIDAYS",
    "amounts",
    "apply_all",
    "descriptors",
    "is_business_day",
    "postings",
]

#: 固定的节假日日期（元旦/独立日/退伍军人节/圣诞；浮动节日见 :func:`federal_holidays`）。
_FIXED = {(1, 1), (7, 4), (11, 11), (12, 25)}

#: 会被提前到工作日的入账类型。
_BUSINESS_DAY_TYPES = frozenset({"ACH", "FEE", "INTEREST", "CHECK"})

#: POS 行可能带的收单方前缀（真账单里最常见的一批）。
_POS_PREFIXES = ("SQ *", "TST* ", "PAYPAL *", "AMZN Mktp US*", "SP ")

#: 这些类型金额保持整数（真账单里租金/订阅/手续费都是整的）。
_ROUND_TYPES = frozenset({"ACH", "ZELLE", "FEE", "INTEREST", "CHECK", "TRANSFER", "PAYMENT", "ATM"})


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    day = date(year, month, 1)
    offset = (weekday - day.weekday()) % 7
    return day + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    next_month = date(year + month // 12, month % 12 + 1, 1)
    day = next_month - timedelta(days=1)
    return day - timedelta(days=(day.weekday() - weekday) % 7)


def federal_holidays(year: int) -> frozenset[date]:
    """美国联邦假日（够用就行：这些日子银行不处理 ACH）。"""
    days = {date(year, month, day) for month, day in _FIXED}
    days.add(_nth_weekday(year, 1, 0, 3))       # MLK
    days.add(_nth_weekday(year, 2, 0, 3))       # Presidents
    days.add(_last_weekday(year, 5, 0))         # Memorial
    days.add(_nth_weekday(year, 9, 0, 1))       # Labor
    days.add(_nth_weekday(year, 10, 0, 2))      # Columbus
    days.add(_nth_weekday(year, 11, 3, 4))      # Thanksgiving
    observed: set[date] = set()
    for day in days:
        if day.weekday() == 5:                  # 周六 → 周五补
            observed.add(day - timedelta(days=1))
        elif day.weekday() == 6:                # 周日 → 周一补
            observed.add(day + timedelta(days=1))
        else:
            observed.add(day)
    return frozenset(observed)


HOLIDAYS: frozenset[date] = federal_holidays(2026) | federal_holidays(2027)


def is_business_day(day: date) -> bool:
    return day.weekday() < 5 and day not in HOLIDAYS


def _stem(ledger: LedgerDocument, row: LedgerRow, salt: str) -> int:
    key = f"{salt}:{ledger.seed}:{row.date.isoformat()}:{row.amount_cents}:{row.description}"
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)


def amounts(ledger: LedgerDocument) -> tuple[list[LedgerRow], int]:
    """给「太整齐」的消费金额补零头，返回 (新行, 改动数)。"""
    out: list[LedgerRow] = []
    changed = 0
    for row in ledger.rows:
        if row.type in _ROUND_TYPES or row.amount_cents % 5 != 0 or row.amount_cents < 100:
            out.append(row)
            continue
        cents = 1 + _stem(ledger, row, "cents") % 99
        out.append(replace(row, amount_cents=(row.amount_cents // 100) * 100 + cents))
        changed += 1
    return out, changed


#: 商户类型 → 真账单上常见的收单方前缀。前缀是收单机构（Square/Toast/PayPal…）
#: 打在描述前的标记，与商户类别强相关 —— 随机抽一个会拼出
#: `AMZN Mktp US*TRADER JOES`、`PAYPAL *UBER *TRIP` 这种一眼假的组合（实测件）。
_PREFIX_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("STARBUCKS", "COFFEE", "CAFE", "BAKERY", "DONUT", "TEA HOUSE", "JUICE"), ("SQ *", "TST* ")),
    (("RESTAURANT", "GRILL", "PIZZA", "TACO", "BURGER", "KITCHEN", "BBQ", "SUSHI",
      "THAI", "BISTRO", "DELI", "NOODLE", "RAMEN", "CANTINA", "DINER"), ("TST* ", "SQ *")),
    (("WHOLE FOODS", "WHOLEFDS", "TRADER JOE", "KROGER", "SAFEWAY", "H-E-B", "HEB ",
      "COSTCO", "ALDI", "PUBLIX", "SPROUTS"), ("",)),
    (("AMAZON", "AMZN"), ("AMZN Mktp US*",)),
    (("PAYPAL", "EBAY", "ETSY", "VENMO"), ("PAYPAL *",)),
    (("SHELL", "CHEVRON", "EXXON", "MOBIL", "CIRCLE K", "VALERO", "SUNOCO", "76 "),
     ("SP ",)),
    (("UBER", "LYFT", "DOORDASH", "GRUBHUB", "INSTACART", "AIRBNB"), ("",)),
)


def _prefix_for(ledger: LedgerDocument, row: LedgerRow, text: str) -> str:
    """商户名命中类别就取该类的收单方前缀；对不上类别一律不加。"""
    up = text.upper()
    for keys, prefixes in _PREFIX_RULES:
        if any(k in up for k in keys):
            if not prefixes:
                return ""
            return prefixes[_stem(ledger, row, "prefix") % len(prefixes)]
    return ""


def descriptors(ledger: LedgerDocument) -> tuple[list[LedgerRow], int]:
    """POS 行补收单方前缀：只给「该类商户真会带前缀」的行加，且绝不叠两个前缀。"""
    out: list[LedgerRow] = []
    changed = 0
    for row in ledger.rows:
        text = row.description.lstrip()
        if row.type != "POS" or not text or text.startswith(_POS_PREFIXES):
            out.append(row)
            continue
        prefix = _prefix_for(ledger, row, text)
        if not prefix:
            out.append(row)
            continue
        out.append(replace(row, description=f"{prefix}{text}"))
        changed += 1
    return out, changed


#: 收单方城市码（真账单里 ATM/POS 行末尾的 3 字母，来自收单行所在地）。
_CITY_CODES = ("AUS", "CHI", "NYC", "SFO", "SEA", "DEN", "ATL", "DFW", "PHX", "BOS",
               "MIA", "PDX", "MSP", "STL", "CLT", "SLC", "LAS", "SAN", "DET", "TPA")

#: ATM 行的尾巴：真样本是 `TERMINAL 0721 AUS` 这种「终端号 + 城市码」，
#: 不是一串无源字母（旧实现会拼出 `725LUIMG` 这种一眼假的串）。
_ATM_TAIL = re.compile(r"\s+(?:TERMINAL\s+)?[A-Z0-9]{5,}$")

#: 已经规范的尾巴（`5923 PDX` / `TERMINAL 5923 PDX`）：上游自己给的终端号 + 城市码，不再二次加工。
_ATM_CANON = re.compile(r"\s+(?:TERMINAL\s+)?\d{3,6}(?:\s+[A-Z]{3})?$")


#: 美国主要城市 → 真账单上用的三字母城市码（ATM 行尾）
_CITY_CODE_BY_NAME = {
    "austin": "AUS", "houston": "HOU", "dallas": "DFW", "fort worth": "DFW",
    "san antonio": "SAT", "chicago": "CHI", "new york": "NYC", "brooklyn": "NYC",
    "queens": "NYC", "los angeles": "LAX", "san francisco": "SFO", "oakland": "OAK",
    "san jose": "SJC", "san diego": "SAN", "sacramento": "SMF", "seattle": "SEA",
    "portland": "PDX", "denver": "DEN", "atlanta": "ATL", "phoenix": "PHX",
    "boston": "BOS", "miami": "MIA", "minneapolis": "MSP", "st. louis": "STL",
    "saint louis": "STL", "charlotte": "CLT", "salt lake city": "SLC",
    "las vegas": "LAS", "detroit": "DET", "tampa": "TPA", "orlando": "MCO",
    "philadelphia": "PHL", "pittsburgh": "PIT", "cleveland": "CLE",
    "cincinnati": "CVG", "columbus": "CMH", "nashville": "BNA", "raleigh": "RDU",
    "washington": "WAS", "baltimore": "BWI", "indianapolis": "IND",
    "kansas city": "MCI", "milwaukee": "MKE", "omaha": "OMA", "tucson": "TUS",
}


def _holder_city_code(ledger: LedgerDocument) -> str:
    """开户人所在城市的三字母码（客户住 Austin，ATM 行的城市码就该是 AUS）。"""
    city = (ledger.holder.city_state or "").split(",")[0].strip().lower()
    if city.endswith(" tx") or " " in city:
        city = " ".join(city.split()[:-1]) if city.split()[-1].isalpha() and len(city.split()[-1]) == 2 else city
    code = _CITY_CODE_BY_NAME.get(city)
    if code:
        return code
    return ""


def _atm_tail(ledger: LedgerDocument, row: LedgerRow) -> str:
    """`5923 PDX`：终端号 + 城市码。

    真账单的 ATM 行就是这么短（``ATM WITHDRAWAL 0925 AUSTIN TX``）。这里不写 ``TERMINAL``
    这个词：描述列按像素裁宽度，多这 9 个字符就会把终端号截成半截（``TERMINAL 59``）。
    """
    digest = _stem(ledger, row, "atm")
    terminal = 1000 + digest % 9000
    city = _holder_city_code(ledger) or _CITY_CODES[(digest // 9000) % len(_CITY_CODES)]
    return f"{terminal} {city}"


def atm_tails(ledger: LedgerDocument) -> tuple[list[LedgerRow], int]:
    """把 ATM 行的乱码尾巴换成「终端号 + 城市码」。"""
    out: list[LedgerRow] = []
    changed = 0
    for row in ledger.rows:
        if row.type != "ATM" or not row.description.strip():
            out.append(row)
            continue
        if _ATM_CANON.search(row.description.strip()):
            out.append(row)
            continue
        head = _ATM_TAIL.sub("", row.description.strip()).rstrip()
        new = f"{head} {_atm_tail(ledger, row)}"
        if new != row.description:
            changed += 1
        out.append(replace(row, description=new))
    return out, changed


def postings(ledger: LedgerDocument) -> tuple[list[LedgerRow], int]:
    """把非工作日入账的 ACH/费用/利息/支票提前到上一个工作日。"""
    out: list[LedgerRow] = []
    moved = 0
    floor = ledger.period.start
    for row in ledger.rows:
        day = row.date
        if row.type not in _BUSINESS_DAY_TYPES or is_business_day(day):
            out.append(row)
            continue
        while not is_business_day(day) and day > floor:
            day -= timedelta(days=1)
        if day != row.date:
            moved += 1
        out.append(replace(row, date=day, posted=day))
    return out, moved


def _rebalance(ledger: LedgerDocument, rows: list[LedgerRow]) -> list[LedgerRow]:
    """按日期重排并重算余额链（期初 + 贷 − 借 = 期末 保持不变）。"""
    order = sorted(range(len(rows)), key=lambda i: (rows[i].date, i))
    balance = ledger.opening_cents
    out: list[LedgerRow] = []
    for index in order:
        row = rows[index]
        balance += row.amount_cents if row.direction == "credit" else -row.amount_cents
        out.append(replace(row, balance_cents=balance))
    return out


def apply_all(ledger: LedgerDocument) -> tuple[LedgerDocument, dict[str, int]]:
    """一次性跑完三段，返回 (新账本, 各段改动计数)。"""
    cents_changed = 0
    rows, desc_changed = descriptors(ledger)
    ledger = replace(ledger, rows=tuple(rows))
    rows, atm_changed = atm_tails(ledger)
    ledger = replace(ledger, rows=tuple(rows))
    rows, moved = postings(ledger)
    rows = _rebalance(ledger, rows)
    extras = dict(ledger.extras)
    extras.update(
        cents_nudged=cents_changed,
        descriptors_prefixed=desc_changed,
        atm_tails=atm_changed,
        postings_shifted=moved,
    )
    # 期末余额以流水重算为准：结果包里报错的 closing 不带进 PDF
    closing = rows[-1].balance_cents if rows else ledger.opening_cents
    return replace(ledger, rows=tuple(rows), closing_cents=closing, extras=extras), {
        "cents": cents_changed,
        "descriptors": desc_changed,
        "atm": atm_changed,
        "postings": moved,
    }
