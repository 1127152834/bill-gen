from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import tempfile
import threading
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import run


class Upstream(BaseHTTPRequestHandler):
    def do_GET(self):
        body=self.path.encode()
        self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def log_message(self,*args):pass


class GatewayTests(unittest.TestCase):
    def test_explicit_prefix_and_editor_referer_reach_the_same_upstream(self):
        upstream=ThreadingHTTPServer(('127.0.0.1',0),Upstream)
        threading.Thread(target=upstream.serve_forever,daemon=True).start()
        service={'id':'wf','name':'Test','prefix':'/wf','port':upstream.server_port}
        with patch.multiple(run,HOST='127.0.0.1',GATE_PORT=0,SERVICES=[service]):
            gateway=run.make_gateway();threading.Thread(target=gateway.serve_forever,daemon=True).start()
            origin=f'http://127.0.0.1:{gateway.server_port}'
            try:
                for path,headers in [('/wf/api/test?x=1',{}),('/api/test?x=1',{'Referer':origin+'/wf/'})]:
                    with urlopen(Request(origin+path,headers=headers),timeout=3) as response:self.assertEqual(response.read(),b'/api/test?x=1')
            finally:gateway.shutdown();gateway.server_close();upstream.shutdown();upstream.server_close()

    def test_static_editor_assets_cannot_escape_the_selected_directory(self):
        with tempfile.TemporaryDirectory() as temp,patch.multiple(run,HOST='127.0.0.1',GATE_PORT=0):
            root=Path(temp);(root/'public').mkdir();(root/'public/index.html').write_text('test editor');(root/'private.txt').write_text('not public')
            with patch.object(run,'SERVICES',[{'id':'ally','name':'Test','prefix':'/ally','static':str(root/'public')}]):
                gateway=run.make_gateway();threading.Thread(target=gateway.serve_forever,daemon=True).start()
                origin=f'http://127.0.0.1:{gateway.server_port}'
                try:
                    with urlopen(origin+'/ally/',timeout=3) as response:self.assertEqual(response.read(),b'test editor')
                    with self.assertRaises(HTTPError) as error:urlopen(origin+'/ally/%2e%2e/private.txt',timeout=3)
                    self.assertEqual(error.exception.code,403)
                    error.exception.close()
                finally:gateway.shutdown();gateway.server_close()
