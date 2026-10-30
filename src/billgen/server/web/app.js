/* BillGen: dependency-free, schema-preserving document workspace. */
const $ = (id) => document.getElementById(id);
const clone = (value) => JSON.parse(JSON.stringify(value));
const PAGE_SIZE = 10;
const DRAFT_PREFIX = "billgen:workbench:v2:";
const legacyPaths = {
  "boa-personal": "/boa/?tpl=personal",
  "boa-business": "/boa/?tpl=business",
  wellsfargo: "/wf/",
  mercury: "/merc/?template=mercury",
  capitalone: "/merc/?template=capitalone",
  sofi: "/merc/?template=sofi",
  "chase-personal": "/chase/",
  "chase-business": "/chasebiz/",
  "usbank-personal": "/us/",
  "usbank-business": "/usbiz/",
  alliant: "/alliant/",
  ally: "/ally/",
};
const labels = {
  template: "模板标识",
  auto: "自动计算",
  watermark: "模板数据水印",
  encrypt: "模板数据加密",
  account: "账户信息",
  account_info: "账户信息",
  account_number: "账户号码",
  account_name: "账户名称",
  account_holder: "账户持有人",
  name: "姓名 / 名称",
  address: "地址",
  city: "城市",
  state: "州 / 地区",
  zip: "邮政编码",
  zip_code: "邮政编码",
  phone: "联系电话",
  email: "电子邮箱",
  statement: "账单信息",
  statement_date: "账单日期",
  statement_period: "账单周期",
  period: "账单周期",
  period_start: "开始日期",
  period_end: "结束日期",
  start_date: "开始日期",
  end_date: "结束日期",
  transactions: "交易明细",
  transaction: "交易",
  deposits: "入账明细",
  withdrawals: "支出明细",
  summary: "账户概览",
  balances: "余额信息",
  balance: "余额",
  beginning_balance: "期初余额",
  ending_balance: "期末余额",
  opening_balance: "期初余额",
  closing_balance: "期末余额",
  amount: "金额",
  date: "日期",
  description: "描述",
  type: "类型",
  bank: "银行信息",
  customer: "客户信息",
  holder: "持有人信息",
  company: "公司信息",
  business_name: "公司名称",
  pages: "页面数据",
  items: "项目明细",
  totals: "汇总",
  fees: "费用",
  interest: "利息",
  payment: "付款信息",
  payments: "付款明细",
  debit: "借记金额",
  credit: "贷记金额",
  currency: "币种",
  meta: "文档元数据",
  metadata: "文档元数据",
  header: "页眉",
  footer: "页脚",
  routing_number: "路由号码",
  checking: "支票账户",
  savings: "储蓄账户",
  details: "详细信息",
  text: "文本",
  value: "值",
};
const state = {
  templates: [],
  current: null,
  data: null,
  options: {},
  dirty: false,
  revision: 0,
  sequence: 0,
  loadSequence: 0,
  job: null,
  page: 0,
  zoom: 100,
  rendering: false,
  jsonValid: true,
  jsonRaw: "",
  draftTimer: null,
  formTimer: null,
  mode: "form",
  storageWarning: false,
  synth: { ready: false, templates: [], busy: false },
};
function node(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}
function title(key) {
  return labels[key] || key.replace(/[_-]/g, " ");
}
function typeOf(value) {
  return value === null
    ? "null"
    : Array.isArray(value)
      ? "array"
      : typeof value;
}
function isStatic() {
  return (
    state.current &&
    (state.current.kind === "static-web" ||
      state.current.server_render === false)
  );
}
function message(text, level = "error") {
  const el = $("message");
  el.replaceChildren();
  el.dataset.level = level;
  el.hidden = !text;
  if (!text) return;
  const close = node("button", "", "×");
  close.type = "button";
  close.setAttribute("aria-label", "关闭提示");
  close.onclick = () => (el.hidden = true);
  el.append(close, document.createTextNode(text));
}
function status(text, kind = "ready") {
  $("document-status").textContent = text;
  $("document-status").dataset.state = kind;
}
function refreshStatus() {
  if (state.rendering) status("正在渲染", "rendering");
  else if (!state.jsonValid) status("JSON 待修正", "error");
  else if (state.dirty) status("有未预览修改", "dirty");
  else status(state.job ? "预览已更新" : "已就绪");
}
function setBusy(busy) {
  state.rendering = busy;
  $("render-overlay").hidden = !busy;
  updateActions();
  refreshStatus();
}
function updateActions() {
  const disabled = !state.current || isStatic() || !state.data;
  [
    "import-button",
    "export-json",
    "reset-button",
    "preview-button",
    "download-button",
    "empty-preview-button",
  ].forEach(
    (id) =>
      ($(id).disabled =
        disabled ||
        (state.rendering &&
          [
            "preview-button",
            "download-button",
            "empty-preview-button",
          ].includes(id))),
  );
  $("previous-page").disabled = !state.job || state.page === 0;
  $("next-page").disabled =
    !state.job || state.page >= (state.job?.links?.pages || []).length - 1;
}
async function api(path, options) {
  const response = await fetch(path, options);
  let body;
  try {
    body = await response.json();
  } catch {
    throw Error(`服务返回了非 JSON 响应（${response.status}）`);
  }
  if (!response.ok)
    throw Error(
      `${body.error?.message || "请求失败"}${body.request_id ? " · 请求 " + body.request_id.slice(0, 10) : ""}`,
    );
  return body;
}
function saveDraft() {
  if (!state.current || isStatic() || !state.data) return;
  clearTimeout(state.draftTimer);
  try {
    localStorage.setItem(
      DRAFT_PREFIX + state.current.id,
      JSON.stringify({
        data: state.data,
        options: state.options,
        raw: state.jsonRaw,
        valid: state.jsonValid,
        dirty: state.dirty,
        savedAt: Date.now(),
      }),
    );
    $("draft-status").textContent =
      "草稿已保存 · " +
      new Date().toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
      });
  } catch {
    if (!state.storageWarning) {
      state.storageWarning = true;
      message(
        "浏览器草稿空间不足或不可用；请导出 JSON 保存当前修改。",
        "warning",
      );
    }
    $("draft-status").textContent = "草稿尚未保存，请导出 JSON";
  }
}
function scheduleSave() {
  clearTimeout(state.draftTimer);
  state.draftTimer = setTimeout(saveDraft, 350);
  $("draft-status").textContent = "正在保存草稿…";
}
function markDirty() {
  state.revision++;
  state.dirty = true;
  scheduleSave();
  refreshStatus();
  if (state.job)
    $("preview-note").textContent = "数据已修改 · 当前预览为上一次生成结果";
}
function changed() {
  state.jsonRaw = JSON.stringify(state.data, null, 2);
  state.jsonValid = true;
  markDirty();
}
function invalidNumber() {
  return (
    state.mode === "form" &&
    $("schema-form").querySelector('[aria-invalid="true"]')
  );
}
function jsonChanged() {
  state.jsonRaw = $("json-editor").value;
  try {
    const data = JSON.parse(state.jsonRaw);
    if (!data || Array.isArray(data) || typeof data !== "object")
      throw Error("文档根节点必须是 JSON 对象");
    state.data = data;
    state.jsonValid = true;
    $("json-feedback").textContent = "JSON 有效 · 已同步到文档数据";
    $("json-feedback").classList.remove("error");
    $("json-editor").setAttribute("aria-invalid", "false");
  } catch (error) {
    state.jsonValid = false;
    $("json-feedback").textContent = error.message;
    $("json-feedback").classList.add("error");
    $("json-editor").setAttribute("aria-invalid", "true");
  }
  state.revision++;
  state.dirty = true;
  scheduleSave();
  refreshStatus();
  if (state.job)
    $("preview-note").textContent = "数据已修改 · 当前预览为上一次生成结果";
}
function readJson() {
  if (!state.jsonValid) {
    message("请先修正 JSON 编辑器中的语法或根对象错误。");
    $("json-editor").focus();
    return false;
  }
  const invalid = invalidNumber();
  if (invalid) {
    message("请先修正标记的数字字段。");
    invalid.focus();
    return false;
  }
  return true;
}
function at(path) {
  return path.reduce((value, key) => value[key], state.data);
}
function assign(path, value) {
  if (!path.length) return;
  let parent = at(path.slice(0, -1));
  parent[path[path.length - 1]] = value;
  changed();
}
function emptyLike(value) {
  if (Array.isArray(value)) return [];
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value).map(([k, v]) => [k, emptyLike(v)]),
    );
  if (typeof value === "number") return 0;
  if (typeof value === "boolean") return false;
  if (value === null) return null;
  return "";
}
function scalar(key, value, path) {
  const row = node("div", "field-row");
  const id =
    "field-" +
    path.map((p) => encodeURIComponent(String(p)).replace(/%/g, "_")).join("-");
  const label = node("label", "field-label", title(String(key)));
  label.htmlFor = id;
  label.append(
    node(
      "small",
      "",
      String(key) +
        " · " +
        ({ string: "文本", number: "数字", boolean: "布尔值", null: "空值" }[
          typeOf(value)
        ] || typeOf(value)),
    ),
  );
  row.append(label);
  if (value === null) {
    const box = node("div");
    box.append(node("span", "null-value", "null"));
    const edit = node("button", "button small", "编辑 JSON");
    edit.type = "button";
    edit.onclick = () => switchMode("json");
    box.append(edit);
    row.append(box);
    return row;
  }
  let input;
  if (typeof value === "boolean") {
    input = node("input");
    input.type = "checkbox";
    input.checked = value;
    input.addEventListener("change", () => assign(path, input.checked));
  } else {
    input = node(
      typeof value === "string" && (value.includes("\n") || value.length > 100)
        ? "textarea"
        : "input",
      "field-input",
    );
    if (input.tagName === "INPUT") input.type = "text";
    input.value = String(value);
    if (typeof value === "number") input.inputMode = "decimal";
    input.addEventListener("input", () => {
      if (typeof value === "number") {
        const text = input.value.trim();
        const valid = text !== "" && Number.isFinite(Number(text));
        input.setAttribute("aria-invalid", String(!valid));
        if (!valid) return;
        assign(path, Number(text));
      } else assign(path, input.value);
    });
  }
  input.id = id;
  input.dataset.path = JSON.stringify(path);
  row.append(input);
  return row;
}
function complex(key, value, path, depth = 0) {
  if (!value || typeof value !== "object") return scalar(key, value, path);
  const array = Array.isArray(value);
  const details = node("details", "schema-group");
  details.dataset.path = JSON.stringify(path);
  const summary = node("summary", "", title(String(key)));
  summary.append(
    node(
      "span",
      "group-count",
      `${array ? value.length + " 项" : Object.keys(value).length + " 字段"} · ${array ? "数组" : "分组"}`,
    ),
  );
  details.append(summary);
  const body = node("div", "group-body");
  details.append(body);
  let mounted = false;
  details.addEventListener("toggle", () => {
    if (details.open && !mounted) {
      mounted = true;
      if (array) arrayBody(body, path);
      else objectBody(body, path, depth + 1);
    }
  });
  return details;
}
function objectBody(container, path, depth = 0) {
  const data = at(path);
  for (const [key, value] of Object.entries(data))
    container.append(complex(key, value, [...path, key], depth));
  if (!Object.keys(data).length)
    container.append(
      node("p", "array-empty", "空对象 · 可在 JSON 编辑器添加字段"),
    );
}
function arrayBody(container, path, page = 0) {
  container.replaceChildren();
  const array = at(path);
  const pageCount = Math.max(1, Math.ceil(array.length / PAGE_SIZE));
  page = Math.min(page, pageCount - 1);
  const bar = node("div", "array-toolbar");
  bar.append(node("span", "", `共 ${array.length} 项 · 每页 ${PAGE_SIZE} 项`));
  const add = node("button", "button small", "＋ 添加项目");
  add.type = "button";
  add.onclick = () => {
    array.push(array.length ? emptyLike(array[0]) : "");
    changed();
    arrayBody(container, path, Math.floor((array.length - 1) / PAGE_SIZE));
    updateGroupCount(container, array.length);
  };
  bar.append(add);
  container.append(bar);
  if (!array.length)
    container.append(
      node("p", "array-empty", "暂无项目，点击「添加项目」开始。"),
    );
  array
    .slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)
    .forEach((value, offset) => {
      const index = page * PAGE_SIZE + offset;
      const entry = node("div", "array-entry");
      const head = node("div", "array-item-head");
      head.append(
        node("span", "", `项目 ${String(index + 1).padStart(2, "0")}`),
      );
      const duplicate = node("button", "button small", "复制");
      duplicate.type = "button";
      duplicate.setAttribute("aria-label", `复制第 ${index + 1} 项`);
      duplicate.onclick = () => {
        array.splice(index + 1, 0, clone(array[index]));
        changed();
        arrayBody(container, path, page);
        updateGroupCount(container, array.length);
      };
      const remove = node("button", "button small danger", "删除");
      remove.type = "button";
      remove.setAttribute("aria-label", `删除第 ${index + 1} 项`);
      remove.onclick = () => {
        if (
          value &&
          typeof value === "object" &&
          !confirm(`删除第 ${index + 1} 项？此操作会移除此项的全部字段。`)
        )
          return;
        array.splice(index, 1);
        changed();
        arrayBody(container, path, page);
        updateGroupCount(container, array.length);
      };
      head.append(duplicate, remove);
      entry.append(head, complex(String(index), value, [...path, index], 1));
      container.append(entry);
    });
  if (pageCount > 1) {
    const pagination = node("div", "array-pagination");
    const previous = node("button", "button small", "上一页"),
      next = node("button", "button small", "下一页");
    previous.type = next.type = "button";
    previous.disabled = page === 0;
    next.disabled = page === pageCount - 1;
    previous.onclick = () => arrayBody(container, path, page - 1);
    next.onclick = () => arrayBody(container, path, page + 1);
    pagination.append(
      previous,
      node("span", "", `${page + 1} / ${pageCount}`),
      next,
    );
    container.append(pagination);
  }
}
function updateGroupCount(container, length) {
  const count = container.parentElement.querySelector(
    ":scope > summary .group-count",
  );
  if (count) count.textContent = `${length} 项 · 数组`;
}
function renderForm() {
  const container = $("schema-form");
  container.replaceChildren();
  if (!state.data) return;
  container.classList.add("root-fields");
  objectBody(container, []);
}
function switchMode(mode) {
  if (mode === state.mode) return;
  if (invalidNumber()) {
    message("请先修正数字字段，再切换编辑模式。");
    invalidNumber().focus();
    return;
  }
  if (mode === "form" && !readJson()) return;
  state.mode = mode;
  const form = mode === "form";
  $("form-panel").hidden = !form;
  $("json-panel").hidden = form;
  ["form", "json"].forEach((name) => {
    $(name + "-tab").setAttribute("aria-selected", String(mode === name));
    $(name + "-tab").tabIndex = mode === name ? 0 : -1;
  });
  if (form) renderForm();
  else {
    $("json-editor").value =
      state.jsonRaw || JSON.stringify(state.data, null, 2);
    $("json-feedback").textContent = state.jsonValid
      ? "有效的 JSON 对象"
      : "JSON 尚待修正";
    $("json-feedback").classList.toggle("error", !state.jsonValid);
    $("json-editor").setAttribute("aria-invalid", String(!state.jsonValid));
  }
}
function renderOptions() {
  const el = $("options-form");
  el.replaceChildren();
  const meta = state.current;
  const supports = meta.supports || [];
  for (const key of ["watermark", "encrypt", "page_dpi", "font_policy"]) {
    if (!(Array.isArray(supports) ? supports.includes(key) : supports[key]))
      continue;
    const row = node("label", "option-row");
    row.append(
      node(
        "span",
        "",
        {
          watermark: "保留水印",
          encrypt: "PDF 加密",
          page_dpi: "预览分辨率",
          font_policy: "字体策略",
        }[key],
      ),
    );
    let input;
    const defaults = meta.default_options || {};
    const value =
      state.options[key] ??
      defaults[key] ??
      {
        watermark: true,
        encrypt: false,
        page_dpi: 100,
        font_policy: "declared",
      }[key];
    if (["watermark", "encrypt"].includes(key)) {
      input = node("input");
      input.type = "checkbox";
      input.checked = value;
    } else if (key === "font_policy") {
      input = node("select");
      for (const [v, t] of [
        ["declared", "模板声明"],
        ["subset", "字体子集"],
        ["embed-all", "完整嵌入"],
      ]) {
        const option = node("option", "", t);
        option.value = v;
        input.append(option);
      }
      input.value = value;
    } else {
      input = node("input");
      input.type = "number";
      input.min = 36;
      input.max = 300;
      input.step = 1;
      input.value = value;
    }
    input.id = "option-" + key;
    input.addEventListener("change", () => {
      if (
        key === "page_dpi" &&
        (!input.checkValidity() || !Number.isInteger(Number(input.value)))
      ) {
        input.reportValidity();
        return;
      }
      state.options[key] =
        input.type === "checkbox"
          ? input.checked
          : key === "page_dpi"
            ? Number(input.value)
            : input.value;
      markDirty();
    });
    row.append(input);
    el.append(row);
  }
  $("render-settings").hidden = !el.childElementCount;
}
function renderNav() {
  const nav = $("template-nav");
  nav.replaceChildren();
  const query = $("template-search").value.trim().toLowerCase();
  let group = "";
  for (const template of state.templates) {
    if (
      query &&
      !`${template.id} ${template.display_name}`.toLowerCase().includes(query)
    )
      continue;
    const currentGroup =
      template.kind === "static-web" ? "浏览器模板" : "文档模板";
    if (currentGroup !== group) {
      group = currentGroup;
      nav.append(node("div", "nav-group-label", group));
    }
    const button = node("button", "template-button");
    button.type = "button";
    button.dataset.templateId = template.id;
    button.setAttribute(
      "aria-current",
      String(template.id === state.current?.id),
    );
    button.append(
      node(
        "span",
        "template-monogram",
        {
          wellsfargo: "WF",
          capitalone: "C1",
          "boa-personal": "BA",
          "boa-business": "BA",
          "chase-personal": "CH",
          "chase-business": "CH",
          "usbank-personal": "US",
          "usbank-business": "US",
          mercury: "M",
          sofi: "S",
          alliant: "AT",
          ally: "AL",
        }[template.id] || template.display_name.slice(0, 2),
      ),
    );
    button.append(node("span", "", template.display_name));
    if (template.id === state.current?.id)
      button.append(node("span", "active-dot", "·"));
    button.onclick = () => selectTemplate(template.id);
    nav.append(button);
  }
  if (!nav.childElementCount)
    nav.append(node("p", "nav-loading", "没有匹配的模板"));
}
function clearPreview() {
  state.job = null;
  state.page = 0;
  $("preview-paper").hidden = true;
  $("page-image").removeAttribute("src");
  $("preview-empty").hidden = false;
  $("preview-meta").textContent = "等待生成";
  $("page-label").textContent = "— / —";
  $("preview-note").textContent = "预览会保留模板原有水印设置";
}
async function selectTemplate(id, { initial = false } = {}) {
  const template = state.templates.find((t) => t.id === id);
  if (!template || state.current?.id === id) return;
  if (!initial && invalidNumber()) {
    message("请先修正数字字段，避免切换模板丢失尚未完成的输入。");
    invalidNumber().focus();
    return;
  }
  if (
    !initial &&
    state.current &&
    state.dirty &&
    !isStatic() &&
    !confirm(
      state.jsonValid
        ? "当前模板有未预览的修改。草稿将保留，继续切换模板？"
        : "JSON 仍有错误。原始文本会保存为草稿，继续切换模板？",
    )
  )
    return;
  saveDraft();
  clearTimeout(state.draftTimer);
  const sequence = ++state.loadSequence;
  ++state.sequence;
  state.current = template;
  state.data = null;
  state.options = {};
  state.dirty = false;
  state.jsonValid = true;
  state.jsonRaw = "";
  state.revision++;
  setBusy(false);
  message("");
  clearPreview();
  $("document-title").textContent = template.display_name;
  $("breadcrumb-name").textContent = template.display_name;
  $("template-kind").textContent = isStatic() ? "浏览器模板" : "统一文档模板";
  $("document-description").textContent = isStatic()
    ? "原生浏览器编辑、预览与导出，保持熟悉的完整操作。"
    : "把每一个字段整理妥当，让每一页都清晰有序。";
  $("legacy-link").href = template.legacy_url || legacyPaths[id] || "/";
  document.querySelector(".actionbar").hidden = isStatic();
  $("studio").hidden = isStatic();
  $("static-panel").hidden = !isStatic();
  renderNav();
  updateActions();
  try {
    sessionStorage.setItem("billgen:last-template", id);
  } catch {}
  const url = new URL(location.href);
  url.searchParams.set("template", id);
  history.replaceState(null, "", url);
  if (isStatic()) {
    const entry = template.static_entry || legacyPaths[id];
    $("static-open").href = entry;
    $("static-editor").src = entry;
    status("浏览器内编辑");
    return;
  }
  $("static-editor").removeAttribute("src");
  $("schema-form").replaceChildren(
    node("p", "empty-inline", "正在载入示例与草稿…"),
  );
  status("正在载入");
  try {
    let draft = null;
    try {
      draft = JSON.parse(localStorage.getItem(DRAFT_PREFIX + id) || "null");
    } catch {}
    const validDraft =
      draft?.data &&
      typeof draft.data === "object" &&
      !Array.isArray(draft.data);
    const data = validDraft
      ? draft.data
      : await api(`/api/v1/templates/${encodeURIComponent(id)}/data`);
    if (sequence !== state.loadSequence) return;
    if (!data || typeof data !== "object" || Array.isArray(data))
      throw Error("模板数据应为完整 JSON 对象");
    state.data = data;
    state.options = validDraft ? draft.options || {} : {};
    state.dirty = !!(validDraft && draft.dirty);
    state.jsonRaw =
      validDraft && typeof draft.raw === "string"
        ? draft.raw
        : JSON.stringify(data, null, 2);
    state.jsonValid = validDraft ? draft.valid !== false : true;
    renderForm();
    renderOptions();
    if (state.mode === "json") {
      $("json-editor").value = state.jsonRaw;
      $("json-feedback").textContent = state.jsonValid
        ? "有效的 JSON 对象"
        : "已恢复含错误的 JSON 草稿，请修正后预览";
      $("json-feedback").classList.toggle("error", !state.jsonValid);
    }
    if (!state.jsonValid && state.mode !== "json") switchMode("json");
    $("draft-status").textContent = validDraft
      ? "已恢复此模板的本地草稿"
      : "草稿自动保存在当前浏览器";
    refreshStatus();
    updateActions();
  } catch (error) {
    if (sequence !== state.loadSequence) return;
    status("载入失败", "error");
    message(error.message);
    const retry = node("button", "button", "重新载入");
    retry.type = "button";
    retry.onclick = () => {
      state.current = null;
      selectTemplate(id, { initial: true });
    };
    $("schema-form").replaceChildren(
      node("p", "empty-inline", "模板数据载入失败，请检查本地服务后重试。"),
      retry,
    );
  }
}
function showPage() {
  const pages = state.job?.links?.pages || [];
  if (!pages.length) {
    message("任务已完成，但服务未返回分页图片链接。", "warning");
    return;
  }
  state.page = Math.max(0, Math.min(state.page, pages.length - 1));
  $("preview-empty").hidden = true;
  $("preview-paper").hidden = false;
  $("page-image").src = pages[state.page];
  $("page-image").alt =
    `${state.current.display_name} · 第 ${state.page + 1} 页，共 ${pages.length} 页`;
  $("page-label").textContent = `${state.page + 1} / ${pages.length}`;
  $("preview-viewport").scrollTop = 0;
  updateActions();
}
function setZoom(zoom) {
  state.zoom = Math.min(200, Math.max(40, zoom));
  $("preview-paper").style.width = state.zoom + "%";
  $("zoom-reset").textContent = state.zoom + "%";
  $("zoom-out").disabled = state.zoom <= 40;
  $("zoom-in").disabled = state.zoom >= 200;
}
async function renderDocument(mode = "preview") {
  if (!state.data || isStatic() || state.rendering || !readJson()) return;
  for (const el of $("options-form").querySelectorAll("input[type=number]"))
    if (!el.checkValidity()) {
      el.reportValidity();
      return;
    }
  saveDraft();
  const sequence = ++state.sequence;
  const revision = state.revision;
  const id = state.current.id;
  const data = clone(state.data),
    options = clone(state.options);
  setBusy(true);
  message("");
  try {
    const job = await api(
      `/api/v1/templates/${encodeURIComponent(id)}/${mode}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data, options }),
      },
    );
    if (sequence !== state.sequence || state.current?.id !== id) return;
    state.job = job;
    state.page = 0;
    state.dirty = state.revision !== revision;
    showPage();
    $("preview-meta").textContent =
      `${job.pages || job.links?.pages?.length || 0} 页${job.elapsed_ms !== undefined ? " · " + (job.elapsed_ms / 1000).toFixed(1) + "s" : ""}`;
    $("preview-note").textContent = state.dirty
      ? "数据已修改 · 当前预览为上一次生成结果"
      : "已生成 · " +
        new Date().toLocaleTimeString("zh-CN", {
          hour: "2-digit",
          minute: "2-digit",
        });
    saveDraft();
    if (mode === "export") {
      if (!job.links?.pdf) throw Error("服务未返回 PDF 下载链接");
      const link = node("a");
      link.href = job.links.pdf;
      link.download = `${id}.pdf`;
      document.body.append(link);
      link.click();
      link.remove();
    }
  } catch (error) {
    if (sequence === state.sequence) {
      message(error.message);
      status("生成失败", "error");
      $("preview-meta").textContent = "生成失败";
    }
  } finally {
    if (sequence === state.sequence) {
      setBusy(false);
      if ($("message").dataset.level === "error" && !$("message").hidden)
        status("生成失败", "error");
    }
  }
}
function exportJson() {
  if (!readJson()) return;
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(state.data, null, 2)], {
      type: "application/json;charset=utf-8",
    }),
  );
  const link = node("a");
  link.href = url;
  link.download = `${state.current.id}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function importJson(file) {
  if (!file) return;
  const id = state.current?.id,
    loadSequence = state.loadSequence,
    revision = state.revision;
  if (!id || isStatic()) return;
  try {
    if (file.size > 2 * 1024 * 1024)
      throw Error("JSON 文件超过 2 MiB，请精简后导入。");
    const data = JSON.parse(await file.text());
    if (id !== state.current?.id || loadSequence !== state.loadSequence) return;
    if (!data || Array.isArray(data) || typeof data !== "object")
      throw Error("导入文件的根节点必须是 JSON 对象");
    if (
      (state.dirty || revision !== state.revision || invalidNumber()) &&
      !confirm("导入会替换当前模板的全部数据与尚未完成的输入。继续导入？")
    )
      return;
    state.data = data;
    changed();
    renderForm();
    if (state.mode === "json") $("json-editor").value = state.jsonRaw;
    message("JSON 已导入，所有原始字段与数据类型均已保留。", "success");
  } catch (error) {
    message("导入失败：" + error.message);
  } finally {
    $("import-file").value = "";
  }
}
async function resetExample() {
  if (
    !state.current ||
    isStatic() ||
    !confirm("恢复此模板的原始示例？当前草稿会被替换，其他模板保持不变。")
  )
    return;
  const id = state.current.id;
  const revision = state.revision,
    loadSequence = state.loadSequence;
  try {
    const data = await api(`/api/v1/templates/${encodeURIComponent(id)}/data`);
    if (id !== state.current?.id || loadSequence !== state.loadSequence) return;
    if (
      revision !== state.revision &&
      !confirm("载入示例期间数据已修改，仍要替换？")
    )
      return;
    state.data = data;
    state.options = {};
    changed();
    renderForm();
    renderOptions();
    if (state.mode === "json") $("json-editor").value = state.jsonRaw;
    saveDraft();
    message("已恢复示例。输出设置恢复为模板默认值。", "success");
  } catch (error) {
    message(error.message);
  }
}
const ACCOUNT_LABELS = {
  checking: "个人支票账户",
  business_checking: "商业支票账户",
};
async function loadSynthCapabilities() {
  try {
    const result = await api("/api/v1/synth/capabilities");
    state.synth.templates = result.templates || [];
    state.synth.ready = state.synth.templates.length > 0;
  } catch {
    state.synth.ready = false;
  }
  $("synth-open").hidden = !state.synth.ready;
}
function synthTemplate(id) {
  return state.synth.templates.find((item) => item.id === id);
}
function syncSynthAccounts() {
  const template = synthTemplate($("synth-template").value);
  const select = $("synth-account");
  select.replaceChildren();
  for (const account of template?.accounts || []) {
    const option = node("option", "", ACCOUNT_LABELS[account] || account);
    option.value = account;
    select.append(option);
  }
  if (!template) {
    const option = node("option", "", "该模板不接受账本数据");
    option.value = "";
    select.append(option);
  }
  $("synth-submit").disabled = !template;
}
function openSynth() {
  const options = $("synth-template");
  options.replaceChildren();
  for (const template of state.synth.templates) {
    const option = node("option", "", template.display_name);
    option.value = template.id;
    options.append(option);
  }
  const supported = state.synth.templates.some(
    (item) => item.id === state.current?.id,
  );
  options.value = supported
    ? state.current.id
    : state.synth.templates[0]?.id || "";
  syncSynthAccounts();
  $("synth-status").textContent = "等待结果包";
  $("synth-dialog").showModal();
}
async function submitSynth() {
  if (state.synth.busy) return;
  const template = $("synth-template").value;
  if (!synthTemplate(template)) {
    $("synth-status").textContent = "请选择支持的模板";
    return;
  }
  let payload;
  try {
    payload = JSON.parse($("synth-json").value);
  } catch {
    $("synth-status").textContent = "结果包不是有效的 JSON";
    return;
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    $("synth-status").textContent = "结果包根节点必须是对象";
    return;
  }
  const account = $("synth-account").value;
  const cycle = Number($("synth-cycle").value || "-1");
  state.synth.busy = true;
  $("synth-submit").disabled = true;
  $("synth-status").textContent = "正在排版…";
  try {
    const job = await api("/api/v1/synth/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        template,
        result: payload,
        account: account || undefined,
        cycle: Number.isFinite(cycle) ? cycle : -1,
        watermark: $("synth-watermark").checked,
        brand: $("synth-brand").checked,
      }),
    });
    applySynthJob(job);
    $("synth-dialog").close();
  } catch (error) {
    $("synth-status").textContent = error.message;
  } finally {
    state.synth.busy = false;
    $("synth-submit").disabled = !synthTemplate($("synth-template").value);
  }
}
function applySynthJob(job) {
  const template = state.templates.find((item) => item.id === job.template_id);
  if (!template) throw Error("服务返回了未知模板");
  const switching = state.current?.id !== template.id;
  if (switching) {
    ++state.loadSequence;
    state.current = template;
    state.options = {};
    state.jsonValid = true;
    $("document-title").textContent = template.display_name;
    $("breadcrumb-name").textContent = template.display_name;
    $("template-kind").textContent = "统一文档模板";
    $("document-description").textContent =
      "把每一个字段整理妥当，让每一页都清晰有序。";
    $("legacy-link").href = template.legacy_url || legacyPaths[template.id] || "/";
    document.querySelector(".actionbar").hidden = false;
    $("studio").hidden = false;
    $("static-panel").hidden = true;
    renderNav();
  }
  state.data = job.data;
  state.jsonRaw = JSON.stringify(job.data, null, 2);
  state.dirty = false;
  state.revision++;
  renderForm();
  renderOptions();
  if (state.mode === "json") $("json-editor").value = state.jsonRaw;
  state.job = job;
  state.page = 0;
  showPage();
  const ledger = job.ledger || {};
  $("preview-meta").textContent =
    `${job.pages} 页 · ${ledger.rows ?? 0} 笔 · ` +
    (ledger.period ? ledger.period[0] + " → " + ledger.period[1] : "");
  $("preview-note").textContent =
    `场景结果 · ${ledger.bank || ""} · ${ACCOUNT_LABELS[ledger.account] || ledger.account || ""}`;
  status("已导入场景结果");
  message(
    "已按场景结果重新排版。字段可继续在左侧微调，再生成预览或导出 PDF。",
    "success",
  );
  saveDraft();
  updateActions();
}
$("synth-open").onclick = openSynth;
$("synth-close").onclick = () => $("synth-dialog").close();
$("synth-cancel").onclick = () => $("synth-dialog").close();
$("synth-submit").onclick = submitSynth;
$("synth-template").onchange = syncSynthAccounts;
$("synth-file-button").onclick = () => $("synth-file").click();
$("synth-file").onchange = async () => {
  const file = $("synth-file").files[0];
  if (!file) return;
  try {
    if (file.size > 4 * 1024 * 1024) throw Error("结果包超过 4 MiB");
    $("synth-json").value = await file.text();
    $("synth-status").textContent = `已载入 ${file.name}`;
  } catch (error) {
    $("synth-status").textContent = error.message;
  } finally {
    $("synth-file").value = "";
  }
};
$("template-search").addEventListener("input", renderNav);
$("form-tab").onclick = () => switchMode("form");
$("json-tab").onclick = () => switchMode("json");
document.querySelector(".editor-tabs").addEventListener("keydown", (event) => {
  if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
    event.preventDefault();
    const mode =
      event.key === "Home"
        ? "form"
        : event.key === "End"
          ? "json"
          : state.mode === "form"
            ? "json"
            : "form";
    switchMode(mode);
    $(state.mode + "-tab").focus();
  }
});
$("json-editor").addEventListener("input", jsonChanged);
$("preview-button").onclick = () => renderDocument();
$("empty-preview-button").onclick = () => renderDocument();
$("download-button").onclick = () => renderDocument("export");
$("export-json").onclick = exportJson;
$("import-button").onclick = () => $("import-file").click();
$("import-file").onchange = () => importJson($("import-file").files[0]);
$("reset-button").onclick = resetExample;
$("previous-page").onclick = () => {
  state.page--;
  showPage();
};
$("next-page").onclick = () => {
  state.page++;
  showPage();
};
$("zoom-out").onclick = () => setZoom(state.zoom - 10);
$("zoom-in").onclick = () => setZoom(state.zoom + 10);
$("zoom-reset").onclick = () => setZoom(100);
$("page-image").onerror = () =>
  message("预览图片载入失败，任务可能已过期。请重新生成预览。");
