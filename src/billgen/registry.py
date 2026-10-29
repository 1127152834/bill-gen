"""13 个模板的唯一注册表。

替换掉旧仓库里散落的 6 份 ``TEMPLATES`` / ``DATA_FILES`` 字典与 8 处
``SERVICES`` 端口编排。新增一个模板 = 加一条 :class:`TemplateSpec`。
"""
from __future__ import annotations

from functools import lru_cache

from . import paths
from .engine.adapters import build_renderer
from .engine.loader import ThemeLoader
from .spec import RenderOptions, TemplateSpec

__all__ = ["SPECS", "SPEC_BY_ID", "TemplateNotFound", "get_renderer", "get_spec", "loader"]


class TemplateNotFound(KeyError):
    pass


_CREDIT_CARD = frozenset({"watermark", "encrypt", "preview"})
_DOCUMENT = frozenset({"watermark", "encrypt", "preview"})

SPECS: tuple[TemplateSpec, ...] = (
    # ---- 从零绘制：坐标 + 布局 JSON -------------------------------------
    TemplateSpec(
        "boa-personal", "BOA Personal", "from-scratch", "boa", "path-out",
        module="generate", entry="build_pdf",
        default_data="editor/data_personal.json", variant="personal",
        supports=_CREDIT_CARD, legacy_prefixes=("/boa",),
    ),
    TemplateSpec(
        "boa-business", "BOA Business", "from-scratch", "boa", "path-out",
        module="generate", entry="build_pdf",
        default_data="editor/data_business.json", variant="business",
        supports=_CREDIT_CARD, legacy_prefixes=("/boa/business",),
    ),
    TemplateSpec(
        "wellsfargo", "Wells Fargo", "from-scratch", "wellsfargo", "path-out",
        module="generate", entry="build_pdf",
        default_data="editor/data.json",
        supports=_CREDIT_CARD, legacy_prefixes=("/wf", "/wellsfargo"),
        # 富国的 fonts 默认是 declared（原稿清单声明），不要悄悄改成 subset
        default_options=RenderOptions(font_policy="declared"),
    ),
    TemplateSpec(
        "amex", "American Express", "from-scratch", "amex", "document",
        module="generate_sample_statement", entry="build_pdf",
        supports=_CREDIT_CARD, legacy_prefixes=("/ae",),
    ),
    TemplateSpec(
        "mercury", "Mercury", "from-scratch", "mercury", "global",
        module="generate_statement", entry="build",
        supports=_DOCUMENT, legacy_prefixes=("/mercury",),
    ),
    TemplateSpec(
        "capitalone", "Capital One", "from-scratch", "capitalone", "global",
        module="c1_generate", entry="build",
        supports=_DOCUMENT, legacy_prefixes=("/c1", "/capitalone"),
    ),
    TemplateSpec(
        "sofi", "SoFi", "from-scratch", "sofi", "global",
        module="sofi_generate", entry="build",
        supports=_DOCUMENT, legacy_prefixes=("/sofi",),
    ),
    # ---- 底稿重放：打开 prepared PDF，按 span 表原位替换 -----------------
    TemplateSpec(
        "chase-personal", "Chase Personal", "replay", "chase", "path-out",
        module="generate_personal", entry="build_pdf",
        default_data="editor_personal/data_personal.json", variant="personal",
        supports=_CREDIT_CARD, legacy_prefixes=("/chase", "/chase/personal"),
    ),
    TemplateSpec(
        "chase-business", "Chase Business", "replay", "chase", "path-out",
        module="generate_business", entry="build_pdf",
        default_data="editor_business/data_business.json", variant="business",
        supports=_CREDIT_CARD, legacy_prefixes=("/chase/business",),
    ),
    TemplateSpec(
        "usbank-personal", "US Bank Personal", "replay", "usbank", "path-out",
        module="generate_us", entry="build_pdf",
        default_data="editor_us/data_us.json",
        supports=_CREDIT_CARD, legacy_prefixes=("/us", "/usbank"),
    ),
    TemplateSpec(
        "usbank-business", "US Bank Business", "replay", "usbank", "path-out",
        module="generate_biz", entry="build_pdf",
        default_data="editor_biz/data_biz.json",
        supports=_CREDIT_CARD, legacy_prefixes=("/us/business", "/usbank/business"),
    ),
    # ---- 纯前端：浏览器内 pdf-lib 渲染 -----------------------------------
    TemplateSpec(
        "alliant", "Alliant", "static-web", "web/alliant", "static",
        legacy_prefixes=("/alliant",),
    ),
    TemplateSpec(
        "ally", "Ally Bank", "static-web", "web/ally", "static",
        legacy_prefixes=("/ally",),
    ),
)

SPEC_BY_ID: dict[str, TemplateSpec] = {spec.id: spec for spec in SPECS}


@lru_cache(maxsize=1)
def loader() -> ThemeLoader:
    return ThemeLoader(paths.ROOT)


@lru_cache(maxsize=None)
def get_spec(spec_id: str) -> TemplateSpec:
    try:
        return SPEC_BY_ID[spec_id]
    except KeyError:
        raise TemplateNotFound(
            f"未知模板 {spec_id!r}；可用：{', '.join(SPEC_BY_ID)}"
        ) from None


@lru_cache(maxsize=None)
def get_renderer(spec_id: str):
    """返回该模板的渲染器实例（引擎模块只加载一次）。"""
    return build_renderer(get_spec(spec_id), loader())
