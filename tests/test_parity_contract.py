"""静态资源门禁也必须检测模板缺失与依赖丢失。"""
from billgen.parity.baseline import compare

STATIC = {
    "kind": "static-web", "files": 3, "bytes": 1024,
    "entry": "index.html", "pdf_lib": True, "fontkit": True,
}


def test_missing_static_template_is_a_hard_difference():
    assert compare({"ally": STATIC}, {}) == ["ally: missing template"]


def test_static_inventory_changes_are_hard_differences():
    for key, value in (("files", 0), ("bytes", 0), ("entry", None), ("pdf_lib", False), ("fontkit", False)):
        changed = {**STATIC, key: value}
        diffs = compare({"ally": STATIC}, {"ally": changed})
        assert any(line.startswith(f"ally.{key}:") for line in diffs)


def test_legacy_vendor_keys_are_normalized():
    old = {"kind": "static-web", "vendor_pdf_lib": True, "vendor_fontkit": True}
    assert compare({"ally": old}, {"ally": STATIC}) == []
