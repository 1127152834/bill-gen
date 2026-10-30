"""Mercury 验证信「网页编辑器」：本地起一个小服务，左边填字段，右边实时看 PDF。

    .venv/bin/python tools/letter_studio.py            # 默认 http://127.0.0.1:8770
    .venv/bin/python tools/letter_studio.py --port 9000 --open

只依赖标准库 + PyMuPDF，不需要跑整个 billgen 服务，也不写数据库：
每次改动把表单 POST 上来，服务端用 `billgen.themes.mercury.letter.build()` 现出一份 PDF
直接回给浏览器（iframe 里预览），点「下载」就存本地。字段值存在浏览器 localStorage 里，
刷新不丢；也能导出/导入 JSON，和 `--data letter.json` 那条命令行路径互通。
"""
from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

import fitz

from billgen.themes.mercury.letter import DEFAULT_DATA, build

PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Mercury 验证信 · 编辑器</title>
<style>
  :root {
    --ink: #363644; --muted: #71718e; --blue: #5266eb; --line: #e7e7ef;
    --bg: #f6f7fb; --card: #ffffff;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font: 14px/1.55 -apple-system, "SF Pro Text", "Helvetica Neue", Arial, sans-serif;
  }
  header {
    display: flex; align-items: center; gap: 12px;
    padding: 14px 20px; background: var(--card); border-bottom: 1px solid var(--line);
    position: sticky; top: 0; z-index: 5;
  }
  header h1 { font-size: 15px; margin: 0; font-weight: 600; letter-spacing: .02em; }
  header .hint { color: var(--muted); font-size: 12px; margin-left: auto; }
  main { display: grid; grid-template-columns: minmax(320px, 420px) 1fr; gap: 18px; padding: 18px; align-items: start; }
  @media (max-width: 900px) { main { grid-template-columns: 1fr; } }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 16px; }
  fieldset { border: 0; margin: 0 0 6px; padding: 0; }
  legend { color: var(--blue); font-weight: 600; font-size: 12px; letter-spacing: .04em; text-transform: uppercase; padding: 10px 0 6px; }
  label { display: block; margin: 9px 0 3px; font-size: 12px; color: var(--muted); }
  input[type=text], textarea {
    width: 100%; padding: 8px 10px; border: 1px solid var(--line); border-radius: 7px;
    font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--ink); background: #fcfcff;
  }
  input[type=text]:focus, textarea:focus { outline: 2px solid #cfd6ff; border-color: #b9c2ff; }
  textarea { resize: vertical; min-height: 56px; }
  .row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .check { display: flex; align-items: center; gap: 8px; margin-top: 10px; font-size: 13px; color: var(--ink); }
  .check input { width: 15px; height: 15px; accent-color: var(--blue); }
  .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
  button {
    font: 13px/1 inherit; padding: 9px 13px; border-radius: 7px; border: 1px solid var(--line);
    background: #fff; color: var(--ink); cursor: pointer;
  }
  button.primary { background: var(--blue); border-color: var(--blue); color: #fff; font-weight: 600; }
  button:hover { filter: brightness(.97); }
  #status { font-size: 12px; color: var(--muted); margin-top: 10px; min-height: 18px; }
  #error {
    display: none; margin-top: 10px; padding: 9px 11px; border-radius: 7px;
    background: #fff1f1; border: 1px solid #ffd3d3; color: #b42318; font-size: 12.5px; white-space: pre-wrap;
  }
  .preview { position: relative; }
  .preview iframe { width: 100%; height: calc(100vh - 110px); border: 1px solid var(--line); border-radius: 10px; background: #fff; }
  .preview .empty { position: absolute; inset: 0; display: grid; place-items: center; color: var(--muted); }
</style>
</head>
<body>
<header>
  <h1>Mercury 验证信 · 编辑器</h1>
  <span class="hint">改左边任意字段，右边实时出片；改完点「下载 PDF」</span>
</header>
<main>
  <section class="card">
    <form id="form" autocomplete="off">
      <fieldset>
        <legend>信件</legend>
        <label for="date">出信日期</label>
        <input type="text" id="date" data-path="date" placeholder="2026-11-12 或 November 12, 2026">
        <label for="salutation">称呼</label>
        <input type="text" id="salutation" data-path="salutation">
      </fieldset>

      <fieldset>
        <legend>客户</legend>
        <label for="company.name">公司名（正文里加粗）</label>
        <input type="text" id="company.name" data-path="company.name">
        <label for="company.signatory">账户签署人</label>
        <input type="text" id="company.signatory" data-path="company.signatory">
        <label for="company.address">公司地址（一行一条，最多 2 行）</label>
        <textarea id="company.address" data-path="company.address" data-list="1" rows="2"></textarea>
      </fieldset>

      <fieldset>
        <legend>账户</legend>
        <label for="account.opened">开户日期</label>
        <input type="text" id="account.opened" data-path="account.opened">
        <div class="row">
          <div>
            <label for="account.routing">路由号</label>
            <input type="text" id="account.routing" data-path="account.routing">
          </div>
          <div>
            <label for="account.number">账号</label>
            <input type="text" id="account.number" data-path="account.number">
          </div>
        </div>
      </fieldset>

      <fieldset>
        <legend>银行</legend>
        <label for="bank.name">银行名</label>
        <input type="text" id="bank.name" data-path="bank.name">
        <label for="bank.address">银行地址（一行一条，最多 2 行）</label>
        <textarea id="bank.address" data-path="bank.address" data-list="1" rows="2"></textarea>
      </fieldset>

      <fieldset>
        <legend>签署</legend>
        <div class="row">
          <div>
            <label for="signer.name">姓名</label>
            <input type="text" id="signer.name" data-path="signer.name">
          </div>
          <div>
            <label for="signer.title">头衔</label>
            <input type="text" id="signer.title" data-path="signer.title">
          </div>
        </div>
        <label class="check"><input type="checkbox" id="signer.signature" data-path="signer.signature" data-bool="1"> 保留手写签名</label>
        <label class="check"><input type="checkbox" id="watermark" data-path="watermark" data-bool="1"> 盖 SAMPLE 水印</label>
      </fieldset>

      <fieldset>
        <legend>页脚</legend>
        <label for="footer">页脚说明</label>
        <input type="text" id="footer" data-path="footer">
      </fieldset>
    </form>

    <div class="actions">
      <button class="primary" id="download" type="button">下载 PDF</button>
      <button id="download-png" type="button">下载 PNG</button>
      <button id="export" type="button">导出 JSON</button>
      <button id="import-btn" type="button">导入 JSON</button>
      <button id="reset" type="button">恢复默认</button>
      <input type="file" id="import" accept="application/json" hidden>
    </div>
    <div id="error"></div>
    <div id="status"></div>
  </section>

  <section class="preview">
    <div class="empty" id="empty">正在准备预览…</div>
    <iframe id="viewer" title="预览"></iframe>
  </section>
</main>

<script>
const state = { data: null, url: null, timer: null, seq: 0 };

function setStatus(text) { document.getElementById('status').textContent = text; }
function showError(text) {
  const box = document.getElementById('error');
  box.style.display = text ? 'block' : 'none';
  box.textContent = text || '';
}

function getPath(obj, path) {
  return path.split('.').reduce((acc, key) => (acc == null ? undefined : acc[key]), obj);
}
function setPath(obj, path, value) {
  const keys = path.split('.');
  let cur = obj;
  keys.slice(0, -1).forEach((key) => { if (typeof cur[key] !== 'object' || cur[key] === null) cur[key] = {}; cur = cur[key]; });
  cur[keys[keys.length - 1]] = value;
}

function fillForm(data) {
  document.querySelectorAll('[data-path]').forEach((el) => {
    const value = getPath(data, el.dataset.path);
    if (el.dataset.bool) el.checked = value !== false;
    else if (el.dataset.list) el.value = Array.isArray(value) ? value.join('\n') : (value || '');
    else el.value = value == null ? '' : value;
  });
}

function readForm() {
  const data = JSON.parse(JSON.stringify(state.data));
  document.querySelectorAll('[data-path]').forEach((el) => {
    const path = el.dataset.path;
    if (el.dataset.bool) setPath(data, path, el.checked);
    else if (el.dataset.list) setPath(data, path, el.value.split('\n').map((s) => s.trim()).filter(Boolean));
    else setPath(data, path, el.value);
  });
  return data;
}

async function renderPdf(blob = true) {
  const data = readForm();
  state.data = data;
  localStorage.setItem('mercury-letter-data', JSON.stringify(data));
  const seq = ++state.seq;
  setStatus('渲染中…');
  const resp = await fetch('/api/render', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  });
  if (seq !== state.seq) return null;            // 快速连打时丢掉旧响应
  if (!resp.ok) {
    let msg = '渲染失败';
    try { msg = (await resp.json()).error || msg; } catch (e) {}
    showError(msg); setStatus(''); return null;
  }
  showError('');
  const bytes = await resp.blob();
  const url = URL.createObjectURL(bytes);
  if (state.url) URL.revokeObjectURL(state.url);
  state.url = url;
  document.getElementById('viewer').src = url;
  document.getElementById('empty').style.display = 'none';
  setStatus('已更新 · ' + new Date().toLocaleTimeString());
  return bytes;
}

function scheduleRender() {
  clearTimeout(state.timer);
  state.timer = setTimeout(() => { renderPdf().catch((e) => showError(String(e))); }, 220);
}

function download(blob, name) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

async function init() {
  const defaults = await (await fetch('/api/defaults')).json();
  const saved = localStorage.getItem('mercury-letter-data');
  state.data = saved ? JSON.parse(saved) : defaults;
  fillForm(state.data);
  await renderPdf();
  document.querySelectorAll('[data-path]').forEach((el) => el.addEventListener('input', scheduleRender));
  document.getElementById('download').onclick = async () => {
    const bytes = await renderPdf();
    if (bytes) download(bytes, 'mercury-verification-letter.pdf');
  };
  document.getElementById('download-png').onclick = async () => {
    const resp = await fetch('/api/png', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(readForm()),
    });
    if (!resp.ok) { showError('PNG 导出失败'); return; }
    download(await resp.blob(), 'mercury-verification-letter.png');
  };
  document.getElementById('export').onclick = () => {
    download(new Blob([JSON.stringify(readForm(), null, 2)], { type: 'application/json' }), 'letter.json');
  };
  document.getElementById('import-btn').onclick = () => document.getElementById('import').click();
  document.getElementById('import').onchange = async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    const data = JSON.parse(await file.text());
    state.data = Object.assign(JSON.parse(JSON.stringify(defaults)), data);
    fillForm(state.data);
    renderPdf();
  };
  document.getElementById('reset').onclick = () => {
    state.data = JSON.parse(JSON.stringify(defaults));
    fillForm(state.data);
    renderPdf();
  };
}

