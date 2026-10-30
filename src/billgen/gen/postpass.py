"""交付前的确定性收尾：把「一眼假」的地理与金额硬伤就地改掉。

模型（lifeModel）负责「这个人怎么花钱」，但它对地理与品类的记忆并不可靠：
Austin 的户头上会出现 Con Edison，Temple City 的加油记录会是 $3.41。
审计能发现问题，却只能写 warning；用户拿到的 JSON 里那行错商户还在。

这一层只做**确定性**的事，做完立刻能被 ``monthpack.check_delivered()`` 复验：

1. **公用事业按地区纠正**（``fix_utilities``）——电/燃气付给谁，是地理事实，不是创作。
2. **越区连锁替换**（``fix_chains``）——Whataburger 不进加州，QuikTrip 不进加州。
3. **离谱金额修正**（``fix_amounts``）——加油 $3.41、租金 $275 这种档位不像真实消费。

改金额会动到账：函数会把 ``closing`` 与 ``totals`` 一并按新的流水合计算回去，
保证「期初 + 流水 = 期末」这条硬约束仍然成立。
"""

from __future__ import annotations

import re
import hashlib
import random
from typing import Any, Iterable, Mapping

__all__ = [
    "CATEGORY_RANGES",
    "parse_range",
    "fix_budget",
    "fix_utilities",
    "fix_chains",
    "fix_amounts",
    "fix_online_places",
    "dedupe_utilities",
    "repair_month",
]


# ---------------------------------------------------------------------------
# 地区 → 公用事业
# ---------------------------------------------------------------------------

#: ``(州, 城市关键字) -> (电, 燃气)``。城市关键字按子串匹配，命中越具体越优先。
_UTILITIES: tuple[tuple[tuple[str, str], tuple[str, str]], ...] = (
    # 加州 · 洛杉矶县（LADWP 只管洛杉矶市，Temple City 这些卫星城是 SCE + SoCalGas）
    (("CA", "temple city"), ("SOUTHERN CALIFORNIA EDISON", "SO CAL GAS")),
    (("CA", "alhambra"), ("SOUTHERN CALIFORNIA EDISON", "SO CAL GAS")),
    (("CA", "monterey park"), ("SOUTHERN CALIFORNIA EDISON", "SO CAL GAS")),
    (("CA", "arcadia"), ("SOUTHERN CALIFORNIA EDISON", "SO CAL GAS")),
    (("CA", "san gabriel"), ("SOUTHERN CALIFORNIA EDISON", "SO CAL GAS")),
    (("CA", "rosemead"), ("SOUTHERN CALIFORNIA EDISON", "SO CAL GAS")),
    (("CA", "pasadena"), ("PASADENA WATER AND POWER", "SO CAL GAS")),
    (("CA", "los angeles"), ("LADWP", "SO CAL GAS")),
    (("CA", "san francisco"), ("PG&E", "PG&E")),
    (("CA", "oakland"), ("PG&E", "PG&E")),
    (("CA", "san jose"), ("PG&E", "PG&E")),
    (("CA", "sacramento"), ("SMUD", "PG&E")),
    (("CA", "san diego"), ("SDG&E", "SDG&E")),
    # 德州
    (("TX", "austin"), ("AUSTIN ENERGY", "TEXAS GAS SERVICE")),
    (("TX", "round rock"), ("AUSTIN ENERGY", "TEXAS GAS SERVICE")),
    (("TX", "dallas"), ("TXU ENERGY", "ATMOS ENERGY")),
    (("TX", "houston"), ("CENTERPOINT ENERGY", "CENTERPOINT ENERGY")),
    (("TX", "san antonio"), ("CPS ENERGY", "CPS ENERGY")),
    # 纽约
    (("NY", "new york"), ("CON EDISON", "NATIONAL GRID")),
    (("NY", "brooklyn"), ("CON EDISON", "NATIONAL GRID")),
    (("NY", "queens"), ("CON EDISON", "NATIONAL GRID")),
    (("NY", "flushing"), ("CON EDISON", "NATIONAL GRID")),
    # 华盛顿 / 伊利诺伊 / 马萨诸塞 / 佛州
    (("WA", "seattle"), ("SEATTLE CITY LIGHT", "PUGET SOUND ENERGY")),
    (("IL", "chicago"), ("COMED", "PEOPLES GAS")),
    (("MA", "boston"), ("EVERSOURCE", "EVERSOURCE")),
    (("FL", "miami"), ("FPL", "TECO PEOPLES GAS")),
)

#: 州级兜底（城市没命中时用）。
_STATE_DEFAULT: dict[str, tuple[str, str]] = {
    "CA": ("PG&E", "PG&E"),
    "TX": ("TXU ENERGY", "ATMOS ENERGY"),
    "NY": ("CON EDISON", "NATIONAL GRID"),
    "WA": ("PUGET SOUND ENERGY", "PUGET SOUND ENERGY"),
    "IL": ("COMED", "PEOPLES GAS"),
    "MA": ("EVERSOURCE", "EVERSOURCE"),
    "FL": ("FPL", "TECO PEOPLES GAS"),
}

