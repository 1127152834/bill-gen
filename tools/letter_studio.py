"""Mercury 验证信「网页编辑器」的独立小服务（不想起整个 billgen 时用它）。

    .venv/bin/python tools/letter_studio.py            # 默认 http://127.0.0.1:8770
    .venv/bin/python tools/letter_studio.py --port 9000 --open

页面与渲染逻辑都在 `billgen.themes.mercury.letter_web` 里，这里只负责 HTTP 壳；
billgen 主服务的 `/letters/mercury/` 用的是同一份页面与同一组函数，
所以哪边改都在改同一处。
"""
from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from billgen.themes.mercury.letter_web import PAGE, defaults, render_pdf, render_png


class _Handler(BaseHTTPRequestHandler):
    server_version = "billgen-letter-studio/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # 别把每条请求打到终端
        return

    def _send(self, code: int, body: bytes, content_type: str,
              extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode(),
                   "application/json; charset=utf-8")

    def _payload(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        data = json.loads(self.rfile.read(length) or b"{}") if length else {}
        return data if isinstance(data, dict) else {}

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            return
        if self.path == "/api/defaults":
            self._json(200, defaults())
            return
        if self.path == "/healthz":
            self._json(200, {"status": "ok"})
            return
        self._json(404, {"error": f"没有这个路径：{self.path}"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in ("/api/render", "/api/png"):
            self._json(404, {"error": f"没有这个路径：{self.path}"})
            return
        try:
            body = render_pdf(self._payload()) if self.path == "/api/render" else render_png(self._payload())
        except ValueError as err:      # 行数/长度超限：把话原样告诉前端
            self._json(400, {"error": str(err)})
            return
        except Exception as err:       # noqa: BLE001
            self._json(500, {"error": f"{type(err).__name__}: {err}"})
            return

        if self.path == "/api/render":
            self._send(200, body, "application/pdf",
                       {"Content-Disposition": 'inline; filename="mercury-verification-letter.pdf"'})
        else:
            self._send(200, body, "image/png",
                       {"Content-Disposition": 'attachment; filename="mercury-verification-letter.png"'})


def serve(port: int = 8770, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """起服务并在后台线程里跑；返回 server，方便测试里 shutdown()。"""
    server = ThreadingHTTPServer((host, port), _Handler)
    thread = threading.Thread(target=server.serve_forever, name="letter-studio", daemon=True)
    thread.start()
    return server


def main() -> None:
    ap = argparse.ArgumentParser(description="Mercury 验证信网页编辑器（独立服务）")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--open", action="store_true", help="起来后自动打开浏览器")
    args = ap.parse_args()

    server = serve(args.port, args.host)
    url = f"http://{args.host}:{args.port}/"
    print(f"编辑器已启动：{url}\n（Ctrl+C 停止）")
    if args.open:
        webbrowser.open(url)
    try:
        while True:
            threading.Event().wait(1.0)
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
