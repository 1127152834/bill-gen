"""账本 → PDF 的唯一入口。

调用方（CLI、HTTP、未来的工作台）只需要：

    ledger = from_game_result(payload, account="checking", cycle=-1)
    result = render("chase-personal", ledger)

渲染永远走既有的单服务渲染器（同一套主题引擎、同一套水印与元数据规则），
这里只负责把账本翻译成模板认得的数据形状。
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from .. import branding, pdfpost, registry
from ..spec import RenderOptions, RenderResult
from . import profile
from .adapters import binding_for
from .ledger import LedgerDocument, from_game_result
from .lint import verify

__all__ = [
    "SUPPORTED",
    "apply_brand",
    "apply_docinfo",
    "build_data",
    "fingerprint",
    "is_supported",
    "render",
    "render_from_result",
    "supported_ids",
]

SUPPORTED: tuple[str, ...] = tuple(
    spec.id for spec in registry.SPECS if spec.kind != "static-web"
)


def supported_ids() -> tuple[str, ...]:
    """能直接吃账本的模板（静态前端模板没有服务端版式）。"""
    return SUPPORTED


def is_supported(template_id: str) -> bool:
    if template_id not in SUPPORTED:
        return False
    try:
        binding_for(template_id)
    except Exception:
        return False
    return True


_TRAILER_ID = re.compile(rb"/ID\s*\[<([0-9A-Fa-f]{32})>\s*<([0-9A-Fa-f]{32})>\]")


def fingerprint(pdf: bytes) -> str:
    """版式指纹：页尺寸 + 文本层 + 图片像素，忽略 PDF 内部的随机 /ID。"""
    import hashlib

    import fitz

    digest = hashlib.sha256()
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        for page in doc:
            rect = page.rect
            digest.update(f"{rect.width:.2f}x{rect.height:.2f}".encode())
            digest.update(page.get_text("text").encode("utf-8"))
            for image in page.get_images(full=True):
                digest.update(repr(image).encode())
    return digest.hexdigest()[:16]


def normalize_id(pdf: bytes) -> bytes:
    """把 trailer 的 /ID 固定下来，同样的数据永远得到同样的字节。

    不能沿用原文件里的第一个 ID —— 它是每轮渲染随机生成的（品牌替换后
    走 `tobytes()` 尤其明显），必须由文件内容自己算。

    真 PDF 的两个 ID 是「创建时 ID」与「当前 ID」：首值来自出件时的时间+路径，
    次值在增量更新后才与首值不同，绝大多数账单样本两者**不相等**。
    两个值写同一个 hash 是 PyMuPDF 的特征指纹（pdfid 一眼看到 /ID[<X><X>]），
    所以次值用另一撮盐派生，长度与首值一致。
    """
    import hashlib

    match = _TRAILER_ID.search(pdf)
    if match is None:
        return pdf
    body = pdf[: match.start()] + pdf[match.end() :]
    head = hashlib.sha256(body).hexdigest()[:32].upper().encode()
    tail = hashlib.sha256(body + b"\x00file-id").hexdigest()[:32].upper().encode()
    return pdf[: match.start()] + b"/ID[<" + head + b"><" + tail + b">]" + pdf[match.end() :]


_BANK_FIELDS = ("name", "legal", "domain", "phone", "po_box", "po_box_alt", "city_state_zip",
                "phone_service", "phone_es", "phone_intl", "phone_metro")


#: 「从零手写」的主题默认数据里没有 `bank` 字典（联系方式散在 contact_* 字段里），
#: 只靠 defaults 认不出这家是谁 —— 于是行名留空时账本身份全空，SoFi 封面的寄送地址
#: 与客服线整块变成空白。按模板归属补一张表。
TEMPLATE_BANK = {
    "boa": "Bank of America",
    "chase": "JPMorgan Chase",
    "usbank": "U.S. Bank",
    "wellsfargo": "Wells Fargo",
    "capitalone": "Capital One",
    "mercury": "Mercury",
    "sofi": "SoFi",
}


def _adopt_template_bank(
    ledger: LedgerDocument, defaults: dict[str, Any], template_id: str = ""
) -> LedgerDocument:
    """行名留空 = 用模板自带的那家银行（模板自己知道该印什么，避免版式与行名错配）。"""
    if ledger.bank.name:
        return ledger
    bank = {k: str(v) for k, v in (defaults.get("bank") or {}).items() if k in _BANK_FIELDS}
    name = bank.get("name", "").strip()
    if not name:
        name = TEMPLATE_BANK.get(template_id.split("-")[0], "")
    if not name:
        return ledger
    from .ledger import BankIdentity, real_traits, refresh_references, routing_for

    fields = {key: bank.get(key, "") for key in _BANK_FIELDS}
    # 模板只写自己用得到的那几个键（富国写 `phone_name`/`phone_main`/`legal_na`），
    # 直接灌进 BankIdentity 会得到「有名无电话」的半截身份，再经 bank_contact 反写
    # 就把封面上的真电话抹成空串（实测：Questions? 一栏整个号段消失）。
    # 真行用真行联系方式表补齐，别让派生逻辑去猜。
    traits = real_traits(name) or {}
    for key, value in traits.items():
        if not fields.get(key):
            fields[key] = value
    if not fields.get("name"):
        fields["name"] = name
    banked = BankIdentity(**fields)
    account = ledger.account
    routing = routing_for(name, ledger.seed)
    adopted = replace(ledger, bank=banked, account=replace(account, routing=routing))
    if routing != account.routing:
        # 账本装配时银行名还空着，ACH trace 用的是临时路由号；这里按真行路由号重挂一遍。
        adopted = refresh_references(adopted)
    return adopted


def _same_bank_as_template(name: str, template_id: str) -> bool:
    """账本行名与模板自带行名是否同属一家（忽略大小写与法定后缀）。"""
    try:
        bank = registry.get_renderer(template_id).default_data().get("bank") or {}
    except Exception:  # 取不到就按「不同」处理，宁可按品牌重绘
        return False
    template_name = str(bank.get("name") or "").strip()
    if not template_name:
        # 从零手写的主题默认数据里没有 `bank` 字典（SoFi/Amex/Mercury 都是），
        # 但它们的 logo 是矢量原稿 —— 认不出「同一家」就会在真 logo 上盖一个假字标。
        template_name = TEMPLATE_BANK.get(template_id.split("-")[0], "").strip()
    if not template_name:
        return False
    from .ledger import real_traits

    mine, theirs = real_traits(name), real_traits(template_name)
    if mine is not None and mine is theirs:
        return True
    key = lambda text: " ".join(text.replace(",", " ").lower().split())
    return key(name) == key(template_name)


def build_data(template_id: str, ledger: LedgerDocument, *, verify_rows: bool = True) -> dict[str, Any]:
    binding = binding_for(template_id)
    renderer = registry.get_renderer(template_id)
    defaults = renderer.default_data()
    ledger = _adopt_template_bank(ledger, defaults, template_id)
    data = binding.build(ledger, defaults)
    if verify_rows:
        verify(ledger, data, template_id)
    return data


def render(
    template_id: str,
    ledger: LedgerDocument,
    options: RenderOptions | None = None,
    *,
    verify_rows: bool = True,
    stable_bytes: bool = True,
    brand: bool = True,
    docinfo: bool = True,
    scrub: bool = True,
    entropy: bool = False,
) -> RenderResult:
    renderer = registry.get_renderer(template_id)
    # 行名留空时先换成模板自带的那家，后续的品牌重绘才知道该画谁。
    ledger = _adopt_template_bank(ledger, renderer.default_data(), template_id)
    data = build_data(template_id, ledger, verify_rows=verify_rows)
    # 水印是输出选项而不是数据：capitalone/sofi/mercury 只认 opts.watermark，
    # 不把账本上的开关传下去，它们就会一直盖 `SAMPLE`（出件形态直接穿帮）。
    opts = options or renderer.spec.default_options
    if options is None and opts.watermark != ledger.watermark:
        opts = replace(opts, watermark=ledger.watermark)
    result = renderer.render(data, opts)
    if brand:
        result = replace(result, pdf=apply_brand(result.pdf, template_id, ledger))
    if docinfo:
        result = replace(result, pdf=apply_docinfo(result.pdf, template_id, ledger))
    if scrub:
        # 洗版：XRef 流 + 对象流、随机字体子集前缀、补 XMP。
        # 真出件每次字节都不同，所以默认带 entropy（同一份数据两次渲染不一样）。
        result = replace(result, pdf=pdfpost.finalize(
            result.pdf,
            seed=f"{ledger.seed}:{template_id}",
            producer=profile.producer_of(template_id)[0],
            xmp=renderer.spec.theme in pdfpost.XMP_THEMES,
            entropy=entropy,
        ))
    if not stable_bytes:
        return result
    return replace(result, pdf=normalize_id(result.pdf))


def apply_brand(pdf: bytes, template_id: str, ledger: LedgerDocument) -> bytes:
    """把版式里烙死的真实行标换成账本自己的行标（物理删除原图，不是盖白块）。"""
    import fitz

    # 没指定行名 = 用模板自带的那家，底稿原样保留，一个字都不换。
    if not ledger.bank.name.strip():
        return pdf
    if template_id not in branding.PLACEMENTS:
        return pdf
    # 账本行名就是模板自带那家时保留原稿行标：重画的 wordmark 只有字没有图形
    # （BOA 的旗、Chase 的八边形都在原图里），换成占位字标反而更假。
    same_bank = _same_bank_as_template(ledger.bank.name, template_id)
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        branding.apply(doc, template_id, branding.brand_of_ledger(ledger),
                       redraw_logo=not same_bank)
        return doc.tobytes(garbage=3, deflate=True)


def apply_docinfo(pdf: bytes, template_id: str, ledger: LedgerDocument) -> bytes:
    """改 docinfo（Producer/Creator/出件时间）并摘掉 tagged-PDF 标记。"""
    import fitz

    with fitz.open(stream=pdf, filetype="pdf") as doc:
        profile.apply(doc, template_id, ledger)
        return doc.tobytes(garbage=3, deflate=True)


def render_from_result(
    payload: dict[str, Any],
    template_id: str,
    *,
    account: str | None = None,
    cycle: int = -1,
    bank_name: str | None = None,
    watermark: bool = True,
    options: RenderOptions | None = None,
    brand: bool = True,
) -> RenderResult:
    ledger = from_game_result(
        payload, account=account, cycle=cycle, bank_name=bank_name, watermark=watermark
    )
    return render(template_id, ledger, options, brand=brand)