#: 会被当成「公用事业代扣」的商户名（含错误的跨区公司）。
_UTILITY_TOKENS = (
    "LADWP",
    "PG&E",
    "PGANDE",
    "XCEL ENERGY",
    "CON EDISON",
    "CONED",
    "DUKE ENERGY",
    "AUSTIN ENERGY",
    "CENTERPOINT",
    "COMED",
    "NATIONAL GRID",
    "SO CAL GAS",
    "SOCALGAS",
    "SDG&E",
    "FPL",
    "EVERSOURCE",
    "TEXAS GAS SERVICE",
    "ATMOS",
    "PUGET SOUND",
    "SEATTLE CITY LIGHT",
    "SOUTHERN CALIFORNIA EDISON",
    "TXU ENERGY",
    "CPS ENERGY",
    "PASADENA WATER",
    "SMUD",
    "TECO",
    "PEOPLES GAS",
)

_GAS_TOKENS = ("SO CAL GAS", "SOCALGAS", "TEXAS GAS SERVICE", "ATMOS", "NATIONAL GRID", "PEOPLES GAS", "TECO")


# ---------------------------------------------------------------------------
# 越区连锁
# ---------------------------------------------------------------------------

#: ``连锁 -> 允许出现的州``；不在这里的州一律换成地区替代品。
_CHAIN_STATES: dict[str, frozenset[str]] = {
    "WHATABURGER": frozenset({"TX", "NM", "OK", "LA", "AR", "MS", "AL", "AZ", "KS", "MO", "TN", "GA", "FL", "CO"}),
    "QUIKTRIP": frozenset({"MO", "KS", "OK", "AR", "IA", "IL", "NE", "TX", "AZ", "CO", "GA", "SC", "NC", "TN", "AL", "MS", "LA", "NM", "SD", "MN", "FL", "IN", "KY", "VA", "WV", "OH"}),
    "BUC-EE'S": frozenset({"TX", "AL", "FL", "GA", "KY", "SC", "TN", "CO", "MO"}),
    "IN-N-OUT": frozenset({"CA", "AZ", "NV", "UT", "TX", "OR", "CO", "ID", "WA"}),
    "WEGMANS": frozenset({"NY", "NJ", "PA", "VA", "MD", "MA", "CT", "DE", "DC", "NC", "ME", "NH", "RI", "VT"}),
    "PUBLIX": frozenset({"FL", "GA", "SC", "NC", "TN", "AL", "KY", "VA"}),
    "H-E-B": frozenset({"TX", "MX"}),
    # Kroger 退出加州后由 Ralphs / Vons / Food 4 Less 承牌，别在 CA 账单上印 KROGER。
    "KROGER": frozenset({"OH", "MI", "IN", "IL", "KY", "TN", "VA", "WV", "GA", "SC", "NC", "AL", "MS", "LA", "TX", "AR", "MO", "KS", "NE", "AZ", "UT", "ID", "WA", "OR", "AK", "WY", "NM", "NV", "MD", "DE", "DC", "CO"}),
    "VONS": frozenset({"CA", "NV"}),
    "FOOD 4 LESS": frozenset({"CA", "NV", "IL", "IN", "MO", "KS", "NE", "IA", "TX", "OR", "WA", "AZ", "NM"}),
    "WAWA": frozenset({"PA", "NJ", "DE", "MD", "VA", "FL", "DC", "NC", "GA", "AL", "SC", "TN", "KY", "OH", "IN"}),
    "SHEETZ": frozenset({"PA", "VA", "WV", "MD", "OH", "NC", "KY", "NY", "SC", "TN", "GA", "AL", "MI", "IN"}),
    "SPROUTS": frozenset({"AZ", "CA", "CO", "FL", "GA", "KS", "LA", "MO", "MS", "NV", "NM", "OK", "TN", "TX", "UT", "VA", "WA", "WY", "AL", "DE", "IL", "IN", "MD", "MI", "MN", "NE", "NJ", "OH", "OR", "PA", "SC", "SD", "WI"}),
    "SHAKE SHACK": frozenset({"NY", "CA", "TX", "MA", "NJ", "CT", "DC", "FL", "GA", "IL", "MD", "NV", "PA", "VA", "WA", "AZ", "CO", "IN", "MO", "NC", "OH", "TN", "UT", "WI"}),
}

