"""向后兼容层 —— 遗留模板脚本仍会 ``from runtime_paths import data_path``。

真正的实现在 :mod:`billgen.paths`；这里只做两件事：

1. 把 ``<repo>/src`` 放进 ``sys.path``，让 ``python3 src/billgen/themes/boa/generate.py``
   这种「直接执行单个模板」的老用法还能 ``import billgen``；
2. 原样转发 ``data_path`` / ``font_path`` / ``cjk_font_path``，其中 ``data_path``
   会按搬迁表把旧逻辑路径（``富国/statement_template/editor/data.json``）翻译到
   新物理位置（``src/billgen/themes/wellsfargo/editor/data.json``）。

新代码请直接 ``from billgen import paths``，不要用本模块。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_SRC = ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from billgen import paths as _paths  # noqa: E402
from billgen.paths import cjk_font_path, font_path  # noqa: E402,F401

__all__ = ["ROOT", "cjk_font_path", "data_path", "font_path"]


def data_path(relative: str, root: Path | str | None = None) -> str:
    """旧签名 ``data_path(relative, root=...)`` → 新实现（含搬迁映射）。"""
    return _paths.data_path(relative, root=Path(root) if root else _paths.ROOT)
