"""历史入口的参数与产物适配；每次调用使用独立数据副本。

主题加载与渲染同处一个激活窗口，保护仍使用短名 import 和字体缓存的遗留引擎。
Document 引擎直接接收数据，不再通过模块 DATA/WATERMARK/META 交换请求状态。
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
import time
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .. import paths
from ..spec import RenderOptions, RenderResult, TemplateSpec
from .base import RenderError
from .loader import ThemeLoader

__all__ = [
    "LegacyDocumentRenderer", "LegacyGlobalRenderer", "LegacyPathRenderer",
    "StaticWebRenderer", "build_renderer",
]


def _assemble_kwargs(func: Callable[..., Any], opts: RenderOptions) -> dict[str, Any]:
    params = inspect.signature(func).parameters
    values = {
        "watermark": opts.watermark,
        "with_watermark": opts.watermark,
        "mark": True,
        "fonts": opts.font_policy,
    }
    return {name: value for name, value in values.items() if name in params}


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
        """返回独立副本；内置示例的读取也在主题激活窗口内。"""
        with self._loader.activated(self.spec.theme, self._theme_dir):
            if self.spec.default_data:
                path = self._theme_dir / self.spec.default_data
                if not path.is_file():
                    # Wheel 不包含运行中的用户 JSON；使用独立打包的合成示例。
                    from importlib.resources import files

                    examples = files("billgen").joinpath("examples")
                    if not examples.joinpath("manifest.json").is_file():
                        examples = paths.SRC.parent / "examples"
                    manifest = json.loads(examples.joinpath("manifest.json").read_text(encoding="utf-8"))
                    relative = path.relative_to(paths.THEMES.parent.parent).as_posix()
                    logical = "src/" + relative
                    entry = next((item for item in manifest["files"]
                                  if paths.relocated(item["target"]) == logical), None)
                    if entry is None:
                        raise RenderError(self.spec_id, f"缺少默认数据文件：{path}")
                    path = examples.joinpath(Path(entry["source"]).name)
                data = json.loads(path.read_text(encoding="utf-8"))
                if self.spec.variant:
                    data.setdefault("template", self.spec.variant)
                return data
            mod = self._module()
            # 演示/部署预览用 `DEMO_DATA`（虚构真人 + 真实行名/账号/路由），
            # `DATA` 留给 parity 回归（必须与资产里的样张一字不差）。
            demo = getattr(mod, "DEMO_DATA", None)
            if isinstance(demo, dict):
                return copy.deepcopy(demo)
            datasets = getattr(mod, "DATASETS", None)
            if isinstance(datasets, dict) and "demo" in datasets:
                return copy.deepcopy(datasets["demo"])
            inline = getattr(mod, "DATA", None)
            return copy.deepcopy(inline) if isinstance(inline, dict) else {}

    def render(self, data: Any, opts: RenderOptions | None = None) -> RenderResult:
        opts = opts or self.spec.default_options
        started = time.perf_counter()
        try:
            # 包括 load、惰性 import、字体缓存和 Document 关闭的完整临界区。
            with self._loader.activated(self.spec.theme, self._theme_dir):
                mod = self._module()
                func = getattr(mod, self.spec.entry or "", None)
                if not callable(func):
                    raise RenderError(self.spec_id, f"模块缺少入口 {self.spec.entry!r}")
                payload = self._produce(mod, func, copy.deepcopy(data), opts)
                pages = _page_count(payload)
        except RenderError:
            raise
        except Exception as exc:
            raise RenderError(self.spec_id, f"渲染失败：{exc}", exc) from exc
        payload = _scrub(payload, self.spec, data, opts)
        return RenderResult(
            pages=pages, pdf=payload,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    def _produce(self, mod, func, data, opts) -> bytes:
        raise NotImplementedError


def _subset_embedded_fonts(payload: bytes) -> bytes:
    """按实际用到的字符裁一遍已嵌入字体，并合并重复的字体对象。

    主题里的字体是从原始对账单抽出来的子集，直接嵌入时 PyMuPDF 会按
    insert_text 的次数各写一份字体对象（富国 6–12 份、Mercury 4 份）。
    同一页上重复嵌入既撑大文件，也让字体清单看起来不像账单引擎出件。
    """
    import fitz

    try:
        with fitz.open(stream=payload, filetype="pdf") as doc:
            doc.subset_fonts()
            return doc.tobytes(garbage=4, deflate=True)
    except Exception:
        return payload          # 裁字体失败不该让整次渲染失败


def _scrub(payload: bytes, spec: TemplateSpec, data: Any,
           opts: RenderOptions | None = None) -> bytes:
    """出件前的洗版：换掉 PyMuPDF 的对象结构/字体子集前缀/文件头。

    直渲路径没有 ledger，用 data 本身派生 seed —— 同样的 data 出同样的结构，
    不同 data 得到不同的字体子集名，不会被"同一份生成器"串起来。
    """
    from .. import pdfpost

    if not payload.startswith(b"%PDF-"):
        return payload
    seed = hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()[:16]
    producer = ""
    try:
        import fitz

        with fitz.open(stream=payload, filetype="pdf") as probe:
            producer = (probe.metadata or {}).get("producer") or ""
    except Exception:
        producer = ""
    if opts is not None and opts.font_policy == "subset":
        payload = _subset_embedded_fonts(payload)
    return pdfpost.finalize(
        payload, seed=seed, producer=producer, xmp=spec.theme in pdfpost.XMP_THEMES
    )


class LegacyPathRenderer(_BaseRenderer):
    """入口把 PDF 写入 out_path；工作文件仅在本请求临时目录内。"""

    def _produce(self, mod, func, data, opts) -> bytes:
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "statement.pdf"
            # These legacy engines read encryption from data, not a keyword.
            # Always override the request copy: preview must not inherit data.encrypt.
            if self.spec.theme in {"chase", "usbank"}:
                data["encrypt"] = opts.encrypt
            func(data, str(out), **_assemble_kwargs(func, opts))
            if not out.is_file():
                raise RenderError(self.spec_id, "入口未产出 PDF")
            return out.read_bytes()


def _document_bytes(doc) -> bytes:
    """沿用 P1 的保存参数，并在保存失败时也释放 Document。"""
    try:
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "statement.pdf"
            doc.save(str(out), garbage=3, deflate=True)
            return out.read_bytes()
    finally:
        doc.close()


class LegacyDocumentRenderer(_BaseRenderer):
    """AE：第二个参数是 dataset_label，而非输出文件名。"""

    def _produce(self, mod, func, data, opts) -> bytes:
        doc = func(data, **_assemble_kwargs(func, opts))
        try:
            # AE owns the legacy RC4 permissions and serialization defaults.
            return mod.pdf_bytes(doc, encrypt=opts.encrypt)
        finally:
            doc.close()


class LegacyGlobalRenderer(_BaseRenderer):
    """兼容注册表旧 adapter 名；实现已改为显式 data/watermark 参数。"""

    def _produce(self, mod, func, data, opts) -> bytes:
        return _document_bytes(func(data=data, watermark=opts.watermark))


class StaticWebRenderer(_BaseRenderer):
    """纯浏览器模板保留静态资源入口。"""

    def render(self, data: Any = None, opts: RenderOptions | None = None) -> RenderResult:
        raise RenderError(self.spec_id, "该模板在浏览器内渲染，无服务端 PDF 产物")

    def inventory(self) -> dict[str, Any]:
        root = self._theme_dir
        files = sorted(p for p in root.rglob("*") if p.is_file())
        return {
            "files": len(files), "bytes": sum(p.stat().st_size for p in files),
            "entry": "index.html" if (root / "index.html").is_file() else None,
            "pdf_lib": any("pdf-lib" in p.name for p in files),
            "fontkit": any("fontkit" in p.name for p in files),
        }


def _page_count(payload: bytes) -> int:
    import fitz

    with fitz.open(stream=payload, filetype="pdf") as doc:
        return doc.page_count


_ADAPTERS = {
    "path-out": LegacyPathRenderer, "document": LegacyDocumentRenderer,
    "global": LegacyGlobalRenderer, "static": StaticWebRenderer,
}


def build_renderer(spec: TemplateSpec, loader: ThemeLoader):
    try:
        cls = _ADAPTERS[spec.adapter]
    except KeyError:
        raise RenderError(spec.id, f"未知适配器类型：{spec.adapter}") from None
    return cls(spec, loader)
