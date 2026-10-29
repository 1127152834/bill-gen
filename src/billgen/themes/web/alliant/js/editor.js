/* ============================================================
 * 可视化编辑器 —— 表单 → 数据 → StatementEngine 实时渲染
 * ============================================================ */
(function () {
  "use strict";
  const $ = sel => document.querySelector(sel);
  const LS_KEY = "statement-editor-data";

  let data = load() || JSON.parse(JSON.stringify(window.SAMPLE_DATA));
  /* 元数据创建时间默认值：算好直接填进字段（肉眼可见、导出不再变）=
     Period 止次日 07:00-07:59 随机；仅当值为空或仍属自动值(_cdAuto)时重算 */
  function applyAutoCreation() {
    data.meta = data.meta || {};
    if (data.meta._cdAuto === false) return;
    data.meta._cdAuto = true;
    const v = window.PDFExporter && PDFExporter.defaultCreation
      && PDFExporter.defaultCreation(data.statement.periodEnd);
    if (v) data.meta.creationDate = v;
    const i = document.querySelector('#editor input[data-path="meta.creationDate"]');
    if (i && i.value !== data.meta.creationDate) i.value = data.meta.creationDate;
  }
  applyAutoCreation();
  data.watermark = Object.assign({ enabled: true, text: "SAMPLE", color: "#0F6191", opacity: 0.10 }, data.watermark);
  /* 固化块（与原稿逐字符一致）：载入即强制为原稿文本，编辑器中不可编辑 */
  function applyBaked(d) {
    if (!window.BAKED || !d) return;
    d.statement = d.statement || {};
    d.statement.docTitle = window.BAKED.docTitle.text;
    d.branding = d.branding || {};
    d.branding.addrLine1 = window.BAKED.addrLine1.text;
    d.disclosure = d.disclosure || {};
    d.disclosure.heading = window.BAKED.disclosure.headingText;
    d.disclosure.paragraphs = window.BAKED.disclosure.paragraphTexts.slice();
  }
  applyBaked(data);
  let zoom = 0;

  /* ---------- 数据持久化 ---------- */
  function save() { try { localStorage.setItem(LS_KEY, JSON.stringify(data)); } catch (e) { } }
  function load() {
    try {
      const s = localStorage.getItem(LS_KEY);
      if (!s) return null;
      const d = JSON.parse(s);
      if (!d || !Array.isArray(d.accounts)) return null;   // 结构不完整 → 回退示例数据
      /* 迁移：旧版本默认值指向已删除的占位图文件（会裂图/导出失败）→ 还原为复刻默认 */
      if (d.reporting && d.reporting.promoUrl === "assets/promo-placeholder.svg") d.reporting.promoUrl = "";
      if (d.branding && d.branding.logo === "assets/promo-placeholder.svg") d.branding.logo = "";
      return d;
    } catch (e) { return null; }
  }

  /* ---------- 路径读写 ---------- */
  function getPath(obj, path) { return path.reduce((o, k) => (o == null ? o : o[k]), obj); }
  function setPath(obj, path, val) {
    const last = path[path.length - 1];
    const parent = path.slice(0, -1).reduce((o, k) => o[k], obj);
    parent[last] = val;
  }

  /* ---------- 表单构造 ---------- */
  const F = {
    text: (label, path, placeholder) =>
      `<label class="fld"><span>${label}</span><input type="text" data-path="${path}" value="${escAttr(getPath(data, path.split(".")))}" placeholder="${placeholder || ""}"></label>`,
    area: (label, path, rows) =>
      `<label class="fld"><span>${label}</span><textarea rows="${rows || 3}" data-path="${path}">${escAttr(getPath(data, path.split(".")))}</textarea></label>`,
    num: (label, path) =>
      `<label class="fld"><span>${label}</span><input type="number" step="0.01" data-path="${path}" value="${escAttr(getPath(data, path.split(".")))}"></label>`,
  };
  const escAttr = s => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");

  function buildEditor() {
    const root = $("#editor");
    let h = "";

    /* —— 元数据（默认值与原稿一致） —— */
    h += `<section class="grp"><h3>📄 元数据（PDF/文档）</h3><div class="grid2">`;
    h += F.text("Title", "meta.title");
    h += F.text("Author", "meta.author");
    h += F.text("Subject", "meta.subject");
    h += F.text("Keywords", "meta.keywords");
    h += F.text("Creator", "meta.creator");
    h += F.text("Producer", "meta.producer");
    h += F.text("CreationDate", "meta.creationDate", "默认 = Period止次日凌晨7-8点随机(UTC,无时区)；可手改，清空还原");
    h += `<label class="fld"><span>导出 PDF 文件名</span><input type="text" data-path="meta.pdfFileName" value="${escAttr((data.meta && data.meta.pdfFileName) || "")}" placeholder="留空 = 账号-statement.pdf"></label>`;
    h += F.text("Tray 标记(隐藏白字)", "meta.tray");
    h += F.text("Sheet 序号(隐藏白字)", "meta.sheet");
    h += `</div><p class="hint">以上默认值取自原稿 PDF 元数据；「导出 PDF」时全部写入 PDF 文档属性。文件名字段决定导出文件名（留空 = 账号-statement.pdf，自动补 .pdf 后缀）。</p></section>`;

    /* —— 水印 —— */
    const wmOn = data.watermark.enabled !== false;
    h += `<section class="grp${wmOn ? "" : " dim"}"><h3>🔏 SAMPLE 水印</h3>
      <label class="fld chk"><input type="checkbox" data-path="watermark.enabled"${wmOn ? " checked" : ""}><span>启用水印</span></label><div class="grid2">`;
    h += F.text("水印文字", "watermark.text");
    h += `<label class="fld"><span>颜色</span><input type="color" data-path="watermark.color" value="${data.watermark.color}"></label>`;
    h += `<label class="fld"><span>不透明度 ${(data.watermark.opacity * 100).toFixed(0)}%</span><input type="range" min="2" max="40" step="1" data-path="watermark.opacity" data-scale="0.01" value="${Math.round(data.watermark.opacity * 100)}"></label>`;
    h += `</div><p class="hint">开关对预览与「导出 PDF」同步生效；关闭后整份文档无水印。</p></section>`;

    /* —— 机构与页眉 —— */
    h += `<section class="grp"><h3>🏦 机构信息（左上联系块）</h3><div class="grid2">`;
    h += F.text("机构名称", "branding.institution");
    h += F.text("电话", "branding.phone");
    h += F.text("网站（粗体行）", "branding.website");
    h += F.text("询问标签", "branding.inquiriesLabel");
    h += `<label class="fld"><span>地址行 1 🔒 已按原稿固化</span><input type="text" value="${escAttr(data.branding.addrLine1)}" disabled></label>`;
    h += F.text("地址行 2", "branding.addrLine2");
    h += `</div>
      <div class="row"><label class="btn-file">上传自定义 LOGO<input type="file" accept="image/*" id="logo-file" hidden></label>
      <button class="btn ghost" id="logo-clear">恢复复刻 LOGO</button></div>
      <p class="hint">默认 LOGO 为原稿矢量复刻（assets/logo.svg，52.24,1.28 起 189.48×43.52pt，逐路径提取）；可上传任意图片替换。机构名称文字为可编辑示例文本（用于披露段落等）。</p></section>`;

    /* —— 账单信息 —— */
    h += `<section class="grp"><h3>🧾 账单信息（右上页眉）</h3><div class="grid2">`;
    h += `<label class="fld"><span>文档标题（右上大标题）🔒 已按原稿固化</span><input type="text" value="${escAttr(data.statement.docTitle)}" disabled></label>`;
    h += F.text("Account Number", "statement.accountNumber");
    h += F.text("Statement Period 起", "statement.periodStart");
    h += F.text("Statement Period 止", "statement.periodEnd");
    h += `</div><p class="hint">Page（N of M）由分页自动计算，无需填写。</p></section>`;

    /* —— 客户地址 —— */
    h += `<section class="grp"><h3>📬 客户地址块</h3>`;
    data.customer.lines.forEach((l, i) => { h += F.text(`第 ${i + 1} 行`, `customer.lines.${i}`); });
    h += `<div class="row"><button class="btn ghost" id="addr-add">+ 地址行</button><button class="btn ghost" id="addr-del">− 末行</button></div></section>`;

    /* —— 账户与流水 —— */
    data.accounts.forEach((acc, ai) => {
      const rows = [];
      let bal = acc.beginning;
      (acc.transactions || []).forEach(t => { bal += (t.dir === "withdrawal" ? -1 : 1) * Math.abs(t.amount || 0); });
      h += `<section class="grp acc" data-ai="${ai}"><h3>💰 账户 ${escAttr(acc.id)} — ${escAttr(acc.name)}
        <span class="hbtns"><button class="btn tiny" data-acc-up="${ai}">上移</button><button class="btn tiny" data-acc-down="${ai}">下移</button><button class="btn tiny danger" data-acc-del="${ai}">删除账户</button></span></h3><div class="grid2">`;
      h += F.text("ID", `accounts.${ai}.id`);
      h += F.text("账户名称", `accounts.${ai}.name`);
      h += F.num("期初余额 Beginning", `accounts.${ai}.beginning`);
      h += F.text("APY（留空显示 –）", `accounts.${ai}.apy`);
      h += F.num("Dividends Earned（留空显示 –）", `accounts.${ai}.dividendsEarned`);
      h += F.num("YTD Dividends", `accounts.${ai}.ytdDividends`);
      h += `</div>`;
      h += F.area("APY 备注行（表尾自动折行，留空隐藏）", `accounts.${ai}.note`, 2);
      h += `<h4>流水（${(acc.transactions || []).length} 笔）<button class="btn tiny" data-tx-add="${ai}">+ 添加流水</button></h4>
        <div class="txwrap"><table class="tx"><thead><tr><th>日期</th><th>金额</th><th>方向</th><th>余额</th><th></th></tr></thead><tbody>`;
      let run = acc.beginning;
      (acc.transactions || []).forEach((t, ti) => {
        run += (t.dir === "withdrawal" ? -1 : 1) * Math.abs(t.amount || 0);
        /* 侧栏窄（430px）：每笔流水分两行——上行 日期/金额/方向/余额/操作，下行 描述占满全宽 */
        h += `<tr data-ai="${ai}" data-ti="${ti}" class="txr">
          <td><input type="text" data-path="accounts.${ai}.transactions.${ti}.date" value="${escAttr(t.date)}"></td>
          <td><input type="number" step="0.01" data-path="accounts.${ai}.transactions.${ti}.amount" value="${escAttr(t.amount)}"></td>
          <td><select data-path="accounts.${ai}.transactions.${ti}.dir">
            <option value="deposit"${t.dir !== "withdrawal" ? " selected" : ""}>存入</option>
            <option value="withdrawal"${t.dir === "withdrawal" ? " selected" : ""}>取出</option></select></td>
          <td class="run">${run.toFixed(2)}</td>
          <td class="ops">
            <button class="btn tiny" data-tx-up="${ai}:${ti}" title="上移">↑</button>
            <button class="btn tiny" data-tx-down="${ai}:${ti}" title="下移">↓</button>
            <button class="btn tiny danger" data-tx-del="${ai}:${ti}" title="删除">✕</button>
          </td></tr>
        <tr class="txr-desc"><td colspan="5"><input type="text" class="desc" data-path="accounts.${ai}.transactions.${ti}.desc" value="${escAttr(t.desc)}" placeholder="交易描述（保存到账单后自动折行）"></td></tr>`;
      });
      h += `</tbody></table></div>
        <div class="stats">期初 ${fmt(acc.beginning)} ｜ 存入合计 ${fmt(sum(acc, "deposit"))} ｜ 取出合计 ${fmt(sum(acc, "withdrawal"))} ｜ 期末 ${fmt(bal)}</div>
        </section>`;
    });
    h += `<section class="grp"><button class="btn" id="acc-add">+ 添加账户区段</button>
      <p class="hint">披露文本框固定跟随第 1 个账户区段（与原稿排版顺序一致）；区段间距、分页与页码自动联动。</p></section>`;

    /* —— 披露文本 —— */
    h += `<section class="grp"><h3>⚖️ 披露文本 🔒 已按原稿固化（逐字符坐标，不可编辑）</h3>`;
    h += `<label class="fld"><span>标题</span><input type="text" value="${escAttr(data.disclosure.heading)}" disabled></label>`;
    h += `<label class="fld"><span>段落</span><textarea rows="10" disabled>${escAttr(data.disclosure.paragraphs.join("\n\n"))}</textarea></label>`;
    h += `</section>`;

    /* —— 年度报告 —— */
    h += `<section class="grp"><h3>📊 年度报告区段（末页）</h3><div class="grid2">`;
    h += F.text("年份", "reporting.year");
    h += F.text("标签文本", "reporting.label");
    h += F.text("合计值（留空 = 自动求和 YTD）", "reporting.valueOverride");
    h += `</div>
      <div class="row"><label class="btn-file">上传横幅图片<input type="file" accept="image/*" id="promo-file" hidden></label>
      <button class="btn ghost" id="promo-reset">恢复复刻横幅</button></div>
      <p class="hint">横幅区域 168.24×37.68pt，位于原稿图片位置；默认为原稿第 2 页位图 LOGO 复刻（assets/promo-logo.jpg）。</p></section>`;

    root.innerHTML = h;

    // 披露段落编辑框（数组 ↔ 文本）
    const ta = root.querySelector('[data-path="disclosure._paras"]');
    if (ta) {
      ta.value = data.disclosure.paragraphs.join("\n\n");
      ta.addEventListener("input", () => {
        data.disclosure.paragraphs = ta.value.split(/\n\s*\n/).map(s => s.replace(/^\n+|\n+$/g, "")).filter(s => s.length);
        refresh();
      });
      ta.addEventListener("blur", () => buildEditor());
    }
    bindInputs();
  }

  const fmt = n => (typeof n === "number" ? "$" + n.toFixed(2) : "$0.00");
  function sum(acc, dir) {
    return (acc.transactions || []).filter(t => dir === "withdrawal" ? t.dir === "withdrawal" : t.dir !== "withdrawal")
      .reduce((s, t) => s + Math.abs(t.amount || 0), 0);
  }

  /* ---------- 输入绑定 ---------- */
  function bindInputs() {
    document.querySelectorAll("#editor [data-path]").forEach(el => {
      if (el.tagName === "TEXTAREA" && el.dataset.path === "disclosure._paras") return;
      const handler = e => {
        const path = el.dataset.path.split(".");
        let v = el.type === "checkbox" ? el.checked : e.target.value;
        if (el.type === "number") v = v === "" ? "" : parseFloat(v);
        if (el.dataset.scale) v = parseFloat(v) * parseFloat(el.dataset.scale);
        if (el.tagName === "SELECT") v = el.value;
        setPath(data, path, v);
        const p = el.dataset.path;
        if (p === "meta.creationDate") {
          if (String(v).trim() === "") { data.meta._cdAuto = true; applyAutoCreation(); }   // 清空=回到自动默认
          else data.meta._cdAuto = false;                        // 手工值冻结
        }
        if (p === "statement.periodEnd") applyAutoCreation();    // 止日期变 → 跟随重算
        refresh();
        if (el.type === "checkbox") { save(); buildEditor(); }   // 开关即时重绘表单(置灰状态)
      };
      el.addEventListener(el.tagName === "SELECT" || el.type === "color" || el.type === "range" ? "input" : "input", handler);
      el.addEventListener("change", () => { save(); });
    });

    // 文件上传
    const lf = $("#logo-file");
    if (lf) lf.addEventListener("change", e => {
      const f = e.target.files[0]; if (!f) return;
      const rd = new FileReader();
      rd.onload = () => { data.branding.logo = rd.result; save(); buildEditor(); refresh(); };
      rd.readAsDataURL(f);
    });
    const lc = $("#logo-clear"); if (lc) lc.addEventListener("click", () => { data.branding.logo = ""; save(); buildEditor(); refresh(); });
    const pf = $("#promo-file");
    if (pf) pf.addEventListener("change", e => {
      const f = e.target.files[0]; if (!f) return;
      const rd = new FileReader();
      rd.onload = () => { data.reporting.promoUrl = rd.result; save(); refresh(); };
      rd.readAsDataURL(f);
    });
    const pr = $("#promo-reset"); if (pr) pr.addEventListener("click", () => { data.reporting.promoUrl = ""; save(); refresh(); });
    const aa = $("#addr-add"); if (aa) aa.addEventListener("click", () => { data.customer.lines.push(""); save(); buildEditor(); refresh(); });
    const ad = $("#addr-del"); if (ad) ad.addEventListener("click", () => { data.customer.lines.pop(); save(); buildEditor(); refresh(); });

    // 账户操作
    document.querySelectorAll("[data-acc-del]").forEach(b => b.addEventListener("click", () => {
      if (data.accounts.length <= 1) return alert("至少保留一个账户区段");
      data.accounts.splice(+b.dataset.accDel, 1); save(); buildEditor(); refresh();
    }));
    document.querySelectorAll("[data-acc-up]").forEach(b => b.addEventListener("click", () => {
      const i = +b.dataset.accUp; if (i === 0) return;
      [data.accounts[i - 1], data.accounts[i]] = [data.accounts[i], data.accounts[i - 1]]; save(); buildEditor(); refresh();
    }));
    document.querySelectorAll("[data-acc-down]").forEach(b => b.addEventListener("click", () => {
      const i = +b.dataset.accDown; if (i >= data.accounts.length - 1) return;
      [data.accounts[i + 1], data.accounts[i]] = [data.accounts[i], data.accounts[i + 1]]; save(); buildEditor(); refresh();
    }));
    $("#acc-add").addEventListener("click", () => {
      data.accounts.push({ id: String(data.accounts.length + 10), name: "NEW ACCOUNT", beginning: 0, apy: "", dividendsEarned: "", ytdDividends: "", note: "", transactions: [{ date: data.statement.periodStart, desc: "DEPOSIT SAMPLE TRANSACTION ", amount: 100, dir: "deposit" }] });
      save(); buildEditor(); refresh();
    });

    // 流水操作（联动：计数/合计/余额/分页自动重算）
    document.querySelectorAll("[data-tx-add]").forEach(b => b.addEventListener("click", () => {
      const acc = data.accounts[+b.dataset.txAdd];
      const last = acc.transactions[acc.transactions.length - 1];
      acc.transactions.push({ date: (last && last.date) || data.statement.periodStart, desc: "DEPOSIT SAMPLE TRANSACTION ", amount: 0, dir: "deposit" });
      save(); buildEditor(); refresh();
    }));
    document.querySelectorAll("[data-tx-del]").forEach(b => b.addEventListener("click", () => {
      const [ai, ti] = b.dataset.txDel.split(":").map(Number);
      data.accounts[ai].transactions.splice(ti, 1); save(); buildEditor(); refresh();
    }));
    document.querySelectorAll("[data-tx-up]").forEach(b => b.addEventListener("click", () => {
      const [ai, ti] = b.dataset.txUp.split(":").map(Number);
      if (ti === 0) return;
      const t = data.accounts[ai].transactions;
      [t[ti - 1], t[ti]] = [t[ti], t[ti - 1]]; save(); buildEditor(); refresh();
    }));
    document.querySelectorAll("[data-tx-down]").forEach(b => b.addEventListener("click", () => {
      const [ai, ti] = b.dataset.txDown.split(":").map(Number);
      const t = data.accounts[ai].transactions;
      if (ti >= t.length - 1) return;
      [t[ti + 1], t[ti]] = [t[ti], t[ti + 1]]; save(); buildEditor(); refresh();
    }));
  }

  /* ---------- 水印配置（开关/文字；关闭 → null = 引擎与 PDF 导出均不渲染） ---------- */
  function wmCfg() {
    const w = data.watermark || {};
    if (w.enabled === false || w.text === "") return null;
    return { text: w.text || "SAMPLE", color: w.color || "#0F6191", opacity: w.opacity != null ? w.opacity : 0.10 };
  }

  /* ---------- 渲染预览 ---------- */
  let renderTimer = null;
  function refresh() {
    clearTimeout(renderTimer);
    renderTimer = setTimeout(() => {
      // 焦点保持：文本输入进行中不重排预览（输完整个值才算一次输入）；
      // range/color 等即时控件不在保持范围，拖动仍实时预览
      const ae = document.activeElement;
      if (ae && ((ae.tagName === 'INPUT' && (ae.type === 'text' || !ae.type || ae.type === 'number'))
          || ae.tagName === 'TEXTAREA') && document.getElementById('editor').contains(ae)) {
        renderTimer = setTimeout(refresh, 400);
        return;
      }
      const out = StatementEngine.render(data, { watermark: wmCfg() });
      $("#stage").innerHTML = `<div class="statement-pages">${out.html}</div>`;
      $("#pageinfo").textContent = `共 ${out.pageCount} 页`;
      applyZoom();
      save();
    }, 120);
  }

  /* ---------- 缩放 ---------- */
  function applyZoom() {
    if (!zoom) zoom = fitZoom();
    const stage = $("#stage");
    stage.style.transform = `scale(${zoom})`;
    const wrap = $("#stage-wrap");
    const first = stage.querySelector(".page");
    if (first) {
      const w = first.offsetWidth, hgt = stage.scrollHeight;
      stage.style.width = (w * zoom + 48 / zoom) + "px";
      stage.style.height = hgt * zoom + "px";
    }
    $("#zoomval").textContent = Math.round(zoom * 100) + "%";
  }
  function fitZoom() {
    const wrap = $("#stage-wrap");
    const first = $("#stage .page");
    if (!first) return 1;
    return Math.min(1, (wrap.clientWidth - 60) / first.offsetWidth);
  }

  /* ---------- 导出文件名（可编辑；留空 = 账号-statement.pdf；自动补 .pdf 并过滤非法字符） ---------- */
  function pdfFileName() {
    let n = ((data.meta && data.meta.pdfFileName) || "").trim();
    if (!n) n = (((data.statement && data.statement.accountNumber) || "statement") + "-statement");
    n = n.replace(/[\\/:*?"<>|]/g, "").trim();
    if (!n) n = "statement";
    return /\.pdf$/i.test(n) ? n : n + ".pdf";
  }

  /* ---------- 工具栏 ---------- */
  function bindToolbar() {
    $("#zoom-fit").addEventListener("click", () => { zoom = fitZoom(); applyZoom(); });
    // 焦点离开任一文本输入 → 立即按完整值刷新
    $("#editor").addEventListener("focusout", () => refresh());
    $("#zoom-in").addEventListener("click", () => { zoom = Math.min(2, zoom + 0.1); applyZoom(); });
    $("#zoom-out").addEventListener("click", () => { zoom = Math.max(0.2, zoom - 0.1); applyZoom(); });
    $("#btn-print").addEventListener("click", () => window.print());
    $("#btn-pdf").addEventListener("click", async () => {
      const btn = $("#btn-pdf"), old = btn.textContent;
      btn.textContent = "生成中…"; btn.disabled = true;
      try {
        await PDFExporter.download(data, { watermark: wmCfg() }, pdfFileName());
      } catch (e) { alert("PDF 生成失败: " + e.message); console.error(e); }
      btn.textContent = old; btn.disabled = false;
    });
    $("#btn-reset").addEventListener("click", () => {
      if (!confirm("重置为示例数据？当前编辑内容将丢失。")) return;
      localStorage.removeItem(LS_KEY);
      data = JSON.parse(JSON.stringify(window.SAMPLE_DATA));
      applyAutoCreation();
      buildEditor(); refresh();
    });
    $("#btn-export").addEventListener("click", () => {
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "statement-data.json";
      a.click();
      URL.revokeObjectURL(a.href);
    });
    $("#btn-import").addEventListener("change", e => {
      const f = e.target.files[0]; if (!f) return;
      const rd = new FileReader();
      rd.onload = () => {
        try {
          const j = JSON.parse(rd.result);
          if (!j.accounts || !Array.isArray(j.accounts)) throw new Error("缺少 accounts");
          data = j; applyBaked(data); save(); buildEditor(); refresh();
        } catch (err) { alert("导入失败: " + err.message); }
      };
      rd.readAsText(f);
    });
    window.addEventListener("resize", () => { zoom = 0; applyZoom(); });
  }

  /* ---------- 启动 ---------- */
  function boot() {
    document.title = (data.meta && data.meta.title) || "Statement";
    buildEditor();
    bindToolbar();
    refresh();
    document.fonts && document.fonts.ready.then(() => refresh());
  }
  window.addEventListener("DOMContentLoaded", boot);
})();
