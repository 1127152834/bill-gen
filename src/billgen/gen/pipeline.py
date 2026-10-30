"""账单生成流水线：Node LangGraph 版本的 Python 移植。

节点顺序与 ``flow/packages/core/src/graph/build.ts`` 完全一致::

    normalize → planResearch → research → lifeModel → schedule
              → descriptors → validate → audit(最多回退 1 轮) → finalize

设计约定（对应 docs/DECISIONS.md）：

* **硬卡只卡机器能判定的东西**（决策 13）：账本算术、期间、行数区间、占位词。
  这些失败只写进该月的 ``ok`` / ``warnings``，不抛异常、不连坐其它月份。
* **AI 审计只写 warnings**：``audit`` 的 finding 无论如何都不改 ``ok``，最多按
  规定回退一轮重跑 lifeModel，然后照常交付。
* **调研类别没有白名单**（决策 14）：提示词只给示例，代码也不校验类别。
* 账本（``LedgerDocument``）完全由 Python 算出来，lifeModel 只提供生活参数。

和 TS 版的差异都写在各函数 docstring 里，汇总见 ``DIFFERENCES``。
"""

from __future__ import annotations

import calendar
import hashlib
import json
import random
import re
import tempfile
from collections.abc import Iterable, Mapping, MutableSequence, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..delivery import merge_entry
from ..synth import bridge, monthpack
from ..synth.adapters import binding_for
from ..synth.ledger import (
    AccountRef,
    BankIdentity,
    Holder,
    LedgerDocument,
    LedgerRow,
    Period,
)
from ..synth.money import aba_routing, account_number, digits
from . import envelope, seasonal
from . import prompts as P
from .llm import LlmBadJson, LlmError
from .tavily import TavilyError, TavilyQuotaExceeded

try:  # jobs.py 由别人维护；缺失时保留一个同名异常，方便测试与降级。
    from ..jobs import JobCancelled
except ImportError:  # pragma: no cover - 只在 jobs.py 还没落地时走到
    class JobCancelled(Exception):  # type: ignore[no-redef]
        """任务被取消（jobs.JobCancelled 的兜底定义）。"""


#: 与 TS 版对齐的差异清单，测试和交付报告都会念一遍。
DIFFERENCES: tuple[str, ...] = (
    "JSON 顶层：Node 允许数组，Python 的 json_chat 只接受对象，"
    "所以 queries/descriptors/findings 各包一层对象（提示词与 schema 同步改）。",
    "调度器：TS 有 credit 账户的双账户联动、5 轮透支修复；Python 只做 checking，"
    "透支改为一次性抬升 openingBalanceCents（确定性、可复现）。",
    "税务日：TS 从 calendar.ts 读完整税表；Python 只保留 4/6/9/1 月 15 日四个估算税日。",
    "商户描述：Node 的 descriptors 节点在 assemble 阶段套模板；Python 在 "
    "descriptors 节点后立即套用，validate/audit 看到的就是最终描述文本。",
    "审计表：Node 用完整 Statement 渲染；Python 用同一份流水拼紧凑文本，"
    "避免为了实现 Statement schema 再造一套模型。",
    "月结自检：额外接受可选的 monthClosings（整数分，提示词不要求模型输出），"
    "用来做算术硬卡的测试钩子；真实链路只信账本重算。",
    "AI 审计失败（LlmError）只记 warning，不阻断交付；Node 会把错误抛给图。",
    "随机源：同一个 seed + 同一模板 + 同一个月列表 → 同一份流水（TS 用 uuid 作默认 seed）。",
    "生活模型：TS 两轮不合格抛 LifeModelError；Python 退到确定性兜底模型并记 warning，"
    "整批不中断（算术硬卡仍会把对应月份标成 ok:false）。",
    "地理位置：TS 读 metros.json / state-tax.json；Python 内置常用 metro + 州级税率回退，"
    "避免把数据文件复制进包。",
    "校验规则按 TS 的意图收敛：R4 只看固定账单（收入天然波动）、R9 只要求 POS 有事实来源、"
    "R7 跳过线上消费（线上订单本来就没有门店城市）。",
)


# ---- 对外数据结构 --------------------------------------------------------

@dataclass(slots=True)
class MonthOutcome:
    """一个月的交付结果。"""

    month: str
    data: dict
    ok: bool
    report: dict
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class GenResult:
    """整批（1/3/6 个月）的交付结果。"""

    template: str
    months: list[MonthOutcome]
    warnings: list[str]
    audit: dict
    usage: dict
    out_dir: str = ""


class LifeModelError(ValueError):
    """lifeModel 连续两轮都没给出可用的模型。"""


# ---- 小工具 --------------------------------------------------------------

_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")
_BAD_CHARS_RE = re.compile(r"[^A-Z0-9 #*.,/&'\-{}]")
_PRICE_RE = re.compile(r"\$\s?([0-9]{1,4})(?:\.([0-9]{2}))?")

#: 常见连锁店；用来决定要不要给 {STORE}/{CITY}。
_CHAINS = {
    "walmart", "target", "costco", "kroger", "safeway", "aldi", "trader joe",
    "whole foods", "starbucks", "mcdonald", "chipotle", "subway", "cvs",
    "walgreens", "home depot", "lowe", "best buy", "amazon", "netflix",
    "spotify", "shell", "chevron", "exxon", "circle k", "7-eleven", "publix",
    "h-e-b", "heb", "wegmans", "albertsons", "dollar general",
}

#: 类别 → 默认单笔金额区间（分）；只用于兜底，不进提示词。
_CATEGORY_TICKETS: dict[str, tuple[int, int]] = {
    "groceries": (1800, 9800),
    "dining": (1400, 6200),
    "fuel": (3200, 7200),
    "healthcare": (1200, 8500),
    "transit": (250, 4200),
    "charity": (1000, 5000),
    "utilities": (4500, 14000),
    "subscriptions": (999, 2299),
    "insurance": (6500, 18000),
    "rent": (95000, 240000),
    "shopping": (1200, 9000),
    "pets": (1500, 7500),
}

_MENU_ENDINGS = (0, 25, 49, 50, 75, 95, 99)

#: 城市 → (metro 名, 综合销售税率, 生活成本档位)。缺省走州级估算。
_METROS: dict[tuple[str, str], tuple[str, float, str]] = {
    ("austin", "TX"): ("Austin-Round Rock", 0.0825, "mid"),
    ("houston", "TX"): ("Houston-The Woodlands", 0.0825, "mid"),
    ("dallas", "TX"): ("Dallas-Fort Worth", 0.0825, "mid"),
    ("san antonio", "TX"): ("San Antonio", 0.0825, "low"),
    ("new york", "NY"): ("New York-Newark", 0.08875, "high"),
    ("brooklyn", "NY"): ("New York-Newark", 0.08875, "high"),
    ("chicago", "IL"): ("Chicago-Naperville", 0.1025, "mid"),
    ("los angeles", "CA"): ("Los Angeles-Long Beach", 0.095, "high"),
    ("san francisco", "CA"): ("San Francisco-Oakland", 0.08625, "high"),
    ("san diego", "CA"): ("San Diego", 0.0775, "high"),
    ("seattle", "WA"): ("Seattle-Tacoma", 0.1025, "high"),
    ("portland", "OR"): ("Portland-Vancouver", 0.0, "mid"),
    ("denver", "CO"): ("Denver-Aurora", 0.081, "mid"),
    ("phoenix", "AZ"): ("Phoenix-Mesa", 0.086, "mid"),
    ("atlanta", "GA"): ("Atlanta-Sandy Springs", 0.089, "mid"),
    ("miami", "FL"): ("Miami-Fort Lauderdale", 0.07, "mid"),
    ("tampa", "FL"): ("Tampa-St. Petersburg", 0.075, "mid"),
    ("boston", "MA"): ("Boston-Cambridge", 0.0625, "high"),
    ("philadelphia", "PA"): ("Philadelphia-Camden", 0.08, "mid"),
    ("detroit", "MI"): ("Detroit-Warren", 0.06, "low"),
    ("columbus", "OH"): ("Columbus", 0.0725, "low"),
    ("nashville", "TN"): ("Nashville-Davidson", 0.0925, "mid"),
    ("charlotte", "NC"): ("Charlotte-Concord", 0.0725, "mid"),
    ("las vegas", "NV"): ("Las Vegas-Henderson", 0.0825, "mid"),
    ("minneapolis", "MN"): ("Minneapolis-St. Paul", 0.0875, "mid"),
    ("salt lake city", "UT"): ("Salt Lake City", 0.071, "mid"),
    ("pittsburgh", "PA"): ("Pittsburgh", 0.07, "low"),
    ("cleveland", "OH"): ("Cleveland-Elyria", 0.08, "low"),
    ("kansas city", "MO"): ("Kansas City", 0.0875, "low"),
}


class _NullCtx:
    """``ctx=None`` 时的空实现：测试可以直接传 None。"""

    def log(self, message: str, percent: int | None = None) -> None:  # noqa: D401
        return None

    def raise_if_cancelled(self) -> None:
        return None


def _null_ctx() -> _NullCtx:
    return _NullCtx()


@dataclass(slots=True)
class _Usage:
    """本次生成里的调用计数；token 数最后从 LlmClient.usage 读。"""

    llm_calls: int = 0
    tavily_queries: int = 0


def _as_ctx(ctx: Any) -> Any:
    return ctx if ctx is not None else _null_ctx()


def _log(ctx: Any, message: str, percent: int | None = None) -> None:
    """兼容三种 ctx：JobContext（percent 仅关键字）、位置参数、只收 message。"""
    ctx = _as_ctx(ctx)
    if percent is None:
        try:
            ctx.log(message)
            return
        except TypeError:  # 只接受 (message, percent) 的假 ctx
            ctx.log(message, None)
            return
    try:
        ctx.log(message, percent=percent)
    except TypeError:
        try:
            ctx.log(message, percent)
        except TypeError:
            ctx.log(message)


def _check(ctx: Any) -> None:
    _as_ctx(ctx).raise_if_cancelled()


def _count_llm(usage: _Usage) -> None:
    usage.llm_calls += 1


def _count_tavily(usage: _Usage) -> None:
    usage.tavily_queries += 1


def _rng(seed: str, *parts: str) -> random.Random:
    """按 seed + 用途派生确定性随机源（TS 是 ``rng.fork(name)``）。"""
    payload = ":".join([seed, *parts]).encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


# ---- 商户名：类别池 + 调研标题清洗 ---------------------------------------

#: 品牌名长度上限。Chase 描述列 38 字符里还要塞日期/城市/卡尾号，
#: 品牌独占 18 已是上限（``WALMART SUPERCENTER`` 那种长名在池子里被截短）。
MERCHANT_MAX_CHARS = 18

#: 调研标题里出现、但绝不是商户名的词。事实（fact）只用来定金额档位与城市/州，
#: 标题片段（``GAS PRICES BY STA``、``AVERAGE GROCER``）一律当噪声洗掉。
_RESEARCH_NOISE: tuple[str, ...] = (
    "pos purchase",
    "debit card purchase",
    "card purchase",
    "purchase",
    "checkcard",
    "average",
    "avg",
    "prices",
    "price",
    "summary",
    "restaurants",
    "restaurant",
    "cost of living",
    "how much",
    "cheapest",
    "cheap",
    "deals",
    "coupons",
    "locations",
    "near me",
    "reviews",
    "rankings",
    "ranking",
    "breakdown",
    "compared",
    "guide",
    "by station",
    "station",
    "stations",
    "per gallon",
    # 类别名 / 账单说明里的通用词：真账单印的是「谁收了钱」，不是「这类花费叫什么」。
    "grocery",
    "groceries",
    "gasoline",
    "prescription",
    "prescriptions",
    "costs",
    "cost",
    "bills",
    "bill",
    "membership",
    "memberships",
    "expense",
    "expenses",
    "insurance",
    "utilities",
    "utility",
    "subscription",
    "subscriptions",
    "service fee",
)