#: 换了地区该换成什么：``(类别, 目标州) -> 候选``。
_REGIONAL_SWAPS: dict[tuple[str, str], tuple[str, ...]] = {
    ("dining", "CA"): ("CHIPOTLE", "PANERA BREAD", "SWEETGREEN", "SHAKE SHACK", "IN-N-OUT"),
    ("dining", "TX"): ("WHATABURGER", "TORCHY'S TACOS", "CHIPOTLE", "PANERA BREAD"),
    ("fuel", "CA"): ("CHEVRON", "SHELL", "ARCO", "76"),
    ("fuel", "TX"): ("BUC-EE'S", "QUIKTRIP", "SHELL", "CHEVRON"),
    ("fuel", "NY"): ("MOBIL", "SHELL", "BP", "CITGO"),
    ("fuel", "WA"): ("SHELL", "CHEVRON", "76", "ARCO"),
    ("grocery", "CA"): ("TRADER JOE'S", "VONS", "RALPHS", "FOOD 4 LESS"),
    ("grocery", "TX"): ("H-E-B", "KROGER", "ALDI"),
    ("grocery", "NY"): ("TRADER JOE'S", "KEY FOOD", "ALDI"),
}

_DEFAULT_SWAP = {
    "dining": ("CHIPOTLE", "PANERA BREAD", "MCDONALD'S", "SUBWAY"),
    "fuel": ("SHELL", "CHEVRON", "MOBIL", "BP"),
    "grocery": ("TRADER JOE'S", "ALDI", "KROGER"),
    "pharmacy": ("CVS", "WALGREENS", "RITE AID"),
}


# ---------------------------------------------------------------------------
# 金额档位（美元）
# ---------------------------------------------------------------------------

#: ``类别 -> (最小, 最大)``。超出区间太远的价格是最容易露馅的一类假。
CATEGORY_RANGES: dict[str, tuple[float, float]] = {
    "fuel": (24.0, 95.0),
    "grocery": (12.0, 260.0),
    "coffee": (3.5, 14.0),
    "dining": (8.0, 85.0),
    "pharmacy": (5.0, 90.0),
    "subscription": (4.99, 26.0),
    "utility": (28.0, 260.0),
    "rent": (850.0, 4200.0),
    "rideshare": (6.0, 48.0),
    "phone": (25.0, 120.0),
    "insurance": (60.0, 320.0),
}

#: 信封用的是复数类目名，金额档用的是单数/习惯名，这里做一层对齐。
_RANGE_ALIASES = {
    "utilities": "utility",
    "groceries": "grocery",
    "health": "pharmacy",
    "subscription": "subscription",
}


def range_for(category: str) -> tuple[float, float]:
    """取类目的单笔金额档（兼容信封的复数类目名）。"""
    key = _RANGE_ALIASES.get(category, category)
    return CATEGORY_RANGES.get(key, CATEGORY_RANGES.get(category, (10.0, 400.0)))


_CATEGORY_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("fuel", ("SHELL", "CHEVRON", "MOBIL", "ARCO", "EXXON", "BP ", "CITGO", "VALERO", "76 ", "CIRCLE K", "QUIKTRIP", "BUC-EE", "SPEEDWAY", "SHEETZ", "WAWA", "EXXONMOBIL")),
    ("coffee", ("STARBUCKS", "DUNKIN", "PEET", "BLUE BOTTLE", "DUTCH BROS", "COFFEE")),
    ("grocery", ("KROGER", "H-E-B", "SPROUTS", "TRADER JOE", "WHOLE FOODS", "SAFEWAY", "ALDI", "COSTCO", "WALMART", "VONS", "RALPHS", "FOOD 4 LESS", "PUBLIX", "WEGMANS", "GROCERY", "MARKET")),
    ("dining", ("MCDONALD", "CHIPOTLE", "PANERA", "IN-N-OUT", "TACO", "PIZZA", "SUBWAY", "CHICK-FIL-A", "WHATABURGER", "SHAKE SHACK", "SWEETGREEN", "RESTAURANT", "BURGER", "SUSHI", "THAI", "PHO")),
    ("pharmacy", ("CVS", "WALGREENS", "RITE AID", "PHARMACY")),
    ("subscription", ("NETFLIX", "SPOTIFY", "HULU", "APPLE.COM", "ADOBE", "DISNEY", "YOUTUBE", "ICLOUD", "AMAZON PRIME")),
    ("rideshare", ("UBER", "LYFT")),
    ("phone", ("VERIZON", "T-MOBILE", "AT&T", "MINT MOBILE")),
    ("insurance", ("GEICO", "PROGRESSIVE", "STATE FARM", "USAA")),
)


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().upper()


def _utilities_for(state: str, city: str) -> tuple[str, str] | None:
    state = (state or "").upper()[:2]
    low = (city or "").lower()
    for (st, needle), pair in _UTILITIES:
        if st == state and needle in low:
            return pair
    return _STATE_DEFAULT.get(state)


def _is_utility(desc: str) -> bool:
    up = _norm(desc)
    return any(token in up for token in _UTILITY_TOKENS)


