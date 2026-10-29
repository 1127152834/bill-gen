// Dependency-free regression harness for the document workspace state machine.
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
class Element {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.value = "";
    this.hidden = false;
    this.attributes = {};
    this.classList = { add() {}, remove() {}, toggle() {} };
    this.listeners = {};
    this.invalid = null;
  }
  append(...children) {
    this.children.push(...children);
    for (const child of children)
      if (child && typeof child === "object") child.parentElement = this;
  }
  replaceChildren(...children) {
    this.children = [];
    this.append(...children);
  }
  setAttribute(key, value) {
    this.attributes[key] = value;
  }
  removeAttribute(key) {
    delete this.attributes[key];
  }
  addEventListener(key, value) {
    this.listeners[key] = value;
  }
  querySelector() {
    return this.invalid;
  }
  querySelectorAll() {
    return [];
  }
  focus() {
    this.focused = true;
  }
  checkValidity() {
    return true;
  }
  get childElementCount() {
    return this.children.length;
  }
}
const elements = new Map(),
  storage = new Map();
const context = {
  console,
  URL,
  Date,
  JSON,
  Number,
  Blob,
  setTimeout: () => 1,
  clearTimeout() {},
  confirm: () => true,
  localStorage: {
    setItem: (k, v) => storage.set(k, v),
    getItem: (k) => storage.get(k),
  },
  document: {
    getElementById: (id) => {
      if (!elements.has(id)) elements.set(id, new Element());
      return elements.get(id);
    },
    createElement: (tag) => new Element(tag),
    createTextNode: (text) => ({ textContent: text }),
    querySelector: () => new Element(),
  },
  location: { href: "http://localhost/" },
  history: { replaceState() {} },
  sessionStorage: { setItem() {} },
  fetch: () => {
    throw Error("unexpected fetch");
  },
};
vm.createContext(context);
let source = fs.readFileSync(
  new URL("../src/billgen/server/web/app.js", import.meta.url),
  "utf8",
);
source = source.slice(
  0,
  source.indexOf('$("template-search").addEventListener'),
);
vm.runInContext(
  source +
    ";globalThis.test={state,readJson,markDirty,jsonChanged,assign,scalar,saveDraft,selectTemplate,switchMode,importJson,resetExample,arrayBody};",
  context,
);
const t = context.test,
  s = t.state;
s.current = { id: "a", kind: "from-scratch" };
s.data = { amount: "123.00", count: 2, extra: { unknown: "kept" } };
s.mode = "json";
s.jsonRaw = '{"bad":';
s.jsonValid = false;
t.markDirty();
assert.equal(s.jsonRaw, '{"bad":');
assert.equal(s.jsonValid, false);
s.jsonValid = true;
s.jsonRaw = JSON.stringify(s.data);
const revision = s.revision;
assert.equal(t.readJson(), true);
assert.equal(s.revision, revision, "validation must not mark data dirty");
context.document.getElementById("schema-form").invalid = new Element();
assert.equal(t.readJson(), true, "hidden stale invalid fields must be ignored");
s.mode = "form";
assert.equal(t.readJson(), false);
t.switchMode("json");
assert.equal(s.mode, "form", "invalid number blocks mode switching");
s.templates = [{ id: "a" }, { id: "b" }];
await t.selectTemplate("b");
assert.equal(s.current.id, "a", "invalid number blocks template switching");
elements.get("schema-form").invalid = null;
t.assign(["amount"], "00123.40");
assert.equal(s.data.amount, "00123.40");
assert.equal(typeof s.data.amount, "string");
assert.equal(s.data.extra.unknown, "kept");
const row = t.scalar("count", 2, ["count"]);
row.children[1].value = "4.25";
row.children[1].listeners.input();
assert.equal(s.data.count, 4.25);
assert.equal(typeof s.data.count, "number");
s.mode = "json";
context.document.getElementById("json-editor").value = '{"broken":';
t.jsonChanged();
t.saveDraft();
assert.equal(
  JSON.parse(storage.get("billgen:workbench:v2:a")).raw,
  '{"broken":',
);
assert.equal(JSON.parse(storage.get("billgen:workbench:v2:a")).valid, false);
s.mode = "form";
s.jsonValid = true;
s.data = {
  items: Array.from({ length: 1000 }, (_, i) => ({ amount: String(i) })),
};
const body = new Element();
t.arrayBody(body, ["items"]);
assert.equal(
  body.children.filter((e) => e.className === "array-entry").length,
  10,
  "only 10 of 1000 array entries mount",
);
s.current = { id: "a" };
s.loadSequence = 1;
s.dirty = false;
let resolve;
const file = { size: 12, text: () => new Promise((r) => (resolve = r)) };
const pendingImport = t.importJson(file);
s.loadSequence = 3;
s.data = { sentinel: 1 };
resolve('{"overwrite":true}');
await pendingImport;
assert.equal(s.data.sentinel, 1, "A-B-A stale import ignored");
let done;
context.fetch = () => new Promise((r) => (done = r));
s.loadSequence = 4;
const reset = t.resetExample();
s.loadSequence = 6;
s.data = { sentinel: 2 };
done({ ok: true, json: async () => ({ overwrite: true }) });
await reset;
assert.equal(s.data.sentinel, 2, "A-B-A stale reset ignored");
console.log(
  "PASS: frontend state regression (raw drafts, options, pure validation, numeric protection, string types, unknown fields, paginated arrays, import/reset races).",
);
