"""Shared legacy skin is an additive HTML-only response decoration."""
from pathlib import Path

import pytest
from test_legacy_mounts import Request

from billgen import paths
from billgen.engine.loader import ThemeLoader
from billgen.server.legacy import MOUNTS, LegacyMounts, _inject_editor_assets, _prefix_urls


@pytest.fixture
def mounts(tmp_path, monkeypatch):
    monkeypatch.setenv('BILL_GEN_DATA_DIR', str(tmp_path))
    return LegacyMounts(ThemeLoader(paths.ROOT))


@pytest.mark.parametrize('mount', MOUNTS, ids=lambda mount: mount.id)
def test_all_mount_pages_receive_root_assets_once(mounts, mount):
    request = Request(mount.prefix + '/')
    assert mounts.dispatch(request)
    assert request.status == 200
    html = request.wfile.getvalue()
    assert html.count(b'href="/web-assets/legacy.css"') == 1
    assert html.count(b'src="/web-assets/legacy.js"') == 1
    assert (mount.prefix + '/web-assets/').encode() not in html
    assert int(request.response_headers['content-length']) == len(html)
    assert html.rfind(b'/web-assets/legacy.js') < html.lower().rfind(b'</body>')
    # Prefix rewriting and original executable editor code are preserved.
    if mount.editor:
        assert (mount.prefix + '/api/').encode() in html
        assert b'<script>' in html
    elif mount.id == 'alliant':
        assert b'src="js/editor.js"' in html
    else:
        assert b'location.replace("editor.html")' in html


def test_ally_full_editor_keeps_all_functional_scripts(mounts):
    request = Request('/ally/editor.html')
    mounts.dispatch(request)
    html = request.wfile.getvalue()
    for script in (b'id="engine"', b'id="pdfbuilder"', b'id="editor-boot"', b'id="btnExportPdf"', b'id="zoomSel"'):
        assert script in html
    assert html.count(b'src="/web-assets/legacy.js"') == 1
    # The final body is the real document, not an HTML export string in JS.
    assert html.rfind(b'/web-assets/legacy.js') > html.rfind(b'</script>') - 80


def test_injection_is_idempotent_and_preserves_original_bytes():
    source = b'<html><body><script>const html = "</body>"; fetch("/api/data");</script><p>Fields</p></body></html>'
    prefixed = _prefix_urls(source, '/boa')
    decorated = _inject_editor_assets(prefixed)
    assert _inject_editor_assets(decorated) == decorated
    assert b'fetch("/boa/api/data")' in decorated
    assert b'<p>Fields</p>' in decorated
    assert decorated.index(b'/web-assets/legacy.css') > decorated.index(b'</script>')
    assert decorated.replace(b'\n<link rel="stylesheet" href="/web-assets/legacy.css">\n<script src="/web-assets/legacy.js" defer></script>\n', b'') == prefixed


@pytest.mark.parametrize('endpoint', ['/boa/api/data', '/chase/api/data', '/merc/api/state?tpl=mercury', '/alliant/js/editor.js', '/ally/private-assets.js', '/alliant/assets/editor.css'])
def test_non_html_responses_never_receive_skin(mounts, endpoint):
    request = Request(endpoint)
    mounts.dispatch(request)
    assert request.status == 200
    assert b'/web-assets/legacy.' not in request.wfile.getvalue()


def test_skin_is_native_and_namespaced():
    root = Path(__file__).resolve().parents[1] / 'src/billgen/server/web'
    script = (root / 'legacy.js').read_text()
    css = (root / 'legacy.css').read_text()
    assert 'https://' not in script + css
    assert 'bg-legacy-nav' in script
    assert '返回工作台' in script
    assert 'aria-expanded' in script
    assert 'ResizeObserver' in script
    assert '@media screen' in css and '@media print' in css
    assert '.bg-editor-panel' in css
