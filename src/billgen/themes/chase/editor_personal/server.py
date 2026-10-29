#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大通个人版编辑器 — 独立服务（仅个人版，与商业版完全分离）"""
import hashlib, json, os, sys, threading, webbrowser, shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from runtime_paths import data_path
from copy import deepcopy
import tempfile
from billgen.server.state import EditorState, RENDER_LOCK, serialized

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import fitz
from billgen.themes.chase import generate_personal as generate

EDITOR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = data_path("大通/statement_template/editor_personal/data_personal.json")
PREVIEW = data_path("大通/statement_template/editor_personal/preview.pdf")
PV_DIR = data_path("大通/statement_template/editor_personal/pv_png")
STATE = EditorState()
LOCK = RENDER_LOCK


@serialized
def load_data():
    with open(DATA_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def regenerate(data, watermark):
    with LOCK:
        data = deepcopy(data)
        data["watermark"] = watermark
        # 流水顺序权威化：与模板生成同款归一化日期排序，编辑器/生成一致
        if isinstance(data.get("transactions"), list):
            data["transactions"].sort(
                key=lambda r: generate.date_key(r.get("date")))
        generate.build_pdf(data, PREVIEW, watermark=watermark)
        # 生成成功后才落盘，失败不污染已存数据
        with open(DATA_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.makedirs(PV_DIR, exist_ok=True)
        doc = fitz.open(PREVIEW)
        hashes = []
        for i, page in enumerate(doc):
            fp = os.path.join(PV_DIR, f"p{i+1}.png")
            tmp = os.path.join(PV_DIR, f"p{i+1}.tmp.png")
            page.get_pixmap(dpi=100).save(tmp)
            os.replace(tmp, fp)
            hashes.append(hashlib.md5(open(fp, "rb").read()).hexdigest()[:12])
        n = len(doc)
        doc.close()
        for stale in os.listdir(PV_DIR):
            m = stale[1:-4] if stale.startswith("p") else None
            if m and m.isdigit() and not m.endswith("tmp") and int(m) > n:
                os.remove(os.path.join(PV_DIR, stale))
        STATE.watermark = watermark
        STATE.pages, STATE.hashes = n, hashes
        return n, hashes


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    @serialized
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(EDITOR, "index.html"), "rb") as fh:
                self._send(200, fh.read(), "text/html; charset=utf-8")
        elif self.path == "/api/data":
            self._send(200, json.dumps({"personal": load_data(),
                                        "watermark": STATE.watermark}, ensure_ascii=False))
        elif self.path.startswith("/pv/") and self.path.endswith(".png"):
            name = os.path.basename(self.path.split("?")[0])
            fp = os.path.join(PV_DIR, name)
            if os.path.exists(fp):
                with open(fp, "rb") as fh:
                    self._send(200, fh.read(), "image/png", {"Cache-Control": "public, max-age=86400, immutable"})
            else:
                self._send(404, b"", "image/png")
        elif self.path.startswith("/export/statement.pdf"):
            wm = "wm=0" not in self.path
            enc = "enc=1" in self.path
            out = PREVIEW
            if enc:
                data = load_data()
                data["encrypt"] = True
                # An encrypted download must not replace the published preview.
                with tempfile.TemporaryDirectory() as temp:
                    out = os.path.join(temp, "export.pdf")
                    generate.build_pdf(data, out, watermark=wm)
                    with open(out, "rb") as fh:
                        pdf = fh.read()
            else:
                with open(out, "rb") as fh:
                    pdf = fh.read()
            self._send(200, pdf, "application/pdf",
                           {"Content-Disposition": 'attachment; filename="chase_personal_statement.pdf"'})
        elif self.path.startswith("/export/data.json"):
            with open(DATA_FILE, "rb") as fh:
                self._send(200, fh.read(), "application/json",
                           {"Content-Disposition": 'attachment; filename="chase_personal_data.json"'})
        elif self.path == "/api/reset":
            shutil.copy(DATA_FILE + ".bak", DATA_FILE)
            self._send(200, json.dumps({"ok": True}))
        else:
            self._send(404, "not found", "text/plain; charset=utf-8")

    @serialized
    def do_POST(self):
        if self.path != "/api/generate":
            self._send(404, "not found", "text/plain; charset=utf-8")
            return
        n = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            self._send(400, json.dumps({"error": str(e)}))
            return
        data = payload.get("data")
        if not data or data.get("template") != "personal":
            self._send(400, json.dumps({"error": "本编辑器仅处理个人版"}))
            return
        wm = bool(payload.get("watermark", STATE.watermark))
        try:
            pages, hashes = regenerate(data, wm)
            self._send(200, json.dumps({"ok": True, "pages": pages, "hashes": hashes, "tpl": "personal"}))
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send(500, json.dumps({"error": str(e)}))


def main():
    if not os.path.exists(DATA_FILE + ".bak"):
        shutil.copy(DATA_FILE, DATA_FILE + ".bak")
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8795
    host = os.environ.get("HOST", "127.0.0.1")
    try:
        regenerate(load_data(), STATE.watermark)
    except Exception as e:
        print(f"初始生成失败: {e}")
    print(f"大通个人版编辑器: http://127.0.0.1:{port}  (Ctrl+C 退出)")
    if not os.environ.get("NO_BROWSER"):
        webbrowser.open(f"http://127.0.0.1:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
