"""模板契约：把一棵模板数据树讲清楚，让 LLM 知道每个字段该填什么。

MCP 暴露三样东西：骨架（`data`，含原样的示例值）、类型（`types`，路径 → 类型/取值域）、
说明（`notes`，路径 → 人话）。骨架不是空对象——LLM 拿到的是「已经能渲染出稿」的完整样例，
它只需要替换自己要改的部分，这比从零编 JSON 可靠得多。
"""
from __future__ import annotations

import copy
import re
from typing import Any

from .. import registry
from ..engine.base import RenderError

__all__ = ["CONTRACT_VERSION", "contract", "field_notes", "field_types", "rules_for", "skeleton"]

CONTRACT_VERSION = 1

# 金额一律是十进制字符串，不带 $、不带千分位；符号由字段语义决定
_MONEY = "money"
_MONEY_SIGNED = "money:signed"
_DATE_SLASH = "date:MM/DD/YY"
_DATE_SHORT = "date:MM/DD"
_DATE_MONTH_DAY = "date:Mon D"
_DATE_LONG = "date:Month D, YYYY"

# 通用字段说明：所有模板都吃这一套
_COMMON_NOTES: dict[str, str] = {
    "promo": "促销框与法务段落里的商品名/评奖来源。留空会印出原版真实银行的产品名",
    "promo.award_source": "虚构评奖机构名，如 \"Small Business Review\"",
    "promo.brokerage_brand": "券商/关联品牌名，用自己行名替换真实品牌",
    "template": "模板变体名，必须与所选模板要求一致（如 personal / business / us / usbiz），改错会渲染失败",
    "auto": "true 时引擎按明细重算 summary/totals/daily，别手填合计数",
    "watermark": "是否加 SAMPLE 水印。合成样例必须保持 true",
    "encrypt": "是否 RC4 加密 PDF。只看效果时设 false",
    "begin_balance": f"期初余额，{_MONEY} 字符串，如 \"1000.00\"",
    "bank": "银行身份。name / legal / domain 必须自洽，且不能指向真实银行",
    "bank.name": "账单上出现的行名，如 \"Bank of America\"",
    "bank.legal": "法律主体名，通常是 \"<行名>, N.A.\"",
    "bank.domain": "官网域名，与行名同源的真实形态域名，如 \"harborline-bank.com\"（别用 .example / 占位域名）",
    "customer_lines": "持卡人三行地址：姓名 / 街道与门牌 / 城市 州 邮编（写具体值，别写占位）",
    "account_number": "完整账号（可 8–17 位数字）；后四位要与账户一致",
    "account_display": "账单上显示的账号字符串，可用掩码",
    "period": "结算期。label 是给人看的整段，begin/start/end 是各模板要的格式，彼此不能矛盾",
    "period.label": "如 \"July 1, 2026 to July 31, 2026\"",
    "period.begin": _DATE_LONG,
    "period.start": "起始日期，按模板要求（可能只要 \"Jan 1, 2027\"）",
    "period.end": "结束日期，同 start",
    "product": "产品名，如 \"Everyday Checking\"；不要用真实银行的产品名，也不要写 Sample/Example",
    "metadata": "PDF 文档属性。title 用该行真实出件标题，producer/creator 用真实单据供应商名",
    "metadata.title": "该行真实出件标题，如 \"Chase Online Statement\"",
    "metadata.producer": "真实单据供应商，如 \"OpenText Output Transformation Engine 16.6.0\"",
    "metadata.creator": "同上，与模板族一致",
}

# 每类明细行的说明
_ROW_NOTES: dict[str, str] = {
    "date": "交易日，格式见该模板 notes 里的 date_format",
    "desc": "商户或交易描述，大写更真实；长度上限见 limits.desc_chars",
    "amount": "金额。多数模板：存入为正、支出为负；分区表（deposits/withdrawals）里用无符号金额",
    "balance": "该笔之后的最新余额，必须与上一行余额加减一致",
    "type": "交易类型，取值域见 types 里的 enum",
    "deposit": "存入金额（无符号），支出行为空串",
    "withdrawal": "支出金额（无符号），存入行为空串",
    "detail": "交易说明",
    "extra": "附加参考号数组，通常一两行短码",
}