init().catch((e) => showError(String(e)));
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "billgen-letter-studio/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # 别把每条请求打到终端
        return

    # ---- 公共 -------------------------------------------------------------
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
        self._send(code, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _payload(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw or b"{}")
        return data if isinstance(data, dict) else {}

    # ---- 路由 -------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            return
        if self.path == "/api/defaults":
            self._json(200, DEFAULT_DATA)
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
            data = self._payload()
            pdf = build(data)
        except ValueError as err:      # 行数/长度超限：把话原样告诉前端
            self._json(400, {"error": str(err)})
            return
        except Exception as err:       # noqa: BLE001
            self._json(500, {"error": f"{type(err).__name__}: {err}"})
            return

        if self.path == "/api/render":
            self._send(200, pdf, "application/pdf",
                       {"Content-Disposition": 'inline; filename="mercury-verification-letter.pdf"'})
            return

        with fitz.open(stream=pdf, filetype="pdf") as doc:
            png = doc[0].get_pixmap(dpi=144).tobytes("png")
        self._send(200, png, "image/png",
                   {"Content-Disposition": 'attachment; filename="mercury-verification-letter.png"'})


def serve(port: int = 8770, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """起服务并在后台线程里跑；返回 server，方便测试里 shutdown()。"""
    server = ThreadingHTTPServer((host, port), _Handler)
    thread = threading.Thread(target=server.serve_forever, name="letter-studio", daemon=True)
    thread.start()
    return server


def main() -> None:
    ap = argparse.ArgumentParser(description="Mercury 验证信网页编辑器")
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
