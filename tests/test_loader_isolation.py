"""短名 import 缓存、惰性导入与多个 loader 的恢复契约。"""
import sys
from types import ModuleType

import pytest

from billgen.engine.loader import ThemeLoader


def test_foreign_cached_entry_is_not_used_and_is_restored(tmp_path, monkeypatch):
    name = "p2_cached_entry"
    foreign = ModuleType(name)
    foreign.VALUE = "foreign"
    monkeypatch.setitem(sys.modules, name, foreign)
    theme = tmp_path / "theme"
    theme.mkdir()
    (theme / f"{name}.py").write_text("VALUE = 'theme'\n")
    loader = ThemeLoader(tmp_path)
    assert loader.load("test", theme, name).VALUE == "theme"
    assert sys.modules[name] is foreign


def test_failed_import_restores_foreign_entry_and_child(tmp_path, monkeypatch):
    name, child = "p2_broken_entry", "p2_broken_child"
    foreign = ModuleType(name)
    monkeypatch.setitem(sys.modules, name, foreign)
    theme = tmp_path / "theme"
    theme.mkdir()
    (theme / f"{child}.py").write_text("VALUE = 1\n")
    (theme / f"{name}.py").write_text(f"import {child}\nraise ValueError('fixture import failure')\n")
    before_path = sys.path[:]
    with pytest.raises(ValueError, match="fixture import failure"):
        ThemeLoader(tmp_path).load("test", theme, name)
    assert sys.modules[name] is foreign
    assert child not in sys.modules
    assert sys.path == before_path


def test_two_loader_instances_do_not_share_short_name_modules(tmp_path):
    name = "p2_twins_entry"
    loaders = []
    for value in ("one", "two"):
        theme = tmp_path / value
        theme.mkdir()
        (theme / f"{name}.py").write_text(f"VALUE = {value!r}\n")
        loaders.append((ThemeLoader(tmp_path), theme, value))
    for loader, theme, value in loaders * 2:
        assert value == loader.load(value, theme, name).VALUE
        assert name not in sys.modules


def test_lazy_imports_are_cached_and_removed_after_window(tmp_path):
    name, child = "p2_lazy_entry", "p2_lazy_child"
    (tmp_path / f"{child}.py").write_text("VALUE = 'child'\n")
    (tmp_path / f"{name}.py").write_text(f"def run():\n    import {child}\n    return {child}.VALUE\n")
    loader = ThemeLoader(tmp_path)
    mod = loader.load("test", tmp_path, name)
    for _ in range(2):
        with loader.activated("test", tmp_path):
            assert mod.run() == "child"
        assert child not in sys.modules
    assert loader.module("test", child).VALUE == "child"


def test_foreign_indirect_import_is_replaced_before_entry_execution(tmp_path, monkeypatch):
    name, child = "p3_entry", "p3_child"
    foreign = ModuleType(child)
    foreign.VALUE = "foreign"
    monkeypatch.setitem(sys.modules, child, foreign)
    (tmp_path / f"{child}.py").write_text("VALUE = 'local'\n")
    (tmp_path / f"{name}.py").write_text(f"from {child} import VALUE\n")
    loader = ThemeLoader(tmp_path)
    assert loader.load("test", tmp_path, name).VALUE == "local"
    assert sys.modules[child] is foreign