def _is_gas(desc: str) -> bool:
    up = _norm(desc)
    return any(token in up for token in _GAS_TOKENS)


_RENT_TOKENS = (
    "RENT",
    "MGMT",
    "MANAGEMENT",
    "APARTMENTS",
    "APARTMENT",
    "PROPERTY",
    "PROPERTIES",
    "LEASING",
    "RESIDENTIAL",
    "REALTY",
    "HOUSING",
)


def _category_of(desc: str) -> str | None:
    up = _norm(desc)
    for name, tokens in _CATEGORY_TOKENS:
        if any(token in up for token in tokens):
            return name
    if _is_utility(up):
        return "utility"
    if any(token in up for token in _RENT_TOKENS):
        return "rent"
    return None


# ---------------------------------------------------------------------------
# 流水行访问
# ---------------------------------------------------------------------------

_AMOUNT_KEYS = ("amount", "withdrawal", "deposit", "credit", "debit")
_DESC_KEYS = ("desc", "description", "detail", "memo")


def _row_amount(row: Mapping[str, Any]) -> float | None:
    for key in _AMOUNT_KEYS:
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.replace("$", "").replace(",", "").strip())
            except ValueError:
                continue
    return None


def _set_amount(row: dict[str, Any], value: float) -> None:
    for key in _AMOUNT_KEYS:
        if key in row:
            current = row[key]
            row[key] = round(value, 2) if isinstance(current, float) else (
                f"{value:.2f}" if isinstance(current, str) else round(value, 2)
            )
            return
    row["amount"] = round(value, 2)


def _row_desc(row: Mapping[str, Any]) -> str:
    for key in _DESC_KEYS:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _set_desc(row: dict[str, Any], text: str) -> None:
    for key in _DESC_KEYS:
        if key in row:
            row[key] = text
            return
    row["desc"] = text


def _iter_arrays(data: Any) -> Iterable[list[dict[str, Any]]]:
    """找出所有「流水数组」：元素是对象、且带描述 + 金额键。"""
    if isinstance(data, list):
        if data and all(isinstance(item, dict) for item in data):
            if any(_row_amount(item) is not None for item in data) and any(_row_desc(item) for item in data):
                yield data
                return
        for item in data:
            yield from _iter_arrays(item)
        return
    if isinstance(data, dict):
        for value in data.values():
            yield from _iter_arrays(value)


def _iter_rows(data: Any) -> Iterable[dict[str, Any]]:
    for array in _iter_arrays(data):
        for row in array:
            yield row


# ---------------------------------------------------------------------------
# 三个修复
# ---------------------------------------------------------------------------


def fix_utilities(data: dict[str, Any], *, state: str, city: str) -> int:
    """把跨区的电/燃气公司换成档案所在地真正的供应商，返回改了几行。"""
    pair = _utilities_for(state, city)
    if pair is None:
        return 0
    electric, gas = pair
    changed = 0
    for row in _iter_rows(data):
        desc = _row_desc(row)
        if not desc or not _is_utility(desc):
            continue
        want = gas if _is_gas(desc) else electric
        if want in _norm(desc):
            continue
        _set_desc(row, want)
        changed += 1
    return changed


def _swap_for(category: str, state: str) -> tuple[str, ...]:
    return _REGIONAL_SWAPS.get((category, state.upper()[:2])) or _DEFAULT_SWAP.get(category) or ()


def _place_tail(desc: str) -> str:
    """从原描述里取尾部的「城市 州」（没传 city 时用它保地点）。"""
    match = re.search(r"([A-Z][A-Za-z.'\- ]{1,22}?)\s+([A-Z]{2})$", desc.strip())
    if not match:
        return ""
    return f"{match.group(1).strip().upper()} {match.group(2)}"


def fix_chains(data: dict[str, Any], *, state: str, city: str = "") -> int:
    """把不在本州经营的连锁换成同地区的替代品，返回改了几行。

    换名时**整条重写**：`{新品牌} {门店号} {城市 州}`。不要拿原描述做字符串替换，
    否则会留下 `TRADER JOE'S RALPHS KROGER` 这种把旧品牌当城市名的垃圾。
    """
    state = state.upper()[:2]
    if not state:
        return 0
    changed = 0
    for row in _iter_rows(data):
        desc = _row_desc(row)
        up = _norm(desc)
        if not up:
            continue
        for chain, allowed in _CHAIN_STATES.items():
            if chain not in up or state in allowed:
                continue
            category = _category_of(desc) or "dining"
            options = _swap_for(category, state)
            if not options:
                break
            pick = options[sum(map(ord, up)) % len(options)]
            store = re.search(r"\b(\d{3,5})\b", desc)
            parts = [pick]
            if store:
                parts.append(store.group(1))
            place = f"{city.upper()} {state}" if city else _place_tail(desc)
            if place:
                parts.append(place)
            _set_desc(row, " ".join(parts))
            changed += 1
            break
    return changed


