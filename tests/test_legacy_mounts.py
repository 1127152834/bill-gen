"""Exercise actual old handlers without subprocesses or internal HTTP ports."""
import io
import json
from email.message import Message
from types import SimpleNamespace

import pytest

from billgen import paths
from billgen.engine.loader import ThemeLoader
from billgen.server.legacy import MOUNTS, LegacyMounts, _prefix_urls


class Request:
    def __init__(self, path, method='GET', data=None, cookie=None):
        self.path, self.command = path, method
        raw = json.dumps(data).encode() if data is not None else b''
        self.headers = Message()
        self.headers['Content-Length'] = str(len(raw))
        if cookie:
            self.headers['Cookie'] = cookie
        self.rfile, self.wfile = io.BytesIO(raw), io.BytesIO()
        self.server, self.client_address = SimpleNamespace(), ('127.0.0.1', 12345)
        self.request_version = 'HTTP/1.1'
        self.response_headers = {}

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.response_headers[key.lower()] = value

    def end_headers(self):
        pass

    def json(self):
        return json.loads(self.wfile.getvalue())


@pytest.fixture
def mounts(tmp_path, monkeypatch):
    monkeypatch.setenv('BILL_GEN_DATA_DIR', str(tmp_path))
    return LegacyMounts(ThemeLoader(paths.ROOT))


@pytest.mark.parametrize('mount', MOUNTS, ids=lambda m: m.id)
def test_real_page_for_every_mount(mounts, mount):
    req = Request(mount.prefix + '/')
    assert mounts.dispatch(req)
    assert req.status == 200
    assert b'<html' in req.wfile.getvalue().lower()
    assert int(req.response_headers['content-length']) == len(req.wfile.getvalue())
    if mount.editor:
        assert (mount.prefix + '/api/').encode() in req.wfile.getvalue()
        assert b'fetch("/api/' not in req.wfile.getvalue()
        assert b'fetch(`/api/' not in req.wfile.getvalue()


@pytest.mark.parametrize('mount_id,endpoint', [
    ('wf', '/api/data'), ('ae', '/api/presets'), ('merc', '/api/state?tpl=mercury'),
    ('boa', '/api/data'), ('chase', '/api/data'), ('chasebiz', '/api/data'),
    ('us', '/api/data'), ('usbiz', '/api/data'),
])
def test_real_api_without_referer(mounts, mount_id, endpoint):
    req = Request('/' + mount_id + endpoint)
    assert mounts.dispatch(req)
    assert req.status == 200
    assert isinstance(req.json(), dict)


def test_ae_session_cookie_is_mount_scoped(mounts):
    req = Request('/ae/')
    mounts.dispatch(req)
    cookie = req.response_headers['set-cookie']
    assert 'Path=/ae/' in cookie
    repeat = Request('/ae/api/presets', cookie=cookie.split(';', 1)[0])
    mounts.dispatch(repeat)
    assert repeat.status == 200
    assert 'set-cookie' not in repeat.response_headers


def test_generation_and_preview(mounts):
    data = Request('/wf/api/data')
    mounts.dispatch(data)
    req = Request('/wf/api/generate', 'POST', {'data': data.json()['data'], 'watermark': True})
    assert mounts.dispatch(req)
    assert req.status == 200, req.wfile.getvalue()
    assert req.json()['ok'], req.json()
    pages = Request('/wf/api/pages')
    mounts.dispatch(pages)
    assert pages.json()['pages'] >= 1
    pdf = Request('/wf/preview.pdf')
    mounts.dispatch(pdf)
    assert pdf.status == 200
    assert pdf.wfile.getvalue().startswith(b'%PDF-')


@pytest.mark.parametrize('length,status', [('-1', 400), ('wat', 400), ('4194305', 413)])
def test_bad_body_length_rejected_before_read(mounts, length, status):
    req = Request('/wf/api/generate', 'POST', {'hello': 1})
    req.headers.replace_header('Content-Length', length)
    mounts.dispatch(req)
    assert req.status == status
    assert req.rfile.tell() == 0


def test_transfer_encoding_and_short_read(mounts):
    req = Request('/wf/api/generate', 'POST')
    req.headers['Transfer-Encoding'] = 'chunked'
    mounts.dispatch(req)
    assert req.status == 400
    req = Request('/wf/api/generate', 'POST')
    req.headers.replace_header('Content-Length', '10')
    mounts.dispatch(req)
    assert req.status == 400


def test_unmatched_prefix_does_not_consume_body(mounts):
    req = Request('/api/data', 'POST', {'hello': 1})
    assert not mounts.dispatch(req)
    assert req.rfile.tell() == 0
    assert not mounts.dispatch(Request('/wf-other/'))


def test_mount_redirect_and_pdf_program_preservation(mounts):
    req = Request('/ally')
    mounts.dispatch(req)
    assert req.status == 308
    assert req.response_headers['location'] == '/ally/'
    program = b'var tu = "/CIDInit /ProcSet findresource begin";'
    assert _prefix_urls(program, '/ally') == program


def test_static_traversal_and_head(mounts):
    req = Request('/ally/%2e%2e/%2e%2e/paths.py')
    mounts.dispatch(req)
    assert req.status == 403
    head = Request('/alliant/', 'HEAD')
    mounts.dispatch(head)
    assert head.status == 200
    assert head.wfile.getvalue() == b''


def test_url_prefixing_keeps_external_urls_and_repairs_retry_regex():
    source = b'''fetch('/api/data'); img.src = `/pv/${h}.png`; img.src.replace(/\\/pv\\/[^/]+\\//, `/pv/${Date.now()}/`); src="https://cdn.test/x"; src="//cdn.test/x"'''
    result = _prefix_urls(source, '/wf')
    assert b"fetch('/wf/api/data')" in result
    assert b'`/wf/pv/${h}.png`' in result
    assert b'/\\/wf\\/pv\\/' in result
    assert b'https://cdn.test/x' in result
    assert b'//cdn.test/x' in result
    download = _prefix_urls(b'location.href = "/export/statement.pdf";', '/boa')
    assert download == b'location.href = "/boa/export/statement.pdf";'
    assert _prefix_urls(download, '/boa') == download


def test_engine_identity_and_interleaved_mounts(mounts):
    for name in ('chase', 'us', 'boa', 'wf', 'chasebiz', 'usbiz', 'chase'):
        req = Request('/' + name + '/api/data')
        mounts.dispatch(req)
        assert req.status == 200
    for name, theme in [('boa', 'boa'), ('wf', 'wellsfargo'), ('chase', 'chase'), ('us', 'usbank')]:
        module = mounts._modules[name]
        assert str(paths.theme_dir(theme)) in module.generate.__file__
    assert mounts._modules['chase'].generate.chstream is not mounts._modules['us'].generate.chstream
