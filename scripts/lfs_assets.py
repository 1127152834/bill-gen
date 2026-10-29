"""构建时补齐公共仓库的 LFS 资源，不读取本机 Git 凭据。"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
BATCH_URL = 'https://github.com/1127152834/bill-gen.git/info/lfs/objects/batch'
POINTER_PATTERN = re.compile(rb'version https://git-lfs.github.com/spec/v1\noid sha256:([0-9a-f]{64})\nsize ([0-9]+)\n?')
MAX_ASSET_BYTES = 128 * 1024 * 1024
MAX_BATCH_BYTES = 2 * 1024 * 1024


def read_pointer(path):
    with Path(path).open('rb') as stream:
        header = stream.read(1025)
    if not header.startswith(b'version https://git-lfs.github.com/spec/'):
        return None
    match = POINTER_PATTERN.fullmatch(header)
    if not match or len(header) >= 1024:
        raise ValueError('模板资源包含无效 LFS 指针')
    size = int(match[2])
    if size < 1 or size > MAX_ASSET_BYTES:
        raise ValueError('LFS 资源大小超出允许范围')
    return {'oid': match[1].decode('ascii'), 'size': size}


def validate_download_url(url):
    if not isinstance(url, str):
        raise ValueError('LFS 下载地址无效')
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        allowed = host == 'github.com' or host.endswith('.githubusercontent.com') or host == 'github-cloud.s3.amazonaws.com'
        if parsed.scheme != 'https' or not allowed or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment:
            raise ValueError('LFS 下载地址不属于允许的 GitHub HTTPS 服务')
    except (TypeError, ValueError):
        raise ValueError('LFS 下载地址不属于允许的 GitHub HTTPS 服务') from None
    return url


class _GithubRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_download_url(newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        old, new = urlsplit(req.full_url), urlsplit(newurl)
        if redirected and (old.scheme, old.hostname, old.port or 443) != (new.scheme, new.hostname, new.port or 443):
            # batch 返回的临时下载头仅授权原下载服务。
            redirected.headers.clear()
            redirected.unredirected_hdrs.clear()
        return redirected


def _download_headers(headers):
    if not isinstance(headers, dict) or len(headers) > 32:
        raise ValueError('LFS 下载请求头无效')
    forbidden = {'host', 'cookie', 'proxy-authorization', 'connection', 'transfer-encoding', 'content-length', 'upgrade', 'trailer', 'te'}
    for key, value in headers.items():
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9-]+', key) or key.lower() in forbidden:
            raise ValueError('LFS 下载请求头无效')
        if not isinstance(value, str) or len(value) > 8192 or any(ord(char) < 32 or ord(char) > 126 for char in value):
            raise ValueError('LFS 下载请求头无效')
    return headers


def _request(opener, request):
    try:
        return opener(request, timeout=60)
    except Exception:
        # 网络异常可能带签名 URL；日志仅保留可操作的固定说明。
        raise ValueError('无法下载公共 LFS 资源；请检查网络、GitHub LFS 配额及资源是否已推送') from None


def _batch_actions(opener, objects):
    request = Request(BATCH_URL, data=json.dumps({'operation': 'download', 'transfers': ['basic'], 'objects': objects}).encode(), headers={
        'Accept': 'application/vnd.git-lfs+json', 'Content-Type': 'application/vnd.git-lfs+json',
    })
    try:
        with _request(opener, request) as response:
            payload = response.read(MAX_BATCH_BYTES + 1)
        if len(payload) > MAX_BATCH_BYTES:
            raise ValueError('LFS 响应过大')
        result = json.loads(payload)
        if not isinstance(result, dict) or not isinstance(result.get('objects'), list):
            raise ValueError('公共 LFS 服务返回无效响应')
        wanted = {item['oid']: item['size'] for item in objects}
        actions = {}
        for item in result['objects']:
            if not isinstance(item, dict):raise ValueError('公共 LFS 服务返回无效资源')
            oid = item['oid']
            if oid not in wanted or item.get('size') != wanted[oid] or item.get('error') or oid in actions:
                raise ValueError('LFS 返回的资源与指针不一致')
            action = item['actions']['download']
            if not isinstance(action, dict):raise ValueError('公共 LFS 服务返回无效下载操作')
            actions[oid] = {'url': validate_download_url(action['href']), 'headers': _download_headers(action.get('header') or {})}
        if set(actions) != set(wanted):
            raise ValueError('LFS 响应缺少所需资源')
        return actions
    except (KeyError, TypeError, json.JSONDecodeError, UnicodeError):
        raise ValueError('公共 LFS 服务返回无效响应') from None
    except (OSError, http.client.HTTPException):
        raise ValueError('公共 LFS 响应读取失败，请检查网络后重试构建') from None


def _download(opener, path, pointer, action):
    temporary = None
    try:
        digest, count = hashlib.sha256(), 0
        with _request(opener, Request(action['url'], headers={'Accept': 'application/octet-stream', **action['headers']})) as response:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.lfs-', delete=False) as output:
                temporary = Path(output.name)
                while chunk := response.read(min(1024 * 1024, pointer['size'] - count + 1)):
                    count += len(chunk)
                    if count > pointer['size']:
                        raise ValueError('LFS 资源大小校验失败')
                    digest.update(chunk)
                    output.write(chunk)
        if count != pointer['size'] or digest.hexdigest() != pointer['oid']:
            raise ValueError('LFS 资源 SHA-256 或大小校验失败')
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    except (OSError, http.client.HTTPException):
        raise ValueError('LFS 资源下载或写入失败，原指针已保留') from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def hydrate_assets(root=ROOT, opener=None):
    from scripts.assets import asset_paths, safe_asset_path
    root = Path(root).resolve()
    plan = []
    for name in asset_paths(root):
        path = safe_asset_path(root, name)
        if not path.is_file():
            raise ValueError(f'缺少仓库内模板资源：{name}')
        pointer = read_pointer(path)
        if pointer:
            plan.append((path, pointer))
    if not plan:
        return 0
    opener = opener or build_opener(_GithubRedirects()).open
    unique = {}
    for _, pointer in plan:
        if pointer['oid'] in unique and unique[pointer['oid']] != pointer['size']:
            raise ValueError('同一 LFS 对象出现冲突大小')
        unique[pointer['oid']] = pointer['size']
    actions = _batch_actions(opener, [{'oid': oid, 'size': size} for oid, size in unique.items()])
    for path, pointer in plan:
        _download(opener, path, pointer, actions[pointer['oid']])
    return len(plan)
