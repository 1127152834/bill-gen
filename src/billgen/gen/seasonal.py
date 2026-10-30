"""美国节假日 / 季节 / 消费事件：让某个月的流水「像那个月」。

真实账单有时间结构：11 月有感恩节采购和黑五，12 月礼品与聚餐，7 月出行和空调费，
8 月返校，2 月情人节，4 月报税，5 月母亲节，6 月父亲节与暑假。模型不会自动想到这些，
所以这里把它们算成日历，再把「这个月该多花在哪」翻译成两类东西：

1. **信封系数**（`ENVELOPE_FACTORS`）——直接缩放 `gen/envelope.py` 算出的月度预算；
2. **事件提示**（`seasonal_notes`）——写进提示词，让模型在叙事里带出节日；
3. **事件补笔**（`inject_event_rows`）——某类目还有预算余量时，补 1–2 笔节日消费
   （礼品、花店、机票、南瓜、火鸡），金额落在该类目信封内，不会凭空放大总额。

节日日期一律按规则算（第 N 个星期几、复活节用 Computus），不写死年份。
"""
from __future__ import annotations

import calendar
import datetime as _dt
import hashlib
import random
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "Event",
    "us_holidays",
    "retail_events",
    "month_events",
    "month_factor",
    "envelope_for_month",
    "seasonal_notes",
    "inject_event_rows",
]


@dataclass(frozen=True, slots=True)
class Event:
    """一个会影响消费的日期。"""

    name: str
    date: _dt.date
    categories: tuple[str, ...] = ()
    merchants: tuple[str, ...] = ()
    note: str = ""
    retail: bool = False


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> _dt.date:
    """某月第 n 个星期几（weekday: 周一=0）。"""
    first = _dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + _dt.timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> _dt.date:
    last_day = calendar.monthrange(year, month)[1]
    last = _dt.date(year, month, last_day)
    return last - _dt.timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> _dt.date:
    """Anonymous Gregorian algorithm（Meeus/Jones/Butcher）。"""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return _dt.date(year, month, day + 1)


def us_holidays(year: int) -> list[Event]:
    """联邦假日 + 家家都过的节日（含购物相关的那几个）。"""
    events = [
        Event("New Year's Day", _dt.date(year, 1, 1), ("dining",), ("CHAMPAGNE BAR",)),
        Event("MLK Day", _nth_weekday(year, 1, 0, 3)),
        Event("Valentine's Day", _dt.date(year, 2, 14), ("dining", "shopping"), ("1-800-FLOWERS", "SEE'S CANDIES")),
        Event("Presidents' Day", _nth_weekday(year, 2, 0, 3), ("shopping",), ("HOME DEPOT",)),
        Event("Easter", _easter(year), ("groceries", "dining"), ("HONEY BAKED HAM",)),
        Event("Mother's Day", _nth_weekday(year, 5, 6, 2), ("dining", "shopping"), ("1-800-FLOWERS", "EDIBLE ARRANGEMENTS")),
        Event("Memorial Day", _last_weekday(year, 5, 0), ("groceries", "fuel"), ("COSTCO", "SHELL OIL")),
        Event("Father's Day", _nth_weekday(year, 6, 6, 3), ("dining", "shopping"), ("HOME DEPOT", "BEST BUY")),
        Event("Juneteenth", _dt.date(year, 6, 19), ("dining",), ()),
        Event("Independence Day", _dt.date(year, 7, 4), ("groceries", "fuel"), ("COSTCO", "SHELL OIL")),
        Event("Labor Day", _nth_weekday(year, 9, 0, 1), ("groceries", "fuel"), ("COSTCO",)),
        Event("Halloween", _dt.date(year, 10, 31), ("groceries", "shopping"), ("TARGET", "PARTY CITY")),
        Event("Veterans Day", _dt.date(year, 11, 11)),
        Event("Thanksgiving", _nth_weekday(year, 11, 3, 4), ("groceries", "dining"), ("KROGER", "TRADER JOE'S")),
        Event("Christmas Eve", _dt.date(year, 12, 24), ("groceries", "dining"), ("TRADER JOE'S",)),
        Event("Christmas", _dt.date(year, 12, 25), ("shopping", "dining"), ("TARGET", "BEST BUY")),
        Event("New Year's Eve", _dt.date(year, 12, 31), ("dining",), ("CHAMPAGNE BAR",)),
    ]
    return events


def retail_events(year: int) -> list[Event]:
    """零售侧的大促（黑五、网一、Prime Day、返校、报税）。"""
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    black_friday = thanksgiving + _dt.timedelta(days=1)
    return [
        Event("Super Bowl", _nth_weekday(year, 2, 6, 1), ("groceries", "dining"), ("KROGER", "DOMINO'S"), "比赛日聚餐饮料", retail=True),
        Event("Tax Day", _dt.date(year, 4, 15), ("shopping",), ("TURBOTAX",), "报税软件/服务", retail=True),
        Event("Back to School", _dt.date(year, 8, 15), ("shopping",), ("TARGET", "STAPLES", "OLD NAVY"), "返校采购", retail=True),
        Event("Prime Day", _nth_weekday(year, 7, 1, 2), ("shopping",), ("AMAZON.COM",), "会员日大促", retail=True),
        Event("Black Friday", black_friday, ("shopping", "dining"), ("BEST BUY", "TARGET", "WALMART", "AMAZON.COM"), "黑五抢购", retail=True),
        Event("Cyber Monday", black_friday + _dt.timedelta(days=3), ("shopping",), ("AMAZON.COM", "BEST BUY"), "网一", retail=True),
        Event("Holiday Gifting", _dt.date(year, 12, 15), ("shopping",), ("TARGET", "MACY'S", "AMAZON.COM"), "圣诞礼品", retail=True),
    ]