#: 账单模板自己会印在描述前面的字头（``POS PURCHASE``/``CHECKCARD {MMDD}``）。
#: 适配器已经印过一遍，pipeline 再拼一次就是 ``POS PURCHASE POS PURCHASE``。
_HOUSE_PREFIXES: tuple[str, ...] = (
    "POS PURCHASE",
    "DEBIT CARD PURCHASE",
    "CARD PURCHASE",
    "CHECKCARD",
    "RECURRING PAYMENT",
    "PURCHASE",
)

#: 卡行会自带门店号的类别（真账单里 ``WHOLEFDS MKT 10427`` 就是这么来的）。
_STATE_ABBREV: frozenset[str] = frozenset(
    """AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO
    MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY""".split()
)

#: 模板里这些词是账单自己的语法（``PPD``/``WEB`` 之类入账方式），不算模型编的说明。
_KEEP_EXTRA_TOKENS: frozenset[str] = frozenset(
    {"USATAXPYMT", "TAX", "PPD", "WEB", "ACH", "PMT", "PAYMT", "DES", "ID", "CO", "INDN", "REF"}
)

_STORE_CATEGORIES: frozenset[str] = frozenset(
    {"groceries", "fuel", "dining", "pharmacy", "retail", "hardware", "pets", "atm"}
)

#: 适配器自己会把「城市 州」拼到**卡行**末尾（再自带一份就成 ``AUSTIN TX AUSTIN TX``）。
_CARD_ADDS_PLACE: frozenset[str] = frozenset({"wellsfargo", "capitalone", "sofi"})
#: 适配器自己就把 ATM 的城市州印上去了。
_ATM_ADDS_PLACE: frozenset[str] = frozenset({"mercury"})
#: 适配器自己会印 ATM 标签；其余的模板得把 ``ATM WITHDRAWAL`` 留在描述里。
_ATM_PRINTS_LABEL: frozenset[str] = frozenset({"chase", "wellsfargo", "usbank", "mercury"})


def _template_family(norm: Mapping[str, Any] | None) -> str:
    """``chase-personal`` → ``chase``：用来判断这个版式的适配器补不补地点/标签。"""
    return str((norm or {}).get("template") or "").split("-")[0].strip().lower()


def _seed_of(norm: Mapping[str, Any] | None, seed: str = "") -> str:
    if seed:
        return str(seed)
    return str((norm or {}).get("seed") or "")


def _strip_house_prefix(text: str) -> str:
    """剥掉适配器已经印过的字头（含 ``CHECKCARD {MMDD}`` 与叠了两层的情况）。"""
    out = re.sub(r"\s+", " ", str(text or "")).strip()
    for _ in range(4):  # 叠几层剥几层，最多防呆 4 层
        upper = out.upper()
        for prefix in _HOUSE_PREFIXES:
            if not upper.startswith(prefix):
                continue
            out = out[len(prefix) :]
            out = re.sub(r"^[\s:#*\-]+", "", out)
            out = re.sub(r"^\{MMDD\}\s*\d{0,4}\s*", "", out)
            out = re.sub(r"^\d{2}/\d{2}\s+", "", out)
            out = re.sub(r"\s+", " ", out).strip()
            break
        else:
            break
    return out


def _scrub_noise(text: str) -> str:
    """大写、单空格，并洗掉叠字头与调研标题词（尾部虚词也一起去掉）。"""
    out = _strip_house_prefix(_BAD_CHARS_RE.sub(" ", str(text or "").upper()))
    for word in _RESEARCH_NOISE:
        out = re.sub(rf"\b{re.escape(word.upper())}\b", " ", out)
    out = re.sub(r"\b(19|20)\d{2}\b", " ", out)  # 搜索标题里的年份
    for _ in range(3):
        trimmed = re.sub(r"\s*\b(IN|NEAR|AT|FOR|BY|AND|THE|OF|TO|VS|ON|WITH|OR)\b\s*$", " ", out)
        if trimmed == out:
            break
        out = trimmed
    out = re.sub(r"^\s*\b(IN|AT|ON|NEAR|THE|OF|FOR|BY|AND|OR|WITH)\b\s+", "", out)
    out = re.sub(r"\s+", " ", out).strip(" .,-/&'*#")
    # ``CHECKCARD 0904 …`` 剥完字头还会剩一串门店号；留着它当商户名很假。
    if len(out) > 8:
        out = re.sub(r"^\d{3,4}\s+", "", out)
    return out


def _clean_merchant(text: str, *, limit: int | None = MERCHANT_MAX_CHARS) -> str:
    """把一段文本洗成能当商户名的短语；``limit=None`` 时只洗不截。"""
    out = _scrub_noise(text)
    if limit and len(out) > limit:
        cut = out[:limit]
        if " " in cut[4:]:
            cut = cut[: cut.rindex(" ")]
        out = cut.strip()
    # 截断会把 "… COSTS IN | AUSTIN" 切成挂在尾巴上的虚词，再收一次尾。
    for _ in range(3):
        trimmed = re.sub(r"\s*\b(A|AN|IN|AT|ON|OF|TO|FOR|BY|AND|OR|THE|WITH|NEAR|VS)\b\s*$", "", out)
        if trimmed == out:
            break
        out = trimmed
    # 描述里常见的尾巴是门店所在州（"AUSTIN TX"）：州名不是商户的一部分。
    tail = re.search(r"\s+([A-Z]{2})$", out)
    if tail and tail.group(1) in _STATE_ABBREV:
        out = out[: tail.start()]
    out = out.strip(" .,-/&'*#")
    # "GAS" / "ATM" 这种两三个字母的残片当不了商户名，交给品牌池。
    return out if any(len(token) >= 4 for token in out.split()) else ""


def _merchant_for(
    category: str, *, key: str, seed: str, fallback: str = "", state: str = ""
) -> str:
    """按类别从品牌池挑商户名；池里没有就清洗兜底文本（决策 14：类别不设白名单）。"""
    pool = P.merchant_pool(category, state=state) or P.merchant_pool(category)
    rng = _rng(seed, "merchant", str(key or category or "merchant"))
    if pool:
        return rng.choice(list(pool))
    cleaned = _clean_merchant(fallback)
    # 洗成 "GAS" / "AUSTIN" / "GROCERY" 这种单词残片宁可换品牌：真商户名很少是
    # 一个光秃秃的类别词或城市名。
    if len(cleaned) >= 4 and len(cleaned.split()) >= 2:
        return cleaned
    # 类别既不在池里、兜底文本又被洗空了：拿零售池（真品牌）顶上，绝不留占位词。
    return rng.choice(list(P.merchant_pool("retail")))


def _store_number(key: str, seed: str) -> str:
    """连锁店门店号（``WHOLEFDS MKT 10427``）；真账单的卡行几乎都带一个。"""
    return f"{_rng(seed, 'store', str(key or 'store')).randint(1000, 9999)}"



def _parse_month(month: str) -> tuple[int, int]:
    match = _MONTH_RE.match(month or "")
    if not match:
        raise ValueError(f"月份格式必须是 YYYY-MM：{month!r}")
    return int(match.group(1)), int(match.group(2))


def _last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _month_start(month: str) -> date:
    year, mon = _parse_month(month)
    return date(year, mon, 1)


def _month_end(month: str) -> date:
    year, mon = _parse_month(month)
    return date(year, mon, _last_day(year, mon))


def _parse_months(months: Sequence[str]) -> list[str]:
    """校验月份列表：非空、格式对、连续、不重复。"""
    if not months:
        raise ValueError("months 不能为空")
    out: list[str] = []
    for raw in months:
        if not isinstance(raw, str):
            raise ValueError(f"月份必须是字符串：{raw!r}")
        _parse_month(raw)
        if raw in out:
            raise ValueError(f"月份重复：{raw}")
        out.append(raw)
    ordered = sorted(out)
    if ordered != list(out):
        raise ValueError(f"月份必须按时间升序：{list(out)}")
    for prev, nxt in zip(ordered, ordered[1:], strict=False):
        year, mon = _parse_month(prev)
        expect = f"{year + 1:04d}-01" if mon == 12 else f"{year:04d}-{mon + 1:02d}"
        if nxt != expect:
            raise ValueError(f"月份必须连续：{prev} 之后应为 {expect}，实际 {nxt}")
    return list(out)


