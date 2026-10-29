import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.request import Request

from scripts.lfs_assets import _GithubRedirects, hydrate_assets, read_pointer, validate_download_url


class LfsAssetTests(unittest.TestCase):
    def fixture(self, temp, content=b'fixture-font'):
        root = Path(temp)
        (root / 'scripts').mkdir()
        (root / 'scripts/private-assets.json').write_text(json.dumps({'files': ['bank/assets/font.ttf']}))
        path = root / 'bank/assets/font.ttf'
        path.parent.mkdir(parents=True)
        oid = hashlib.sha256(content).hexdigest()
        pointer = f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(content)}\n'.encode()
        path.write_bytes(pointer)
        return root, path, oid, pointer

    def transport(self, oid, expected, download=None, href='https://github-cloud.githubusercontent.com/object', headers=None):
        def request(req, timeout):
            if req.get_method() == 'POST':
                body = json.loads(req.data)
                self.assertEqual(body['operation'], 'download')
                self.assertEqual(body['objects'], [{'oid': oid, 'size': len(expected)}])
                self.assertNotIn('Authorization', req.headers)
                return io.BytesIO(json.dumps({'objects': [{'oid': oid, 'size': len(expected), 'actions': {'download': {'href': href, 'header': headers or {}}}}]}).encode())
            for key,value in (headers or {}).items():self.assertEqual(req.get_header(key.capitalize()),value)
            return io.BytesIO(expected if download is None else download)
        return request

    def test_hydrates_only_allowlisted_pointer_and_verifies_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            root, path, oid, pointer = self.fixture(temp)
            other = root / 'unlisted.pdf'; other.write_bytes(pointer)
            self.assertEqual(hydrate_assets(root, opener=self.transport(oid, b'fixture-font')), 1)
            self.assertEqual(path.read_bytes(), b'fixture-font')
            self.assertEqual(other.read_bytes(), pointer)
            self.assertEqual(hydrate_assets(root, opener=lambda *_args, **_kw: self.fail('already hydrated')), 0)

    def test_bad_hash_and_short_or_oversized_download_leave_pointer_untouched(self):
        for downloaded in [b'wrong-font!!', b'short', b'fixture-font-too-long']:
            with self.subTest(downloaded=downloaded), tempfile.TemporaryDirectory() as temp:
                root, path, oid, pointer = self.fixture(temp)
                with self.assertRaises(ValueError):
                    hydrate_assets(root, opener=self.transport(oid, b'fixture-font', downloaded))
                self.assertEqual(path.read_bytes(), pointer)
                self.assertEqual(list(path.parent.iterdir()), [path])

    def test_rejects_pointer_with_invalid_hash_or_unbounded_size(self):
        with tempfile.TemporaryDirectory() as temp:
            root, path, oid, _ = self.fixture(temp)
            for body in ['version https://git-lfs.github.com/spec/v1\noid sha256:nope\nsize 12\n', f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize 9999999999999\n']:
                path.write_text(body)
                with self.assertRaises(ValueError): read_pointer(path)

    def test_rejects_manifest_and_symlink_escape_before_network(self):
        with tempfile.TemporaryDirectory() as temp:
            root, path, _, _ = self.fixture(temp)
            outside = root.parent / (root.name + '-outside.ttf')
            try:
                outside.write_bytes(b'private')
                path.unlink(); path.symlink_to(outside)
                with self.assertRaises(ValueError): hydrate_assets(root, opener=lambda *_args, **_kw: self.fail('must not access network'))
                self.assertEqual(outside.read_bytes(), b'private')
            finally: outside.unlink(missing_ok=True)
        with tempfile.TemporaryDirectory() as temp:
            root, _, _, _ = self.fixture(temp)
            (root / 'scripts/private-assets.json').write_text(json.dumps({'files': ['../font.ttf']}))
            with self.assertRaises(ValueError): hydrate_assets(root)

    def test_rejects_non_github_or_insecure_download_locations(self):
        for url in ['http://github.com/object', 'https://127.0.0.1/object', 'https://githubusercontent.com.evil.test/object', 'https://user:secret@github.com/object', 'https://github.com:8443/object']:
            with self.subTest(url=url), self.assertRaises(ValueError): validate_download_url(url)
        self.assertEqual(validate_download_url('https://github-cloud.s3.amazonaws.com/object?signature=value'), 'https://github-cloud.s3.amazonaws.com/object?signature=value')

    def test_remote_errors_do_not_expose_signed_urls(self):
        with tempfile.TemporaryDirectory() as temp:
            root, path, _, pointer = self.fixture(temp)
            def failing_request(*_args, **_kwargs):
                raise OSError('https://example.invalid/?token=secret')
            with self.assertRaises(ValueError) as error: hydrate_assets(root, opener=failing_request)
            self.assertNotIn('secret', str(error.exception))
            self.assertEqual(path.read_bytes(), pointer)

    def test_download_uses_batch_headers_without_reading_local_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            root,path,oid,_=self.fixture(temp)
            headers={'Authorization':'Bearer synthetic-test-token','X-Amz-Security-Token':'synthetic-storage-token'}
            self.assertEqual(hydrate_assets(root,opener=self.transport(oid,b'fixture-font',headers=headers)),1)
            self.assertEqual(path.read_bytes(),b'fixture-font')

    def test_cross_origin_redirect_drops_download_headers(self):
        request=Request('https://github.com/object',headers={'Authorization':'Bearer synthetic','X-Storage-Signature':'synthetic','Accept':'application/octet-stream'})
        handler=_GithubRedirects()
        same=handler.redirect_request(request,None,302,'redirect',{},'https://github.com/next')
        self.assertEqual(same.get_header('Authorization'),'Bearer synthetic')
        other=handler.redirect_request(request,None,302,'redirect',{},'https://github-cloud.githubusercontent.com/object')
        self.assertIsNone(other.get_header('Authorization'))
        self.assertIsNone(other.get_header('X-storage-signature'))

    def test_rejects_host_cookie_and_injected_response_headers(self):
        for headers in [{'Host':'example.invalid'},{'Cookie':'synthetic'},{'Authorization':'synthetic\r\nHost: example.invalid'}]:
            with self.subTest(headers=headers),tempfile.TemporaryDirectory() as temp:
                root,path,oid,pointer=self.fixture(temp)
                with self.assertRaises(ValueError):hydrate_assets(root,opener=self.transport(oid,b'fixture-font',headers=headers))
                self.assertEqual(path.read_bytes(),pointer)

    def test_stream_failure_does_not_expose_remote_details(self):
        class BrokenStream(io.BytesIO):
            def read(self,*args):raise OSError('https://example.invalid/?token=secret')
        with tempfile.TemporaryDirectory() as temp:
            root,path,_,pointer=self.fixture(temp)
            with self.assertRaises(ValueError) as error:hydrate_assets(root,opener=lambda *_args,**_kwargs:BrokenStream())
            self.assertNotIn('secret',str(error.exception))
            self.assertEqual(path.read_bytes(),pointer)
