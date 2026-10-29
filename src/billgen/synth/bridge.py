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

from .. import branding, registry
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
    """
    import hashlib

    match = _TRAILER_ID.search(pdf)
    if match is None:
        return pdf
    body = pdf[: match.start()] + pdf[match.end() :]
    stable = hashlib.sha256(body).hexdigest()[:32].upper().encode()
    return pdf[: match.start()] + b"/ID[<" + stable + b"><" + stable + b">]" + pdf[match.end() :]


def build_data(template_id: str, ledger: LedgerDocument, *, verify_rows: bool = True) -> dict[str, Any]:
    binding = binding_for(template_id)
    renderer = registry.get_renderer(template_id)
    data = binding.build(ledger, renderer.default_data())
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
) -> RenderResult:
    renderer = registry.get_renderer(template_id)
    data = build_data(template_id, ledger, verify_rows=verify_rows)
    result = renderer.render(data, options or renderer.spec.default_options)
    if brand:
        result = replace(result, pdf=apply_brand(result.pdf, template_id, ledger))
    if docinfo:
        result = replace(result, pdf=apply_docinfo(result.pdf, template_id, ledger))
    if not stable_bytes:
        return result
    return replace(result, pdf=normalize_id(result.pdf))


def apply_brand(pdf: bytes, template_id: str, ledger: LedgerDocument) -> bytes:
    """把版式里烙死的真实行标换成账本自己的行标（物理删除原图，不是盖白块）。"""
    import fitz

    if template_id not in branding.PLACEMENTS:
        return pdf
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        branding.apply(doc, template_id, branding.brand_of_ledger(ledger))
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
