"""注册表与主题目录的契约测试：13 个模板必须齐全且自洽。"""
from __future__ import annotations

import pytest

from billgen import paths, registry

EXPECTED_IDS = {
    "boa-personal", "boa-business", "wellsfargo", "amex", "mercury",
    "capitalone", "sofi", "chase-personal", "chase-business",
    "usbank-personal", "usbank-business", "alliant", "ally",
}


def test_all_thirteen_templates_registered():
    assert {spec.id for spec in registry.SPECS} == EXPECTED_IDS


def test_spec_ids_are_unique():
    ids = [spec.id for spec in registry.SPECS]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("spec", registry.SPECS, ids=lambda s: s.id)
def test_theme_directory_exists(spec):
    assert paths.theme_dir(spec.theme).is_dir()


@pytest.mark.parametrize("spec", registry.SPECS, ids=lambda s: s.id)
def test_adapter_is_known(spec):
    assert spec.adapter in {"path-out", "document", "global", "static"}


@pytest.mark.parametrize(
    "spec", [s for s in registry.SPECS if s.kind != "static-web"], ids=lambda s: s.id
)
def test_python_templates_declare_module_and_entry(spec):
    assert spec.module, f"{spec.id} 缺少 module"
    assert spec.entry, f"{spec.id} 缺少 entry"


@pytest.mark.parametrize("spec", [s for s in registry.SPECS if s.default_data], ids=lambda s: s.id)
def test_declared_default_data_exists(spec):
    assert (paths.theme_dir(spec.theme) / spec.default_data).is_file()


def test_unknown_template_raises():
    with pytest.raises(registry.TemplateNotFound):
        registry.get_spec("nope")


def test_capabilities_cover_known_flags():
    known = {"watermark", "encrypt", "preview"}
    for spec in registry.SPECS:
        assert spec.supports <= known, f"{spec.id} 声明了未知能力：{spec.supports - known}"
