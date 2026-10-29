"""Transport-free application API and real legacy encryption contracts."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

import fitz
import pytest

from billgen import registry
from billgen.parity.baseline import PARITY_KEYS, probe
from billgen.server.application import ApplicationError, ApplicationService
from billgen.spec import RenderResult


class FakeRenderer:
    def __init__(self):
        self.calls = []

    def default_data(self):
        return {"customer": {"name": "DEFAULT"}}

    def render(self, data, opts):
        self.calls.append((data, opts))
        name = data["customer"]["name"]
        data["customer"]["name"] = "mutated"
        with fitz.open() as doc:
            doc.new_page(width=200, height=100).insert_text((10, 30), name)
            kwargs = ({"encryption": fitz.PDF_ENCRYPT_AES_256, "owner_pw": "fixture"}
                      if opts.encrypt else {})
            return RenderResult(pages=1, pdf=doc.tobytes(**kwargs))


@pytest.fixture
def fake():
    renderer = FakeRenderer()
    with patch.object(registry, "get_renderer", return_value=renderer):
        yield renderer


def test_inventory_and_static_contract():
    service = ApplicationService()
    items = {item["id"]: item for item in service.list_templates()}
    assert len(items) == 13
    assert items["ally"]["static_entry"] == "/ally/index.html"
    assert items["ally"]["supports"] == []
    assert items["ally"]["server_render"] is False
    assert items["amex"]["default_options"]["encrypt"] is True
    assert items["chase-personal"]["default_options"]["encrypt"] is False
    assert "encrypt" not in items["boa-personal"]["supports"]
    for method in (service.default_data, service.preview, service.export_pdf):
        with pytest.raises(ApplicationError) as error:
            method("ally")
        assert error.value.code == "static_template"


def test_preview_export_options_and_artifacts(fake):
    service = ApplicationService()
    preview = service.preview("amex", options={"page_dpi": 72}, request_id="req-1")
    assert preview["options"]["encrypt"] is False
    assert preview["pdf"]["encrypted"] is False
    assert preview["page_info"][0]["pixel_width"] == 200
    assert service.get_page_png(preview["job_id"], 0).startswith(b"\x89PNG")
    assert service.get_pdf(preview["job_id"]).startswith(b"%PDF")
    assert service.get_job(preview["job_id"])["request_id"] == "req-1"
    exported = service.export_pdf("amex", request_id="req-1")
    assert exported["pdf"]["encrypted"] is True
    assert exported["job_id"] != preview["job_id"]
    assert service.export_pdf("chase-personal")["options"]["encrypt"] is False
    assert service.export_pdf("amex", options={"encrypt": False})["pdf"]["encrypted"] is False
    with pytest.raises(ApplicationError, match="support encryption"):
        service.export_pdf("boa-personal", options={"encrypt": True})
    preview["pdf"]["metadata"].clear()
    assert service.get_job(preview["job_id"])["pdf"]["metadata"]


@pytest.mark.parametrize("options", [{"encrypt": 1}, {"watermark": "false"},
    {"page_dpi": True}, {"page_dpi": 0}, {"page_dpi": 301}, {"font_policy": "bad"},
    {"unknown": 1}, []])
def test_bad_options(options, fake):
    with pytest.raises(ApplicationError) as error:
        ApplicationService().preview("amex", options=options)
    assert error.value.code == "invalid_options"
    assert not fake.calls


@pytest.mark.parametrize("data", [[], "bad", {"value": float("nan")}, {"value": object()}])
def test_bad_data(data, fake):
    with pytest.raises(ApplicationError) as error:
        ApplicationService().preview("amex", data)
    assert error.value.code == "invalid_data"


def test_request_copies_and_threads(fake):
    service = ApplicationService()
    data = {"customer": {"name": "ALPHA"}}
    service.preview("amex", data)
    assert data["customer"]["name"] == "ALPHA"
    default = service.default_data("amex")
    default.clear()
    assert service.default_data("amex")["customer"]["name"] == "DEFAULT"
    names = ["BRAVO", "CHARLIE", "DELTA"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(lambda name: service.preview("amex", {"customer": {"name": name}}), names))
    for job, name in zip(jobs, names, strict=True):
        with fitz.open(stream=service.get_pdf(job["job_id"]), filetype="pdf") as doc:
            assert name in doc[0].get_text()


def test_cache_count_ttl_bytes_and_close(fake):
    service = ApplicationService(max_jobs=1, ttl_seconds=10)
    with patch("billgen.server.application.time.monotonic", return_value=100):
        first = service.preview("amex")
        second = service.preview("amex")
        with pytest.raises(ApplicationError) as error:
            service.get_job(first["job_id"])
        assert error.value.code == "job_not_found"
        assert service.get_job(second["job_id"])
    with (patch("billgen.server.application.time.monotonic", return_value=111),
          pytest.raises(ApplicationError)):
        service.get_pdf(second["job_id"])
    with pytest.raises(ApplicationError) as error:
        ApplicationService(max_cache_bytes=1).preview("amex")
    assert error.value.code == "artifact_too_large"
    service.close()
    assert not service._jobs


def test_uniform_errors_and_limits(fake):
    with pytest.raises(ApplicationError) as error:
        ApplicationService(max_data_bytes=1).preview("amex")
    assert error.value.status == 413
    with (patch.object(fake, "render", side_effect=RuntimeError("secret /private/path traceback")),
          pytest.raises(ApplicationError) as error):
        ApplicationService().preview("amex")
    assert error.value.to_dict() == {"code": "render_failed", "message": "Template rendering failed"}
    assert error.value.__suppress_context__
    with pytest.raises(ApplicationError) as error:
        ApplicationService().preview("missing")
    assert error.value.status == 404
    service = ApplicationService()
    job = service.preview("amex")
    for index in (-1, True, 1, "0"):
        with pytest.raises(ApplicationError) as error:
            service.get_page_png(job["job_id"], index)
        assert error.value.code == "page_not_found"


@pytest.mark.parametrize("template_id", ["amex", "chase-personal", "chase-business", "usbank-personal", "usbank-business"])
def test_real_encryption_roundtrip(template_id):
    service = ApplicationService()
    data = service.default_data(template_id)
    data["encrypt"] = True
    preview = service.preview(template_id, data, options={"page_dpi": 36})
    assert preview["pdf"]["encrypted"] is False
    exported = service.export_pdf(template_id, data, options={"encrypt": True, "page_dpi": 36})
    assert exported["pdf"]["encrypted"] is True
    assert exported["pages"] == preview["pages"]
    assert data["encrypt"] is True


@pytest.mark.parametrize("template_id", ["amex", "chase-personal", "chase-business", "usbank-personal", "usbank-business"])
def test_direct_default_and_explicit_encryption_preserve_parity(template_id):
    spec = registry.get_spec(template_id)
    assert spec.default_options.encrypt is (template_id == "amex")
    plain = probe(template_id, replace(spec.default_options, encrypt=False))
    encrypted = probe(template_id, replace(spec.default_options, encrypt=True))
    assert {key: plain[key] for key in PARITY_KEYS} == {key: encrypted[key] for key in PARITY_KEYS}
