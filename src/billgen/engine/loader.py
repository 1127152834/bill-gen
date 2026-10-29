"""主题模块的包加载与外部旧式主题兼容。

P4: 内置主题使用 billgen.themes.* 规范包导入，load() 与 activated()
不再注入主题路径或切换短名模块。以下隔离逻辑仅留给外部旧式主题测试/
兼容使用，执行窗口仍由共享锁保护。

历史背景：

为什么需要它
------------
11 个 Python 模板的引擎模块同名：``generate``（BOA 与大通分发器各一份）、
``chstream`` / ``barcode``（大通与 USbank 各一份），而且都以
``sys.path.insert(0, BASE) + import generate`` 的方式加载。同一进程里第二次
``import generate`` 会命中 ``sys.modules`` 缓存，拿到上一个模板的模块 ——
这正是原 ``run.py`` 必须起 8 个子进程来换取隔离的根因。

本模块的做法
------------
每个主题在**首次使用**时加载一次，并把该主题产生的所有短名模块对象留存在
``_ThemeState`` 里；切换主题时只做两件事：把上一主题的短名从 ``sys.modules``
摘掉、把新主题的短名绑回去。于是

* 每个主题的模块只 exec 一次（不会有重复 import 开销），
* 同一个进程里可以并存 13 套同名模块（各自持有自己的全局），
* 渲染顺序执行 + 锁保护，不存在跨主题污染。

P2 在完整调用窗口内串行化，并在退出时恢复外部导入环境。
短名 import 的包内相对化仍是后续工作；完成后本模块可退化为普通包导入。
"""
from __future__ import annotations

import importlib
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from ..server.state import RENDER_LOCK

__all__ = ["ThemeLoader", "LoaderError"]


class LoaderError(RuntimeError):
    pass


@dataclass
class _ThemeState:
    directory: Path
    modules: dict[str, ModuleType] = field(default_factory=dict)  # 短名 -> 模块对象


def _modules_under(directory: Path, namespace: dict[str, ModuleType]) -> dict[str, ModuleType]:
    root = str(directory.resolve()) + os.sep
    found: dict[str, ModuleType] = {}
    for name, mod in list(namespace.items()):
        filename = getattr(mod, "__file__", None)
        if not filename:
            continue
        try:
            if str(Path(filename).resolve()).startswith(root):
                found[name] = mod
        except OSError:
            continue
    return found


# sys.path/sys.modules 是进程级资源；所有 loader 实例必须共用同一把锁。
_IMPORT_LOCK = RENDER_LOCK


