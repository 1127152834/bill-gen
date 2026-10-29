"""引擎公共类型与异常。"""
from __future__ import annotations

from ..spec import RenderOptions, RenderResult


class RenderError(RuntimeError):
    """渲染失败（数据非法、资产缺失、字体不全等）。"""

    def __init__(self, spec_id: str, message: str, cause: BaseException | None = None) -> None:
        super().__init__(f"[{spec_id}] {message}")
        self.spec_id = spec_id
        self.cause = cause


__all__ = ["RenderError", "RenderOptions", "RenderResult"]
