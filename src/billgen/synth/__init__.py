"""把「生成出来的财务人生」翻译成可渲染的账单账本。

分工：
``flow/`` 负责数据可信（人设、商户、金额分布、跨期连续性、证据）；
``synth`` 负责把数据映射到 13 套像素级版式，并在渲染前做账本闭合与不丢行自检。
"""
from __future__ import annotations

from .adapters import BINDINGS, Binding, binding_for
from .bridge import (
    SUPPORTED,
    apply_brand,
    apply_docinfo,
    build_data,
    fingerprint,
    is_supported,
    normalize_id,
    render,
    render_from_result,
    supported_ids,
)
from .ledger import (
    AccountRef,
    BankIdentity,
    CreditTerms,
    Holder,
    LedgerDocument,
    LedgerError,
    LedgerRow,
    Period,
    from_game_result,
)
from .lint import Issue, SynthLintError, verify
from .recommend import Candidate, catalog_for, recommend, score_all

__all__ = [
    "SUPPORTED",
    "apply_brand",
    "apply_docinfo",
    "AccountRef",
    "BankIdentity",
    "CreditTerms",
    "Holder",
    "BINDINGS",
    "Binding",
    "Candidate",
    "Issue",
    "LedgerDocument",
    "LedgerError",
    "LedgerRow",
    "Period",
    "SynthLintError",
    "binding_for",
    "build_data",
    "catalog_for",
    "recommend",
    "score_all",
    "fingerprint",
    "normalize_id",
    "from_game_result",
    "is_supported",
    "render",
    "render_from_result",
    "supported_ids",
    "verify",
]