def top_up_to_envelope(
    data: dict[str, Any],
    env: Mapping[str, Any],
    *,
    seed: str = "",
    state: str = "",
    city: str = "",
) -> dict[str, int]:
    """把低于信封下限的类目补到区间内（改已有行优先，必要时补一张该有的票）。

    模型常见毛病是「类目漏了」或「金额偏小」：11 月只有电费没有燃气、加油只有五六笔
    小额。这里按信封下限补齐，单笔仍受 `CATEGORY_RANGES` 约束；公用事业缺一家的，
    就把该州另一家（燃气）补上——真账单本来就是电、气各一张。
    """
    from . import envelope as envelope_mod

    counts = {"scaled": 0, "added": 0}
    totals = envelope_mod._realized(_iter_rows(data))
    for category, bucket in env.items():
        low, high = int(bucket[0]), int(bucket[1])
        actual = totals.get(envelope_mod._canonical(category), 0)
        if not low or actual >= low * 0.9:
            continue
        rows = [
            row
            for row in _iter_rows(data)
            if envelope_mod._canonical(_category_of(_row_desc(row)) or "") == envelope_mod._canonical(category)
            and (_row_amount(row) or 0) < 0
        ]
        lo_range, hi_range = range_for(category)
        deficit = low * 1.05 - actual
        # 多跑几轮：每轮给每行留一点余量地抬，直到补够或所有行都顶到单笔上限。
        for _ in range(6):
            if deficit <= 0:
                break
            progressed = False
            for row in rows:
                if deficit <= 0:
                    break
                amount = abs(float(_row_amount(row) or 0.0))
                room = hi_range - amount
                if room <= 1.0:
                    continue
                lift = min(deficit / 100.0 / max(len(rows), 1), room)
                if lift <= 0.5:
                    continue
                _set_amount(row, -(round(amount + lift, 2)))
                deficit -= int(round(lift * 100))
                counts["scaled"] += 1
                progressed = True
            if not progressed:
                break
        # 高频小额类目：抬到顶还不够就是「这个月少跑了几趟」，补一两笔新消费更真实。
        if category in {"fuel", "groceries", "dining", "shopping"} and deficit > 0:
            arrays = list(_iter_arrays(data))
            if arrays and rows:
                rng = random.Random(hashlib.sha256(f"{seed}|{category}|topup".encode()).hexdigest())
                while deficit > 0 and counts["added"] < 3:
                    base = rows[rng.randrange(len(rows))]
                    amount = round(rng.uniform(lo_range, min(hi_range, deficit / 100.0)), 2)
                    if amount < lo_range:
                        break
                    day = rng.randrange(3, 27)
                    arrays[0].append(
                        {
                            "date": f"{base.get('date', '')[:3]} {day}",
                            "desc": _row_desc(base),
                            "amount": -amount,
                            "type": base.get("type", "Card"),
                        }
                    )
                    deficit -= int(round(amount * 100))
                    counts["added"] += 1

        # 公用事业：缺一家就补另一家（电/气各一张）
        if category == "utilities" and deficit > 0:
            pair = _utilities_for(state, city)
            present_words = {word for row in rows for word in re.findall(r"[A-Z]+", _norm(_row_desc(row)))}
            missing = ""
            if pair:
                for name in pair:
                    need = [word for word in re.findall(r"[A-Z]+", _norm(name)) if len(word) > 1]
                    if not all(word in present_words for word in need[:2]):
                        missing = name
                        break
            if missing:
                # 给「另一家公用事业」留位置：已有那张先压到 75% 上限，再按余额补
                for row in rows:
                    amount_now = abs(float(_row_amount(row) or 0.0))
                    cap = hi_range * 0.75
                    if amount_now > cap:
                        _set_amount(row, -round(cap, 2))
                        deficit += int(round((amount_now - cap) * 100))
                want = min(max(deficit / 100.0, lo_range * 1.6), hi_range * 0.6)
                amount = round(want, 2)
                day = str(rows[0].get("date") or "").strip() if rows else ""
                row = {
                    "date": day or "15",
                    "desc": missing,
                    "amount": -amount,
                    "type": "ACH",
                }
                arrays = list(_iter_arrays(data))
                if arrays:
                    arrays[0].append(row)
                deficit -= int(round(amount * 100))
                counts["added"] += 1
    if counts["scaled"] or counts["added"]:
        _rescale_amounts(data)
        sort_rows(data)
    return counts


