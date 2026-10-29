#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BOA 对账单模板可视化编辑器 — 个人版 + 商业版双模板
用法:  python3 editor/server.py  [端口(默认 8794)]
"""
import json, os, sys, threading, webbrowser, shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from runtime_paths import data_path
from copy import deepcopy
from billgen.server.state import EditorState, RENDER_LOCK, serialized

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import fitz  # noqa: E402
from billgen.themes.boa import generate

EDITOR = os.path.dirname(os.path.abspath(__file__))
DATA_FILES = {
    "personal": data_path("BOA/statement_template/editor/data_personal.json"),
    "business": data_path("BOA/statement_template/editor/data_business.json"),
}
PREVIEW = {t: data_path(f"BOA/statement_template/editor/preview_{t}.pdf") for t in DATA_FILES}
PV_DIR = data_path("BOA/statement_template/editor/pv_png")
STATES = {tpl: EditorState() for tpl in DATA_FILES}
LOCK = RENDER_LOCK


def _make_bak():
    for t, f in DATA_FILES.items():
        bak = f + ".bak"
        if not os.path.exists(bak):
            shutil.copy(f, bak)


@serialized
def load_data(tpl):
    with open(DATA_FILES[tpl], encoding="utf-8") as fh:
        return json.load(fh)


_MONEY_KEYS = {"amount", "balance", "begin_balance"}
_MONEY_SUM_KEYS = {"begin", "deposits", "withdrawals", "checks", "fees", "ending"}


def plain_money(node):
    """把 `$1,284.73` 这类展示格式还原成 `1284.73`。

    编辑器前端会把金额格式化成带 $ 与千分位的文本再提交回来；如果原样落盘，示例数据就
    再也不能作为 `synth render` 的默认入参（MCP 校验只收纯数字）。
    """
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if isinstance(value, str) and (key in _MONEY_KEYS or key in _MONEY_SUM_KEYS):
                cleaned = value.replace("$", "").replace(",", "").strip()
                if cleaned and cleaned.replace(".", "", 1).replace("-", "", 1).isdigit():
                    node[key] = cleaned
            else:
                plain_money(value)
    elif isinstance(node, list):
        for item in node:
            plain_money(item)
    return node


def regenerate(tpl, data, watermark):
    with LOCK:
        data = plain_money(deepcopy(data))
        state = STATES[tpl]
        data["watermark"] = watermark
        generate.build_pdf(data, PREVIEW[tpl], watermark=watermark)
        with open(DATA_FILES[tpl], "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.makedirs(PV_DIR, exist_ok=True)
        doc = fitz.open(PREVIEW[tpl])
        for i, page in enumerate(doc):
            fp = os.path.join(PV_DIR, f"{tpl}_p{i+1}.png")
            tmp = os.path.join(PV_DIR, f"{tpl}_p{i+1}.tmp.png")
            page.get_pixmap(dpi=100).save(tmp)
            os.replace(tmp, fp)
        n = len(doc)
        doc.close()
        for stale in os.listdir(PV_DIR):
            if stale.startswith(tpl + "_p") and stale.endswith(".png"):
                m = stale[len(tpl) + 2:-4]
                if m.isdigit() and int(m) > n:
                    os.remove(os.path.join(PV_DIR, stale))
        state.watermark = watermark
        state.pages = n
        return n


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
            tpl = "business" if "business" in (self.path + "|" + str(self.headers.get("Referer", ""))) else None
            self._send(200, json.dumps({"personal": load_data("personal"),
                                        "business": load_data("business"),
                                        "watermark": STATES["personal"].watermark,
                                        "watermarks": {t: st.watermark for t, st in STATES.items()}}, ensure_ascii=False))
        elif self.path == "/api/pages":
            qs = self.path
            n1 = len(fitz.open(PREVIEW["personal"])) if os.path.exists(PREVIEW["personal"]) else 0
            n2 = len(fitz.open(PREVIEW["business"])) if os.path.exists(PREVIEW["business"]) else 0
            self._send(200, json.dumps({"personal": n1, "business": n2}))
        elif self.path.startswith("/pv/") and self.path.endswith(".png"):
            name = os.path.basename(self.path.split("?")[0])  # /pv/{版本}/{tpl}_pN.png
            fp = os.path.join(PV_DIR, name)
            if os.path.exists(fp):
                with open(fp, "rb") as fh:
                    self._send(200, fh.read(), "image/png",
                               {"Cache-Control": "max-age=60"})
            else:
                self._send(404, b"", "image/png")
        elif self.path.startswith("/export/statement.pdf"):
            tpl = "business" if "tpl=business" in self.path else "personal"
            with open(PREVIEW[tpl], "rb") as fh:
                self._send(200, fh.read(), "application/pdf",
                           {"Content-Disposition": 'attachment; filename="BOA_statement_SAMPLE.pdf"'})
        elif self.path == "/export/data.json":
            self._send(200, json.dumps({"personal": load_data("personal"),
                                        "business": load_data("business")},
                                       ensure_ascii=False, indent=2), "application/json",
                       {"Content-Disposition": 'attachment; filename="boa_data.json"'})
        elif self.path == "/api/reset":
            for t, f in DATA_FILES.items():
                shutil.copy(f + ".bak", f)
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
        tpl = payload.get("template", "personal")
        if tpl not in DATA_FILES:
            self._send(400, json.dumps({"error": "unknown template"}))
            return
        data = payload.get("data")
        wm = bool(payload.get("watermark", STATES[tpl].watermark))
        try:
            pages = regenerate(tpl, data, wm)
            self._send(200, json.dumps({"ok": True, "pages": pages, "tpl": tpl}))
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send(500, json.dumps({"error": str(e)}))


def main():
    _make_bak()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8794
    host = os.environ.get("HOST", "127.0.0.1")
    # 初始预生成
    for t in DATA_FILES:
        try:
            regenerate(t, load_data(t), STATES[t].watermark)
        except Exception as e:
            print(f"初始生成 {t} 失败: {e}")
    print(f"BOA 对账单模板编辑器已启动: http://{'127.0.0.1' if host in ('0.0.0.0','') else host}:{port}  (Ctrl+C 退出)")
    if not os.environ.get("NO_BROWSER"):
        webbrowser.open(f"http://127.0.0.1:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