def _parse_iso(value: Any) -> date:
    match = _ISO_RE.match(str(value or ""))
    if not match:
        raise ValueError(f"日期格式必须是 YYYY-MM-DD：{value!r}")
    return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _month_of(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _shift_days(value: date, days: int) -> date:
    return value + timedelta(days=days)


def _next_business_day(value: date) -> date:
    """周末顺延到周一；节假日表在 Python 版里省略（见 DIFFERENCES）。"""
    while value.weekday() >= 5:
        value = value + timedelta(days=1)
    return value


def _clamp(value: date, start: date, end: date) -> date:
    if value < start:
        return start
    if value > end:
        return end
    return value


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _min_max(value: Any) -> tuple[int, int]:
    """``{"min":a,"max":b}`` / 整数 / 列表 → 两个整数。"""
    if isinstance(value, Mapping):
        low = _int(value.get("min", value.get("minCents", 0)), 0)
        high = _int(value.get("max", value.get("maxCents", low)), low)
        return low, max(low, high)
    if isinstance(value, (list, tuple)) and value:
        low = _int(value[0])
        high = _int(value[1]) if len(value) > 1 else low
        return low, max(low, high)
    number = _int(value)
    return number, number


# ---- 归一化：normalize 节点 ---------------------------------------------

def node_normalize(
    profile: Mapping[str, Any],
    *,
    template: str,
    months: Sequence[str],
    seed: str = "",
) -> dict:
    """把请求压成后续节点共用的 ``norm`` 字典。

    TS 的 ``normalizeRequest`` 还要处理 credit 周期、真实银行周期（决策 6）；
    Python 版只有 checking，所以周期就是自然月，首尾由月份列表决定。
    """
    if not isinstance(profile, Mapping) or not profile:
        raise ValueError("profile 不能为空")
    month_list = _parse_months(months)
    business = P.is_business_profile(profile)

    address = profile.get("address") if isinstance(profile.get("address"), Mapping) else {}
    city = str(address.get("city") or profile.get("city") or "Austin").strip() or "Austin"
    state = str(address.get("state") or profile.get("state") or "TX").strip().upper()[:2] or "TX"
    metro, tax_rate, col_tier = _METROS.get(
        (city.lower(), state), (f"{city} Metro", 0.07, "mid")
    )
    if isinstance(profile.get("location"), Mapping):
        loc = profile["location"]
        city = str(loc.get("city") or city)
        state = str(loc.get("state") or state).upper()[:2]
        metro = str(loc.get("metro") or metro)
        tax_rate = float(loc.get("combinedSalesTaxRate") or tax_rate)
        col_tier = str(loc.get("colTier") or col_tier)

    kind = "business_checking" if business else "checking"
    try:
        binding = binding_for(template)
        if kind not in binding.accounts:
            kind = sorted(binding.accounts)[0]
    except Exception:  # 模板没 binding 时 generate 已经拦过，这里只兜底
        kind = "checking"

    # 预算信封：按收入档算出每月各类目该花多少，再按月做季节缩放。
    base_env = envelope.envelope(profile, {"location": {"col_tier": col_tier}}, rent_hint=profile.get("rent_hint"))
    month_env = {}
    for month in month_list:
        year_i, mon_i = _parse_month(month)
        month_env[month] = {
            name: [int(lo), int(hi)]
            for name, (lo, hi) in seasonal.envelope_for_month(base_env, year_i, mon_i).items()
        }

    row_range = profile.get("rowRange") if isinstance(profile.get("rowRange"), Mapping) else {}
    row_min = max(6, _int(row_range.get("min"), 16))
    row_max = max(row_min, _int(row_range.get("max"), 26))

    last4 = digits(f"{seed}:last4", 4)
    if last4 == "0000":
        last4 = "1042"

    start = _month_start(month_list[0])
    end = _month_end(month_list[-1])
    tax_dates = _estimated_tax_dates(start, end)

    return {
        "template": template,
        "envelope": {name: list(pair) for name, pair in base_env.items()},
        "month_envelopes": month_env,
        "seasonal": {
            month: seasonal.seasonal_notes(_parse_month(month)[0], _parse_month(month)[1])
            for month in month_list
        },
        "subject_type": "business" if business else "personal",
        "seed": seed,
        "months": month_list,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "location": {
            "city": city,
            "state": state,
            "metro": metro,
            "combined_sales_tax_rate": tax_rate,
            "col_tier": col_tier,
        },
        "accounts": {"checking": {"min": row_min, "max": row_max}},
        "row_range": {"min": row_min, "max": row_max},
        "account_kind": kind,
        "account_last4": last4,
        "estimated_tax_dates": [item.isoformat() for item in tax_dates],
        "bank_name": str(profile.get("bankName") or profile.get("bank_name") or ""),
        "opening_hint_cents": _int(profile.get("openingBalanceCents"), 0) or None,
    }


def _estimated_tax_dates(start: date, end: date) -> list[date]:
    """四个估算税日：4/15、6/15、9/15、次年 1/15。"""
    dates: list[date] = []
    for year in range(start.year - 1, end.year + 1):
        if start <= date(year, 4, 15) <= end:
            dates.append(date(year, 4, 15))
        if start <= date(year, 6, 15) <= end:
            dates.append(date(year, 6, 15))
        if start <= date(year, 9, 15) <= end:
            dates.append(date(year, 9, 15))
        if start <= date(year + 1, 1, 15) <= end:
            dates.append(date(year + 1, 1, 15))
    return sorted(set(dates))


# ---- 调研：planResearch 节点 --------------------------------------------

def _default_queries(norm: Mapping[str, Any]) -> list[dict]:
    """模型没给计划（或全被丢掉）时的确定性兜底计划。"""
    loc = norm.get("location") or {}
    city = loc.get("city") or "Austin"
    code = str(loc.get("state") or "TX").upper()
    name = P.state_name(code)
    year = (_month_start(norm["months"][0])).year
    return [
        {"id": "q1", "category": "groceries", "query": f"grocery prices {city} {code} {year}", "scope": "metro"},
        {"id": "q2", "category": "fuel", "query": f"gas prices {city} {code}", "scope": "metro"},
        {"id": "q3", "category": "rent", "query": f"apartments for rent {city} {code} monthly", "scope": "metro"},
        {"id": "q4", "category": "dining", "query": f"restaurant prices {city} {code}", "scope": "metro"},
        {"id": "q5", "category": "utilities", "query": f"electricity rate {name} residential", "scope": "state"},
        {"id": "q6", "category": "insurance", "query": f"car insurance monthly premium {name}", "scope": "state"},
        {"id": "q7", "category": "subscriptions", "query": "netflix standard plan price", "scope": "national"},
        {"id": "q8", "category": "subscriptions", "query": "monthly cell phone plan unlimited price", "scope": "national"},
    ]


def node_plan_research(
    norm: Mapping[str, Any],
    profile: Mapping[str, Any],
    *,
    llm: Any,
    usage: _Usage,
    warnings: MutableSequence[str] | None = None,
    log: Any = None,
) -> list[dict]:
    """调研计划；模型失手时用 :func:`_default_queries` 继续。"""
    warnings = warnings if warnings is not None else []
    messages = P.plan_research_messages(profile, norm)
    queries: list[dict] = []
    try:
        data = _json_object(
            llm, messages, schema=P.RESEARCH_PLAN_SCHEMA, usage=usage, max_tokens=2000
        )
        raw = data.get("queries")
        if isinstance(raw, list):
            queries = _clean_queries(raw, norm)
    except LlmError as exc:
        warnings.append(f"调研计划生成失败，已用内置计划：{type(exc).__name__}: {exc}")
    if not queries:
        queries = _default_queries(norm)
        if not warnings or "内置计划" not in warnings[-1]:
            warnings.append("调研计划为空，已用内置计划")
    _log(log, f"planResearch：{len(queries)} 条查询", 12)
    return queries[: P.MAX_QUERIES]


def _clean_queries(raw: Iterable[Any], norm: Mapping[str, Any]) -> list[dict]:
    """去重 id、补 city/state 文本、类别小写；不做类别白名单（决策 14）。"""
    loc = norm.get("location") or {}
    city = str(loc.get("city") or "")
    code = str(loc.get("state") or "").upper()
    state = P.state_name(code)
    metro_hint = str(loc.get("metro") or city)
    cleaned: list[dict] = []
    seen_ids: set[str] = set()
    seen_groups: set[tuple[str, str]] = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, Mapping):
            continue
        query = str(item.get("query") or "").strip()
        scope = str(item.get("scope") or "metro").lower()
        if scope not in {"metro", "state", "national"}:
            scope = "metro"
        category = str(item.get("category") or "other").strip().lower()[:32] or "other"
        if not query:
            continue
        if scope == "metro" and city and city.lower() not in query.lower():
            query = f"{query} {city} {code}".strip()
        elif scope == "state" and state and state.lower() not in query.lower():
            query = f"{query} {state}".strip()
        elif scope == "national":
            query = query.replace(f"{city} {code}", "").replace(city, "").strip() or query
        group = "national" if scope == "national" else ("state" if scope == "state" else f"metro:{metro_hint}")
        key = (group, category)
        if key in seen_groups:
            continue
        seen_groups.add(key)
        qid = str(item.get("id") or f"q{index}").strip() or f"q{index}"
        if qid in seen_ids:
            suffix = 2
            while f"{qid}-{suffix}" in seen_ids:
                suffix += 1
            qid = f"{qid}-{suffix}"
        seen_ids.add(qid)
        cleaned.append({"id": qid, "category": category, "query": query, "scope": scope})
        if len(cleaned) >= P.MAX_QUERIES:
            break
    return cleaned


# ---- 调研：research 节点 ------------------------------------------------

def node_research(
    norm: Mapping[str, Any],
    queries: Sequence[Mapping[str, Any]],
    *,
    tavily: Any,
    usage: _Usage,
    warnings: MutableSequence[str] | None = None,
    log: Any = None,
) -> list[dict]:
    """逐条检索并把结果压成事实；额度不足时降级继续（不抛异常）。"""
    warnings = warnings if warnings is not None else []
    facts: list[dict] = []
    seen: set[str] = set()
    for query in queries:
        try:
            results = tavily.search(str(query.get("query") or ""), max_results=2)
            _count_tavily(usage)  # 只统计真正发出去的检索
        except TavilyQuotaExceeded as exc:
            warnings.append(f"Tavily 额度用尽，调研降级继续：{exc}")
            break
        except TavilyError as exc:
            warnings.append(
                f"category {query.get('category')} dropped: {type(exc).__name__}"
            )
            continue
        except Exception as exc:  # 假客户端/网络层抛别的异常也不该拖垮整批
            warnings.append(f"category {query.get('category')} dropped: {type(exc).__name__}")
            continue
        for result in results or []:
            fact = _fact_from_result(query, result, norm, len(facts) + 1)
            if fact and fact["name"].lower() not in seen:
                seen.add(fact["name"].lower())
                facts.append(fact)
    _log(log, f"research：{len(facts)} 条事实", 32)
    return facts


def _clean_name(title: str, url: str) -> str:
    text = re.sub(r"\s+", " ", title or "").strip()
    for sep in (" | ", " – ", " — ", " - "):
        if sep in text:
            text = text.split(sep)[0].strip()
    text = re.sub(r"^(best|cheapest|top)\s+", "", text, flags=re.I)
    text = re.sub(r"\s+(prices?|deals?|coupons?)$", "", text, flags=re.I)
    if not text:
        host = re.sub(r"^www\.", "", (url or "").split("//")[-1].split("/")[0])
        text = host.split(".")[0] if host else "Local Merchant"
    return text[:60].strip() or "Local Merchant"


def _fact_from_result(
    query: Mapping[str, Any],
    result: Any,
    norm: Mapping[str, Any],
    index: int,
) -> dict | None:
    url = str(getattr(result, "url", "") or "").strip()
    title = str(getattr(result, "title", "") or "").strip()
    content = str(getattr(result, "content", "") or "")
    if not url:
        return None
    name = _clean_name(title, url)
    scope = str(query.get("scope") or "metro")
    loc = norm.get("location") or {}
    if scope == "national":
        city, state = "", "US"
    else:
        city = str(loc.get("city") or "")
        state = str(loc.get("state") or "").upper()
    category = str(query.get("category") or "other").lower()
    chain = name.lower() in _CHAINS or "locations" in content.lower()
    prices: list[dict] = []
    for match in _PRICE_RE.finditer(content):
        dollars = int(match.group(1))
        cents = int(match.group(2) or 0)
        value = dollars * 100 + cents
        if 50 <= value <= 500_000:
            prices.append({"item": "listed price", "priceCents": value, "sourceUrl": url})
        if len(prices) >= 2:
            break
    return {
        "id": f"f{index}",
        "name": name,
        "chain": chain,
        "category": category,
        "city": city,
        "state": state,
        "sourceUrl": url,
        "priceItems": prices,
    }


# ---- lifeModel 节点 ------------------------------------------------------

def node_life_model(
    norm: Mapping[str, Any],
    profile: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
    *,
    llm: Any,
    usage: _Usage,
    warnings: MutableSequence[str] | None = None,
    feedback: Sequence[str] | None = None,
    log: Any = None,
) -> dict:
    """生成生活模型；两轮都不合格时退到确定性兜底模型（不炸整批）。"""
    warnings = warnings if warnings is not None else []
    messages = P.life_model_messages(profile, norm, facts, feedback=feedback)
    try:
        data = _json_object(
            llm,
            messages,
            schema=P.FINANCIAL_MODEL_SCHEMA,
            usage=usage,
            max_tokens=P.LIFE_MODEL_MAX_TOKENS,
        )
    except LlmError as exc:
        warnings.append(f"lifeModel 生成失败，已用兜底模型继续：{type(exc).__name__}: {exc}")
        _log(log, "lifeModel：兜底模型", 45)
        return _fallback_model(norm, facts, {})
    model = _normalize_model(data, norm, seed=str(norm.get("seed") or ""))
    problems = _model_issues(model, facts, norm)
    if problems:
        patch_messages = list(messages) + [P.life_model_patch_message(problems, model)]
        try:
            patched = _json_object(
                llm,
                patch_messages,
                schema=P.MODEL_PATCH_SCHEMA,
                usage=usage,
                max_tokens=P.PATCH_MAX_TOKENS,
            )
            model = _apply_patch(model, patched.get("patch"))
        except LlmError as exc:
            warnings.append(f"lifeModel 补丁轮失败：{type(exc).__name__}: {exc}")
        problems = _model_issues(model, facts, norm)
    if problems:
        warnings.append("lifeModel 自检未过，已用兜底模型继续：" + "；".join(problems[:3]))
        model = _fallback_model(norm, facts, model)
    _log(log, "lifeModel：生活模型就绪", 45)
    return model


def _json_object(
    llm: Any,
    messages: list[dict],
    *,
    schema: dict | None,
    usage: _Usage,
    max_tokens: int,
    retries: int = 1,
    temperature: float = 0.2,
) -> dict:
    """``json_chat`` + 一次本地重试；``LlmBadJson`` 之外的原样上抛。"""
    attempt = 0
    current = messages
    while True:
        try:
            data = llm.json_chat(
                current, schema=schema, temperature=temperature, max_tokens=max_tokens
            )
        except LlmBadJson as exc:
            if attempt >= retries:
                raise
            attempt += 1
            current = list(current) + [
                {
                    "role": "user",
                    "content": f"上一次输出不是合法 JSON 对象（{exc}），请只输出 JSON 对象。",
                }
            ]
            continue
        _count_llm(usage)
        if isinstance(data, dict):
            return data
        raise LlmBadJson("模型返回的顶层不是 JSON 对象")


