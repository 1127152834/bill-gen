"""HTTP 契约：场景结果直出账单（/api/v1/synth/*）。"""
from __future__ import annotations

import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import fitz
import pytest

from billgen.server.http import make_server
from billgen.synth import supported_ids


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv("BILL_GEN_DATA_DIR", str(tmp_path))
    server = make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def get(origin, path):
    with urlopen(origin + path, timeout=30) as response:
        return response.status, response.headers, response.read()


def post(origin, path, body):
    request = Request(origin + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urlopen(request, timeout=120) as response:
        return response.status, json.loads(response.read())


def expect_error(origin, path, body, code):
    with pytest.raises(HTTPError) as caught:
        post(origin, path, body)
    assert caught.value.code == code
    payload = json.loads(caught.value.read())
    return payload["error"]["code"]


def result(account_type="checking"):
    """最小闭合结果包：期初 1000 + 2500 − 1200 − 300 − 500 = 1500。"""
    rows = [
        ("2027-01-04", "ACME PAYROLL DIRECT DEP", 250_000, "credit", "ACH"),
        ("2027-01-06", "CAMDEN LIVING RENT PMT 0106", 120_000, "debit", "ACH"),
        ("2027-01-12", "ATM WITHDRAWAL 0112 TERMINAL", 30_000, "debit", "ATM"),
        ("2027-01-20", "CHECK 1042", 50_000, "debit", "CHECK"),
    ]
    transactions = [
        {"id": f"t{i}", "transactionDate": day, "postedDate": day, "description": desc,
         "memo": "", "type": kind, "direction": direction, "amountCents": cents}
        for i, (day, desc, cents, direction, kind) in enumerate(rows)
    ]
    return {
        "id": "gen-http", "seed": "http-test", "createdAt": "2027-02-01T00:00:00.000Z",
        "subject": {"name": "Maya Okafor", "age": 34,
                    "address": {"street": "2104 S 1st St", "city": "Austin", "state": "TX", "zip": "78704"},
                    "occupation": "Designer", "household": "single", "hobbies": []},
        "statements": [{
            "bankName": "Harborline Bank", "accountType": account_type, "accountLast4": "1347",
            "periodStart": "2027-01-01", "periodEnd": "2027-01-31",
            "months": [{"cycleStart": "2027-01-01", "cycleEnd": "2027-01-31",
                        "openingBalanceCents": 100_000, "closingBalanceCents": 150_000,
                        "totalCreditsCents": 250_000, "totalDebitsCents": 200_000,
                        "transactionCount": len(transactions)}],
            "transactions": transactions,
        }],
        "audit": {"rules": [], "findings": []},
        "usage": {"llmCalls": 0, "tokens": 0, "tavilyQueries": 0},
        "warnings": [], "status": "completed",
    }


def test_capabilities_list_every_mapped_template(web):
    payload = json.loads(get(web, "/api/v1/synth/capabilities")[2])
    ids = {row["id"] for row in payload["templates"]}
    assert ids == set(supported_ids())
    assert "boa-personal" in ids and "alliant" not in ids
    business = {row["id"]: row["accounts"] for row in payload["templates"]}
    assert business["chase-business"] == ["business_checking"]
    assert business["boa-personal"] == ["checking"]


@pytest.mark.parametrize("template,readable_text", [
    # 底稿重放型（chase / usbank）页面文本走自定义 CMap，抽不出可读字符串，只验版式产物。
    ("boa-personal", True), ("wellsfargo", True),
    ("chase-personal", False), ("usbank-personal", False),
])
def test_render_returns_job_pdf_and_pages(web, template, readable_text):
    status, job = post(web, "/api/v1/synth/render",
                       {"template": template, "result": result(), "watermark": True})
    assert status == 201
    assert job["pages"] >= 1
    assert job["ledger"]["rows"] == 4
    assert job["ledger"]["closing_cents"] == 150_000
    assert job["ledger"]["bank"] == "Harborline Bank"
    assert isinstance(job["data"], dict) and job["data"]["watermark"] is True
    pdf = get(web, job["links"]["pdf"])[2]
    assert pdf.startswith(b"%PDF-")
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        text = "".join(page.get_text() for page in doc)
    if readable_text:
        assert "MAYA OKAFOR" in text
        assert "1,500.00" in text
        assert "Harborline Bank" in text
    else:
        assert len(pdf) > 20_000
    png = get(web, job["links"]["pages"][0])[2]
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_business_template(web):
    status, job = post(web, "/api/v1/synth/render",
                       {"template": "usbank-business", "result": result("business_checking"),
                        "account": "business_checking"})
    assert status == 201 and job["ledger"]["account"] == "business_checking"


def test_account_mismatch_is_rejected(web):
    code = expect_error(web, "/api/v1/synth/render",
                        {"template": "chase-business", "result": result()}, 422)
    assert code == "account_mismatch"


def test_static_template_is_rejected(web):
    code = expect_error(web, "/api/v1/synth/render",
                        {"template": "alliant", "result": result()}, 422)
    assert code == "static_template"


def test_unknown_template_is_404(web):
    code = expect_error(web, "/api/v1/synth/render",
                        {"template": "nope", "result": result()}, 404)
    assert code == "template_not_found"


def test_missing_template_field(web):
    code = expect_error(web, "/api/v1/synth/render", {"result": result()}, 400)
    assert code == "invalid_request"


def test_reported_closing_is_recomputed_from_rows(web):
    """账本以流水重算余额：结果包里报错的期末余额不会带进 PDF（realism 阶段重建余额链）。"""
    payload = result()
    payload["statements"][0]["months"][0]["closingBalanceCents"] = 999_999
    status, job = post(web, "/api/v1/synth/render",
                       {"template": "boa-personal", "result": payload})
    assert status == 201
    assert job["ledger"]["closing_cents"] == 150_000


def test_closure_that_cannot_be_rebuilt_is_rejected(web):
    """没有账期汇总就无法重算余额，按 invalid_result 拒掉（422）。"""
    payload = result()
    payload["statements"][0]["months"] = []
    code = expect_error(web, "/api/v1/synth/render",
                        {"template": "boa-personal", "result": payload}, 422)
    assert code == "invalid_result"


def test_unknown_option_field_is_rejected(web):
    code = expect_error(web, "/api/v1/synth/render",
                        {"template": "boa-personal", "result": result(), "nonsense": 1}, 400)
    assert code == "invalid_request"


def test_brand_flag_switches_logo(web):
    """默认换上行标；显式 brand=false 保留模板原稿 logo。"""
    body = {"template": "chase-personal", "result": result()}
    branded = get(web, post(web, "/api/v1/synth/render", body)[1]["links"]["pdf"])[2]
    plain = get(web, post(web, "/api/v1/synth/render",
                          {**body, "brand": False})[1]["links"]["pdf"])[2]
    assert branded != plain
    with fitz.open(stream=branded, filetype="pdf") as doc:
        assert sum(len(page.get_images()) for page in doc) == 3
    with fitz.open(stream=plain, filetype="pdf") as doc:
        assert sum(len(page.get_images()) for page in doc) == 1


def test_brand_flag_must_be_boolean(web):
    code = expect_error(web, "/api/v1/synth/render",
                        {"template": "boa-personal", "result": result(), "brand": "yes"}, 400)
    assert code == "invalid_options"


def test_synth_render_is_byte_stable(web):
    body = {"template": "chase-personal", "result": result()}
    first = get(web, post(web, "/api/v1/synth/render", body)[1]["links"]["pdf"])[2]
    second = get(web, post(web, "/api/v1/synth/render", body)[1]["links"]["pdf"])[2]
    assert first == second