class ThemeLoader:
    """按主题加载并切换引擎模块，保证同名模块互不干扰。"""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._states: dict[str, _ThemeState] = {}
        self._active: str | None = None
        self._lock = _IMPORT_LOCK

    # -- 公共 API ---------------------------------------------------------
    def load(self, theme: str, theme_dir: Path, module: str) -> ModuleType:
        """返回 ``theme`` 的主题主模块（首次调用时加载）。"""
        package = _package_for(theme_dir)
        if package is not None:
            with self._lock:
                state = self._states.setdefault(theme, _ThemeState(Path(theme_dir)))
                if module not in state.modules:
                    loaded = importlib.import_module(f"{package}.{module}")
                    if not _is_own(Path(theme_dir), loaded):
                        raise LoaderError(f"主题模块不属于指定目录：{module} ({theme_dir})")
                    state.modules[module] = loaded
                return state.modules[module]
        with self.activated(theme, theme_dir):
            state = self._states[theme]
            if module not in state.modules:
                self._import_into(state, module)
            return state.modules[module]

    def module(self, theme: str, name: str) -> ModuleType | None:
        state = self._states.get(theme)
        return state.modules.get(name) if state else None

    @contextmanager
    def activated(self, theme: str, theme_dir: Path):
        """在 ``theme`` 的模块视图 + ``sys.path`` 下执行一段代码。

        遗留引擎会在**函数体内**做惰性 import（如 BOA ``generate.py:681`` 的
        ``import orig_embed``），依赖进程级 ``sys.path`` 长期包含主题目录 ——
        老服务端靠启动时 ``sys.path.insert(0, BASE)`` 一次性满足了这个假设。
        这里显式在调用窗口内维持该假设，窗口外恢复，避免污染其它主题。
        """
        if _package_for(theme_dir) is not None:
            # Built-in themes use qualified imports; retain serialization only.
            # No mutation of process-wide sys.path or short-name sys.modules.
            with self._lock:
                yield self
            return
        with self._lock:
            saved = sys.path[:]
            previous_modules = dict(sys.modules)
            previous_active = self._active
            # 记录入口同名项，即使 import 中途失败也恢复外部缓存。
            local_names = {path.stem for path in Path(theme_dir).glob("*.py")}
            sys.path.insert(0, str(self._root))
            sys.path.insert(0, str(Path(theme_dir)))
            # 间接 import 同样可能命中外部同名模块，须在入口执行前切换。
            state = self._activate(theme, Path(theme_dir))
            for name in local_names:
                if name in state.modules:
                    sys.modules[name] = state.modules[name]
                else:
                    sys.modules.pop(name, None)
            try:
                yield self
            finally:
                # 包括函数体内的惰性导入；下次切主题时这些模块也必须切换。
                state.modules.update(_modules_under(state.directory, dict(sys.modules)))
                owned = local_names | {name for item in self._states.values() for name in item.modules}
                for name in owned:
                    if name in previous_modules:
                        sys.modules[name] = previous_modules[name]
                    else:
                        sys.modules.pop(name, None)
                self._active = previous_active
                sys.path[:] = saved

    # -- 内部实现 ---------------------------------------------------------
    def _activate(self, theme: str, theme_dir: Path) -> _ThemeState:
        state = self._states.get(theme)
        if state is None:
            state = _ThemeState(directory=Path(theme_dir))
            self._states[theme] = state
            self._install(theme, state)
            return state
        if self._active != theme:
            self._install(theme, state)
        return state

    def _install(self, theme: str, state: _ThemeState) -> None:
        """把 sys.modules 的短名视图切换到 ``theme``。"""
        if self._active is not None and self._active != theme:
            for name in self._states[self._active].modules:
                current = sys.modules.get(name)
                if current is self._states[self._active].modules[name]:
                    sys.modules.pop(name, None)
        for name, mod in state.modules.items():
            sys.modules[name] = mod
        self._active = theme

    def _import_into(self, state: _ThemeState, module: str) -> None:
        saved = sys.path[:]
        # 仓库根在前（runtime_paths 兼容层），主题目录在后（更优先）
        sys.path.insert(0, str(self._root))
        sys.path.insert(0, str(state.directory))
        try:
            # 显式加载主题入口，避免命中进程中无关包的同名短模块缓存。
            candidate = state.directory / (module.replace(".", os.sep) + ".py")
            cached = sys.modules.get(module)
            if candidate.is_file() and cached is not None and not _is_own(state.directory, cached):
                sys.modules.pop(module)
            mod = importlib.import_module(module)
            if not _is_own(state.directory, mod):
                raise LoaderError(f"主题模块不属于指定目录：{module} ({state.directory})")
        finally:
            sys.path[:] = saved
        seen = _modules_under(state.directory, dict(sys.modules))
        # 新加载的模块对象并入本主题视图（含间接 import 的同目录模块）
        for name, obj in seen.items():
            state.modules.setdefault(name, obj)
        state.modules[module] = mod
        # 只把自己目录下的模块留在 sys.modules（其余保持原样）
        for name in list(state.modules):
            if name in sys.modules and _is_own(state.directory, sys.modules[name]):
                continue
            sys.modules.pop(name, None)
        for name, obj in state.modules.items():
            sys.modules[name] = obj


def _package_for(directory: Path) -> str | None:
    """Only actual bundled theme directories take the qualified import path."""
    root = Path(__file__).resolve().parents[1] / "themes"
    directory = Path(directory).resolve()
    if directory.parent == root.resolve() and directory.is_dir():
        return f"billgen.themes.{directory.name}"
    return None


def _is_own(directory: Path, mod: ModuleType) -> bool:
    filename = getattr(mod, "__file__", None)
    if not filename:
        return False
    try:
        return str(Path(filename).resolve()).startswith(str(directory.resolve()) + os.sep)
    except OSError:
        return False