def _cadence(raw: Any, seed: str) -> dict:
    if isinstance(raw, Mapping):
        kind = str(raw.get("kind") or raw.get("type") or "monthly").lower()
        out = {"kind": kind if kind in {"weekly", "biweekly", "monthly", "irregular"} else "monthly"}
        if raw.get("day") is not None:
            out["day"] = min(28, max(1, _int(raw.get("day"), 12)))
        if raw.get("weekday") is not None:
            out["weekday"] = min(6, max(0, _int(raw.get("weekday"), 4)))
        if raw.get("perMonth") is not None:
            out["perMonth"] = raw["perMonth"]
        return out
    text = str(raw or "monthly").lower()
    return {"kind": text if text in {"weekly", "biweekly", "monthly", "irregular"} else "monthly", "day": 12}


def _normalize_model(
    data: Mapping[str, Any],
    norm: Mapping[str, Any],
    *,
    seed: str = "",
) -> dict:
    """把模型输出补成调度器能吃的形状；缺的字段给确定性默认值。"""
    model: dict[str, Any] = {}

    streams: list[dict] = []
    for index, item in enumerate(data.get("incomeStreams") or [], start=1):
        if not isinstance(item, Mapping):
            continue
        channel = str(item.get("channel") or "ach_client").lower()
        if channel not in {"ach_client", "zelle", "mobile_deposit", "card_sales"}:
            channel = "ach_client"
        low, high = _min_max(item.get("amountCents"))
        if high <= 0:
            low, high = 320_000, 460_000
        seasonality = item.get("seasonality")
        if not isinstance(seasonality, list) or len(seasonality) != 12:
            seasonality = [1.0] * 12
        streams.append(
            {
                "id": str(item.get("id") or f"inc{index}"),
                "label": str(item.get("label") or "Client payment"),
                "payerName": str(item.get("payerName") or "Client")[:16],
                "channel": channel,
                "cadence": _cadence(item.get("cadence"), seed),
                "amountCents": {"min": low, "max": high},
                "seasonality": [float(value or 1.0) for value in seasonality],
            }
        )
    if not streams:
        streams.append(
            {
                "id": "inc1",
                "label": "Client invoices",
                "payerName": "Ridgeway Studio",
                "channel": "ach_client",
                "cadence": {"kind": "monthly", "day": 8},
                "amountCents": {"min": 320_000, "max": 460_000},
                "seasonality": [1.0] * 12,
            }
        )
    model["incomeStreams"] = streams

    recurring: list[dict] = []
    for index, item in enumerate(data.get("recurring") or [], start=1):
        if not isinstance(item, Mapping):
            continue
        amount = item.get("amount")
        if isinstance(amount, Mapping):
            kind = str(amount.get("kind") or "fixed").lower()
            if kind == "seasonal":
                low, high = _min_max({"min": amount.get("minCents"), "max": amount.get("maxCents")})
                amount_norm = {
                    "kind": "seasonal",
                    "minCents": max(1, low),
                    "maxCents": max(max(1, low), high),
                    "peakMonths": [int(m) for m in (amount.get("peakMonths") or []) if 1 <= _int(m) <= 12],
                }
            else:
                cents = _int(amount.get("cents"), _int(amount.get("amountCents"), 0))
                amount_norm = {"kind": "fixed", "cents": max(1, cents)}
        else:
            amount_norm = {"kind": "fixed", "cents": max(1, _int(amount))}
        rule = item.get("dayRule")
        if isinstance(rule, Mapping):
            rule_kind = str(rule.get("kind") or "dayOfMonth")
            day_rule = {"kind": rule_kind if rule_kind in {"dayOfMonth", "taxDates", "biweekly"} else "dayOfMonth"}
            if day_rule["kind"] == "dayOfMonth":
                day_rule["day"] = min(31, max(1, _int(rule.get("day"), 5)))
            elif day_rule["kind"] == "biweekly":
                day_rule["weekday"] = min(6, max(0, _int(rule.get("weekday"), 4)))
                day_rule["anchor"] = str(rule.get("anchor") or norm["period"]["start"])
        else:
            day_rule = {"kind": "dayOfMonth", "day": 5}
        txn = str(item.get("txnType") or "ACH").upper()
        recurring.append(
            {
                "id": str(item.get("id") or f"rec{index}"),
                "label": str(item.get("label") or "Bill"),
                "kind": str(item.get("kind") or "other").lower(),
                "dayRule": day_rule,
                "amount": amount_norm,
                "account": "checking",
                "priority": min(5, max(1, _int(item.get("priority"), 3))),
                "factId": str(item.get("factId") or ""),
                "txnType": txn if txn in {"ACH", "POS", "CHECK", "ZELLE"} else "ACH",
            }
        )
    model["recurring"] = recurring

    pool: list[dict] = []
    for item in data.get("discretionaryPool") or []:
        if not isinstance(item, Mapping):
            continue
        low, high = _min_max(item.get("ticketCents"))
        if high <= 0:
            low, high = 900, 6400
        if high < int(low * 1.5):
            high = int(low * 1.5) + 137
        pricing = str(item.get("pricing") or "basket").lower()
        if pricing not in {"basket", "fixed_menu", "fuel"}:
            pricing = "basket"
        per_month = item.get("perMonth")
        location = str(item.get("location") or "local").lower()
        if location not in {"local", "online", "travel"}:
            location = "local"
        pool.append(
            {
                "factId": str(item.get("factId") or ""),
                "weight": max(0.1, float(item.get("weight") or 1.0)),
                "perMonth": per_month if isinstance(per_month, Mapping) else _min_max(per_month or 1),
                "ticketCents": {"min": max(1, low), "max": max(1, high)},
                "pricing": pricing,
                "unitPriceCents": _int(item.get("unitPriceCents"), 339) or 339,
                "taxable": bool(item.get("taxable", False)),
                "tip": bool(item.get("tip", False)),
                "account": "checking",
                "weekendBias": min(1.0, max(0.0, float(item.get("weekendBias") or 0.0))),
                "location": location,
            }
        )
    model["discretionaryPool"] = pool

    atm = data.get("atm")
    if isinstance(atm, Mapping):
        choices = [max(2000, _int(value)) for value in (atm.get("amountChoicesCents") or []) if _int(value) > 0]
        choices = [value - value % 2000 or 2000 for value in choices][:5] or [6000, 10000, 20000]
        model["atm"] = {
            "perMonth": min(6, max(0, _int(atm.get("perMonth"), 1))),
            "amountChoicesCents": sorted(set(choices)),
            "feeCents": max(0, _int(atm.get("feeCents"), 350)),
            "waivedProbability": min(1.0, max(0.0, float(atm.get("waivedProbability") or 0.0))),
        }

    opening = _int(data.get("openingBalanceCents"), 0)
    if opening <= 0:
        opening = _rng(seed or str(norm.get("seed")), "opening").randint(240_000, 780_000)
    model["openingBalanceCents"] = opening

    closing = data.get("monthClosings")
    if isinstance(closing, Mapping):
        clean: dict[str, int] = {}
        for key, value in closing.items():
            try:
                clean[str(key)] = _int(value)
            except (TypeError, ValueError):
                continue
        model["monthClosings"] = clean
    return model


def _apply_patch(model: dict, patch: Any) -> dict:
    """数组整段替换、对象浅合并——和 TS 的 applyPatch 一致。"""
    if not isinstance(patch, Mapping):
        return model
    out = dict(model)
    merge_keys = {"atm"}
    for key, value in patch.items():
        if key in merge_keys and isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            merged = dict(out[key])
            merged.update(value)
            out[key] = merged
        else:
            out[key] = value
    return _normalize_model(out, {"period": {"start": "1970-01-01"}, "months": []}, seed="")


_PLACEHOLDER_WORDS = (
    "acme", "example", "sample", "fictional", "dummy", "test", "demo",
    "foo", "bar", "lorem", "xyz", "your company",
)


#: 同月只该出现一次的账单族：房租、电、气、水、网、手机、保险、车贷、税。
#: 键是「在 kind 或 label 里出现就算命中」的词，值是族名。
_BILL_FAMILY_WORDS = (
    # 先认具体的，再退到 kind="utility" 这种笼统分类：
    # 「Austin Water」和「So Cal Gas」都是 utility，但它们是两张不同的账单。
    ("rent", "rent"),
    ("mortgage", "rent"),
    ("housing", "rent"),
    ("landlord", "rent"),
    ("water", "water"),
    ("sewer", "water"),
    ("electric", "electric"),
    ("power", "electric"),
    ("gas", "gas"),
    ("internet", "internet"),
    ("broadband", "internet"),
    ("phone", "phone"),
    ("wireless", "phone"),
    ("insurance", "insurance"),
    ("auto loan", "loan"),
    ("car loan", "loan"),
    ("student loan", "loan"),
    ("tax", "tax"),
    ("utilit", "utility"),
)


def _bill_family(item: Mapping[str, Any]) -> str:
    """判断这笔固定账单属于哪个「族」；无法归类就返回唯一键（不参与去重）。

    真账单里一个月只有一笔电费、一笔房租；模型经常一口气给出两笔电费
    （Con Edison + PG&E）或「rent + housing」两条，交付前必须在调度层挡掉。
    """
    text = f"{item.get('kind') or ''} {item.get('label') or ''}".lower()
    for word, family in _BILL_FAMILY_WORDS:
        if word in text:
            return family
    return f"unique:{item.get('id') or id(item)}"


def _bill_weight(item: Mapping[str, Any]) -> tuple[int, int, int]:
    """同一族里保留哪一笔：先看有没有调研事实，再看金额大不大。"""
    amount = item.get("amount") or {}
    if str(amount.get("kind") or "fixed") == "seasonal":
        cents = int((int(amount.get("minCents") or 0) + int(amount.get("maxCents") or 0)) / 2)
    else:
        cents = int(amount.get("cents") or 0)
    return (1 if item.get("factId") else 0, cents, -int((item.get("dayRule") or {}).get("day") or 0))


def duplicate_bills(items: Sequence[Mapping[str, Any]]) -> list[tuple[str, list[str]]]:
    """返回 ``[(族名, [账单 id…]), …]``，每项都是同月出现了不止一次的账单族。"""
    seen: dict[str, list[str]] = {}
    for item in items:
        family = _bill_family(item)
        if family.startswith("unique:"):
            continue
        seen.setdefault(family, []).append(str(item.get("id") or item.get("label") or "?"))
    return [(family, ids) for family, ids in seen.items() if len(ids) > 1]


def _collapse_bills(items: Sequence[Mapping[str, Any]]) -> tuple[list[dict], list[str]]:
    """同族只留一笔（有事实优先、金额大的优先），返回 ``(保留, 被删的 id)``。"""
    best: dict[str, Mapping[str, Any]] = {}
    order: list[str] = []
    dropped: list[str] = []
    for item in items:
        family = _bill_family(item)
        current = best.get(family)
        if current is None:
            best[family] = item
            order.append(family)
            continue
        if _bill_weight(item) > _bill_weight(current):
            dropped.append(str(current.get("id") or current.get("label") or "?"))
            best[family] = item
        else:
            dropped.append(str(item.get("id") or item.get("label") or "?"))
    kept = [dict(best[family]) for family in order]
    return kept, dropped


def _model_issues(
    model: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
    norm: Mapping[str, Any],
) -> list[str]:
    """lifeModel 的机器检查；只报最关键的几条，避免补丁轮被淹没。"""
    issues: list[str] = []
    opening = _int(model.get("openingBalanceCents"), 0)
    if opening <= 0:
        issues.append("openingBalanceCents 必须是正整数")
    if not model.get("incomeStreams"):
        issues.append("incomeStreams 不能为空")
    for family, ids in duplicate_bills(model.get("recurring") or []):
        issues.append(
            f"recurring 里 {family} 这类账单出现了 {len(ids)} 笔（{', '.join(ids)}），同月同类只能一笔"
        )
    fact_ids = {str(fact.get("id")) for fact in facts}
    used: set[str] = set()
    for item in model.get("recurring") or []:
        fact_id = str(item.get("factId") or "")
        if fact_id and fact_id not in fact_ids:
            issues.append(f"recurring {item.get('id')} 引用了不存在的事实 {fact_id}")
        if fact_id and fact_id in used:
            issues.append(f"事实 {fact_id} 被多个项目重复使用")
        used.add(fact_id)
        if norm.get("subject_type") == "personal" and item.get("kind") == "payroll":
            issues.append("个人主体不能出现 payroll 项目")
        text = f"{item.get('label')} {item.get('id')}".lower()
        if any(word in text for word in _PLACEHOLDER_WORDS):
            issues.append(f"recurring {item.get('id')} 含占位词")
    for item in model.get("discretionaryPool") or []:
        fact_id = str(item.get("factId") or "")
        if fact_id and fact_id not in fact_ids:
            issues.append(f"discretionary 引用了不存在的事实 {fact_id}")
    for stream in model.get("incomeStreams") or []:
        text = f"{stream.get('label')} {stream.get('payerName')} {stream.get('id')}".lower()
        if any(word in text for word in _PLACEHOLDER_WORDS):
            issues.append(f"income {stream.get('id')} 含占位词")
        if norm.get("subject_type") == "personal" and any(
            word in text for word in ("salary", "wage", "payroll", "direct deposit")
        ):
            issues.append(f"income {stream.get('id')} 出现工资类描述")
    return issues


