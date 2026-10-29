"""模板契约：一个模板 = 一条 :class:`TemplateSpec` + 一个 :class:`Renderer` 实例。

这里定义的四件事是整个重构的骨架：

* :class:`TemplateSpec` —— 模板的静态描述（不含任何运行时状态）
* :class:`RenderOptions` —— 输出选项（水印/加密/字体策略），**与业务数据彻底分离**
* :class:`RenderResult` —— 渲染产物（字节 + 页数 + 每页预览哈希）
* :class:`Renderer` —— 统一渲染协议，取代现在 5 种互不兼容的入口签名
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

TemplateKind = Literal["from-scratch", "replay", "static-web"]
AdapterKind = Literal["path-out", "document", "global", "static"]
FontPolicy = Literal["declared", "subset", "embed-all"]

__all__ = [
    "AdapterKind",
    "FontPolicy",
    "RenderOptions",
    "RenderResult",
    "Renderer",
    "TemplateKind",
    "TemplateSpec",
]


@dataclass(frozen=True, slots=True)
class RenderOptions:
    """输出选项。历史实现把水印开关写进数据 JSON（``data["watermark"] = ...``），
    这里把它拉出来当作纯输出选项，引擎不得回写数据。"""

    watermark: bool = True
    encrypt: bool = False
    font_policy: FontPolicy = "declared"
    page_dpi: int = 110

    def __post_init__(self) -> None:  # pragma: no cover - 参数校验
        if self.page_dpi <= 0:
            raise ValueError("page_dpi 必须为正整数")


@dataclass(frozen=True, slots=True)
class RenderResult:
    pages: int
    pdf: bytes
    hashes: tuple[str, ...] = ()
    elapsed_ms: int = 0


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    """模板静态描述。``theme`` 是 ``billgen/themes`` 下的目录键。"""

    id: str
    display_name: str
    kind: TemplateKind
    theme: str
    adapter: AdapterKind
    module: str | None = None           # 相对主题目录的模块名（不含 .py）
    entry: str | None = None            # 入口函数名
    default_data: str | None = None     # 相对主题目录的默认数据文件
    variant: str | None = None          # 数据里的 template 取值
    supports: frozenset[str] = field(default_factory=frozenset)  # {"watermark","encrypt","preview"}
    legacy_prefixes: tuple[str, ...] = ()   # 旧网关/URL 前缀
    default_options: RenderOptions = field(default_factory=RenderOptions)

    def __post_init__(self) -> None:
        if self.kind != "static-web" and not (self.module and self.entry):
            raise ValueError(f"{self.id}: 非静态模板必须声明 module 与 entry")


@runtime_checkable
class Renderer(Protocol):
    """统一渲染协议。实现方不得持有跨请求可变状态。"""

    spec_id: str

    def render(self, data: Any, opts: RenderOptions | None = None) -> RenderResult: ...