$("help-button").onclick = () => $("help-dialog").showModal();
$("help-close").onclick = () => $("help-dialog").close();
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
    event.preventDefault();
    if (isStatic()) {
      message(
        "此模板在浏览器内编辑，请使用下方模板自带的预览与导出操作。",
        "warning",
      );
      return;
    }
    renderDocument();
  }
});
window.addEventListener("beforeunload", (event) => {
  saveDraft();
  if (state.dirty && !isStatic()) {
    event.preventDefault();
    event.returnValue = "";
  }
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) saveDraft();
});
window.addEventListener("storage", (event) => {
  if (event.key === DRAFT_PREFIX + state.current?.id && event.newValue)
    message(
      "另一个窗口更新了此模板草稿。当前编辑内容保持不变；请导出 JSON 后再重新载入以比较。",
      "warning",
    );
});

async function boot() {
  updateActions();
  loadSynthCapabilities();
  try {
    const result = await api("/api/v1/templates");
    state.templates = result.templates || [];
    if (!state.templates.length) throw Error("模板列表为空");
    $("template-count").textContent = state.templates.length;
    let previous;
    try {
      previous = sessionStorage.getItem("billgen:last-template");
    } catch {}
    const requested =
      new URL(location.href).searchParams.get("template") || previous;
    const id = state.templates.some((t) => t.id === requested)
      ? requested
      : state.templates[0].id;
    await selectTemplate(id, { initial: true });
  } catch (error) {
    status("连接失败", "error");
    message(error.message + " · 请刷新页面重试。");
    $("template-nav").replaceChildren(
      node("p", "nav-loading", "模板服务连接失败"),
    );
  }
}
boot();
