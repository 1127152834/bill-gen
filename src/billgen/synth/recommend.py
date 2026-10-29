"""模板推荐：给定一本账本，挑出「哪几套版式最适合装它」。

为什么不交给模型：模板选择是业务决定（个人/商业、有没有支票区、一页能塞几行），
答案由账本形状决定，且必须可复算。模型能猜错的东西就不要让它猜——它只需要在
推荐出来的短名单里做最后一步润色。

打分全部来自可观察事实，不用 LLM、不渲染：
- 账号类型是硬门槛（商业账本进不了个人版式，反之亦然）；
- 描述长度会不会被截断（每个模板的列宽不同）；
- 行数与每页容量的贴合度（29 行塞进 30 行/页的模板，最后一页几乎空白）；
- 账本里有没有支票 / ATM / 手续费 / 日终余额，模板里有没有对应区块；
- 并列时按 (seed, template) 的稳定哈希打散，保证同 seed 复算一致、不同 seed 换风格。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from ..mcp.contract import limits_for
from .adapters import BINDING_BY_ID, BINDINGS, BindingError, binding_for
from .ledger import LedgerDocument

__all__ = ["Candidate", "recommend", "score_all"]


@dataclass(frozen=True)
class Candidate:
    template_id: str
    display_name: str
    score: float
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "template": self.template_id,
            "display_name": self.display_name,
            "score": round(self.score, 3),
            "reasons": list(self.reasons),
        }


def _display_name(template_id: str) -> str:
    from .. import registry

    try:
        return str(registry.get_spec(template_id).display_name)
    except Exception:  # noqa: BLE001 - 显示名只用于展示，取不到就退回 id
        return template_id


def _spread(seed: str, template_id: str) -> float:
    """0..1 的稳定抖动，用来在同分候选里换风格（不用 random，保证可复算）。"""
    digest = hashlib.sha256(f"{seed}|{template_id}".encode()).digest()
    return digest[0] / 255.0


def _row_fit(rows: int, per_page: int) -> tuple[float, str]:
    """分页是否好看：整页、正常残页都行，只有「最后一页孤零零一行」才难看。

    不去奖励「填得满」——真实账单本来就有只写半个月的对账单，空着的下半页很常见；
    该罚的是把 31 行塞进 30 行/页、最后那页只摆一行的那种版式。
    """
    if rows <= 0:
        return 0.5, "no rows to page"
    if rows <= per_page:
        return 1.0, f"single page ({rows}/{per_page} rows)"
    pages = -(-rows // per_page)
    rest = rows % per_page
    if rest == 0:
        return 1.0, f"{pages} full pages ({per_page}/page)"
    if rest == 1:
        return 0.2, f"{pages} pages, the last holds a single row"
    return 0.85, f"{pages} pages, the last holds {rest}/{per_page} rows"


def _desc_fit(longest: int, limit: int) -> tuple[float, str]:
    if longest <= limit:
        return 1.0, f"descriptions fit ({longest}/{limit} chars)"
    over = longest - limit
    return max(0.0, 1.0 - over / max(limit, 1)), f"descriptions truncate ({longest}>{limit} chars, {over} over)"


def _sections(data: dict[str, Any]) -> set[str]:
    """模板数据里实际存在的区块，就是这套版式会画出来的区块。"""
    keys = set(data)
    found = set()
    for name, aliases in (
        ("checks", ("checks",)),
        ("atm", ("atm",)),
        ("daily", ("daily", "daily_balance")),
        ("fees", ("fees",)),
        ("interest", ("interest_earned", "ytd_interest", "apy", "monthly_interest")),
        ("promo", ("promo",)),
        ("micr", ("micr",)),
        ("summary", ("summary", "totals")),
    ):
        if keys & set(aliases):
            found.add(name)
    return found


def _needs(ledger: LedgerDocument) -> set[str]:
    """账本里有哪些「必须画出来，否则读者会发现少东西」的内容。"""
    need = set()
    if ledger.checks:
        need.add("checks")
    if ledger.rows_of("ATM"):
        need.add("atm")
    if ledger.fees:
        need.add("fees")
    if len(ledger.daily_full()) > 1:
        need.add("daily")
    return need


def _built_data(ledger: LedgerDocument, template_id: str) -> dict[str, Any] | None:
    """用适配器干跑一遍拿到真实数据形状；模板不接受这本账本就返回 None。"""
    try:
        binding = binding_for(template_id)
    except BindingError:
        return None
    if not binding.accepts(ledger.account.kind):
        return None
    try:
        return binding.build(ledger, {})
    except Exception:  # noqa: BLE001 - 适配器挑不动这本账本就当它不适用
        return None


def score_all(ledger: LedgerDocument) -> list[Candidate]:
    """所有适用模板的打分明细（已按 score 降序）。"""
    longest = max((len(row.description) for row in ledger.rows), default=0)
    need = _needs(ledger)
    rows = len(ledger.rows)
    out: list[Candidate] = []
    for binding in BINDINGS:
        data = _built_data(ledger, binding.template_id)
        if data is None:
            continue
        limits = limits_for(binding.template_id)
        per_page = int(limits.get("rows_per_page", 24))
        desc_limit = int(limits.get("desc_chars", 38))
        score = 0.0
        reasons: list[str] = [f"accepts {ledger.account.kind}"]
        for weight, (part, reason) in (
            (3.0, _desc_fit(longest, desc_limit)),
            (2.0, _row_fit(rows, per_page)),
        ):
            score += weight * part
            reasons.append(reason)
        sections = _sections(data)
        covered = need & sections
        if need:
            ratio = len(covered) / len(need)
            score += 1.0 * ratio
            missing = sorted(need - sections)
            reasons.append(
                f"renders {len(covered)}/{len(need)} sections this ledger needs"
                + (f" (missing {', '.join(missing)})" if missing else "")
            )
        else:
            reasons.append("ledger has no special sections to cover")
        score += 0.5 * _spread(str(ledger.seed), binding.template_id)
        out.append(Candidate(binding.template_id, _display_name(binding.template_id), score, tuple(reasons)))
    out.sort(key=lambda c: (-c.score, c.template_id))
    return out


def recommend(ledger: LedgerDocument, *, limit: int = 3, exclude: tuple[str, ...] = ()) -> list[Candidate]:
    """挑前 `limit` 套版式；`exclude` 里的模板不会被推荐（比如已经出过片的那套）。"""
    if limit < 1:
        return []
    ranked = [c for c in score_all(ledger) if c.template_id not in exclude]
    return ranked[:limit]


def catalog_for(account_kind: str) -> list[dict[str, Any]]:
    """某账户类型能用的模板清单（不涉及具体账本，供 UI / 模型先看个大概）。"""
    return [
        {"template": b.template_id, "display_name": _display_name(b.template_id), "accounts": sorted(b.accounts)}
        for b in BINDINGS
        if account_kind in b.accounts
    ]


assert BINDING_BY_ID  # 保持导出面稳定，防止误删