def _fallback_model(
    norm: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
    previous: Mapping[str, Any] | None = None,
) -> dict:
    """确定性兜底模型：收入覆盖房租+水电+订阅，其余钱花在调研到的事实上。"""
    seed = str(norm.get("seed") or "")
    rng = _rng(seed, "fallback")
    opening = int(previous.get("openingBalanceCents") or 0) if previous else 0
    if opening <= 0:
        opening = rng.randint(280_000, 640_000)
    housing = next(
        (fact for fact in facts if "rent" in str(fact.get("category") or "").lower()
         or "apartment" in str(fact.get("name") or "").lower()
         or "property" in str(fact.get("name") or "").lower()),
        None,
    )
    rent_cents = 168_500
    if housing:
        prices = [int(item.get("priceCents") or 0) for item in housing.get("priceItems") or []]
        plausible = [value for value in prices if 60_000 <= value <= 400_000]
        if plausible:
            rent_cents = plausible[0]
    recurring = [
        {
            "id": "rec-rent",
            "label": "Monthly rent",
            "kind": "rent",
            "dayRule": {"kind": "dayOfMonth", "day": 3},
            "amount": {"kind": "fixed", "cents": rent_cents},
            "account": "checking",
            "priority": 1,
            "factId": str(housing.get("id")) if housing else "",
            "txnType": "ACH",
        },
        {
            "id": "rec-util",
            "label": "Electric bill",
            "kind": "utility",
            "dayRule": {"kind": "dayOfMonth", "day": 14},
            "amount": {"kind": "seasonal", "minCents": 7200, "maxCents": 15800, "peakMonths": [7, 8, 9, 1]},
            "account": "checking",
            "priority": 2,
            "factId": "",
            "txnType": "ACH",
        },
        {
            "id": "rec-phone",
            "label": "Mobile phone plan",
            "kind": "utility",
            "dayRule": {"kind": "dayOfMonth", "day": 18},
            "amount": {"kind": "fixed", "cents": 6500},
            "account": "checking",
            "priority": 3,
            "factId": "",
            "txnType": "ACH",
        },
        {
            "id": "rec-stream",
            "label": "Streaming subscription",
            "kind": "subscription",
            "dayRule": {"kind": "dayOfMonth", "day": 21},
            "amount": {"kind": "fixed", "cents": 1799},
            "account": "checking",
            "priority": 3,
            "factId": "",
            "txnType": "ACH",
        },
    ]
    pool: list[dict] = []
    for fact in facts[:10]:
        category = str(fact.get("category") or "shopping").lower()
        low, high = _CATEGORY_TICKETS.get(category, (1200, 7200))
        pool.append(
            {
                "factId": str(fact.get("id") or ""),
                "weight": 1.0,
                "perMonth": {"min": 1, "max": 2},
                "ticketCents": {"min": low, "max": high},
                "pricing": "fuel" if category == "fuel" else "basket",
                "unitPriceCents": 339,
                "taxable": category in {"groceries", "fuel", "shopping"},
                "tip": category == "dining",
                "account": "checking",
                "weekendBias": 0.25 if category in {"dining", "shopping"} else 0.1,
                "location": "local",
            }
        )
    while len(pool) < 8:
        pool.append(
            {
                "factId": "",
                "weight": 1.0,
                "perMonth": {"min": 1, "max": 2},
                "ticketCents": {"min": 900, "max": 5400},
                "pricing": "basket",
                "unitPriceCents": 339,
                "taxable": False,
                "tip": False,
                "account": "checking",
                "weekendBias": 0.1,
                "location": "local",
            }
        )
    return {
        "incomeStreams": [
            {
                "id": "inc1",
                "label": "Client invoices",
                "payerName": "Ridgeway Studio",
                "channel": "ach_client",
                "cadence": {"kind": "monthly", "day": 6},
                "amountCents": {"min": 380_000, "max": 520_000},
                "seasonality": [1.0] * 12,
            }
        ],
        "recurring": recurring,
        "discretionaryPool": pool[:12],
        "atm": {
            "perMonth": 1,
            "amountChoicesCents": [6000, 10000, 20000],
            "feeCents": 350,
            "waivedProbability": 0.5,
        },
        "openingBalanceCents": opening,
    }


# ---- schedule 节点 -------------------------------------------------------

def node_schedule(
    norm: Mapping[str, Any],
    model: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
    *,
    seed: str = "",
    log: Any = None,
) -> list[dict]:
    """把生活模型铺成逐笔流水（不含最终描述文本，描述由 descriptors 决定）。"""
    fact_by_id = {str(fact.get("id")): fact for fact in facts}
    seed_key = _seed_of(norm, seed)
    rng = _rng(seed_key, "schedule")
    loc = norm.get("location") or {}
    rate = float(loc.get("combined_sales_tax_rate") or 0.0)
    rows: list[dict] = []
    _collapsed_recurring, _dropped_bills = _collapse_bills(model.get("recurring") or [])
    if _dropped_bills:
        _log(log, f"schedule：合并同类账单，丢弃 {len(_dropped_bills)} 笔（{', '.join(_dropped_bills[:3])}）", 58)

    for month in norm.get("months") or []:
        year, mon = _parse_month(month)
        start, end = _month_start(month), _month_end(month)
        month_rows: list[dict] = []

        for stream in model.get("incomeStreams") or []:
            key = str(stream.get("id") or "income")
            base = _merchant_for(
                "income", key=key, seed=seed_key, fallback=str(stream.get("payerName") or "")
            )
            for when in _income_dates(stream, year, mon, rng):
                amount = _income_amount(stream, mon, rng)
                fact = None
                month_rows.append(
                    _make_row(
                        _next_business_day(when),
                        "DEPOSIT",
                        "credit",
                        amount,
                        key=key,
                        fact=fact,
                        base=base,
                        memo="Client payment",
                        category="income",
                        rng=rng,
                        start=start,
                        end=end,
                        city="",
                        state="",
                    )
                )

        for item in _collapsed_recurring:
            fact = fact_by_id.get(str(item.get("factId") or ""))
            key = str(item.get("id") or "bill")
            category = str(item.get("kind") or "other")
            # 账单上的商户是「真品牌」，不是 lifeModel 随手起的 label；label 只当兜底。
            # 电/气/水/电信按州挑（Austin 的账户不该出现 Con Edison）。
            base = _merchant_for(
                category,
                key=key,
                seed=seed_key,
                fallback=str(item.get("label") or ""),
                state=str(loc.get("state") or ""),
            )
            for when in _recurring_dates(item, year, mon, norm):
                amount = _recurring_amount(item, mon, rng)
                txn = str(item.get("txnType") or "ACH").upper()
                when = _next_business_day(when) if txn in {"ACH", "ZELLE", "CHECK"} else when
                month_rows.append(
                    _make_row(
                        when,
                        txn if txn in {"ACH", "POS", "CHECK", "ZELLE"} else "ACH",
                        "debit",
                        amount,
                        key=key,
                        fact=fact,
                        base=base,
                        memo=f"{item.get('label')} payment",
                        category=category,
                        rng=rng,
                        start=start,
                        end=end,
                        # 银行卡付的固定账单也要有城市/州（R7：POS 行必须有地点）。
                        city=str(loc.get("city") or ""),
                        state=str(loc.get("state") or ""),
                    )
                )

        atm = model.get("atm") or {}
        for _ in range(int(atm.get("perMonth") or 0)):
            choices = atm.get("amountChoicesCents") or [6000]
            amount = int(rng.choice(list(choices)))
            when = date(year, mon, rng.randint(2, _last_day(year, mon)))
            month_rows.append(
                _make_row(
                    when,
                    "ATM",
                    "debit",
                    amount,
                    key="atm",
                    fact=None,
                    base=_merchant_for(
                        "atm", key=f"atm-{month}", seed=seed_key, fallback="ATM WITHDRAWAL"
                    ),
                    memo="Cash withdrawal",
                    category="cash",
                    rng=rng,
                    start=start,
                    end=end,
                    city=str(loc.get("city") or ""),
                    state=str(loc.get("state") or ""),
                )
            )
            if rng.random() > float(atm.get("waivedProbability") or 0):
                month_rows.append(
                    _make_row(
                        when,
                        "FEE",
                        "debit",
                        int(atm.get("feeCents") or 350),
                        key="fee-atm",
                        fact=None,
                        base="ATM FEE",
                        memo="ATM fee",
                        category="fee",
                        rng=rng,
                        start=start,
                        end=end,
                        posted_same_day=True,
                    )
                )

        target = rng.randint(int(norm["row_range"]["min"]), int(norm["row_range"]["max"]))
        pool = list(model.get("discretionaryPool") or [])
        need = target - len(month_rows)
        if pool and need > 0:
            weights = [max(0.1, float(item.get("weight") or 1.0)) for item in pool]
            for _ in range(need):
                item = rng.choices(pool, weights=weights, k=1)[0]
                fact = fact_by_id.get(str(item.get("factId") or ""))
                amount = _purchase_amount(item, rate, rng)
                when = _purchase_date(year, mon, item, rng)
                location = str(item.get("location") or "local")
                city = state = ""
                if location == "local":
                    city = str((fact or {}).get("city") or loc.get("city") or "")
                    state = str((fact or {}).get("state") or loc.get("state") or "")
                elif location == "travel":
                    city = "Denver"
                    state = "CO"
                key = str(item.get("factId") or "shop")
                # 事实只定城市/州与金额档位；商户名按类别从品牌池取（决策 14）。
                category = P.merchant_category_key(
                    str((fact or {}).get("category") or item.get("category") or "shopping")
                )
                # 事实的 name 是搜索标题（"Best Graphic Design Services"），
                # 永远不进商户名：类别不在池里就换零售品牌，也不拿标题凑。
                base = _merchant_for(category, key=key, seed=seed_key, state=state)
                memo = _clean_merchant(
                    str((fact or {}).get("category") or "Purchase"), limit=None
                ).title()
                month_rows.append(
                    _make_row(
                        when,
                        "POS",
                        "debit",
                        amount,
                        key=key,
                        fact=fact,
                        base=base,
                        memo=memo or "Purchase",
                        category=category,
                        rng=rng,
                        start=start,
                        end=end,
                        city=city,
                        state=state,
                        location=location,
                    )
                )

        rows.extend(month_rows)

    rows.sort(key=lambda row: (row["date"], row["posted"], row["amount_cents"]))
    _log(log, f"schedule：{len(rows)} 行", 58)
    return rows


def _income_dates(stream: Mapping[str, Any], year: int, month: int, rng: random.Random) -> list[date]:
    cadence = stream.get("cadence") or {}
    kind = str(cadence.get("kind") or "monthly")
    last = _last_day(year, month)
    if kind == "monthly":
        return [date(year, month, min(last, int(cadence.get("day") or 12)))]
    if kind == "weekly":
        weekday = int(cadence.get("weekday", 4))
        first = date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        days = list(range(1 + offset, last + 1, 7))
        return [date(year, month, day) for day in days]
    if kind == "biweekly":
        weekday = int(cadence.get("weekday", 4))
        first = date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return [date(year, month, day) for day in range(1 + offset, last + 1, 14)]
    raw = cadence.get("perMonth")
    if isinstance(raw, Mapping):
        count = rng.randint(_int(raw.get("min"), 1), _int(raw.get("max"), 1))
    else:
        count = _int(raw, 1)
    count = max(1, min(4, count))
    stamps = sorted(rng.sample(range(2, last + 1), k=min(count, max(1, last - 2))))
    return [date(year, month, stamp) for stamp in stamps]


