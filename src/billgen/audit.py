"""品牌残留审计：文本层里还留着哪些真实银行的字样。

版式是复刻的，真实行名/域名会以三种形态残留：图片 logo、页面文字、法务段落。
第一个由 `billgen.branding` 物理删除并替换；后两个只能逐个模板把字符串改成数据驱动，
所以这里给一把量尺：谁还残留、残留几处，写死在 `KNOWN_GAPS` 里，新增残留就会让测试红。
"""
from __future__ import annotations

import re

__all__ = [
    "BRAND_TOKENS",
    "KNOWN_GAPS",
    "scan_text",
    "unknown_gaps",
]

# 真实行名与域名。用词边界匹配，避免 "PURCHASE" 命中 "CHASE" 这种假阳性。
BRAND_TOKENS: tuple[str, ...] = (
    "Bank of America",
    "Wells Fargo",
    "American Express",
    "JPMorgan Chase",
    "Chase",
    "Balance Connect",
    "Business Fundamentals",
    "SafeBalance",
    "SmartAsset",
    "Merrill",
    "U.S. Bank",
    "USbank",
    "USBANKS",
    "Mercury",
    "Capital One",
    "SoFi",
    "Alliant",
    "Ally Bank",
    "bankofamerica.com",
    "wellsfargo.com",
    "americanexpress.com",
    "chase.com",
    "usbank.com",
    "mercury.com",
    "capitalone.com",
    "sofi.com",
    "alliantcu.org",
    "ally.com",
)

_PATTERNS = tuple(
    (token, re.compile(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", re.IGNORECASE))
    for token in BRAND_TOKENS
)

# 已知残留：字符串烙在复刻底稿/法务段落里，逐字返回版式，改名要重排版。
# 键是模板 id，值是允许残留的 token。任何不在此列的命中都会让审计失败。
# M8 之后 11 套模板的文本层全部干净：sofi 的法务段落改成「品牌态重排」，
# usbank 的跨行散文（`your local U.S.` + `Bank branch.`）两行一起换掉。
KNOWN_GAPS: dict[str, tuple[str, ...]] = {}


def scan_text(text: str) -> dict[str, int]:
    """统计文本里的真实品牌字样。"""
    found: dict[str, int] = {}
    for token, pattern in _PATTERNS:
        count = len(pattern.findall(text))
        if count:
            found[token] = found.get(token, 0) + count
    return found


def unknown_gaps(template_id: str, found: dict[str, int]) -> dict[str, int]:
    """扣掉 `KNOWN_GAPS` 之后仍然残留的部分。"""
    allowed = {token.lower() for token in KNOWN_GAPS.get(template_id, ())}
    return {token: count for token, count in found.items() if token.lower() not in allowed}
