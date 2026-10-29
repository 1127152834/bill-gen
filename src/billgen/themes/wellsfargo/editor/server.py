#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对账单模板可视化编辑器 — 本地服务
用法:  python3 editor/server.py  [端口(默认 8765)]
然后浏览器打开 http://127.0.0.1:8765
"""
import hashlib, json, os, sys, threading, webbrowser, shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from runtime_paths import data_path
from copy import deepcopy
from billgen.server.state import EditorState, RENDER_LOCK, serialized

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # statement_template/
import fitz  # noqa: E402
from billgen.themes.wellsfargo import generate

EDITOR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = data_path("富国/statement_template/editor/data.json")
SAMPLE_DATA_FILE = os.path.join(BASE, "data_sample.json")
PREVIEW = data_path("富国/statement_template/editor/preview.pdf")
PV_DIR = data_path("富国/statement_template/editor/pv_png")
STATE = EditorState()
LOCK = RENDER_LOCK


@serialized
def init_data():
    if not os.path.exists(DATA_FILE):
        shutil.copy(SAMPLE_DATA_FILE, DATA_FILE)
    with open(DATA_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def regenerate(data, watermark, mark, fonts="embedded"):
    with LOCK:
        data = deepcopy(data)
        generate.build_pdf(data, PREVIEW, watermark=watermark, mark=mark, fonts=fonts)
        with open(DATA_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        # 同步渲染每页 PNG 供编辑器图片预览（绕开内嵌 PDF 查看器不自动刷新的问题）
        # 按内容哈希命名：未变化的页 URL 不变 → 浏览器 immutable 缓存直出，
        # 响应只带哈希列表；原子写避免读到半张图
        os.makedirs(PV_DIR, exist_ok=True)
        doc = fitz.open(PREVIEW)
        hs = []
        for page in doc:
            png = page.get_pixmap(dpi=100).tobytes("png")
            h = hashlib.md5(png).hexdigest()[:12]
            fp = os.path.join(PV_DIR, h + ".png")
            if not os.path.exists(fp):
                tmp = fp + ".tmp.png"          # 扩展名须为 .png
                with open(tmp, "wb") as f:
                    f.write(png)
                os.replace(tmp, fp)
            hs.append(h)
        n = len(doc)
        doc.close()
        for f in os.listdir(PV_DIR):           # 清理不再引用的页（防累积）
            if f.endswith(".png") and f[:-4] not in hs:
                try:
                    os.remove(os.path.join(PV_DIR, f))
                except OSError:
                    pass
        STATE.watermark, STATE.mark, STATE.fonts = watermark, mark, fonts
        STATE.pages, STATE.hashes = n, hs
        return n, hs


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静默访问日志
        pass

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        hdrs = {"Content-Type": ctype, "Content-Length": str(len(body)),
                "Cache-Control": "no-store"}
        hdrs.update(extra or {})      # extra 可覆盖默认头（如 /pv 的 immutable）
        self.send_response(code)
        for k, v in hdrs.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    @serialized
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(EDITOR, "index.html"), "rb") as fh:
                self._send(200, fh.read(), "text/html; charset=utf-8")
        elif self.path == "/api/data":
            d = init_data()
            self._send(200, json.dumps({"data": d, **STATE.snapshot()}))
        elif self.path == "/preview.pdf":
            if not os.path.exists(PREVIEW):
                regenerate(init_data(), STATE.watermark, STATE.mark, STATE.fonts)
            with open(PREVIEW, "rb") as fh:
                self._send(200, fh.read(), "application/pdf")
        elif self.path == "/api/pages":
            if not STATE.hashes:
                regenerate(init_data(), STATE.watermark, STATE.mark, STATE.fonts)
            self._send(200, json.dumps({"pages": STATE.pages,
                                        "hashes": STATE.hashes}))
        elif self.path.startswith("/pv/") and self.path.endswith(".png"):
            # /pv/{内容哈希}.png — 路径=内容哈希（无查询串，防内嵌浏览器对 ?t= 的
            # localhost 请求失败），未变化页同 URL 走 immutable 缓存
            name = os.path.basename(self.path.split("?")[0])
            fp = os.path.join(PV_DIR, name)
            if name.endswith(".png") and os.path.isfile(fp):
                with open(fp, "rb") as fh:
                    self._send(200, fh.read(), "image/png",
                               {"Cache-Control": "public, max-age=86400, immutable"})
            else:
                self._send(404, '{"error":"no such page"}')
        elif self.path == "/export/data.json":
            with open(DATA_FILE, "rb") as fh:
                self._send(200, fh.read(), "application/json",
                           {"Content-Disposition": "attachment; filename=data.json"})
        elif self.path == "/export/statement.pdf":
            with open(PREVIEW, "rb") as fh:
                self._send(200, fh.read(), "application/pdf",
                           {"Content-Disposition": "attachment; filename=wells_fargo_statement.pdf"})
        elif self.path == "/api/reset":
            shutil.copy(SAMPLE_DATA_FILE, DATA_FILE)
            regenerate(init_data(), STATE.watermark, STATE.mark, STATE.fonts)
            self._send(200, json.dumps({"ok": True, "data": init_data()}))
        else:
            self._send(404, '{"error":"not found"}')

    @serialized
    def do_POST(self):
        if self.path != "/api/generate":
            self._send(404, '{"error":"not found"}')
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n))
            data = payload["data"]
            regenerate(data, bool(payload.get("watermark", True)),
                       bool(payload.get("mark", True)),
                       str(payload.get("fonts", "declared")))
            self._send(200, '{"ok":true}')
        except Exception as e:
            self._send(200, json.dumps({"ok": False, "error": str(e)}))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8791
    base_port = port
    host = os.environ.get("HOST", "127.0.0.1")   # Docker 内设 HOST=0.0.0.0
    init_data()
    regenerate(init_data(), STATE.watermark, STATE.mark, STATE.fonts)
    for _ in range(20):  # 端口被占则自动 +1
        try:
            srv = ThreadingHTTPServer((host, port), Handler)
            break
        except OSError:
            port += 1
    else:
        raise SystemExit("无可用端口")
    if port != base_port:
        print(f"提示: 端口 {base_port} 被占用，已改用 {port}")
    shown = (f"http://127.0.0.1:{port}" if host == "127.0.0.1"
             else f"http://<服务器IP>:{port}")
    print(f"对账单模板编辑器已启动: {shown}  (Ctrl+C 退出)")
    if not os.environ.get("NO_BROWSER"):         # 容器内无浏览器，跳过自动打开
        try:
            threading.Timer(0.6, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
        except Exception:
            pass
    srv.serve_forever()


if __name__ == "__main__":
    main()