def _income_amount(stream: Mapping[str, Any], month: int, rng: random.Random) -> int:
    low, high = _min_max(stream.get("amountCents"))
    base = rng.randint(max(1, low), max(max(1, low), high))
    seasonality = stream.get("seasonality") or [1.0] * 12
    factor = float(seasonality[(month - 1) % 12] or 1.0)
    return max(1, int(round(base * factor)))


def _recurring_dates(item: Mapping[str, Any], year: int, month: int, norm: Mapping[str, Any]) -> list[date]:
    rule = item.get("dayRule") or {}
    kind = str(rule.get("kind") or "dayOfMonth")
    last = _last_day(year, month)
    if kind == "taxDates":
        wanted = {
            value
            for value in norm.get("estimated_tax_dates") or []
            if value.startswith(f"{year:04d}-{month:02d}")
        }
        return [date.fromisoformat(value) for value in sorted(wanted)]
    if kind == "biweekly":
        anchor = date.fromisoformat(str(rule.get("anchor") or norm["period"]["start"]))
        first = date(year, month, 1)
        delta = (anchor - first).days
        offset = delta % 14
        return [date(year, month, day) for day in range(1 + offset, last + 1, 14)]
    return [date(year, month, min(last, max(1, int(rule.get("day") or 5))))]


def _recurring_amount(item: Mapping[str, Any], month: int, rng: random.Random) -> int:
    amount = item.get("amount") or {}
    if str(amount.get("kind") or "fixed") == "seasonal":
        low, high = _min_max({"min": amount.get("minCents"), "max": amount.get("maxCents")})
        peaks = {int(value) for value in amount.get("peakMonths") or []}
        if month in peaks:
            span = max(1, high - low)
            return max(1, int(round(high - span * rng.random() * 0.4)))
        span = max(1, high - low)
        return max(1, int(round(low + span * rng.random() * 0.6)))
    return max(1, _int(amount.get("cents"), 1000))


def _purchase_amount(item: Mapping[str, Any], tax_rate: float, rng: random.Random) -> int:
    low, high = _min_max(item.get("ticketCents"))
    pricing = str(item.get("pricing") or "basket")
    if pricing == "fuel":
        unit = int(item.get("unitPriceCents") or 339)
        gallons = rng.uniform(8.0, 16.0)
        cents = int(round(unit * gallons / 10))
    elif pricing == "fixed_menu":
        cents = rng.randint(low, max(low, high))
        hundreds = cents - cents % 100
        cents = hundreds + rng.choice(_MENU_ENDINGS)
    else:
        cents = rng.randint(low, max(low, high))
        if cents % 100 == 0:
            cents += rng.randint(1, 99)
    cents = max(1, cents)
    if item.get("taxable"):
        cents = int(round(cents * (1.0 + tax_rate)))
    if item.get("tip"):
        cents += int(round(cents * rng.uniform(0.15, 0.22)))
        if rng.random() < 0.5:
            cents += (100 - cents % 100) % 100
    return max(1, cents)


def _purchase_date(year: int, month: int, item: Mapping[str, Any], rng: random.Random) -> date:
    last = _last_day(year, month)
    day = rng.randint(1, last)
    when = date(year, month, day)
    if rng.random() < float(item.get("weekendBias") or 0) and when.weekday() < 5:
        when = _shift_days(when, 5 - when.weekday())
    return _clamp(when, date(year, month, 1), date(year, month, last))


def _make_row(
    when: date,
    txn_type: str,
    direction: str,
    amount: int,
    *,
    key: str,
    fact: Mapping[str, Any] | None,
    base: str,
    memo: str,
    category: str,
    rng: random.Random,
    start: date,
    end: date,
    city: str | None = None,
    state: str | None = None,
    location: str = "local",
    posted_same_day: bool = False,
) -> dict:
    when = _clamp(when, start, end)
    if posted_same_day or txn_type in {"ATM", "FEE"}:
        posted = when
    elif txn_type in {"POS", "REFUND"}:
        posted = _clamp(_shift_days(when, rng.randint(1, 2)), start, end)
    else:
        posted = when
    fact = fact or {}
    return {
        "date": when,
        "posted": posted,
        "type": txn_type,
        "direction": direction,
        "amount_cents": max(1, int(amount)),
        "merchant_key": key,
        "fact_id": str(fact.get("id") or ""),
        "category": category,
        "city": str(city if city is not None else fact.get("city") or ""),
        "state": str(state if state is not None else fact.get("state") or "").upper(),
        "base_description": str(base),
        "memo": str(memo),
        "location": location,
        "source": category,
    }


# ---- descriptors 节点 ----------------------------------------------------

def node_descriptors(
    norm: Mapping[str, Any],
    profile: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    facts: Sequence[Mapping[str, Any]],
    *,
    llm: Any,
    usage: _Usage,
    warnings: MutableSequence[str] | None = None,
    log: Any = None,
) -> dict[str, dict]:
    """一次调用拿到所有商户的描述模板；失败就退回确定性模板。"""
    warnings = warnings if warnings is not None else []
    fact_by_id = {str(fact.get("id")): fact for fact in facts}
    items: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        key = str(row.get("merchant_key") or "")
        # 收入方是人物/客户，不进商户描述表（TS 同样过滤 income stream）。
        if not key or key in seen or str(row.get("category")) == "income":
            continue
        seen.add(key)
        fact = fact_by_id.get(str(row.get("fact_id") or ""))
        items.append(
            {
                "merchantKey": key,
                "template": str(row.get("base_description") or ""),
                "memo": str(row.get("memo") or ""),
                "type": str(row.get("type") or ""),
                "fact": P.compact_fact(fact) if fact else None,
            }
        )
    descriptors: dict[str, dict] = {}
    if items:
        messages = P.descriptors_messages(profile, norm, items)
        try:
            data = _json_object(
                llm,
                messages,
                schema=P.DESCRIPTORS_SCHEMA,
                usage=usage,
                max_tokens=3000,
            )
            for item in data.get("descriptors") or []:
                if not isinstance(item, Mapping):
                    continue
                key = str(item.get("merchantKey") or "")
                if key:
                    descriptors[key] = {
                        "template": str(item.get("template") or ""),
                        "memo": str(item.get("memo") or ""),
                    }
        except LlmError as exc:
            warnings.append(f"商户描述生成失败，改用内置模板：{type(exc).__name__}: {exc}")
    for item in items:
        key = str(item["merchantKey"])
        entry = descriptors.get(key) or {}
        template = _sanitize_template(entry.get("template") or "", item.get("fact"))
        if not template:
            template = _default_template(item, norm)
        descriptors[key] = {
            "template": template,
            "memo": str(entry.get("memo") or item.get("memo") or "Purchase")[:40],
        }
    _log(log, f"descriptors：{len(descriptors)} 个模板", 66)
    return descriptors


def _default_template(item: Mapping[str, Any], norm: Mapping[str, Any]) -> str:
    """没有模型输出时的描述模板：只给商户名本体（字头与日期由账单自己印）。"""
    key = str(item.get("merchantKey") or "")
    category = P.merchant_category_key(str((item.get("fact") or {}).get("category") or ""))
    seed = _seed_of(norm)
    name = _clean_merchant(str(item.get("template") or ""), limit=None)
    if not name:
        name = _merchant_for(category or "retail", key=key, seed=seed)
    txn = str(item.get("type") or "POS").upper()
    if txn in {"POS", "ATM"}:
        return f"{name} {_store_number(key, seed)}".strip()
    if txn == "FEE":
        return name or "MONTHLY SVC FEE"
    return name or key.upper()


def _sanitize_template(template: str, fact: Any) -> str:
    """只留已知占位符、合法字符和大写；超长截断（TS 同样规则）。"""
    text = str(template or "").upper()
    if not text:
        return ""

    def replace(match: re.Match) -> str:
        name = match.group(1).strip().upper()
        if name in P.KNOWN_PLACEHOLDERS:
            return "{" + name + "}"
        return ""

    text = _PLACEHOLDER_RE.sub(replace, text)
    text = _BAD_CHARS_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > P.DESCRIPTOR_MAX_CHARS:
        text = text[: P.DESCRIPTOR_MAX_CHARS].rstrip()
    return text


def _render_template(
    template: str,
    row: Mapping[str, Any],
    fact: Mapping[str, Any] | None,
    norm: Mapping[str, Any],
    rng: random.Random,
) -> str:
    """把占位符换成人话；未知占位符直接删掉。"""
    fact = fact or {}
    when = row.get("date")
    if not isinstance(when, date):
        when = _parse_iso(when)
    store = str(fact.get("storeNumber") or "")
    if not store:
        # 门店号也该确定性：同一个商户在整个账本里始终是同一家店。
        store = _store_number(str(row.get("merchant_key") or ""), _seed_of(norm))
    values = {
        "STORE": store,
        "CITY": str(row.get("city") or "").upper(),
        "ST": str(row.get("state") or "").upper()[:2],
        "MMDD": f"{when.month:02d}{when.day:02d}",
        "REF": "".join(str(rng.randint(0, 9)) for _ in range(10)),
        "ID": "".join(str(rng.randint(0, 9)) for _ in range(4)),
        "LAST4": str(norm.get("account_last4") or "0000"),
    }

    def replace(match: re.Match) -> str:
        return values.get(match.group(1).strip().upper(), "")

    text = _PLACEHOLDER_RE.sub(replace, str(template or ""))
    return text.replace("{", "").replace("}", "")


def _merchant_display(row: Mapping[str, Any], *, seed: str = "") -> str:
    """商户名 = 品牌（+ 连锁店门店号）；描述、merchant 字段共用同一份。"""
    key = str(row.get("merchant_key") or "")
    brand = _clean_merchant(str(row.get("base_description") or ""), limit=None)
    if not brand:
        brand = _merchant_for(str(row.get("category") or "retail"), key=key, seed=seed)
    category = P.merchant_category_key(str(row.get("category") or ""))
    if category in _STORE_CATEGORIES:
        return f"{brand} {_store_number(key, seed)}".strip()
    return brand


def _place_of(row: Mapping[str, Any]) -> str:
    city = re.sub(r"\s+", " ", re.sub(r"[^A-Z ]", " ", str(row.get("city") or "").upper())).strip()
    state = str(row.get("state") or "").upper()[:2]
    if not city or not state or state == "US":
        return ""
    return f"{city} {state}"


def _final_description(
    row: Mapping[str, Any],
    text: str,
    norm: Mapping[str, Any],
    *,
    seed: str = "",
) -> str:
    """最终描述：品牌名与城市州由 pipeline 确定性拼，字头留给适配器。

    POS/ATM 不看模型模板（模型一自由发挥就会把调研标题或叠前缀写回来）；账单类行
    用模板，但品牌名必须在位、标题噪声先洗掉。
    """
    seed = _seed_of(norm, seed)
    txn = str(row.get("type") or "POS").upper()
    family = _template_family(norm)
    display = _merchant_display(row, seed=seed)
    if txn in {"POS", "ATM"}:
        parts = [display]
        place = _place_of(row)
        adds_place = family in (_ATM_ADDS_PLACE if txn == "ATM" else _CARD_ADDS_PLACE)
        if place and not adds_place:
            parts.append(place)
        if txn == "ATM" and family not in _ATM_PRINTS_LABEL:
            parts.insert(0, "ATM WITHDRAWAL")
        return _scrub_noise(" ".join(parts)) or _scrub_noise(display) or "PURCHASE"
    brand = _scrub_noise(display)
    if txn == "FEE":
        # 账单自己印 "Fee <日期>"，描述只要费用名（"ATM FEE"/"MONTHLY SVC FEE"）。
        return brand or _scrub_noise(text) or "SERVICE FEE"
    desc = _scrub_noise(text)
    if brand:
        # 真账单的 ACH 行是「收款方 + 引用号」：模板里没数字的多半是模型自己编的
        # 说明（"CON EDISON UTIL BILL"），只留品牌；带数字的引用号（IRS
        # ``USATAXPYMT 1234567890``、``PPD ID:8837``）才留在描述里。
        brand_tokens = brand.split()
        extra = [token for token in desc.split() if token not in brand_tokens]
        if txn != "ZELLE" and not any(re.search(r"\d", token) for token in extra):
            extra = [token for token in extra if token in _KEEP_EXTRA_TOKENS]
        desc = " ".join([brand, *extra]).strip()
    return desc or brand or "PAYMENT"


