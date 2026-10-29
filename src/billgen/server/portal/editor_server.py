#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
账单模板 · 可视化编辑器（多模板版）
--------------------------------
启动:  python3 editor_server.py     (或双击 启动编辑器.command)
打开:  http://127.0.0.1:8787
退出:  Ctrl+C

支持模板:
  mercury — Mercury/Choice 商业账户（mercury-statement-template/）
  c1     — Capital One 360 个人账户（c1-statement-template/）
"""
import base64
import hashlib
import importlib
import json
import os
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from runtime_paths import data_path

BASE = os.path.dirname(os.path.abspath(__file__))

TEMPLATES = {
    "mercury": {
        "name": "Mercury 商业账户",
        "dir": os.path.join(os.path.dirname(BASE), "mercury-statement-template"),
        "module": "generate_statement",
        "filename": "Demo-Bank-Statement",
        "accent_ban": True,          # 模板字体缺重音字母 → 禁止输入
    },
    "c1": {
        "name": "Capital One 个人账户",
        "dir": os.path.join(os.path.dirname(BASE), "c1-statement-template"),
        "module": "c1_generate",
        "filename": "C1-Personal-Statement",
        "accent_ban": False,         # Optimist 字体重音全覆盖 → 允许
    },
    "sofi": {
        "name": "SoFi 个人账户",
        "dir": os.path.join(os.path.dirname(BASE), "sofi-statement-template"),
        "module": "sofi_generate",
        "filename": "SoFi-Statement",
        "accent_ban": False,         # TTNorms 字体重音全覆盖 → 允许
    },
}

_mods = {}
def mod(tpl):
    if tpl not in _mods:
        d = TEMPLATES[tpl]["dir"]
        if d not in sys.path:
            sys.path.insert(0, d)
        _mods[tpl] = importlib.import_module(TEMPLATES[tpl]["module"])
    return _mods[tpl]

DEFAULT_STATE = {}
for tid, cfg in TEMPLATES.items():
    m = mod(tid)
    DEFAULT_STATE[tid] = {
        "data": json.loads(json.dumps(m.DATA)),
        "watermark": True,
        "filename": cfg["filename"],
    }

LOCK = threading.Lock()
PREVIEW_DPI = 100
PV_DIR = data_path("水星C1sofi/statement-editor/pv_png")     # 预览页 PNG（内容哈希寻址，immutable 缓存）

# ================================================================ 字体子集缓存
from fontTools import subset as _subset
from fontTools.ttLib import TTFont as _TTF
import tempfile as _tempfile

_SUBSET_CACHE = {}

def _make_font_subset(base_path, unicodes):
    key = (base_path, tuple(sorted(unicodes)))
    if key in _SUBSET_CACHE:
        return _SUBSET_CACHE[key]
    opts = _subset.Options()
    opts.font_number = 0
    opts.layout_features = ["kern", "liga"]
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    font = _subset.load_font(base_path, opts)
    ss = _subset.Subsetter(opts)
    ss.populate(unicodes=list(unicodes))
    ss.subset(font)
    fd, path = _tempfile.mkstemp(suffix=".ttf", prefix="cjk_sub_")
    os.close(fd)
    _subset.save_font(font, path, opts)
    font.close()
    _SUBSET_CACHE[key] = path
    return path

# ================================================================ 字符策略
_CJK_OK_RANGES = ((0x2E80, 0x2EFF), (0x3000, 0x303F), (0x3040, 0x30FF),
                  (0x3130, 0x318F), (0x3400, 0x4DBF), (0x4E00, 0x9FFF),
                  (0xAC00, 0xD7AF), (0xF900, 0xFAFF), (0xFF00, 0xFFEF),
                  (0x20000, 0x2FA1F))
_ACCENT_RANGES = ((0x00C0, 0x00D6), (0x00D8, 0x00F6), (0x00F8, 0x024F),
                  (0x02C6, 0x02DD), (0x1E80, 0x1E85), (0x1E8A, 0x1E8B),
                  (0x1E9E, 0x1E9E), (0x1EF2, 0x1EF3), (0x1E00, 0x1E7F))

_POLICY = {}
def _init_policy(tpl):
    m = mod(tpl)
    if tpl == "mercury":
        latin = set(_TTF(m.FONT_VAR).getBestCmap().keys())
        latin &= set(_TTF(m.FONT_480).getBestCmap().keys())
    else:
        latin = set(_TTF(m._FP[m._REG]).getBestCmap().keys())
        latin &= set(_TTF(m._FP[m._BOLD]).getBestCmap().keys())
    _POLICY[tpl] = {
        "latin": latin,
        "cjk": m._CJK_CMAP,
        "accent_ban": TEMPLATES[tpl]["accent_ban"],
    }

def classify_char(tpl, ch):
    p = _POLICY[tpl]
    cp = ord(ch)
    if cp < 0x20 or cp == 0x7F:
        return "missing"
    if cp in p["latin"]:
        if p["accent_ban"] and any(a <= cp <= b for a, b in _ACCENT_RANGES):
            return "accent"
        return "ok"
    if p["accent_ban"] and any(a <= cp <= b for a, b in _ACCENT_RANGES):
        return "accent"
    if any(a <= cp <= b for a, b in _CJK_OK_RANGES) and cp in p["cjk"]:
        return "cjk"
    return "missing"

# ================================================================ 数据清洗
def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0

def normalize(tpl, raw):
    d = raw or {}
    _num = lambda v: float(v) if str(v).replace("-","").replace(".","").isdigit() else 0.0
    def _f(v): return round(_num(v), 2)
    def _s(v): return str(v or "")

    if tpl == "mercury":
        addr = list(d.get("address") or []) + ["", ""]
        out = {
            "period_label": _s(d.get("period_label")),
            "period_start": _s(d.get("period_start")),
            "period_end": _s(d.get("period_end")),
            "period_days": _s(d.get("period_days")),
            "timezone": _s(d.get("timezone")),
            "company": _s(d.get("company")),
            "address": [_s(addr[0]), _s(addr[1])],
            "ein": _s(d.get("ein")),
            "account_type": _s(d.get("account_type")),
            "account_number": _s(d.get("account_number")),
            "routing_number": _s(d.get("routing_number")),
            "beginning_balance": _f(d.get("beginning_balance")),
            "footer_line1": _s(d.get("footer_line1")),
            "footer_line2": _s(d.get("footer_line2")),
            "transactions": [],
        }
        for t in (d.get("transactions") or []):
            amt = _f(t.get("amount"))
            out["transactions"].append({
                "date": _s(t.get("date")), "desc": _s(t.get("desc")),
                "type": _s(t.get("type") or "ACH In"),
                "icon": _s(t.get("icon") or "achOut"),
                "dir": _s(t.get("dir") or ("in" if amt >= 0 else "out")),
                "amount": amt,
            })
        return out

    elif tpl == "c1":
        addr = list(d.get("address_lines") or []) + ["", "", "", ""]
        out = {
            "customer_name": _s(d.get("customer_name")),
            "address_lines": [_s(addr[0]), _s(addr[1]), _s(addr[2]), _s(addr[3])],
            "tagline": _s(d.get("tagline")),
            "headline_month": _s(d.get("headline_month")),
            "period_range": _s(d.get("period_range")),
            "summary_start_label": _s(d.get("summary_start_label")),
            "summary_end_label": _s(d.get("summary_end_label")),
            "account_name": _s(d.get("account_name")),
            "account_title": _s(d.get("account_title")),
            "account_number": _s(d.get("account_number")),
            "apy": _s(d.get("apy")),
            "days_in_cycle": _s(d.get("days_in_cycle")),
            "opening_balance": _f(d.get("opening_balance")),
            "interest_earned": _f(d.get("interest_earned")),
            "overdraft_fees": _f(d.get("overdraft_fees")),
            "finance_charges": _f(d.get("finance_charges")),
            "ytd_interest": _f(d.get("ytd_interest")),
            "closing_date": _s(d.get("closing_date")),
            "footer_site": _s(d.get("footer_site")),
            "footer_phone": _s(d.get("footer_phone")),
            "footer_address": _s(d.get("footer_address")),
            "note_text": _s(d.get("note_text")),
            "error_heading": _s(d.get("error_heading")),
            "transactions": [],
        }
        for t in (d.get("transactions") or []):
            out["transactions"].append({
                "date": _s(t.get("date")), "desc": _s(t.get("desc")),
                "memo": _s(t.get("memo")), "amount": _f(t.get("amount")),
            })
        for key, n in (("error_paragraphs", 1), ("error_items", 3), ("final_paragraphs", 1)):
            lst = d.get(key) or []
            out[key] = [_s(lst[i]) if i < len(lst) else "" for i in range(n)]
        for k in ("fees_overdraft_period", "fees_overdraft_ytd",
                  "fees_return_period", "fees_return_ytd"):
            out[k] = _f(d.get(k))
        return out

    else:  # sofi
        addr = list(d.get("address_lines") or []) + ["", "", "", ""]
        contact = list(d.get("contact_address") or []) + ["", "", ""]
        out = {
            "holder_name": _s(d.get("holder_name") or d.get("customer_name")),
            "member_since": _s(d.get("member_since")),
            "address_lines": [_s(addr[0]), _s(addr[1]), _s(addr[2]), _s(addr[3])],
            "account_number": _s(d.get("account_number")),
            "statement_period": _s(d.get("statement_period")),
            "account_type": _s(d.get("account_type") or "Checking"),
            "account_last4": _s(d.get("account_last4")),
            "tagline": _s(d.get("tagline")),
            "headline_month": _s(d.get("headline_month")),
            "period_range": _s(d.get("period_range")),
            "summary_start_label": _s(d.get("summary_start_label")),
            "summary_end_label": _s(d.get("summary_end_label")),
            "account_name": _s(d.get("account_name")),
            "account_title": _s(d.get("account_title")),
            "apy": _s(d.get("apy")),
            "days_in_cycle": _s(d.get("days_in_cycle")),
            "opening_balance": _f(d.get("opening_balance")),
            "interest_earned": _f(d.get("interest_earned")),
            "overdraft_fees": _f(d.get("overdraft_fees")),
            "finance_charges": _f(d.get("finance_charges")),
            "ytd_interest": _f(d.get("ytd_interest")),
            "closing_date": _s(d.get("closing_date")),
            "footer_site": _s(d.get("footer_site")),
            "footer_phone": _s(d.get("footer_phone")),
            "footer_address": _s(d.get("footer_address")),
            "note_text": _s(d.get("note_text")),
            "error_heading": _s(d.get("error_heading")),
            "current_balance": _f(d.get("current_balance")),
            "current_as_of": _s(d.get("current_as_of")),
            "beginning_balance": _f(d.get("beginning_balance")),
            "beginning_as_of": _s(d.get("beginning_as_of")),
            "balance_note": _s(d.get("balance_note")),
            "legal_footer": _s(d.get("legal_footer")),
            "footnote1": _s(d.get("footnote1")),
            "monthly_interest": _f(d.get("monthly_interest") or d.get("interest_earned")),
            "contact_website": _s(d.get("contact_website")),
            "contact_phone": _s(d.get("contact_phone")),
            "contact_address": [_s(contact[0]), _s(contact[1]), _s(contact[2])],
            "transactions": [],
        }
        for t in (d.get("transactions") or []):
            out["transactions"].append({
                "date": _s(t.get("date")), "type": _s(t.get("type")),
                "desc": _s(t.get("desc")), "txid": _s(t.get("txid")),
                "amount": _f(t.get("amount")),
            })
        for key, n in (("error_paragraphs", 1), ("error_items", 3), ("final_paragraphs", 1)):
            lst = d.get(key) or []
            out[key] = [_s(lst[i]) if i < len(lst) else "" for i in range(n)]
        for k in ("fees_overdraft_period", "fees_overdraft_ytd",
                  "fees_return_period", "fees_return_ytd"):
            out[k] = _f(d.get(k))
        return out

def sanitize(tpl, data):
    rm = {"accent": set(), "missing": set()}

    def clean(s):
        out = []
        for ch in s:
            st = classify_char(tpl, ch)
            if st == "accent":
                rm["accent"].add(ch)
            elif st == "missing":
                rm["missing"].add(ch)
            else:
                out.append(ch)
        return "".join(out)

    def walk(v):
        if isinstance(v, str):
            return clean(v)
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        return v

    return walk(data), rm

# ================================================================ 构建
def _set_cjk(tpl, data):
    m = mod(tpl)
    base = m.FONT_CJK_BASE
    if not base:
        m.FONT_CJK = None
        return
    latin = _POLICY[tpl]["latin"]
    needed = set()
    if hasattr(m, 'collect_texts'):
        texts = m.collect_texts(data)
    else:
        texts = _flatten(data)
    for s in texts:
        for ch in s:
            if ord(ch) not in latin and any(a <= ord(ch) <= b for a, b in _CJK_OK_RANGES):
                needed.add(ord(ch))
    if not needed:
        m.FONT_CJK = None
    else:
        try:
            m.FONT_CJK = _make_font_subset(base, needed)
        except Exception:
            m.FONT_CJK = base

def _build_mercury(m, data, meta=None):
    return m.build(meta=meta)

def _build_c1(m, data, meta=None):
    return m.build(meta=meta)

def _build_sofi(m, data, meta=None):
    return m.build(meta=meta)

BUILDERS = {"mercury": _build_mercury, "c1": _build_c1, "sofi": _build_sofi}

def _produce(tpl, raw_data, watermark, for_export=False, meta=None):
    """生成 PDF。for_export=True 时序列化+垃圾回收；预览直接返回 doc（更快）。"""
    data, rm = sanitize(tpl, normalize(tpl, raw_data))
    m = mod(tpl)
    import fitz
    with LOCK:
        m._fallback_used.clear()
        _set_cjk(tpl, data)
        m.DATA = data
        m.WATERMARK = bool(watermark)
        doc = BUILDERS[tpl](m, data, meta)
        if for_export:
            out = doc.tobytes(garbage=4, deflate=True)
            doc.close()
        else:
            out = doc
    info = {"removed_accent": sorted(rm["accent"]),
            "removed_missing": sorted(rm["missing"]),
            "fallback": sorted(m._fallback_used)}
    return out, info

def render_previews(tpl, raw_data, watermark, meta=None):
    """预览：构建后直接光栅化（跳过 PDF 序列化，约省 40% 耗时）。
    页 PNG 按内容哈希落盘（响应只带哈希，未变化页走浏览器 immutable 缓存，
    不再每次 base64 全量回传）。"""
    doc, info = _produce(tpl, raw_data, watermark, for_export=False, meta=meta)
    try:
        os.makedirs(PV_DIR, exist_ok=True)
        hs = []
        for pg in doc:
            png = pg.get_pixmap(dpi=PREVIEW_DPI).tobytes("png")
            h = hashlib.md5(png).hexdigest()[:12]
            fp = os.path.join(PV_DIR, h + ".png")
            if not os.path.exists(fp):
                tmp = fp + ".tmp.png"
                with open(tmp, "wb") as f:
                    f.write(png)
                os.replace(tmp, fp)
            hs.append(h)
        for f in os.listdir(PV_DIR):        # 清理不再引用的页（防累积）
            if f.endswith(".png") and f[:-4] not in hs:
                try:
                    os.remove(os.path.join(PV_DIR, f))
                except OSError:
                    pass
        info["pages"] = hs
    finally:
        doc.close()
    return info

# ================================================================ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "StmtEditor/2.0"

    def log_message(self, *args):
        pass

    def _reply(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        hdrs = {"Content-Type": ctype, "Content-Length": str(len(body)),
                "Cache-Control": "no-store"}
        hdrs.update(extra or {})      # extra 可覆盖默认头（如 /pv 的 immutable）
        self.send_response(code)
        for k, v in hdrs.items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if p in ("/", "/index.html", "/editor.html"):
            try:
                with open(os.path.join(BASE, "editor.html"), "rb") as f:
                    html = f.read()
            except FileNotFoundError:
                self._reply(500, "editor.html not found", "text/plain; charset=utf-8")
                return
            self._reply(200, html, "text/html; charset=utf-8")
        elif p == "/api/state":
            tpl = (qs.get("tpl") or ["mercury"])[0]
            if tpl not in TEMPLATES:
                self._reply(404, "unknown template")
                return
            _init_policy(tpl)
            state = dict(DEFAULT_STATE[tpl])
            state["accent_ban"] = TEMPLATES[tpl]["accent_ban"]
            state["meta"] = json.loads(json.dumps(getattr(mod(tpl), "META", {})))
            state["template"] = tpl
            state["templates"] = {k: v["name"] for k, v in TEMPLATES.items()}
            self._reply(200, json.dumps(state))
        elif p.startswith("/pv/") and p.endswith(".png"):
            # 预览页 PNG：路径=内容哈希（无查询串，防内嵌浏览器对 ?t= 的 localhost
            # 请求失败）；basename 防目录穿越
            name = os.path.basename(p)
            fp = os.path.join(PV_DIR, name)
            if name.endswith(".png") and os.path.isfile(fp):
                with open(fp, "rb") as f:
                    body = f.read()
                self._reply(200, body, "image/png",
                            extra={"Content-Type": "image/png",
                                   "Cache-Control": "public, max-age=86400, immutable"})
            else:
                self._reply(404, "no such page", "text/plain; charset=utf-8")
        else:
            self._reply(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._reply(400, "bad request", "text/plain; charset=utf-8")
            return
        tpl = payload.get("tpl", "mercury")
        if tpl not in TEMPLATES:
            self._reply(404, "unknown template")
            return
        _init_policy(tpl)
        try:
            if p == "/api/render":
                result = render_previews(tpl, payload.get("data"),
                                         payload.get("watermark", True),
                                         payload.get("meta"))
                self._reply(200, json.dumps(result))
            elif p == "/api/export":
                pdf, _ = _produce(tpl, payload.get("data"),
                                  payload.get("watermark", True), for_export=True,
                                  meta=payload.get("meta"))
                self._reply(200, pdf, "application/pdf",
                            extra={"Content-Disposition":
                                   'attachment; filename="statement.pdf"'})
            else:
                self._reply(404, "not found", "text/plain; charset=utf-8")
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._reply(500, json.dumps({"error": str(e)}))


class Server(ThreadingHTTPServer):
    daemon_threads = True


def main():
    host = os.environ.get("HOST", "127.0.0.1")
    port, srv = int(os.environ.get("PORT", "8787")), None
    for p in range(port, port + 20):
        try:
            srv = Server((host, p), Handler)
            port = p
            break
        except OSError:
            continue
    if srv is None:
        print("找不到可用端口 (8787-8806)")
        sys.exit(1)
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}"
    print("=" * 50)
    print("  账单模板 · 可视化编辑器（多模板）")
    for tid, cfg in TEMPLATES.items():
        print(f"    {tid}: {cfg['name']}")
    print(f"  地址: {url}")
    print("  退出: Ctrl+C")
    print("=" * 50)
    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")


if __name__ == "__main__":
    main()
