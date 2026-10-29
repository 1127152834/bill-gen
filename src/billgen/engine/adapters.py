"""把 5 种历史入口签名统一成 :class:`billgen.spec.Renderer`。

历史签名清单（实测）
--------------------
======================================  ==================================
``build_pdf(d, out, watermark=True)``   BOA / 大通 / USbank / 富国
``build_pdf(d, out, watermark, mark, fonts)``  富国
``build_pdf(data, label, with_watermark, metadata, xmp)``  AE（返回 Document）
``build(meta=None)`` + 模块全局 DATA/WATERMARK/META    Mercury / C1 / SoFi
（无后端，浏览器内渲染）                  alliant / ally
======================================  ==================================

适配层只做「参数装配 + 产物归一」，不碰任何版式逻辑 —— 所以搬迁和接口统一
不会改变任何一页 PDF。
"""
from __future__ import annotations

import inspect
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

from .. import paths
from ..spec import RenderOptions, RenderResult, TemplateSpec
from .base import RenderError
from .loader import ThemeLoader

__all__ = [
    "LegacyDocumentRenderer",
    "LegacyGlobalRenderer",
    "LegacyPathRenderer",
    "StaticWebRenderer",
    "build_renderer",
]


def _assemble_kwargs(func: Callable[..., Any], opts: RenderOptions) -> dict[str, Any]:
    """按目标函数真实签名装配选项，避免 ``unexpected keyword argument``。

    水印参数名在仓库里并不统一（``watermark`` / ``with_watermark``），这正是
    要把入口签名收敛掉的证据；适配层在这里吸收掉差异。
    """
    params = inspect.signature(func).parameters
    kw: dict[str, Any] = {}
    if "watermark" in params:
        kw["watermark"] = opts.watermark
    if "with_watermark" in params:
        kw["with_watermark"] = opts.watermark
    if "mark" in params:
        kw["mark"] = True                      # 富国的「标记」是独立开关，历史默认开
    if "fonts" in params:
        kw["fonts"] = opts.font_policy
    return kw


class _BaseRenderer:
    spec_id: str

    def __init__(self, spec: TemplateSpec, loader: ThemeLoader) -> None:
        self.spec = spec
        self.spec_id = spec.id
        self._loader = loader
        self._theme_dir = paths.theme_dir(spec.theme)

    def _module(self):
        assert self.spec.module
        return self._loader.load(self.spec.theme, self._theme_dir, self.spec.module)

    def default_data(self) -> dict[str, Any]:
        """取模板自带的示例数据。

        优先级：``spec.default_data`` 指向的 JSON 文件 → 模块内 ``DATASETS['demo']``
        → 模块级 ``DATA``（Mercury / C1 / SoFi 的写法）→ 空字典。
        """
        import copy
        import json

        if self.spec.default_data:
            path = self._theme_dir / self.spec.default_data
            if not path.is_file():
                raise RenderError(self.spec_id, f"缺少默认数据文件：{path}")
            data = json.loads(path.read_text(encoding="utf-8"))
            if self.spec.variant:
                data.setdefault("template", self.spec.variant)
            return data
        mod = self._module()
        datasets = getattr(mod, "DATASETS", None)
        if isinstance(datasets, dict) and "demo" in datasets:
            return copy.deepcopy(datasets["demo"])
        inline = getattr(mod, "DATA", None)
        if isinstance(inline, dict):
            return copy.deepcopy(inline)
        return {}


class LegacyPathRenderer(_BaseRenderer):
    """入口把 PDF 写入 ``out_path``。"""

    def render(self, data: Any, opts: RenderOptions | None = None) -> RenderResult:
        opts = opts or self.spec.default_options
        mod = self._module()
        func = getattr(mod, self.spec.entry or "", None)
        if not callable(func):
            raise RenderError(self.spec_id, f"模块缺少入口 {self.spec.entry!r}")
        kw = _assemble_kwargs(func, opts)
        started = time.perf_counter()
        with self._loader.activated(self.spec.theme, self._theme_dir):
            with TemporaryDirectory() as tmp:
                out = Path(tmp) / "statement.pdf"
                try:
                    func(data, str(out), **kw)
                except Exception as exc:  # noqa: BLE001 - 统一包成 RenderError
                    raise RenderError(self.spec_id, f"渲染失败：{exc}", exc) from exc
                if not out.is_file():
                    raise RenderError(self.spec_id, "入口未产出 PDF")
                payload = out.read_bytes()
        return RenderResult(
            pages=_page_count(payload),
            pdf=payload,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )


