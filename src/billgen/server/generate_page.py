"""`/generate/` 页面的 HTML（账单 JSON 生成器）。

与 `/letters/mercury/` 同一套路：页面是纯静态字符串，数据全走 JSON 接口，
这样它既能被主服务挂在 `/generate/` 下，也能单独用 `python -m billgen.server.generate_page`
起一个本地端口调样式（末节有 `__main__`）。

接口契约（由 `src/billgen/server/http.py` 提供）：
    GET    /api/v1/profiles                  -> {"profiles": [Profile, ...]}
    POST   /api/v1/profiles                  -> Profile（body = Profile 或 {kind, bank, display_name, payload}）
    PUT    /api/v1/profiles/<id>             -> Profile
    DELETE /api/v1/profiles/<id>             -> {"deleted": true}
    POST   /api/v1/profiles/import           -> {"profiles": [...], "errors": [...]}（body = {kind: "json"|"csv", text: "..."}）
    POST   /api/v1/gen/jobs                  -> 202 Job（body = {profile_id, template, months, seed?}）
    GET    /api/v1/gen/jobs                  -> {"jobs": [...]}
    GET    /api/v1/gen/jobs/<id>             -> Job
    POST   /api/v1/gen/jobs/<id>/cancel      -> Job
    GET    /api/v1/gen/jobs/<id>/files/<name> -> 单份文件
    GET    /api/v1/gen/jobs/<id>/package     -> zip
"""
from __future__ import annotations

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>BillGen · 账单 JSON 生成器</title>
<style>
  :root { --ink:#17202b; --muted:#6a7686; --line:#e3e8ef; --bg:#f6f7f9; --blue:#155eaa; --warn:#b45309; --bad:#b42318; }
  * { box-sizing: border-box; }
  body { margin:0; font:14px/1.5 -apple-system, "Segoe UI", system-ui, "PingFang SC", sans-serif; color:var(--ink); background:var(--bg); }
  header { display:flex; align-items:center; gap:14px; padding:14px 22px; background:#fff; border-bottom:1px solid var(--line); }
  header h1 { font-size:16px; margin:0; font-weight:650; }
  header .spacer { flex:1; }
  header a { color:var(--blue); text-decoration:none; font-size:13px; }
  main { display:grid; grid-template-columns: 340px minmax(0,1fr); gap:18px; padding:18px 22px 40px; align-items:start; }
  .card { background:#fff; border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
  .card h2 { font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); margin:0 0 10px; }
  label { display:block; font-size:12px; color:var(--muted); margin:9px 0 3px; }
  input, select, textarea { width:100%; padding:7px 9px; border:1px solid var(--line); border-radius:7px; font:inherit; background:#fff; }
  textarea { min-height:74px; resize:vertical; font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12px; }
  .row { display:flex; gap:8px; }
  .row > * { flex:1; }
  button { font:inherit; padding:7px 12px; border-radius:7px; border:1px solid var(--blue); background:var(--blue); color:#fff; cursor:pointer; }
  button.ghost { background:#fff; color:var(--blue); }
  button.danger { background:#fff; color:var(--bad); border-color:#f0c7c2; }
  button:disabled { opacity:.55; cursor:not-allowed; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:7px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
  th { color:var(--muted); font-weight:500; font-size:12px; }
  .pill { display:inline-block; padding:1px 7px; border-radius:99px; font-size:11px; border:1px solid var(--line); background:#f2f5f9; color:var(--muted); }
  .pill.done { background:#e7f6ec; color:#1a7f43; border-color:#bfe6cd; }
  .pill.failed, .pill.cancelled { background:#fdecea; color:var(--bad); border-color:#f5c6c0; }
  .pill.running { background:#eaf2fd; color:var(--blue); border-color:#c3d9f7; }
  .muted { color:var(--muted); }
  .err { color:var(--bad); font-size:12px; white-space:pre-wrap; }
  .warn { color:var(--warn); font-size:12px; }
  .bar { height:6px; background:#eef1f6; border-radius:99px; overflow:hidden; }
  .bar > i { display:block; height:100%; background:var(--blue); width:0; transition:width .3s; }
  .log { max-height:230px; overflow:auto; font:12px/1.45 ui-monospace, Menlo, monospace; background:#fbfcfe; border:1px solid var(--line); border-radius:8px; padding:8px 10px; }
  .files a { color:var(--blue); text-decoration:none; }
  .empty { color:var(--muted); font-size:13px; padding:10px 0; }
  .hidden { display:none; }
</style>
</head>
<body>
<header>
  <h1>账单 JSON 生成器</h1>
  <span class="pill">每月一份模板 JSON</span>
  <span class="spacer"></span>
  <a href="/" target="_blank" rel="noopener">← 工作台</a>
  <a href="/letters/mercury/" target="_blank" rel="noopener">水星验证信</a>
</header>

<main>
  <section>
    <div class="card">
      <h2>档案库</h2>
      <div class="row">
        <select id="kind">
          <option value="person">个人</option>
          <option value="business">企业</option>
        </select>
        <select id="bank"></select>
      </div>
      <label for="display_name">显示名</label>
      <input id="display_name" placeholder="如 王梅 / Lumen Creative LLC" />
      <div id="person-fields">
        <label for="p_name">姓名（账单上的持卡人）</label>
        <input id="p_name" placeholder="MEI WANG" />
        <label for="p_street">街道</label>
        <input id="p_street" placeholder="2104 S 1st St" />
        <div class="row">
          <div><label for="p_city">城市</label><input id="p_city" placeholder="Austin" /></div>
          <div><label for="p_state">州</label><input id="p_state" placeholder="TX" maxlength="2" /></div>
          <div><label for="p_zip">邮编</label><input id="p_zip" placeholder="78704" maxlength="5" /></div>
        </div>
        <label for="p_occupation">职业</label>
        <input id="p_occupation" placeholder="freelance graphic designer" />
        <label for="p_income">月收入区间（可选）</label>
        <input id="p_income" placeholder="4000-6000" />
      </div>
      <div id="business-fields" class="hidden">
        <label for="b_legal">法定名称</label>
        <input id="b_legal" placeholder="Lumen Creative LLC" />
        <div class="row">
          <div><label for="b_entity">实体类型</label>
            <select id="b_entity"><option>LLC</option><option>S-Corp</option><option>Sole Prop</option></select>
          </div>
          <div><label for="b_industry">行业</label><input id="b_industry" placeholder="design services" /></div>
        </div>
        <label for="b_street">街道</label>
        <input id="b_street" placeholder="2104 S 1st St" />
        <div class="row">
          <div><label for="b_city">城市</label><input id="b_city" placeholder="Austin" /></div>
          <div><label for="b_state">州</label><input id="b_state" placeholder="TX" maxlength="2" /></div>
          <div><label for="b_zip">邮编</label><input id="b_zip" placeholder="78704" maxlength="5" /></div>
        </div>
      </div>
      <div class="row" style="margin-top:12px">
        <button id="save-profile">保存档案</button>
        <button id="reset-profile" class="ghost">清空</button>
      </div>
      <p id="profile-msg" class="err"></p>
    </div>

    <div class="card" style="margin-top:14px">
      <h2>批量导入</h2>
      <label for="import-format">格式</label>
      <select id="import-format"><option value="json">JSON</option><option value="csv">CSV</option></select>
      <label for="import-text">内容</label>
      <textarea id="import-text" placeholder='[{"kind":"person","bank":"chase-personal","display_name":"王梅","name":"MEI WANG","street":"2104 S 1st St","city":"Austin","state":"TX","zip":"78704"}]'></textarea>
      <div class="row" style="margin-top:8px"><button id="do-import" class="ghost">导入</button></div>
      <p id="import-msg" class="err"></p>
    </div>
  </section>

  <section>
    <div class="card">
      <h2>提交生成</h2>
      <div class="row">
        <div>
          <label for="job-profile">档案</label>
          <select id="job-profile"></select>
        </div>
        <div>
          <label for="job-template">银行版式</label>
          <select id="job-template"></select>
        </div>
      </div>
      <div class="row">
        <div><label for="job-from">起始月份</label><input id="job-from" type="month" /></div>
        <div><label for="job-count">连续月数</label>
          <select id="job-count"><option>1</option><option>2</option><option selected>3</option><option>6</option></select>
        </div>
        <div><label for="job-seed">随机种子（可选）</label><input id="job-seed" placeholder="可留空" /></div>
      </div>
      <div class="row" style="margin-top:12px">
        <button id="submit-job">开始生成</button>
        <button id="refresh-jobs" class="ghost">刷新任务</button>
      </div>
      <p id="job-msg" class="err"></p>
    </div>

    <div class="card" style="margin-top:14px">
      <h2>任务</h2>
      <div id="jobs"><p class="empty">还没有任务。</p></div>
    </div>

    <div class="card" style="margin-top:14px">
      <h2>当前任务详情</h2>
      <div id="detail"><p class="empty">选一个任务看进度与产物。</p></div>
    </div>
  </section>
</main>

<script>
const API = (location.pathname.replace(/\\/generate\\/?$/, '') || '') + '/api/v1';
const $ = (id) => document.getElementById(id);
const state = { profiles: [], jobs: [], current: null, timer: null };

async function api(path, options) {
  const res = await fetch(API + path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, options));
  const text = await res.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch (e) { body = { error: text }; }
  if (!res.ok) {
    const msg = body && body.error ? (body.error.message || body.error) : ('HTTP ' + res.status);
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return body;
}

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([k, v]) => {
    if (k === 'text') node.textContent = v;
    else if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else node.setAttribute(k, v);
  });
  (children || []).forEach((c) => node.appendChild(c));
  return node;
}

async function loadTemplates() {
  // 模板清单来自既有 /templates 接口；只用能出片的版式。
  try {
    const data = await api('/templates');
    const list = (data.templates || data || []).filter((t) => !t.kind || t.kind !== 'static-web');
    const bankSel = $('bank'); const tmplSel = $('job-template');
    list.forEach((t) => {
      const id = t.id || t.template; const name = t.display_name || t.name || id;
      bankSel.appendChild(el('option', { value: id, text: name + ' · ' + id }));
      tmplSel.appendChild(el('option', { value: id, text: name }));
    });
  } catch (e) { /* 模板清单拿不到时不影响档案编辑 */ }
}

async function loadProfiles() {
  const data = await api('/profiles');
  state.profiles = data.profiles || [];
  const sel = $('job-profile');
  sel.innerHTML = '';
  if (!state.profiles.length) { sel.appendChild(el('option', { value: '', text: '（先建一份档案）' })); return; }
  state.profiles.forEach((p) => sel.appendChild(el('option', { value: p.id, text: p.display_name + ' · ' + p.bank })));
}

function profilePayload() {
  const kind = $('kind').value;
  const common = {
    kind,
    bank: $('bank').value,
    display_name: $('display_name').value.trim(),
  };
  if (kind === 'person') {
    common.payload = {
      name: $('p_name').value.trim(),
      address: { street: $('p_street').value.trim(), city: $('p_city').value.trim(), state: $('p_state').value.trim().toUpperCase(), zip: $('p_zip').value.trim() },
      occupation: $('p_occupation').value.trim(),
      monthly_income_hint: $('p_income').value.trim(),
    };
  } else {
    common.payload = {
      legalName: $('b_legal').value.trim(),
      entityType: $('b_entity').value,
      industry: $('b_industry').value.trim(),
      address: { street: $('b_street').value.trim(), city: $('b_city').value.trim(), state: $('b_state').value.trim().toUpperCase(), zip: $('b_zip').value.trim() },
    };
  }
  return common;
}

async function saveProfile() {
  $('profile-msg').textContent = '';
  try {
    const body = profilePayload();
    if (!body.display_name) throw new Error('显示名不能为空');
    await api('/profiles', { method: 'POST', body: JSON.stringify(body) });
    $('reset-profile').click();
    await loadProfiles();
  } catch (e) { $('profile-msg').textContent = e.message; }
}

async function doImport() {
  $('import-msg').textContent = '';
  try {
    const out = await api('/profiles/import', { method: 'POST', body: JSON.stringify({ kind: $('import-format').value, text: $('import-text').value }) });
    await loadProfiles();
    const errs = out.errors || [];
    $('import-msg').textContent = '导入 ' + (out.profiles || []).length + ' 条' + (errs.length ? '，' + errs.length + ' 条有问题：' + errs.map((e) => '第' + e.row + '行 ' + e.error).join('；') : '');
  } catch (e) { $('import-msg').textContent = e.message; }
}

function monthsFromForm() {
  const from = $('job-from').value;
  const count = parseInt($('job-count').value, 10);
  if (!from) throw new Error('先选起始月份');
  const [y, m] = from.split('-').map(Number);
  const out = [];
  for (let i = 0; i < count; i += 1) {
    const d = new Date(Date.UTC(y, m - 1 + i, 1));
    out.push(d.getUTCFullYear() + '-' + String(d.getUTCMonth() + 1).padStart(2, '0'));
  }
  return out;
}

async function submitJob() {
  $('job-msg').textContent = '';
  try {
    const profile_id = $('job-profile').value;
    if (!profile_id) throw new Error('先选一份档案');
    const body = { profile_id, template: $('job-template').value, months: monthsFromForm() };
    if ($('job-seed').value.trim()) body.seed = $('job-seed').value.trim();
    const job = await api('/gen/jobs', { method: 'POST', body: JSON.stringify(body) });
    state.current = job.id;
    await loadJobs();
    startPolling();
  } catch (e) { $('job-msg').textContent = e.message; }
}

async function loadJobs() {
  const data = await api('/gen/jobs');
  state.jobs = data.jobs || [];
  const host = $('jobs');
  host.innerHTML = '';
  if (!state.jobs.length) { host.appendChild(el('p', { class: 'empty', text: '还没有任务。' })); return; }
  const table = el('table');
  table.appendChild(el('thead', {}, [el('tr', {}, ['任务', '档案', '版式', '月份', '状态', '进度'].map((t) => el('th', { text: t })))]));
  const tbody = el('tbody');
  state.jobs.forEach((j) => {
    const tr = el('tr');
    const pct = j.progress && j.progress.length ? (j.progress[j.progress.length - 1].percent || 0) : 0;
    tr.appendChild(el('td', {}, [el('button', { class: 'ghost', 'data-id': j.id, text: j.id.slice(0, 8) })]));
    tr.appendChild(el('td', { text: j.profile_id.slice(0, 8) }));
    tr.appendChild(el('td', { text: j.template }));
    tr.appendChild(el('td', { text: (j.months || []).join(', ') }));
    tr.appendChild(el('td', {}, [el('span', { class: 'pill ' + j.status, text: j.status })]));
    tr.appendChild(el('td', { text: pct + '%' }));
    tr.querySelector('button').addEventListener('click', () => { state.current = j.id; renderDetail(); });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  host.appendChild(table);
}

function renderJob(job) {
  const host = $('detail');
  host.innerHTML = '';
  const pct = job.progress && job.progress.length ? (job.progress[job.progress.length - 1].percent || 0) : 0;
  host.appendChild(el('p', {}, [el('span', { class: 'pill ' + job.status, text: job.status }), el('span', { class: 'muted', text: '  ' + job.template + ' · ' + (job.months || []).join(', ') })]));
  const bar = el('div', { class: 'bar' }, [el('i')]);
  bar.firstChild.style.width = pct + '%';
  host.appendChild(bar);
  if (job.error) host.appendChild(el('p', { class: 'err', text: job.error }));
  const actions = el('div', { class: 'row', style: 'margin:10px 0' });
  if (job.status === 'running' || job.status === 'queued') {
    const btn = el('button', { class: 'danger', text: '取消任务' });
    btn.addEventListener('click', async () => { await api('/gen/jobs/' + job.id + '/cancel', { method: 'POST' }); renderDetail(); });
    actions.appendChild(btn);
  }
  actions.appendChild(el('a', { href: API + '/gen/jobs/' + job.id + '/package', text: '下载 zip 包' }));
  host.appendChild(actions);

  const result = job.result || {};
  const files = result.files || [];
  if (files.length) {
    const list = el('div', { class: 'files' });
    files.forEach((f) => {
      const name = typeof f === 'string' ? f : (f.name || f.path || '');
      const ok = typeof f === 'string' ? true : f.ok !== false;
      const row = el('div', { style: 'padding:4px 0' });
      row.appendChild(el('a', { href: API + '/gen/jobs/' + job.id + '/files/' + encodeURIComponent(name), text: name }));
      if (!ok) row.appendChild(el('span', { class: 'warn', text: '  校验未通过' }));
      list.appendChild(row);
    });
    host.appendChild(el('h3', { style: 'font-size:13px;margin:14px 0 6px', text: '产物' }));
    host.appendChild(list);
  }
  const warnings = (job.result && job.result.warnings) || [];
  if (warnings.length) {
    host.appendChild(el('h3', { style: 'font-size:13px;margin:14px 0 6px', text: '提示（不阻断交付）' }));
    const box = el('div', { class: 'log' });
    warnings.forEach((w) => box.appendChild(el('div', { class: 'warn', text: '· ' + w })));
    host.appendChild(box);
  }
  host.appendChild(el('h3', { style: 'font-size:13px;margin:14px 0 6px', text: '进度日志' }));
  const log = el('div', { class: 'log' });
  (job.progress || []).forEach((p) => log.appendChild(el('div', { text: '[' + (p.at || '').slice(11, 19) + '] ' + p.message })));
  host.appendChild(log);
}

async function renderDetail() {
  if (!state.current) return;
  try {
    const job = await api('/gen/jobs/' + state.current);
    renderJob(job);
    if (job.status === 'running' || job.status === 'queued') startPolling(); else stopPolling();
  } catch (e) { $('detail').innerHTML = '<p class="err">' + e.message + '</p>'; }
}

function startPolling() { stopPolling(); state.timer = setInterval(async () => { await loadJobs(); await renderDetail(); }, 1500); }
function stopPolling() { if (state.timer) { clearInterval(state.timer); state.timer = null; } }

$('kind').addEventListener('change', () => {
  const person = $('kind').value === 'person';
  $('person-fields').classList.toggle('hidden', !person);
  $('business-fields').classList.toggle('hidden', person);
});
$('save-profile').addEventListener('click', saveProfile);
$('do-import').addEventListener('click', doImport);
$('submit-job').addEventListener('click', submitJob);
$('refresh-jobs').addEventListener('click', async () => { await loadJobs(); });
$('reset-profile').addEventListener('click', () => {
  ['display_name','p_name','p_street','p_city','p_state','p_zip','p_occupation','p_income','b_legal','b_industry','b_street','b_city','b_state','b_zip']
    .forEach((id) => { const node = $(id); if (node) node.value = ''; });
  $('profile-msg').textContent = '';
});

(async function boot() {
  await loadTemplates();
  try { await loadProfiles(); } catch (e) { $('profile-msg').textContent = e.message; }
  try { await loadJobs(); } catch (e) { /* 队列未初始化时静默 */ }
})();
</script>
</body>
</html>
"""


def page_bytes() -> bytes:
    return PAGE.encode("utf-8")


def main() -> int:  # pragma: no cover - 仅本地调样式用
    import argparse

    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    parser = argparse.ArgumentParser(description="只起一个空壳服务调 /generate 页面样式")
    parser.add_argument("--port", type=int, default=8771)
    args = parser.parse_args()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = page_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a) -> None:
            pass

    print(f"http://127.0.0.1:{args.port}/generate/")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