def _rescale_amounts(data: dict[str, Any]) -> None:
    """按新的流水合计算回期末与合计，保持「期初 + 流水 = 期末」。"""
    rows = list(_iter_rows(data))
    total = 0.0
    for row in rows:
        amount = _row_amount(row)
        if amount is not None:
            total += amount
    opening = None
    for key in ("beginning_balance", "begin_balance", "opening_balance", "beginningBalanceCents"):
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            opening = float(value)
            break
    if opening is None:
        return
    closing = round(opening + total, 2)
    for key in ("current_balance", "ending_balance", "end_balance", "closing_balance", "statement_balance"):
        if key in data:
            data[key] = closing
    summary = data.get("summary")
    if isinstance(summary, dict):
        for key in ("beginning_balance", "begin_balance"):
            if isinstance(summary.get(key), (int, float)):
                summary[key] = opening
        for key in ("ending_balance", "end_balance", "closing_balance"):
            if isinstance(summary.get(key), (int, float)):
                summary[key] = closing
    deposits = sum(a for _, a in ((r, _row_amount(r)) for r in rows) if a and a > 0)
    withdrawals = sum(-a for _, a in ((r, _row_amount(r)) for r in rows) if a and a < 0)
    for key in ("total_deposits", "total_credits", "deposits_total"):
        if key in data:
            data[key] = round(deposits, 2)
    for key in ("total_withdrawals", "total_debits", "withdrawals_total"):
        if key in data:
            data[key] = round(withdrawals, 2)


def fix_amounts(data: dict[str, Any], *, severity: float = 0.35) -> int:
    """把明显离谱的金额拉回该品类的真实档位，返回改了几行。

    容忍度是**不对称**的：下限松（小额小票很常见），上限紧——`hi/0.35` 会让
    $222 的 Subway 混过去，所以上界只用 ``hi*1.5``。``severity`` 仍控制下界。
    """
    changed = 0
    for row in _iter_rows(data):
        desc = _row_desc(row)
        amount = _row_amount(row)
        if not desc or amount is None:
            continue
        category = _category_of(desc)
        if category is None or category not in CATEGORY_RANGES:
            continue
        lo, hi = CATEGORY_RANGES[category]
        abs_amount = abs(amount)
        if lo * severity <= abs_amount <= hi * 1.5:
            continue
        # 用行内容做确定性取值，落在区间中部，重跑不会跳来跳去。
        span = hi - lo
        target = lo + (abs_amount * 7919 + sum(map(ord, _norm(desc)))) % int(span * 100) / 100.0
        new_value = -target if amount < 0 else target
        _set_amount(row, new_value)
        changed += 1
    if changed:
        _rescale_amounts(data)
    return changed


#: 只在线上的商户：真账单不会给它们带持卡人所在城市。
_ONLINE_TOKENS = (
    "NETFLIX",
    "SPOTIFY",
    "HULU",
    "APPLE.COM",
    "ICLOUD",
    "ADOBE",
    "DISNEY",
    "YOUTUBE",
    "AMAZON PRIME",
    "OPENAI",
    "GITHUB",
    "NOTION",
    "DROPBOX",
    "GOOGLE ONE",
    "MICROSOFT",
    "AUDIBLE",
    "GOODRX",
    "AMAZON.COM",
    "AMZN Mktp",
    "EBAY",
    "ETSY",
    "PATREON",
    "SUBSTACK",
    "DUOLINGO",
    "NYTIMES",
    "WASHINGTON POST",
    "CHEGG",
    "COURSERA",
    "VERIZON WIRELESS",
    "T-MOBILE",
    "MINT MOBILE",
)


#: 这些词不是城市名，剥尾巴时保留。
_NOT_CITY = frozenset({"USA", "US", "INC", "LLC", "LTD", "COM", "PAY", "PMT", "AUTOPAY"})


def _drop_place_tail(desc: str) -> str:
    """去掉描述末尾的「城市 州」或「城市, 州」，但不碰 USA/INC 这类词。"""
    tokens = desc.split()
    if len(tokens) >= 2 and re.fullmatch(r"[A-Z]{2}", tokens[-1] or ""):
        tokens = tokens[:-1]
    if len(tokens) >= 2:
        candidate = tokens[-1].strip(",.")
        if candidate and candidate not in _NOT_CITY and re.fullmatch(r"[A-Z][A-Za-z.'-]*", candidate):
            if len(tokens) >= 2:
                tokens = tokens[:-1]
    return " ".join(tokens)


