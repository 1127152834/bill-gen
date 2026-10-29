"""Built-in engines share qualified package identities without alias injection."""
import ast
import importlib
import sys
from pathlib import Path
from types import ModuleType

from billgen import paths
from billgen.engine.loader import ThemeLoader


def test_builtin_loader_uses_canonical_modules_and_preserves_aliases(monkeypatch):
    aliases = {name: ModuleType(name) for name in (
        "generate", "generate_personal", "generate_us", "chstream", "barcode", "orig_embed"
    )}
    for name, module in aliases.items():
        monkeypatch.setitem(sys.modules, name, module)
    before = sys.path[:]
    first, second = ThemeLoader(paths.ROOT), ThemeLoader(paths.ROOT)
    for theme, entry in [("boa", "generate"), ("chase", "generate_personal"),
                         ("usbank", "generate_us"), ("wellsfargo", "generate")]:
        directory = paths.theme_dir(theme)
        canonical = importlib.import_module(f"billgen.themes.{theme}.{entry}")
        assert first.load(theme, directory, entry) is canonical
        assert second.load(theme, directory, entry) is canonical
        with first.activated(theme, directory):
            assert sys.path == before
            assert all(sys.modules[name] is module for name, module in aliases.items())
    assert sys.path == before
    assert all(sys.modules[name] is module for name, module in aliases.items())


def test_no_theme_sibling_short_imports_or_path_injection():
    root = Path(__file__).resolve().parents[1] / "src/billgen/themes"
    violations = []
    for theme in root.iterdir():
        if not theme.is_dir():
            continue
        siblings = {file.stem for file in theme.glob("*.py")}
        for file in theme.rglob("*.py"):
            source = file.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(source)):
                names = [alias.name.split(".")[0] for alias in node.names] if isinstance(node, ast.Import) else []
                if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names.append(node.module.split(".")[0])
                if siblings.intersection(names):
                    violations.append(f"{file.relative_to(root)}:{node.lineno}")
            if "sys.path.insert(" in source:
                violations.append(f"{file.relative_to(root)}:sys.path")
    assert violations == []
