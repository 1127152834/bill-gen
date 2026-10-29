"""In-process compatibility mounts for the original editors.

``LegacyMounts(loader).dispatch(handler)`` sends a response and returns True for
an explicit mount path, otherwise returns False without consuming the request.
No socket, child process, cwd change, or Referer routing is involved. The complete
legacy import/execution window uses the engine's ThemeLoader and RENDER_LOCK;
legacy editor state remains shared per mount, except AE's existing cookie sessions.
"""
from __future__ import annotations

import importlib.util
import io
import mimetypes
import re
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .. import paths
from ..engine.loader import ThemeLoader
from .state import RENDER_LOCK


@dataclass(frozen=True)
class Mount:
    id: str
    name: str
    theme: str
    editor: str | None = None
    engine: str | None = None

    @property
    def prefix(self) -> str:
        return '/' + self.id


MOUNTS = (
    Mount('wf', 'Wells Fargo', 'wellsfargo', 'editor/server.py', 'generate'),
    Mount('ae', 'American Express', 'amex', 'editor.py', 'generate_sample_statement'),
    Mount('merc', 'Mercury / Capital One / SoFi', 'portal', 'editor_server.py'),
    Mount('boa', 'Bank of America', 'boa', 'editor/server.py', 'generate'),
    Mount('chase', 'Chase Personal', 'chase', 'editor_personal/server.py', 'generate_personal'),
    Mount('chasebiz', 'Chase Business', 'chase', 'editor_business/server.py', 'generate_business'),
    Mount('us', 'US Bank Personal', 'usbank', 'editor_us/server.py', 'generate_us'),
    Mount('usbiz', 'US Bank Business', 'usbank', 'editor_biz/server.py', 'generate_biz'),
    Mount('alliant', 'Alliant', 'web/alliant'),
    Mount('ally', 'Ally Bank', 'web/ally'),
)


def _prefix_urls(body: bytes, prefix: str) -> bytes:
    text = body.decode('utf-8')
    # String/attribute/template literals only: do not touch https://, //CDNs,
    # division operators, or user-generated JSON. Includes inline JS and CSS.
    text = re.sub(r'''(["'`])/(?=(?:api|pv|export)/|preview\.pdf)''',
                  lambda m: m[1] + prefix + '/', text)
    text = re.sub(r'''((?:src|href|action)\s*=\s*["'])/(?!/|''' + re.escape(prefix.lstrip('/')) + r'''(?:/|["']))''',
                  lambda m: m[1] + prefix + '/', text, flags=re.IGNORECASE)
    text = re.sub(r'url\(/(?!/)', 'url(' + prefix + '/', text)
    # Existing image retry regexes must consume the mount as well; otherwise
    # replacing /pv/... would prepend the mount a second time.
    text = text.replace(r'/\/pv\/', '/' + prefix.replace('/', r'\/') + r'\/pv\/')
    return text.encode('utf-8')


def _inject_editor_assets(body: bytes) -> bytes:
    """Append the shared editor skin after URL rewriting, once per HTML page.

    Only the response wrapper calls this for HTML; original scripts, exports,
    JSON data and PDF byte streams remain untouched.
    """
    text = body.decode('utf-8')
    assets = []
    if not re.search(r'''(?:href|src)\s*=\s*["']/web-assets/legacy\.css(?:[?"'])''', text, re.I):
        assets.append('<link rel="stylesheet" href="/web-assets/legacy.css">')
    if not re.search(r'''(?:href|src)\s*=\s*["']/web-assets/legacy\.js(?:[?"'])''', text, re.I):
        assets.append('<script src="/web-assets/legacy.js" defer></script>')
    if not assets:
        return body
    addition = '\n' + '\n'.join(assets) + '\n'
    # Use the final closing body: Ally contains HTML export strings in scripts.
    endings = list(re.finditer(r'</body\s*>', text, re.I))
    if endings:
        point = endings[-1].start()
        text = text[:point] + addition + text[point:]
    else:
        text += addition
    return text.encode('utf-8')


class _CaptureMixin:
    """Override transport only; all original handler methods run unchanged."""

    def send_response(self, code, message=None):
        self.response_status = code

    def send_header(self, keyword, value):
        self.response_headers.append((keyword, str(value)))

    def end_headers(self):
        if getattr(self, '_session_cookie', None):
            self.send_header('Set-Cookie', self._session_cookie)
            self._session_cookie = None

    def log_message(self, *args):
        pass