def _trim_to_brand(desc: str) -> str:
    """把线上商户剪到「品牌 + 门店号」为止，后面跟的城市/州一律丢掉。

    `AUDIBLE TEMPLE CITY CA` → `AUDIBLE`；`GOODRX 2548 TEMPLE CITY CA` → `GOODRX 2548`。
    按品牌词定位而不是按尾词剥，才不会把「TEMPLE CITY」这种双词城市剥成 `TEMPLE`。
    """
    tokens = desc.split()
    keys = [re.sub(r"[^A-Z0-9]", "", token.upper()) for token in tokens]
    brand_end = None
    for brand in _ONLINE_TOKENS:
        words = [re.sub(r"[^A-Z0-9]", "", part.upper()) for part in brand.split() if part.strip()]
        if not words:
            continue
        for index in range(len(keys) - len(words) + 1):
            window = keys[index:index + len(words)]
            if window[:-1] == words[:-1] and window[-1].startswith(words[-1]):
                end = index + len(words) - 1
                if brand_end is None or end < brand_end:
                    brand_end = end
                break
    if brand_end is None:
        return desc
    kept = tokens[: brand_end + 1]
    # 品牌后面紧跟的门店号（`GOODRX 2548`）或 `USA` 保留，城市/州一律不要。
    if brand_end + 1 < len(tokens):
        nxt = tokens[brand_end + 1]
        if re.fullmatch(r"\d{3,5}", nxt or "") or re.sub(r"[^A-Z]", "", nxt.upper()) in {"USA", "US"}:
            kept.append(nxt)
    return " ".join(kept)


def fix_online_places(data: dict[str, Any]) -> int:
    """把线上订阅行尾巴上的城市/州去掉，返回改了几行。

    `NETFLIX.COM AUSTIN TX` 是模型把「订阅也算本地消费」带出来的；真账单里
    线上商户只写 `NETFLIX.COM`（少数写 `NETFLIX.COM 866-579-7172`）。
    """
    changed = 0
    for row in _iter_rows(data):
        desc = _row_desc(row)
        up = _norm(desc)
        if not up or not any(token in up for token in _ONLINE_TOKENS):
            continue
        stripped = _trim_to_brand(desc)
        stripped = re.sub(r"\s{2,}", " ", stripped).strip()
        if stripped and stripped != desc:
            _set_desc(row, stripped)
            changed += 1
    return changed


def dedupe_utilities(data: dict[str, Any], *, state: str, city: str) -> int:
    """同月同一家公用事业只留一笔（留金额最大的那张），返回删了几行。

    生活模型经常给「电费 + 电费 + 电费」三笔（不同供应商或同一家重复），
    真账单一个月只会有一张电费单。
    """
    pair = _utilities_for(state, city)
    if pair is None:
        return 0
    known = {pair[0], pair[1]}
    for array in list(_iter_arrays(data)):
        keep: dict[str, int] = {}
        drop: list[int] = []
        for index, row in enumerate(array):
            desc = _norm(_row_desc(row))
            if not desc or not _is_utility(desc):
                continue
            vendor = next((name for name in known if name in desc), None)
            if vendor is None:
                continue
            amount = abs(_row_amount(row) or 0.0)
            if vendor in keep:
                first = keep[vendor]
                if amount > abs(_row_amount(array[first]) or 0.0):
                    drop.append(first)
                    keep[vendor] = index
                else:
                    drop.append(index)
            else:
                keep[vendor] = index
        for index in sorted(set(drop), reverse=True):
            del array[index]
        if drop:
            _rescale_amounts(data)
            return len(set(drop))
    return 0


_RENT_TOKENS = ("RENT", "MGMT", "MANAGEMENT", "PROPERTY", "APARTMENTS", "RESIDENTIAL", "HOUSING", "LEASING", "REALTY")


def parse_range(value: Any) -> tuple[float, float] | None:
    """把 ``"9000-14000"`` / ``"$2,600–$3,200"`` / ``12000`` 解析成区间（美元）。"""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        low = float(value)
        return (low * 0.85, low * 1.15)
    text = str(value)
    numbers = [float(item.replace(",", "")) for item in re.findall(r"\d[\d,]*(?:\.\d+)?", text)]
    if not numbers:
        return None
    if len(numbers) == 1:
        return (numbers[0] * 0.85, numbers[0] * 1.15)
    low, high = min(numbers[:2]), max(numbers[:2])
    return (low, high)


def _is_rent(desc: str) -> bool:
    up = _norm(desc)
    return any(token in up for token in _RENT_TOKENS)


def fix_budget(
    data: dict[str, Any],
    *,
    income_hint: Any = None,
    rent_hint: Any = None,
) -> int:
    """按档案里的收支档把月收入与房租拉回合理区间，返回改了几行。

    档案写了「月入 9000–14000、房租 2600–3200」，结果账单上房租 $275、收入 $275×4，
    这是自相矛盾的——不是数据格式问题，是金额量级问题。这里做确定性对齐：
    房租只留一笔并落到档位中点；收入行按比例放大到档位中点。
    """
    changed = 0
    rent_range = parse_range(rent_hint)
    income_range = parse_range(income_hint)

    for array in list(_iter_arrays(data)):
        rent_rows = [row for row in array if _is_rent(_row_desc(row))]
        if rent_range and rent_rows:
            target = round(sum(rent_range) / 2, 2)
            keep = max(rent_rows, key=lambda row: abs(_row_amount(row) or 0.0))
            for row in rent_rows:
                if row is not keep:
                    array.remove(row)
                    changed += 1
            amount = _row_amount(keep) or -target
            sign = -1.0 if amount < 0 else 1.0
            if abs(abs(amount) - target) > max(60.0, target * 0.25):
                _set_amount(keep, sign * target)
                changed += 1

        if income_range:
            income_rows = [row for row in array if (_row_amount(row) or 0.0) > 0]
            total = sum(_row_amount(row) or 0.0 for row in income_rows)
            low, high = income_range
            if income_rows and not (low * 0.8 <= total <= high * 1.2):
                target_total = (low + high) / 2
                factor = target_total / total if total > 0 else 1.0
                for row in income_rows:
                    amount = _row_amount(row) or 0.0
                    _set_amount(row, amount * factor)
                changed += len(income_rows)
    if changed:
        _rescale_amounts(data)
    return changed