def apply_descriptors(
    norm: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    descriptors: Mapping[str, Mapping[str, Any]],
    *,
    seed: str = "",
) -> list[dict]:
    """套模板并做最终清洗；返回带 ``description`` 的新行列表。"""
    seed_key = _seed_of(norm, seed)
    rng = _rng(seed_key, "descriptors")
    out: list[dict] = []
    for row in rows:
        entry = descriptors.get(str(row.get("merchant_key") or "")) or {}
        template = entry.get("template") or ""
        rendered = _render_template(template, row, None, norm, rng) if template else ""
        description = _final_description(row, rendered, norm, seed=seed_key)
        description = _BAD_CHARS_RE.sub(" ", description.upper())
        description = re.sub(r"\s+", " ", description).strip()[: P.DESCRIPTOR_MAX_CHARS]
        if not description:
            description = "PURCHASE"
        memo = str(entry.get("memo") or row.get("memo") or "Purchase")
        clone = dict(row)
        clone["description"] = description
        clone["memo"] = memo
        ref_payload = f"{seed}|{row.get('merchant_key')}|{row.get('date')}|{row.get('amount_cents')}"
        clone["reference"] = str(
            int.from_bytes(hashlib.sha256(ref_payload.encode("utf-8")).digest()[:6], "big") % 10**10
        ).zfill(10)
        merchant = None
        name = _merchant_display(row, seed=seed_key)
        if name:
            merchant = {
                "name": name[:40],
                "category": str(row.get("category") or "other"),
                "city": str(row.get("city") or ""),
                "state": str(row.get("state") or ""),
            }
        clone["merchant"] = merchant
        out.append(clone)
    return out


# ---- validate 节点 -------------------------------------------------------

_RULE_TARGETS = {
    "R1_count_in_range": "schedule",
    "R2_balance_continuity": "schedule",
    "R3_dates_in_period": "schedule",
    "R4_recurring_consistency": "lifeModel",
    "R6_amount_within_range": "lifeModel",
    "R7_geo_consistency": "lifeModel",
    "R8_no_duplicates": "schedule",
    "R9_evidence_present": "lifeModel",
    "R10_no_payroll_income_personal": "lifeModel",
    "R12_no_placeholder_names": "lifeModel",
    "R14_variety_floor": "lifeModel",
    "R15_no_gateway_income": "lifeModel",
}


def node_validate(
    norm: Mapping[str, Any],
    model: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    *,
    log: Any = None,
) -> dict:
    """机器可判定的规则（决策 13）；AI 审核不在这里。"""
    months = list(norm.get("months") or [])
    by_month: dict[str, list[Mapping[str, Any]]] = {month: [] for month in months}
    for row in rows:
        key = _month_of(row["date"])
        by_month.setdefault(key, []).append(row)

    rules: list[dict] = []

    def add(rule_id: str, violations: list[dict]) -> None:
        rules.append(
            {
                "ruleId": rule_id,
                "passed": not violations,
                "violations": violations[:6],
                "target": _RULE_TARGETS.get(rule_id, "schedule"),
            }
        )

    low, high = int(norm["row_range"]["min"]), int(norm["row_range"]["max"])
    count_issues = [
        {"transactionIds": [], "message": f"{month} 有 {len(items)} 行，应在 {low}-{high} 行"}
        for month, items in by_month.items()
        if not (low <= len(items) <= high)
    ]
    add("R1_count_in_range", count_issues)

    start = _parse_iso(norm["period"]["start"])
    end = _parse_iso(norm["period"]["end"])
    outside = [
        {"transactionIds": [], "message": f"{row['date']} 不在对账期内"}
        for row in rows
        if not (start <= row["date"] <= end)
    ]
    add("R3_dates_in_period", outside)

    fixed: dict[str, set[int]] = {}
    for row in rows:
        # 收入本来就是波动的（季节性/按次），R4 只看固定账单类支出。
        if row.get("type") == "POS" or row.get("category") == "income":
            continue
        fixed.setdefault(str(row.get("merchant_key")), set()).add(int(row["amount_cents"]))
    unstable = [
        {"transactionIds": [], "message": f"{key} 的固定金额出现 {len(values)} 种"}
        for key, values in fixed.items()
        if len(values) > 1 and len(values) > 3
    ]
    add("R4_recurring_consistency", unstable)

    bad_amounts = [
        {"transactionIds": [], "message": f"金额必须为正：{row['amount_cents']}"}
        for row in rows
        if int(row.get("amount_cents") or 0) <= 0
    ]
    add("R6_amount_within_range", bad_amounts)

    geo = [
        {"transactionIds": [], "message": f"{row['merchant_key']} 缺城市/州"}
        for row in rows
        if row.get("type") == "POS"
        and row.get("location") != "online"
        and not (row.get("city") and row.get("state"))
    ]
    add("R7_geo_consistency", geo)

    seen: set[tuple] = set()
    duplicates = []
    for row in rows:
        key = (row["date"], row.get("description") or row.get("base_description"), row["amount_cents"])
        if key in seen:
            duplicates.append({"transactionIds": [], "message": f"重复流水：{key}"})
        seen.add(key)
    add("R8_no_duplicates", duplicates)

    # 只有 POS 消费必须有事实来源；水电/订阅这类账单允许凭生活模型记账。
    no_evidence = [
        {"transactionIds": [], "message": f"{row['type']} 行缺少事实来源"}
        for row in rows
        if row.get("type") == "POS" and not row.get("fact_id")
    ]
    add("R9_evidence_present", no_evidence)

    payroll = [
        {"transactionIds": [], "message": "个人主体出现了工资类存入"}
        for row in rows
        if row.get("direction") == "credit"
        and any(word in str(row.get("description") or "").upper() for word in ("PAYROLL", "SALARY", "WAGE", "DIRECT DEP"))
    ] if norm.get("subject_type") == "personal" else []
    add("R10_no_payroll_income_personal", payroll)

    placeholders = [
        {"transactionIds": [], "message": f"描述含占位词：{row.get('description')}"}
        for row in rows
        if any(word in str(row.get("description") or "").lower() for word in _PLACEHOLDER_WORDS)
    ]
    add("R12_no_placeholder_names", placeholders)

    distinct_keys = {str(row.get("merchant_key")) for row in rows}
    distinct_desc = {str(row.get("description")) for row in rows}
    variety = []
    if len(distinct_keys) < 4:
        variety.append({"transactionIds": [], "message": f"商户只有 {len(distinct_keys)} 个"})
    if len(distinct_desc) < 8:
        variety.append({"transactionIds": [], "message": f"描述只有 {len(distinct_desc)} 种"})
    add("R14_variety_floor", variety)

    gateways = [
        {"transactionIds": [], "message": f"出现支付网关存入：{row.get('description')}"}
        for row in rows
        if row.get("direction") == "credit"
        and any(word in str(row.get("description") or "").upper() for word in ("STRIPE", "SQUARE", "PAYPAL", "SHOPIFY"))
    ]
    add("R15_no_gateway_income", gateways)

    # R2 由最终账本算术保证；这里显式记一条 passed，方便报告对齐 15 条规则。
    add("R2_balance_continuity", [])

    feedback: list[str] = []
    for rule in rules:
        if rule["passed"]:
            continue
        for violation in rule["violations"][:3]:
            feedback.append(f"{rule['ruleId']}: {violation['message']}")
    result = {"rules": rules, "passed": all(rule["passed"] for rule in rules), "feedback": feedback}
    _log(log, f"validate：{sum(1 for rule in rules if rule['passed'])}/{len(rules)} 条通过", 74)
    return result


# ---- audit 节点 ----------------------------------------------------------

def node_audit(
    norm: Mapping[str, Any],
    profile: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    *,
    llm: Any,
    usage: _Usage,
    warnings: MutableSequence[str] | None = None,
    log: Any = None,
) -> dict:
    """AI 审计；结论只进 warnings（决策 13）。"""
    warnings = warnings if warnings is not None else []
    table = P.statement_table(norm, rows)
    try:
        data = _json_object(
            llm,
            P.audit_messages(profile, norm, table),
            schema=P.AUDIT_SCHEMA,
            usage=usage,
            max_tokens=2500,
        )
        findings = _clean_findings(data.get("findings"))
    except LlmError as exc:
        warnings.append(f"AI 审计失败（只记 warning）：{type(exc).__name__}: {exc}")
        findings = []
    _log(log, f"audit：{len(findings)} 条发现", 82)
    return {"findings": findings, "table": table}


def _clean_findings(raw: Any) -> list[dict]:
    findings: list[dict] = []
    if not isinstance(raw, list):
        return findings
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        severity = str(item.get("severity") or "medium").lower()
        if severity not in {"high", "medium", "low"}:
            severity = "medium"
        target = str(item.get("suggestedTarget") or "lifeModel")
        if target not in {"lifeModel", "schedule", "descriptors"}:
            target = "lifeModel"
        ids = [str(value) for value in (item.get("transactionIds") or [])][:8]
        findings.append(
            {
                "severity": severity,
                "transactionIds": ids,
                "problem": str(item.get("problem") or "").strip() or "未说明问题",
                "suggestedTarget": target,
            }
        )
        if len(findings) >= P.MAX_FINDINGS:
            break
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda item: order.get(item["severity"], 3))
    return findings


# ---- finalize：账本 + 月包 + 交付报告 ------------------------------------