class LegacyDocumentRenderer(_BaseRenderer):
    """入口返回 ``fitz.Document``（写盘与否由调用方决定）。"""

    def render(self, data: Any, opts: RenderOptions | None = None) -> RenderResult:
        opts = opts or self.spec.default_options
        mod = self._module()
        func = getattr(mod, self.spec.entry or "", None)
        if not callable(func):
            raise RenderError(self.spec_id, f"模块缺少入口 {self.spec.entry!r}")
        kw = _assemble_kwargs(func, opts)
        started = time.perf_counter()
        try:
            with self._loader.activated(self.spec.theme, self._theme_dir):
                with TemporaryDirectory() as tmp:
                    out = Path(tmp) / "statement.pdf"
                    doc = func(data, str(out), **kw)
                    if hasattr(doc, "save") and not Path(out).is_file():
                        doc.save(str(out), garbage=3, deflate=True)
                    if hasattr(doc, "close"):
                        doc.close()
                    payload = Path(out).read_bytes()
        except RenderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RenderError(self.spec_id, f"渲染失败：{exc}", exc) from exc
        return RenderResult(
            pages=_page_count(payload),
            pdf=payload,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )


class LegacyGlobalRenderer(_BaseRenderer):
    """入口不接收数据，靠模块级 ``DATA`` / ``WATERMARK`` / ``META`` 注入。

    适配层用「设置 → 调用 → 还原」包住，使跨请求状态不外泄（这是 P2 要彻底
    消灭的全局态，先在这里止血）。
    """

    def render(self, data: Any, opts: RenderOptions | None = None) -> RenderResult:
        opts = opts or self.spec.default_options
        mod = self._module()
        func = getattr(mod, self.spec.entry or "", None)
        if not callable(func):
            raise RenderError(self.spec_id, f"模块缺少入口 {self.spec.entry!r}")
        saved = {name: getattr(mod, name) for name in ("DATA", "WATERMARK", "META") if hasattr(mod, name)}
        started = time.perf_counter()
        try:
            if "DATA" in saved:
                mod.DATA = data
            if "WATERMARK" in saved:
                mod.WATERMARK = opts.watermark
            # META 不要置 None：这三个引擎的 build() 会直接迭代模块级 META
            # （``for k, v in META.items()``），置 None 会抛 'NoneType' is not iterable。
            with self._loader.activated(self.spec.theme, self._theme_dir), \
                    TemporaryDirectory() as tmp:
                out = Path(tmp) / "statement.pdf"
                doc = func(meta=None)
                if hasattr(doc, "save"):
                    doc.save(str(out), garbage=3, deflate=True)
                if hasattr(doc, "close"):
                    doc.close()
                payload = Path(out).read_bytes()
        except Exception as exc:  # noqa: BLE001
            raise RenderError(self.spec_id, f"渲染失败：{exc}", exc) from exc
        finally:
            for name, value in saved.items():
                setattr(mod, name, value)
        return RenderResult(
            pages=_page_count(payload),
            pdf=payload,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )


class StaticWebRenderer(_BaseRenderer):
    """纯前端模板：没有 Python 渲染管线，只有静态资源。"""

    def render(self, data: Any = None, opts: RenderOptions | None = None) -> RenderResult:
        raise RenderError(self.spec_id, "该模板在浏览器内渲染，无服务端 PDF 产物")

    def inventory(self) -> dict[str, Any]:
        root = self._theme_dir
        files = sorted(p for p in root.rglob("*") if p.is_file())
        return {
            "files": len(files),
            "bytes": sum(p.stat().st_size for p in files),
            "entry": "index.html" if (root / "index.html").is_file() else None,
            "pdf_lib": any("pdf-lib" in p.name for p in files),
            "fontkit": any("fontkit" in p.name for p in files),
        }


def _page_count(payload: bytes) -> int:
    import fitz

    doc = fitz.open(stream=payload, filetype="pdf")
    try:
        return doc.page_count
    finally:
        doc.close()


_ADAPTERS = {
    "path-out": LegacyPathRenderer,
    "document": LegacyDocumentRenderer,
    "global": LegacyGlobalRenderer,
    "static": StaticWebRenderer,
}


def build_renderer(spec: TemplateSpec, loader: ThemeLoader):
    """按 ``spec.adapter`` 造出对应渲染器（``global`` 为隐式第三种老范式）。"""
    try:
        cls = _ADAPTERS[spec.adapter]
    except KeyError:
        raise RenderError(spec.id, f"未知适配器类型：{spec.adapter}") from None
    return cls(spec, loader)