_MONTHS = {name: index for index, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1
)}


def _row_sort_key(row: Mapping[str, Any]) -> tuple[int, int]:
    """`"Nov 26"` / `"11/26"` / `"2026-11-26"` 都能排的键。"""
    text = str(row.get("date") or row.get("posted") or "")
    match = re.search(r"(\d{1,2})/(\d{1,2})", text)
    if match:
        return (int(match.group(1)), int(match.group(2)))
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        return (int(match.group(2)), int(match.group(3)))
    match = re.match(r"([A-Za-z]{3})[a-z]*\s+(\d{1,2})", text)
    if match:
        month = _MONTHS.get(match.group(1).title(), 0)
        return (month, int(match.group(2)))
    return (0, 0)


def sort_rows(data: dict[str, Any]) -> int:
    """把每个流水数组按日期排好，返回重排过的数组个数。"""
    moved = 0
    for array in _iter_arrays(data):
        ordered = sorted(array, key=_row_sort_key)
        if ordered != array:
            array[:] = ordered
            moved += 1
    return moved


def repair_month(
    data: dict[str, Any],
    *,
    state: str,
    city: str,
    income_hint: Any = None,
    rent_hint: Any = None,
    fix_money: bool = True,
) -> dict[str, int]:
    """把一个月的模板数据整体过一遍，返回每类修改的计数。"""
    report = {
        "utilities": fix_utilities(data, state=state, city=city),
        "duplicate_utilities": dedupe_utilities(data, state=state, city=city),
        "online_places": fix_online_places(data),
        "chains": fix_chains(data, state=state, city=city),
        "budget": fix_budget(data, income_hint=income_hint, rent_hint=rent_hint),
        "amounts": fix_amounts(data) if fix_money else 0,
        "sorted": sort_rows(data),
    }
    return report


_OPENING_KEYS = ("beginning_balance", "begin_balance", "opening_balance")
_CLOSING_KEYS = ("current_balance", "ending_balance", "end_balance", "closing_balance", "statement_balance")


def _opening_of(data: Mapping[str, Any]) -> float | None:
    for key in _OPENING_KEYS:
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    summary = data.get("summary")
    if isinstance(summary, dict):
        for key in ("beginning_balance", "begin_balance"):
            value = summary.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
    return None


def _set_opening(data: dict[str, Any], value: float) -> None:
    for key in _OPENING_KEYS:
        if key in data:
            data[key] = round(value, 2)
            break
    summary = data.get("summary")
    if isinstance(summary, dict):
        for key in ("beginning_balance", "begin_balance"):
            if isinstance(summary.get(key), (int, float)):
                summary[key] = round(value, 2)


def month_totals(data: Mapping[str, Any]) -> tuple[float, float]:
    """返回 ``(期初, 期末)``；期末按期初 + 流水重算，不信任文件里印的数。"""
    opening = _opening_of(data) or 0.0
    total = 0.0
    for row in _iter_rows(data):
        amount = _row_amount(row)
        if amount is not None:
            total += amount
    return round(opening, 2), round(opening + total, 2)


def rechain(months: Sequence[dict[str, Any]]) -> int:
    """把一批月份重新接成一条余额链，返回改动过的月份数。

    收尾阶段会改金额（把 `$4` 的加油拉回 `$40`），这时上个月的期末就变了，
    下个月的期初还停在收尾前算出来的数 —— 跨月就对不上（实测差 25 美元）。
    这里逐月把「上期末」写成「下期初」，再按新流水重算期末。
    """
    changed = 0
    previous: float | None = None
    for data in months:
        if not isinstance(data, dict):
            continue
        opening = _opening_of(data)
        if previous is not None and opening is not None and abs(opening - previous) > 0.005:
            _set_opening(data, previous)
            _rescale_amounts(data)
            changed += 1
        elif previous is not None and opening is None:
            _set_opening(data, previous)
            _rescale_amounts(data)
            changed += 1
        _, closing = month_totals(data)
        previous = closing
    return changed