def _effective_opening(model: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> int:
    """保证账户不透支：必要时一次性抬升期初（确定性，见 DIFFERENCES）。"""
    opening = max(1, _int(model.get("openingBalanceCents"), 100_000))
    balance = opening
    lowest = opening
    for row in rows:
        amount = int(row.get("amount_cents") or 0)
        balance += amount if row.get("direction") == "credit" else -amount
        lowest = min(lowest, balance)
    if lowest < 5_000:
        opening += 5_000 - lowest + 1_731
    return opening


def build_ledger(
    norm: Mapping[str, Any],
    profile: Mapping[str, Any],
    model: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    *,
    template: str,
    seed: str = "",
) -> LedgerDocument:
    """lifeModel 输出 + 流水 → ``LedgerDocument``。

    纯计算：余额逐行重算，保证 ``期初 + Σ有符号金额 = 期末``。这一步绝不交给 LLM。
    """
    business = P.is_business_profile(profile)
    kind = str(norm.get("account_kind") or ("business_checking" if business else "checking"))
    last4 = str(norm.get("account_last4") or digits(seed, 4))
    bank_name = str(norm.get("bank_name") or "")
    if not bank_name:
        bank_name = bridge.TEMPLATE_BANK.get(str(template).split("-")[0], "")
    bank = BankIdentity.derive(bank_name, f"{seed}:bank")
    account = AccountRef(
        kind=kind,
        last4=last4,
        number=account_number(f"{seed}:account", last4),
        routing=aba_routing(f"{seed}:routing"),
        product="Business Checking" if business else "Everyday Checking",
    )
    holder = Holder(
        name=_holder_name(profile),
        address_lines=_address_lines(profile, norm),
        is_business=business,
    )
    opening = _effective_opening(model, rows)
    period = Period(_parse_iso(norm["period"]["start"]), _parse_iso(norm["period"]["end"]))

    ledger_rows: list[LedgerRow] = []
    balance = opening
    check_seq = _rng(seed, "check-no").randint(1000, 1099)
    for row in sorted(rows, key=lambda item: (item["date"], item["posted"], item["amount_cents"])):
        amount = max(1, int(row.get("amount_cents") or 1))
        direction = "credit" if row.get("direction") == "credit" else "debit"
        balance += amount if direction == "credit" else -amount
        txn = str(row.get("type") or "POS").upper()
        check_no = None
        if txn == "CHECK":
            # 支票行必须带票号：没有号的 "Check Paid" 是真账单里不存在的东西。
            check_no = str(row.get("check_no") or check_seq)
            check_seq += 1
        ledger_rows.append(
            LedgerRow(
                date=row["date"],
                posted=row["posted"],
                description=str(row.get("description") or row.get("base_description") or "PURCHASE"),
                memo=str(row.get("memo") or "Purchase"),
                type=txn,
                direction=direction,
                amount_cents=amount,
                balance_cents=balance,
                merchant=row.get("merchant"),
                check_no=check_no,
                reference=str(row.get("reference") or ""),
            )
        )
    return LedgerDocument(
        bank=bank,
        holder=holder,
        account=account,
        period=period,
        opening_cents=opening,
        closing_cents=balance,
        rows=tuple(ledger_rows),
        seed=seed,
        # 交付数据永远不带 SAMPLE 水印：它会把 PDF 印成样张，用户拿到就废了。
        watermark=False,
        extras={
            "months": list(norm.get("months") or []),
            "subjectType": norm.get("subject_type"),
            "template": template,
        },
    )


def _holder_name(profile: Mapping[str, Any]) -> str:
    return str(profile.get("legalName") or profile.get("name") or "Account Holder")[:60]


def _address_lines(profile: Mapping[str, Any], norm: Mapping[str, Any]) -> tuple[str, ...]:
    address = profile.get("address") if isinstance(profile.get("address"), Mapping) else {}
    loc = norm.get("location") or {}
    street = str(address.get("street") or profile.get("street") or "1 Market Street")
    city = str(address.get("city") or loc.get("city") or "Austin")
    state = str(address.get("state") or loc.get("state") or "TX").upper()[:2]
    zip_code = str(address.get("zip") or address.get("zipCode") or digits(city, 5))
    if len(zip_code) != 5 or not zip_code.isdigit():
        zip_code = digits(city, 5)
    return (_holder_name(profile), street, f"{city}, {state} {zip_code}")


def _month_closings(ledger: LedgerDocument, months: Sequence[str]) -> dict[str, int]:
    """逐月累计余额；月份必须连续（normalize 已保证）。"""
    out: dict[str, int] = {}
    balance = ledger.opening_cents
    for month in months:
        for row in ledger.rows:
            if _month_of(row.date) == month:
                balance += row.signed_cents
        out[month] = balance
    return out


def closure_issues(
    model: Mapping[str, Any],
    ledger: LedgerDocument,
    months: Sequence[str],
) -> dict[str, list[str]]:
    """可选的算术硬卡：模型自己声明的月结必须和账本一致。"""
    declared = model.get("monthClosings")
    if not isinstance(declared, Mapping):
        return {}
    actual = _month_closings(ledger, months)
    issues: dict[str, list[str]] = {}
    for month in months:
        if month not in declared:
            continue
        try:
            want = int(declared[month])
        except (TypeError, ValueError):
            continue
        got = actual.get(month)
        if got is None or got != want:
            issues.setdefault(month, []).append(
                f"{month} 期末应为 {got} 分，模型声明 {want} 分"
            )
    return issues


def node_finalize(
    norm: Mapping[str, Any],
    profile: Mapping[str, Any],
    model: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    *,
    template: str,
    seed: str,
    out_dir: Path,
    log: Any = None,
) -> tuple[list[MonthOutcome], dict]:
    """建账本 → 月包 → 校验 → 落盘报告；硬卡失败只改 ok，不抛异常。"""
    months = list(norm.get("months") or [])
    ledger = build_ledger(norm, profile, model, rows, template=template, seed=seed)
    artifacts = monthpack.month_artifacts(template, ledger, months)
    by_month = {artifact.month: artifact for artifact in artifacts}
    issues = closure_issues(model, ledger, months)
    outcomes: list[MonthOutcome] = []
    entries: list[dict] = []
    for month in months:
        artifact = by_month.get(month)
        data = dict(artifact.data) if artifact else {}
        try:
            report = monthpack.check_delivered(template, month, data)
        except Exception as exc:  # 月包渲染异常也要变成该月的问题，而不是整批失败
            report = {
                "month": month,
                "template": template,
                "ok": False,
                "structure": [],
                "content": [{"rule": "check_failed", "path": "", "detail": f"{type(exc).__name__}: {exc}"}],
                "warnings": [],
                "rows": 0,
                "opening": None,
                "closing": None,
            }
        report = dict(report)
        if issues.get(month):
            content = list(report.get("content") or [])
            content.extend(
                {"rule": "balance_closure", "path": "", "detail": detail}
                for detail in issues[month]
            )
            report["content"] = content
            report["ok"] = False
        ok = bool(report.get("ok"))
        month_warnings = [
            str(item.get("detail") or item.get("rule") or "")
            for item in (report.get("content") or [])
            if isinstance(item, Mapping)
        ]
        outcomes.append(
            MonthOutcome(month=month, data=data, ok=ok, report=report, warnings=month_warnings)
        )
        blob = json.dumps(data, ensure_ascii=False, indent=2)
        filename = f"{template}-{month}.json"
        path = out_dir / filename
        path.write_text(blob + "\n", encoding="utf-8")
        entries.append(
            {
                "month": month,
                "template": template,
                "ok": ok,
                "path": filename,
                "sha256": hashlib.sha256(blob.encode("utf-8")).hexdigest(),
                "bytes": len(blob.encode("utf-8")),
                "rows": report.get("rows"),
                "opening": report.get("opening"),
                "closing": report.get("closing"),
                "structure": report.get("structure") or [],
                "content": report.get("content") or [],
                "warnings": report.get("warnings") or [],
            }
        )
    batch: dict = {"ok": False, "template": template, "months": months, "failed": months, "problems": []}
    for entry in entries:
        batch = merge_entry(out_dir, template, entry)
    _log(log, f"finalize：{sum(1 for item in outcomes if item.ok)}/{len(outcomes)} 个月通过", 92)
    return outcomes, batch


# ---- 编排 ---------------------------------------------------------------

def _check_template(template: str) -> None:
    """模板必须能渲染 JSON 月包；static-web 的模板没有账单适配器。"""
    if not isinstance(template, str) or not template.strip():
        raise ValueError("template 不能为空")
    try:
        supported = bridge.is_supported(template)
    except Exception as exc:  # registry 异常也按非法模板处理
        raise ValueError(f"无法识别模板 {template!r}：{exc}") from exc
    if not supported:
        raise ValueError(
            f"模板 {template!r} 不能生成账单 JSON（static-web 或不存在）"
        )


def _derive_seed(profile: Mapping[str, Any], template: str, months: Sequence[str]) -> str:
    payload = json.dumps(
        {"profile": dict(profile), "template": template, "months": list(months)},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def generate(
    ctx: Any,
    *,
    profile: Mapping[str, Any],
    template: str,
    months: Sequence[str],
    llm: Any,
    tavily: Any,
    seed: str = "",
    out_dir: str | Path | None = None,
) -> GenResult:
    """跑完整条链路，返回每月模板 JSON 与交付报告。

    ``ctx`` 可以是 :class:`billgen.jobs.JobContext`、任意带 ``log`` /
    ``raise_if_cancelled`` 的对象，或 ``None``（空实现）。取消时抛
    :class:`billgen.jobs.JobCancelled`。
    """
    ctx = _as_ctx(ctx)
    month_list = _parse_months(months)
    _check_template(template)
    if not isinstance(profile, Mapping) or not profile:
        raise ValueError("profile 不能为空")
    if not seed:
        seed = _derive_seed(profile, template, month_list)
    usage = _Usage()
    warnings: list[str] = []
    target = Path(out_dir) if out_dir is not None else Path(tempfile.mkdtemp(prefix="billgen-gen-"))
    target.mkdir(parents=True, exist_ok=True)

    _check(ctx)
    _log(ctx, "normalize：校验请求", 2)
    norm = node_normalize(profile, template=template, months=month_list, seed=seed)
    norm["bank_name"] = norm.get("bank_name") or bridge.TEMPLATE_BANK.get(template.split("-")[0], "")

    _check(ctx)
    queries = node_plan_research(norm, profile, llm=llm, usage=usage, warnings=warnings, log=ctx)

    _check(ctx)
    facts = node_research(norm, queries, tavily=tavily, usage=usage, warnings=warnings, log=ctx)

    model: dict = {}
    rows: list[dict] = []
    descriptors: dict[str, dict] = {}
    validation: dict = {"rules": [], "passed": True, "feedback": []}
    findings: list[dict] = []
    feedback: list[str] = []

    for attempt in range(2):
        _check(ctx)
        model = node_life_model(
            norm, profile, facts, llm=llm, usage=usage, warnings=warnings,
            feedback=feedback or None, log=ctx,
        )
        _check(ctx)
        try:
            rows = node_schedule(norm, model, facts, seed=seed, log=ctx)
        except Exception as exc:  # 调度异常按校验失败处理，第二轮再兜底
            warnings.append(f"schedule 失败：{type(exc).__name__}: {exc}")
            rows = []
        _check(ctx)
        descriptors = node_descriptors(
            norm, profile, rows, facts, llm=llm, usage=usage, warnings=warnings, log=ctx
        )
        described = apply_descriptors(norm, rows, descriptors, seed=seed)
        rows = described
        _check(ctx)
        validation = node_validate(norm, model, rows, log=ctx)
        for rule in validation["rules"]:
            if not rule["passed"]:
                warnings.append(f"规则未过（{rule['ruleId']}）：{rule['violations'][0]['message']}")
        _check(ctx)
        audit = node_audit(norm, profile, rows, llm=llm, usage=usage, warnings=warnings, log=ctx)
        findings = audit["findings"]
        for finding in findings:
            warnings.append(
                f"audit ({finding['severity']}, {finding['suggestedTarget']}): {finding['problem']}"
            )
        hard = [finding for finding in findings if finding["severity"] == "high"]
        if (not validation["passed"] or hard) and attempt == 0:
            feedback = list(validation["feedback"])
            feedback.extend(
                f"audit ({finding['severity']}, {finding['suggestedTarget']}): {finding['problem']}"
                for finding in hard
            )
            warnings.append("第 1 轮未通过，回退 lifeModel 重跑一次")
            continue
        break

    _check(ctx)
    outcomes, batch = node_finalize(
        norm, profile, model, rows, template=template, seed=seed, out_dir=target, log=ctx
    )

    partial = not all(outcome.ok for outcome in outcomes)
    if partial:
        warnings.append("部分月份未通过硬卡，已标记为 partial，不阻断其它月份")
    if not batch.get("ok"):
        warnings.append("交付批次未全部通过（见 audit.batch）")
    audit_report = {
        "rules": validation.get("rules") or [],
        "passed": bool(validation.get("passed")),
        "findings": findings,
        "batch": batch,
    }
    llm_usage = getattr(llm, "usage", {}) or {}
    usage_report = {
        "llmCalls": usage.llm_calls,
        "tavilyQueries": usage.tavily_queries,
        "tokens": {
            "prompt": int(llm_usage.get("prompt_tokens") or 0),
            "completion": int(llm_usage.get("completion_tokens") or 0),
        },
    }
    _log(ctx, "完成", 100)
    return GenResult(
        template=template,
        months=outcomes,
        warnings=warnings,
        audit=audit_report,
        usage=usage_report,
        out_dir=str(target),
    )


__all__ = [
    "DIFFERENCES",
    "GenResult",
    "JobCancelled",
    "LifeModelError",
    "MonthOutcome",
    "apply_descriptors",
    "build_ledger",
    "closure_issues",
    "generate",
    "node_audit",
    "node_descriptors",
    "node_finalize",
    "node_life_model",
    "node_normalize",
    "node_plan_research",
    "node_research",
    "node_schedule",
    "node_validate",
]
