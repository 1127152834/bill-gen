"""公共 Renderer 的副本、异常恢复、交错主题与线程隔离契约。"""
from __future__ import annotations

import copy
import hashlib
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

import fitz
import pytest

from billgen import registry
from billgen.engine.base import RenderError
from billgen.parity.baseline import PARITY_KEYS, probe

IDS = [spec.id for spec in registry.SPECS if spec.kind != "static-web"]


def text(pdf):
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


@pytest.mark.parametrize("spec_id", IDS)
def test_render_does_not_mutate_input(spec_id):
    renderer = registry.get_renderer(spec_id)
    data = renderer.default_data()
    before = copy.deepcopy(data)
    renderer.render(data)
    assert data == before


def test_default_data_is_a_deep_copy():
    renderer = registry.get_renderer("mercury")
    first = renderer.default_data()
    before = copy.deepcopy(first)
    first.clear()
    assert renderer.default_data() == before


def test_interleaved_and_threaded_rendering_matches_serial_fingerprints():
    ids = ["mercury", "amex", "sofi", "boa-personal", "chase-personal", "usbank-personal"]
    expected = {key: probe(key) for key in ids}
    with ThreadPoolExecutor(max_workers=4) as pool:
        actual = list(pool.map(probe, list(reversed(ids)) + ids))
    for item in actual:
        for key in PARITY_KEYS:
            assert item[key] == expected[item["id"]][key], (item["id"], key)


def test_distinct_requests_do_not_exchange_customer_data():
    renderer = registry.get_renderer("mercury")
    original = renderer.default_data()
    # 选择默认数据里确实显示的客户名字段，显式替换后验证文本归属。
    key = next((k for k in ("customer_name", "company", "holder_name", "account_name")
                if isinstance(original.get(k), str)), None)
    if key is None:
        key = next(k for k, v in original.items() if isinstance(v, str) and v.strip())
    requests = []
    for name, watermark in [("ALPHA REQUEST", True), ("BRAVO REQUEST", False)] * 2:
        data = copy.deepcopy(original)
        data[key] = name
        requests.append((data, name, watermark))

    def run(item):
        data, name, watermark = item
        output = text(renderer.render(data, replace(renderer.spec.default_options, watermark=watermark)).pdf)
        return name, output

    with ThreadPoolExecutor(max_workers=4) as pool:
        for name, output in pool.map(run, requests):
            assert name in output
            assert ("BRAVO REQUEST" if name == "ALPHA REQUEST" else "ALPHA REQUEST") not in output
    assert renderer.default_data() == original


def test_failure_restores_import_environment_and_leaves_next_request_usable():
    renderer = registry.get_renderer("boa-personal")
    module = renderer._module()
    before_path = sys.path[:]
    before_short = {key: sys.modules.get(key) for key in ("generate", "orig_embed", "chstream")}
    with (
        patch.object(module, "build_pdf", side_effect=ValueError("render fixture failure")),
        pytest.raises(RenderError, match="render fixture failure"),
    ):
        renderer.render(renderer.default_data())
    assert sys.path == before_path
    assert all(sys.modules.get(key) is value for key, value in before_short.items())
    assert renderer.render(renderer.default_data()).pages == 4


def test_amex_adapter_does_not_use_temporary_filename_as_dataset_label():
    renderer = registry.get_renderer("amex")
    module = renderer._module()
    original = module.build_pdf
    calls = []

    def wrapped(data, dataset_label="custom", **kwargs):
        calls.append(dataset_label)
        return original(data, dataset_label=dataset_label, **kwargs)

    with patch.object(module, "build_pdf", wrapped):
        renderer.render(renderer.default_data())
    assert calls == ["custom"]


def test_document_is_closed_when_serialization_fails():
    from billgen.engine.adapters import _document_bytes

    class BrokenDocument:
        closed = False

        def save(self, *args, **kwargs):
            raise ValueError("save fixture failure")

        def close(self):
            self.closed = True

    document = BrokenDocument()
    with pytest.raises(ValueError, match="save fixture failure"):
        _document_bytes(document)
    assert document.closed


def test_raster_fingerprint_is_repeatable():
    renderer = registry.get_renderer("boa-personal")

    def raster_digest():
        result = renderer.render(renderer.default_data())
        with fitz.open(stream=result.pdf, filetype="pdf") as doc:
            return [hashlib.sha256(page.get_pixmap().samples).hexdigest() for page in doc]

    assert raster_digest() == raster_digest()