# 各模板的补充说明与约束
_TEMPLATE_NOTES: dict[str, dict[str, str]] = {
    "boa-personal": {
        "deposits": "存入明细，一笔一行",
        "withdrawals": "扣款明细，amount 为负数",
        "fees": "费用明细，amount 为负数；没有就留空数组",
        "summary": "auto=true 时由引擎重算，不要手改",
        "daily": "每日余额表 [[MM/DD, 余额]]，auto=true 时自动生成",
        "qualifications": "开户资格勾选项，与产品匹配即可",
    },
    "boa-business": {"deposits": "存入明细", "withdrawals": "扣款明细（含支票）", "fees": "费用明细"},
    "chase-personal": {
        "transactions": "统一流水，type ∈ deposit / atm / electronic / fee，amount 带符号；优先用它，别同时填分区表",
        "micr": "底部 MICR 行。routing 必须是 9 位合法 ABA（3-7-1 校验位）",
        "micr.routing": "9 位路由号，ABA 校验位必须正确",
        "daily_balance": "每日余额 [[MM/DD, 余额]]",
        "account_display": "页眉账号显示串",
    },
    "chase-business": {
        "deposits": "存入明细",
        "checks": "支票行 {no,date,paid,desc,amount,caret}，支票号递增",
        "electronic_withdrawals": "电子扣款",
        "other_withdrawals": "其他扣款",
        "fees": "费用",
    },
    "wellsfargo": {
        "bank.legal_na": "法律主体名（Wells Fargo 版式单独一个字段）",
        "bank.phone_main": "客服电话，用 555 保留号段",
        "account": "账户块 {product, number, rtn, primary_label}",
        "account.rtn": "9 位路由号，ABA 校验位正确",
        "transactions": "流水行 {date:'1/4', desc, deposit, withdrawal, balance}",
        "summary": "auto=true 时重算",
    },
    "amex": {
        "transactions": "流水行 {date:'10/02/2026', desc, amount(带符号浮点), id, txid, memo, type}",
        "bank": "行名/法律主体/域名；标题与页脚会跟着变",
        "statement_date": "出单日 MM/DD/YYYY",
    },
    "mercury": {
        "transactions": "流水行 {date:'Oct 2', desc, amount, id, txid, memo, type, dir:'in'|'out'}",
        "company": "公司名（商业账户）",
        "ein": "雇主识别号，用 12-3456789 形态的虚构值",
    },
    "capitalone": {
        "transactions": "流水行 {date:'10/02', desc, amount, id, txid, memo, type}",
        "opening_balance": "期初余额（浮点）",
    },
    "sofi": {
        "transactions": "流水行 {date:'10/07/2026', desc, amount, id, txid, memo, type}",
        "beginning_balance": "期初余额（浮点）",
        "legal_footer": "法务页脚，保持版式预断行长度",
    },
    "usbank-personal": {
        "deposits": "存入行 {date:'Jan 4', type, detail, extra[], amount}",
        "withdrawals": "扣款行，同上",
        "imb_serial": "票据序列号，版式固定长度",
        "daily": "每日余额 [[Jan 4, 余额]]",
    },
    "usbank-business": {
        "deposits": "存入行",
        "withdrawals": "扣款行",
        "imb_serial": "票据序列号",
    },
}

# 硬规则：任何模板都必须满足
_COMMON_RULES: tuple[str, ...] = (
    "身份字段必须具体：真实感的人名（Maya Okafor）、街道号 + 街名 + 城市 + 州缩写 + 5 位邮编；"
    "禁止 SAMPLE / EXAMPLE / DEMO / TEST / DUMMY / 占位名字（John Doe）/ 123 Main St / 00000 之类。",
    "账号用 10–17 位数字，路由号必须能过 ABA 校验位；不要整串 0。",
    "商户名只能来自 facts 或账本里已有的行，不要新编一个商户或价格。",
    "流水行文照模板所属银行，不要用一套通用格式：Chase 前缀式（Card Purchase/ACH Debit + 日期 + 商户 + Card 尾号）；"
    "BoA 直接印 NACHA 字段（DES:<名> ID:<号> INDN:<持卡人> CO ID:<5位> PPD|WEB）；"
    "U.S. Bank 有 Ref Number 列，ACH 走两行续行（REF=<15 位 trace> / PAYROLL <公司号>）；"
    "Wells Fargo 用 Card Purchase <MM/DD> <商户> <城市> <州>；Amex 用 DEBIT CARD PURCHASE <商户> <城市> <州>；"
    "Capital One 的 CATEGORY 列填品类词（Groceries/Dining/Gas/Automotive/Bills & Utilities/Income），不许填 Credit/Debit；"
    "SoFi 的 TYPE 列用 Deposit/Direct Payment/Card Purchase/Interest Earned，描述下带 Transaction ID；"
    "Mercury 的 TYPE 列用 ACH In/ACH Out/Card/Wire In/Fee，描述只写商户名。",
    "版式里存在的列必须填满：CATEGORY、Ref Number、Type 这类列留空就是穿帮。",
    "金额是十进制字符串：不写 $、不写千分位（模板自己的格式化字段除外，如 summary）。",
    "期初 + Σ存入 − Σ扣款 = 期末，必须严格闭合；改任何一行都要重算。",
    "余额链连续：本行余额 = 上行余额 + 本行金额。",
    "日期必须落在 period 里，且按时间升序排列。",
    "行名、法律主体、域名、行址、客服电话必须是虚构值，不得指向真实银行。",
    "metadata.title 用模板族真实出件标题（勿写 SAMPLE），watermark 保持 true。",
    "描述长度受版式限制，超长会被裁剪；先按 limits.desc_chars 截断。",
    "auto=true 的模板不要手填 summary/totals/daily，让引擎算。",
)


