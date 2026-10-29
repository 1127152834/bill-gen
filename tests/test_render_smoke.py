"""渲染冒烟 + 确定性：同一份数据两次渲染必须给出同一份文本层。"""
from __future__ import annotations

import hashlib

import fitz
import pytest

from billgen import registry
from billgen.parity.baseline import probe

RENDERABLE = [s.id for s in registry.SPECS if s.kind != "static-web"]


def _text_sha(pdf: bytes) -> str:
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        text = "\n".join(page.get_text() for page in doc)
        return hashlib.sha256(text.encode()).hexdigest()[:16]
    finally:
        doc.close()


@pytest.mark.parametrize("spec_id", RENDERABLE)
def test_template_renders_a_pdf(spec_id):
    renderer = registry.get_renderer(spec_id)
    result = renderer.render(renderer.default_data())
    assert result.pdf.startswith(b"%PDF")
    assert result.pages >= 1
    assert len(result.pdf) > 5_000


@pytest.mark.parametrize("spec_id", RENDERABLE[:3])
def test_render_is_deterministic(spec_id):
    renderer = registry.get_renderer(spec_id)
    data = renderer.default_data()
    first = _text_sha(renderer.render(data).pdf)
    second = _text_sha(renderer.render(data).pdf)
    assert first == second


@pytest.mark.parametrize("spec_id", RENDERABLE[:3])
def test_watermark_toggle_changes_output(spec_id):
    from dataclasses import replace

    renderer = registry.get_renderer(spec_id)
    spec = registry.get_spec(spec_id)
    data = renderer.default_data()
    on = renderer.render(data, replace(spec.default_options, watermark=True)).pdf
    off = renderer.render(data, replace(spec.default_options, watermark=False)).pdf
    assert on != off, "水印开关没有产生任何差异"


def test_parity_probe_is_stable_for_one_template():
    first = probe("boa-personal")
    second = probe("boa-personal")
    for key in ("pages", "size", "text_sha256_16", "text_chars"):
        assert first[key] == second[key]
