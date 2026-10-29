"""可写工作区、旧路径资产和安装包资源的回归测试。"""
import importlib.util
import json
import os
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

from billgen import paths
from billgen.server.bootstrap import prepare_workspace
from scripts.assets import asset_paths, missing_assets, safe_asset_path

ROOT = Path(__file__).resolve().parents[1]


def test_workspace_is_idempotent_and_reset_backups_preserve_user_data(tmp_path, monkeypatch):
    monkeypatch.setenv("BILL_GEN_DATA_DIR", str(tmp_path))
    result = prepare_workspace()
    assert result == {"data_dir": str(tmp_path), "seeded": 8, "backups": 8}
    target = Path(paths.data_path("BOA/statement_template/editor/data_personal.json"))
    backup = Path(str(target) + ".bak")
    original = backup.read_bytes()
    target.write_text('{"name": "user edit"}', encoding="utf-8")
    backup.write_text('{"name": "user backup"}', encoding="utf-8")
    assert prepare_workspace() == {"data_dir": str(tmp_path), "seeded": 0, "backups": 0}
    assert json.loads(target.read_text())["name"] == "user edit"
    assert json.loads(backup.read_text())["name"] == "user backup"
    backup.unlink()
    assert prepare_workspace()["backups"] == 1
    assert backup.read_bytes() == original
    assert json.loads(target.read_text())["name"] == "user edit"


def test_default_workspace_is_user_data_not_cwd_or_package(tmp_path, monkeypatch):
    monkeypatch.delenv("BILL_GEN_DATA_DIR", raising=False)
    monkeypatch.delenv("BILLGEN_ROOT", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.chdir(tmp_path)
    target = Path(paths.data_path("BOA/statement_template/editor/data_personal.json"))
    assert target.is_relative_to(tmp_path)
    assert not target.is_relative_to(paths.THEMES)
    assert target != tmp_path / paths.relocated("BOA/statement_template/editor/data_personal.json")


def test_legacy_manifest_maps_to_real_assets():
    assert missing_assets(ROOT) == []
    for name in asset_paths(ROOT):
        assert safe_asset_path(ROOT, name).is_relative_to(ROOT / "src/billgen/themes")


@pytest.mark.parametrize("template_id", ["boa-personal", "wellsfargo", "chase-personal", "usbank-personal"])
def test_missing_theme_data_falls_back_to_packaged_examples(template_id, monkeypatch):
    from billgen import registry

    renderer = registry.get_renderer(template_id)
    expected = paths.theme_dir(renderer.spec.theme) / renderer.spec.default_data
    original = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda self: False if self == expected else original(self))
    assert isinstance(renderer.default_data(), dict)
    assert renderer.default_data()


def test_docker_uses_installed_single_service():
    """镜像里 billgen 只以「安装过的包 + 控制台脚本」形态出现：不临时拼 PYTHONPATH。"""
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "pip install --no-cache-dir -c constraints-ci.txt '.[mcp]'" in dockerfile
    assert "scripts/assets.py check" in dockerfile
    assert "PYTHONPATH" not in dockerfile
    entrypoint = (ROOT / "deploy" / "entrypoint.sh").read_text()
    assert "billgen serve" in entrypoint
    assert "billgen serve --host" in entrypoint
    assert "BILLGEN_PORT" in entrypoint
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    includes = config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert includes["runtime_paths.py"] == "runtime_paths.py"
    assert includes["examples"] == "billgen/examples"


@pytest.mark.skipif(importlib.util.find_spec("hatchling") is None, reason="wheel builder not installed")
def test_wheel_contains_assets_and_bootstraps_outside_source(tmp_path):
    # build directly through the configured backend, with no network/build isolation.
    script = "from hatchling.build import build_wheel; build_wheel(" + repr(str(tmp_path)) + ")"
    subprocess.run([sys.executable, "-c", script], cwd=ROOT, check=True, capture_output=True)
    wheel = next(tmp_path.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert "runtime_paths.py" in names
        assert "billgen/examples/manifest.json" in names
        assert "billgen/resources/private-assets.json" in names
        assert "billgen/themes/wellsfargo/data_sample.json" in names
        for resource in ("index.html", "app.css", "app.js", "legacy.css", "legacy.js"):
            assert f"billgen/server/web/{resource}" in names
        for name in asset_paths(ROOT):
            packaged = paths.relocated(name).removeprefix("src/")
            assert packaged in names, packaged
        assert not any(name.endswith(".bak") or "/__pycache__/" in name for name in names)
        assert not any(Path(name).name.startswith("data") and name.endswith(".json")
                       and name != "billgen/themes/wellsfargo/data_sample.json" for name in names)
        # A wheel is pure Python: extraction accurately tests import/resource resolution.
        installed = tmp_path / "installed"
        archive.extractall(installed)
    outside = tmp_path / "outside"
    outside.mkdir()
    env = {**os.environ, "PYTHONPATH": str(installed), "BILL_GEN_DATA_DIR": str(tmp_path / "data")}
    code = (
        "import runtime_paths; from pathlib import Path; "
        "from billgen.server.bootstrap import prepare_workspace; "
        "result=prepare_workspace(); assert result['seeded']==8; "
        "assert Path(runtime_paths.data_path('BOA/statement_template/editor/data_personal.json')).is_file(); "
        "assert prepare_workspace()['seeded']==0"
    )
    subprocess.run([sys.executable, "-c", code], cwd=outside, env=env, check=True, capture_output=True)