def _walk(value: Any, path: str = "") -> dict[str, str]:
    """把数据树压成 路径 → 类型 的扁平表。"""
    out: dict[str, str] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            out.update(_walk(item, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, list):
        if value:
            out[path] = f"list[{_kind(value[0])}]"
        else:
            out[path] = "list[?]"
    else:
        out[path] = _kind(value)
    return out


def _kind(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        if re.fullmatch(r"-?\d+(\.\d{2})?", value):
            return "number-as-string"
        if re.fullmatch(r"\d{2}/\d{2}(/\d{2})?", value):
            return "date-string"
        if re.fullmatch(r"\d{2}/\d{2}/\d{4}", value):
            return "date-string"
        return "string"
    if value is None:
        return "null"
    return type(value).__name__


def skeleton(template_id: str) -> dict[str, Any]:
    """该模板的完整默认数据（深拷贝，可安全改动）。

    默认数据里没有、但合成场景必须填的块（目前是 BOA 的促销文案）在这里补成占位符，
    免得 LLM 拿一份「干净默认值」渲染出一张印着真实商品名的稿子。
    """
    renderer = registry.get_renderer(template_id)
    data = copy.deepcopy(renderer.default_data())
    _scaffold(template_id, data)
    return data


def _scaffold(template_id: str, data: dict[str, Any]) -> None:
    bank = data.get("bank") if isinstance(data.get("bank"), dict) else {}
    name = str(bank.get("name") or "Bank of America")
    domain = str(bank.get("domain") or "bankofamerica.com")
    # 这几个模板的引擎把行名当数据读，但默认数据里没给 —— 不补就印原版行名
    if not isinstance(data.get("bank"), dict):
        data["bank"] = {"name": name, "legal": f"{name}, N.A.", "domain": domain}
    # chase 个人版正文里会出现产品名；不给就会印着原版 "CHASE COLLEGE CHECKING"
    if template_id.startswith("chase"):
        data.setdefault("product", "Everyday Checking")
    if not template_id.startswith("boa"):
        return
    data.setdefault("promo", {
        "overdraft_product": f"{name} Balance Guard\u2122",
        "business_checking": f"{name} Business Checking",
        "business_checking_short": f"{name} Business Checking",
        "award_source": "Small Business Review",
        "award_domain": domain,
        "safebalance_product": str(data.get("product") or "Everyday Checking"),
        "form_code": "SMP-01-25-0560.B | 0000000",
        "brokerage_brand": name,
    })


def field_types(template_id: str) -> dict[str, str]:
    return _walk(skeleton(template_id))


def field_notes(template_id: str) -> dict[str, str]:
    notes = dict(_COMMON_NOTES)
    for key, text in _ROW_NOTES.items():
        notes.setdefault(f"#row.{key}", text)
    notes.update(_TEMPLATE_NOTES.get(template_id, {}))
    return notes


def rules_for(template_id: str) -> list[str]:
    return list(_COMMON_RULES)


def limits_for(template_id: str) -> dict[str, Any]:
    if template_id.startswith("boa"):
        return {"desc_chars": 40, "rows_per_page": 26}
    if template_id.startswith("chase"):
        return {"desc_chars": 44, "rows_per_page": 30}
    if template_id.startswith("usbank"):
        return {"desc_chars": 40, "rows_per_page": 28}
    if template_id == "wellsfargo":
        return {"desc_chars": 34, "rows_per_page": 17}
    return {"desc_chars": 38, "rows_per_page": 24}


def required_keys(template_id: str) -> list[str]:
    """模板引擎真正会读的顶层键（脚手架补的键不算：那是为了方便填表，不是硬要求）。"""
    try:
        renderer = registry.get_renderer(template_id)
    except registry.TemplateNotFound as err:  # type: ignore[attr-defined]
        raise ValueError(str(err)) from None
    try:
        data = renderer.default_data()
    except RenderError as err:
        raise ValueError(f"{template_id} 没有默认数据：{err}") from None
    return sorted(data)


def contract(template_id: str) -> dict[str, Any]:
    """MCP 的 `template_contract` 返回值：骨架 + 类型 + 说明 + 规则 + 限额。"""
    try:
        spec = registry.get_spec(template_id)
    except registry.TemplateNotFound as err:  # type: ignore[attr-defined]
        raise ValueError(str(err)) from None
    try:
        data = skeleton(template_id)
    except RenderError as err:
        raise ValueError(f"{template_id} 没有默认数据：{err}") from None
    return {
        "contract_version": CONTRACT_VERSION,
        "template": {
            "id": spec.id,
            "display_name": spec.display_name,
            "kind": spec.kind,
            "supports": sorted(spec.supports),
        },
        "data": data,
        "types": _walk(data),
        "notes": field_notes(template_id),
        "rules": rules_for(template_id),
        "limits": limits_for(template_id),
    }
