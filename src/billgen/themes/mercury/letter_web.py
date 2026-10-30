"""Mercury 验证信网页编辑器的页面与渲染函数。

页面本身是一段自包含的 HTML（表单 + iframe 预览），接口路径按当前页相对解析，
所以同一份页面既能由 `tools/letter_studio.py` 起在本机根路径，也能挂在
`billgen` 服务的 `/letters/mercury/` 下面，前端代码不用改。

`build()` 抛 `ValueError`（行数/长度超限）时，HTTP 层把原话回给前端。
"""
from __future__ import annotations

from typing import Any

import fitz

from .letter import DEFAULT_DATA, build

__all__ = ["PAGE", "defaults", "render_pdf", "render_png"]


def defaults() -> dict[str, Any]:
    """默认数据（前端「恢复默认」和首次加载都用它）。"""
    return DEFAULT_DATA


def render_pdf(payload: dict[str, Any] | None) -> bytes:
    return build(payload or {})


def render_png(payload: dict[str, Any] | None, *, dpi: int = 144) -> bytes:
    with fitz.open(stream=render_pdf(payload), filetype="pdf") as doc:
        return doc[0].get_pixmap(dpi=dpi).tobytes("png")


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
const BASE = location.pathname.endsWith('/') ? location.pathname : location.pathname + '/';
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
  const resp = await fetch(BASE + 'api/render', {
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
  const defaults = await (await fetch(BASE + 'api/defaults')).json();
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
    const resp = await fetch(BASE + 'api/png', {
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


def page_bytes() -> bytes:
    return PAGE.encode()