def month_events(year: int, month: int) -> list[Event]:
    """当月的节日与促销，按日期排序。"""
    merged = [e for e in us_holidays(year) + retail_events(year) if e.date.month == month]
    return sorted(merged, key=lambda e: e.date)


#: 季节性系数：``(类目, 月份) -> 乘数``。只覆盖真的会波动的月份/类目。
ENVELOPE_FACTORS: dict[tuple[str, int], float] = {
    # 空调 / 取暖
    ("utilities", 6): 1.25,
    ("utilities", 7): 1.35,
    ("utilities", 8): 1.35,
    ("utilities", 9): 1.2,
    ("utilities", 12): 1.2,
    ("utilities", 1): 1.25,
    ("utilities", 2): 1.2,
    # 出行（暑假 / 圣诞）
    ("fuel", 6): 1.2,
    ("fuel", 7): 1.25,
    ("fuel", 8): 1.2,
    ("dining", 12): 1.25,
    ("dining", 2): 1.15,
    ("dining", 11): 1.15,
    # 购物
    ("shopping", 8): 1.25,
    ("shopping", 11): 1.9,
    ("shopping", 12): 2.0,
    # 食材（感恩节 / 圣诞）
    ("groceries", 11): 1.35,
    ("groceries", 12): 1.25,
    ("groceries", 7): 1.1,
}


def month_factor(category: str, month: int) -> float:
    """某类目在某月的季节系数（没有就 1.0）。"""
    return ENVELOPE_FACTORS.get((category, month), 1.0)


def envelope_for_month(
    env: Mapping[str, tuple[int, int]],
    year: int,
    month: int,
) -> dict[str, tuple[int, int]]:
    """把基础信封按季节系数缩放到某个月（整数分，向下取整到分）。"""
    out: dict[str, tuple[int, int]] = {}
    for name, (lo, hi) in env.items():
        factor = month_factor(name, month)
        out[name] = (int(round(lo * factor)), int(round(hi * factor)))
    return out


def seasonal_notes(year: int, month: int) -> str:
    """写给提示词的一句话：这个月有哪些事件、该往哪偏。"""
    events = month_events(year, month)
    if not events:
        return f"{year}-{month:02d}：没有美国节日或大促，按常规节奏消费。"
    parts = []
    for event in events:
        cats = "/".join(event.categories) if event.categories else "常态"
        parts.append(f"{event.date.month}/{event.date.day} {event.name}（{event.note or cats}）")
    return (
        f"{year}-{month:02d} 的美国节日与消费事件：" + "；".join(parts)
        + "。这些日期附近要让对应类目明显抬头（例如感恩节前一周食材、黑五当周购物、"
        "圣诞前聚餐与礼品、七月出行与空调费），但整月合计仍要落在信封内。"
    )


_EVENT_AMOUNT_RANGE: dict[str, tuple[float, float]] = {
    "shopping": (35.0, 260.0),
    "groceries": (18.0, 120.0),
    "dining": (25.0, 140.0),
    "fuel": (28.0, 70.0),
}


def _spent(rows: Iterable[Mapping[str, Any]], category: str) -> int:
    """某类目已经花了多少（分）。"""
    from . import postpass

    total = 0
    for row in rows:
        desc = str(row.get("desc") or row.get("description") or row.get("detail") or "")
        if (postpass._category_of(desc) or "other") != category:
            continue
        amount = postpass._row_amount(row)
        if amount is not None and amount < 0:
            total += int(round(-amount * 100))
    return total


def inject_event_rows(
    data: dict[str, Any],
    *,
    year: int,
    month: int,
    seed: str,
    envelope: Mapping[str, tuple[int, int]] | None = None,
    city: str = "",
    state: str = "",
) -> int:
    """在预算还有余量时，补几笔「这个月才有」的消费，返回补了几行。

    规则很克制：每个事件最多补 1 笔，只补节日主类目，金额落在类目信封内、
    且补完之后不越过信封上限；商户必须是事件自己的商户表，城市只在本地店加。
    """
    from . import postpass

    arrays = [array for array in postpass._iter_arrays(data) if array]
    if not arrays:
        return 0
    target = arrays[0]
    env: Mapping[str, tuple[int, int]] = envelope if envelope is not None else {}
    events = [event for event in month_events(year, month) if event.categories and event.merchants]
    if not events:
        return 0

    added = 0
    for event in events:
        category = event.categories[0]
        bucket = env.get(category)
        if not bucket:
            continue
        low, high = int(bucket[0]), int(bucket[1])
        spent = _spent(target, category)
        if spent >= low * 0.8:
            continue  # 这个类目已经够花了，别再补
        rng = random.Random(hashlib.sha256(f"{seed}|{event.name}|{category}".encode()).hexdigest())
        amount = round(rng.uniform(*_EVENT_AMOUNT_RANGE.get(category, (20.0, 120.0))), 2)
        cents = int(round(amount * 100))
        if spent + cents > high:
            continue
        merchant = event.merchants[rng.randrange(len(event.merchants))]
        local = category in {"groceries", "fuel", "dining"} and merchant not in {"AMAZON.COM", "1-800-FLOWERS", "TURBOTAX"}
        place = f" {city.upper()} {state.upper()}" if local and city and state else ""
        day = min(event.date.day + rng.randrange(0, 3), 28)
        from . import postpass as _pp

        base_row = target[0] if target else {}
        target.append(
            _pp._make_row(
                base_row,
                date=f"{calendar.month_abbr[month]} {day}",
                desc=f"{merchant}{place}",
                amount=-amount,
            )
        )
        added += 1
    if added:
        postpass._rescale_amounts(data)
    return added