class LegacyMounts:
    mounts = MOUNTS

    def __init__(self, loader: ThemeLoader | None = None):
        self.loader = loader if loader is not None else ThemeLoader(paths.ROOT)
        self._modules = {}

    def index(self) -> list[dict[str, str]]:
        return [{'id': m.id, 'name': m.name, 'prefix': m.prefix,
                 'url': m.prefix + '/'} for m in self.mounts]

    def _directory(self, mount: Mount) -> Path:
        return Path(__file__).parent / 'portal' if mount.theme == 'portal' else paths.theme_dir(mount.theme)

    def _module(self, mount: Mount):
        if mount.id in self._modules:
            return self._modules[mount.id]
        directory = self._directory(mount)
        if mount.engine:
            # Load the explicit entry before old server's short-name imports.
            self.loader.load(mount.theme, directory, mount.engine)
        name = f'_billgen_legacy_{id(self):x}_{mount.id}'
        spec = importlib.util.spec_from_file_location(name, directory / mount.editor)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self._seed(module)
        self._modules[mount.id] = module
        return module

    @staticmethod
    def _seed(module):
        # Only writable editor defaults are copied. Read-only assets stay in the
        # package, and existing workspace files are never overwritten.
        editor = Path(module.__file__).parent
        targets = getattr(module, 'DATA_FILES', None)
        if targets:
            items = [(Path(p), editor / Path(p).name) for p in targets.values()]
        elif hasattr(module, 'DATA_FILE'):
            p = Path(module.DATA_FILE)
            items = [(p, editor / p.name)]
        else:
            items = []
        for target, source in items:
            if not target.exists() and source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as out:
                    out.write(source.read_bytes())
        # Reset endpoints use .bak snapshots in several original editors.
        for target, _ in items:
            backup = Path(str(target) + '.bak')
            if target.is_file() and not backup.exists():
                with backup.open('xb') as out:
                    out.write(target.read_bytes())

    def dispatch(self, handler: BaseHTTPRequestHandler) -> bool:
        parsed = urlsplit(handler.path)
        mount = next((m for m in self.mounts if parsed.path == m.prefix or
                      parsed.path.startswith(m.prefix + '/')), None)
        if mount is None:
            return False
        if parsed.path == mount.prefix and handler.command in ('GET', 'HEAD'):
            location = mount.prefix + '/' + (('?' + parsed.query) if parsed.query else '')
            self._send(handler, 308, [('Location', location)], b'', '')
            return True
        subpath = parsed.path[len(mount.prefix):] or '/'
        if not mount.editor:
            return self._static(handler, mount, subpath)
        # Read network data before taking the global render/import lock.
        if handler.headers.get('Transfer-Encoding'):
            handler.close_connection = True
            self._send(handler, 400, [], b'Transfer-Encoding is unsupported', mount.prefix)
            return True
        try:
            lengths = handler.headers.get_all('Content-Length', [])
            if len(lengths) > 1:
                raise ValueError('duplicate Content-Length')
            value = handler.headers.get('Content-Length', '0')
            if not value.isascii() or not value.isdigit():
                raise ValueError('invalid Content-Length')
            size = int(value)
        except (ValueError, TypeError):
            handler.close_connection = True
            self._send(handler, 400, [], b'Invalid Content-Length', mount.prefix)
            return True
        if size > 4 * 1024 * 1024:
            handler.close_connection = True
            self._send(handler, 413, [], b'Request body too large', mount.prefix)
            return True
        body = handler.rfile.read(size) if size else b''
        if len(body) != size:
            handler.close_connection = True
            self._send(handler, 400, [], b'Incomplete request body', mount.prefix)
            return True
        with RENDER_LOCK, self.loader.activated(mount.theme, self._directory(mount)):
            module = self._module(mount)
            # Mixin precedes Handler so transport is captured, including AE's
            # session cookie hook without touching its request logic.
            cls = type('MountedHandler', (_CaptureMixin, module.Handler), {})
            request = cls.__new__(cls)
            request.path = subpath + (('?' + parsed.query) if parsed.query else '')
            request.command = handler.command
            request.headers = handler.headers
            request.rfile, request.wfile = io.BytesIO(body), io.BytesIO()
            request.server = handler.server
            request.client_address = handler.client_address
            request.request_version = getattr(handler, 'request_version', 'HTTP/1.1')
            request.response_status, request.response_headers = 200, []
            method = 'GET' if handler.command == 'HEAD' else handler.command
            action = getattr(request, 'do_' + method, None)
            if action is None:
                request.send_error(405, 'Method not allowed')
            else:
                action()
            payload = request.wfile.getvalue()
            headers = request.response_headers
            content_type = next((v for k, v in headers if k.lower() == 'content-type'), '')
            if 'text/html' in content_type or 'javascript' in content_type or 'text/css' in content_type:
                payload = _prefix_urls(payload, mount.prefix)
            if 'text/html' in content_type:
                payload = _inject_editor_assets(payload)
        # Slow clients never hold the process-wide import/render lock.
        self._send(handler, request.response_status, headers, payload, mount.prefix)
        return True

    @staticmethod
    def _send(handler, status, headers, body, prefix):
        handler.send_response(status)
        for key, value in headers:
            if key.lower() in ('content-length', 'connection', 'transfer-encoding'):
                continue
            if key.lower() == 'location' and value.startswith('/') and not value.startswith('//'):
                value = prefix + value
            if key.lower() == 'set-cookie':
                value = re.sub(r'(?i)(;\s*Path=)/(?=;|$)', r'\1' + prefix + '/', value)
            handler.send_header(key, value)
        handler.send_header('Content-Length', str(len(body)))
        handler.end_headers()
        if handler.command != 'HEAD':
            handler.wfile.write(body)

    def _static(self, handler, mount, subpath):
        if handler.command not in ('GET', 'HEAD'):
            self._send(handler, 405, [], b'Method not allowed', mount.prefix)
            return True
        root = self._directory(mount).resolve()
        target = (root / unquote(subpath).lstrip('/')).resolve()
        if not target.is_relative_to(root):
            self._send(handler, 403, [], b'Forbidden', mount.prefix)
            return True
        if target.is_dir():
            target = (target / 'index.html').resolve()
        if not target.is_relative_to(root):
            self._send(handler, 403, [], b'Forbidden', mount.prefix)
            return True
        if not target.is_file():
            self._send(handler, 404, [], b'Not found', mount.prefix)
            return True
        ctype = mimetypes.guess_type(target)[0] or 'application/octet-stream'
        payload = target.read_bytes()
        if ctype in ('text/html', 'text/javascript', 'application/javascript', 'text/css'):
            payload = _prefix_urls(payload, mount.prefix)
        if ctype == 'text/html':
            payload = _inject_editor_assets(payload)
        self._send(handler, 200, [('Content-Type', ctype)], payload, mount.prefix)
        return True
