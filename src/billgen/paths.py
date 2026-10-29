"""运行期路径解析：数据目录、字体定位、主题资产定位。

历史背景
--------
原仓库把「逻辑名」和「物理路径」混成一个东西：引擎里直接写
``data_path("USbank/statement_template/editor_us/data_us.json")``。
P1 把 13 个模板目录迁进 ``src/billgen/themes/`` 之后，这类调用会全部失效。

本模块用一张**搬迁映射表**把它们重定向到新位置，于是 20+ 处调用点
一行都不用改（P4 再把调用点逐个换成 ``theme_dir()``）。

约定
----
* ``data_path(relative)``：仓库内（或 ``BILL_GEN_DATA_DIR`` 下）的可写数据路径，
  拒绝绝对路径、``..``、反斜杠，并防止通过符号链接跳出数据根。
* ``theme_dir(spec_id)``：主题资产目录（只读），包内相对。
* ``font_path(style)`` / ``cjk_font_path()``：系统字体探测，允许环境变量覆盖。
"""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

__all__ = [
    "ROOT",
    "SRC",
    "THEMES",
    "data_path",
    "font_path",
    "cjk_font_path",
    "theme_dir",
    "relocated",
]

_SRC = Path(__file__).resolve().parent.parent          # src/
_SRC_REPO = _SRC.parent                                 # 源码树根（安装后不存在）


def _resolve_root() -> Path:
    configured = os.environ.get("BILLGEN_ROOT")
    if configured:
        return Path(configured).resolve()
    if (_SRC_REPO / "pyproject.toml").is_file():
        return _SRC_REPO
    return Path.cwd().resolve()


ROOT = _resolve_root()
SRC = _SRC
THEMES = _SRC / "billgen" / "themes"

# 旧逻辑路径前缀 → 新物理路径（相对仓库根）。按前缀长度降序匹配。
_RELOCATED: dict[str, str] = {
    "AE": "src/billgen/themes/amex",
    "BOA/statement_template": "src/billgen/themes/boa",
    "USbank/statement_template": "src/billgen/themes/usbank",
    "alliant": "src/billgen/themes/web/alliant",
    "ally bank": "src/billgen/themes/web/ally",
    "富国/statement_template": "src/billgen/themes/wellsfargo",
    "大通/statement_template": "src/billgen/themes/chase",
    "水星C1sofi/mercury-statement-template": "src/billgen/themes/mercury",
    "水星C1sofi/c1-statement-template": "src/billgen/themes/capitalone",
    "水星C1sofi/sofi-statement-template": "src/billgen/themes/sofi",
    "水星C1sofi/statement-editor": "src/billgen/server/portal",
}

_PREFIXES = sorted(_RELOCATED, key=len, reverse=True)

# 主题目录键 → 包内相对目录（不含 src/billgen/themes 前缀）
_THEME_DIRS: dict[str, str] = {
    "amex": "amex",
    "boa": "boa",
    "wellsfargo": "wellsfargo",
    "chase": "chase",
    "usbank": "usbank",
    "mercury": "mercury",
    "capitalone": "capitalone",
    "sofi": "sofi",
    "web/alliant": "web/alliant",
    "web/ally": "web/ally",
}


def relocated(relative: str) -> str:
    """把旧逻辑路径翻译成搬迁后的路径；无匹配时原样返回。"""
    text = str(PurePosixPath(relative))
    for old in _PREFIXES:
        if text == old:
            return _RELOCATED[old]
        if text.startswith(old + "/"):
            return _RELOCATED[old] + text[len(old):]
    return text


def _data_base(root: Path) -> Path:
    configured = os.environ.get("BILL_GEN_DATA_DIR")
    if configured and not Path(configured).is_absolute():
        raise ValueError("BILL_GEN_DATA_DIR 必须是绝对路径")
    return Path(configured).resolve() if configured else Path(root).resolve()


def data_path(relative: str, root: Path | str = ROOT) -> str:
    """返回可写数据文件的绝对路径，父目录自动创建。

    ``relative`` 可以是搬迁前的旧路径，会被 :func:`relocated` 自动翻译。
    """
    rel = PurePosixPath(relocated(str(relative)))
    if rel.is_absolute() or ".." in rel.parts or "\\" in str(rel) or not rel.parts:
        raise ValueError("运行数据路径必须是项目内的相对路径")
    base = _data_base(Path(root))
    target = (base / rel).resolve()
    if not target.is_relative_to(base):
        raise ValueError("运行数据路径不能通过链接跳出数据目录")
    target.parent.mkdir(parents=True, exist_ok=True)
    return str(target)


def theme_dir(theme: str) -> Path:
    """主题目录的绝对路径（只读资产）。``theme`` 是目录键，如 ``chase``、``web/ally``。"""
    rel = _THEME_DIRS.get(theme, theme)
    target = THEMES / rel
    if not target.is_dir():
        raise KeyError(
            f"未登记的主题：{theme}（可用：{', '.join(_THEME_DIRS)}）"
        )
    return target


def font_path(style: str = "Regular") -> str:
    configured = os.environ.get("STATEMENT_FONT_DIR")
    directories = ([Path(configured)] if configured else []) + [
        Path("/usr/share/fonts/truetype/liberation2"),
        Path("/usr/share/fonts/truetype/liberation"),
    ]
    for directory in directories:
        candidate = directory / f"LiberationSans-{style}.ttf"
        if candidate.is_file():
            return str(candidate)
    suffix = {"Regular": "", "Bold": " Bold", "Italic": " Italic", "BoldItalic": " Bold Italic"}[style]
    candidate = Path("/System/Library/Fonts/Supplemental") / f"Arial{suffix}.ttf"
    if candidate.is_file():
        return str(candidate)
    raise RuntimeError("缺少字体：请安装 Liberation Sans，或设置 STATEMENT_FONT_DIR。")


def cjk_font_path() -> str | None:
    configured = os.environ.get("STATEMENT_CJK_FONT")
    candidates = ([Path(configured)] if configured else []) + [
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
    ]
    return next((str(p) for p in candidates if p.is_file()), None)
