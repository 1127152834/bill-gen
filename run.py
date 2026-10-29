#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
账单模板整合版 — 统一启动器 + 单端口反向代理网关（不修改任何子项目核心）
用法:  python3 run.py            # 前台运行，Ctrl+C 全部停止
环境:  HOST=0.0.0.0 时网关监听全部网卡（Docker 用），默认 127.0.0.1
       GATE_PORT 网关端口，默认 8800

对外只暴露网关一个端口；Python 编辑器只绑本机回环（127.0.0.1），由网关转发：
  /            切换页
  /wf/...      富国        （内部 127.0.0.1:8791，去掉前缀转发）
  /ae/...      AE          （内部 127.0.0.1:8792）
  /merc/...    水星C1·SoFi  （内部 127.0.0.1:8793）
编辑器页面内部用根路径（如 /api/...）发请求，网关按请求头 Referer 里的
前缀判断归属后转发，因此子项目前端无需任何改动。
"""
import http.client
import os
import signal
import socket
import subprocess
import sys
import threading
import urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
HOST = os.environ.get("HOST", "127.0.0.1")
GATE_PORT = int(os.environ.get("GATE_PORT", "8800"))

# 子服务固定绑 127.0.0.1（强制覆盖继承的 HOST/AE_HOST，防 Docker 环境串扰）
SERVICES = [
    {"id": "wf", "name": "富国", "prefix": "/wf", "port": 8791,
     "cmd": [sys.executable, "statement_template/editor/server.py", "8791"],
     "cwd": os.path.join(BASE, "富国"),
     "env": {"HOST": "127.0.0.1", "NO_BROWSER": "1"}},
    {"id": "ae", "name": "AE", "prefix": "/ae", "port": 8792,
     "cmd": [sys.executable, "editor.py", "--port", "8792", "--no-browser"],
     "cwd": os.path.join(BASE, "AE"),
     "env": {"AE_HOST": "127.0.0.1"}},
    {"id": "merc", "name": "水星C1·SoFi", "prefix": "/merc", "port": 8793,
     "cmd": [sys.executable, "editor_server.py"],
     "cwd": os.path.join(BASE, "水星C1sofi", "statement-editor"),
     "env": {"HOST": "127.0.0.1", "PORT": "8793", "NO_BROWSER": "1"}},
    {"id": "boa", "name": "BOA", "prefix": "/boa", "port": 8794,
     "cmd": [sys.executable, "editor/server.py", "8794"],
     "cwd": os.path.join(BASE, "BOA", "statement_template"),
     "env": {"HOST": "127.0.0.1", "NO_BROWSER": "1"}},
    {"id": "chase", "name": "大通个人", "prefix": "/chase", "port": 8795,
     "cmd": [sys.executable, "editor_personal/server.py", "8795"],
     "cwd": os.path.join(BASE, "大通", "statement_template"),
     "env": {"HOST": "127.0.0.1", "NO_BROWSER": "1"}},
    {"id": "chasebiz", "name": "大通商业", "prefix": "/chasebiz", "port": 8796,
     "cmd": [sys.executable, "editor_business/server.py", "8796"],
     "cwd": os.path.join(BASE, "大通", "statement_template"),
     "env": {"HOST": "127.0.0.1", "NO_BROWSER": "1"}},
    {"id": "us", "name": "US Bank 个户", "prefix": "/us", "port": 8797,
     "cmd": [sys.executable, "editor_us/server.py", "8797"],
     "cwd": os.path.join(BASE, "USbank", "statement_template"),
     "env": {"HOST": "127.0.0.1", "NO_BROWSER": "1"}},
    {"id": "usbiz", "name": "US Bank 商业", "prefix": "/usbiz", "port": 8798,
     "cmd": [sys.executable, "editor_biz/server.py", "8798"],
     "cwd": os.path.join(BASE, "USbank", "statement_template"),
     "env": {"HOST": "127.0.0.1", "NO_BROWSER": "1"}},
    # ---- 纯静态前端模板（浏览器内编辑/导出 PDF，无后端：网关直接伺服目录，零子进程） ----
    {"id": "alliant", "name": "Alliant", "prefix": "/alliant",
     "static": os.path.join(BASE, "alliant")},
    {"id": "ally", "name": "Ally Bank", "prefix": "/ally",
     "static": os.path.join(BASE, "ally bank")},
]

PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>账单模板整合版</title>
<style>
  * { margin:0; box-sizing:border-box; }
  body { font:14px/1.4 -apple-system,"PingFang SC",sans-serif; display:flex; flex-direction:column; height:100vh; overflow:hidden; }
  header { display:flex; align-items:center; gap:14px; padding:10px 16px; background:#1d2733; color:#fff; flex-shrink:0; }
  header h1 { font-size:16px; font-weight:600; }
  .tabs { display:flex; gap:8px; }
  .tab { padding:7px 16px; border-radius:8px; cursor:pointer; border:1px solid transparent; color:#c9d4e0; user-select:none; }
  .tab:hover { color:#fff; background:#2a3948; }
  .tab.on { background:#3b82f6; color:#fff; font-weight:600; }
  .tab small { opacity:.65; font-weight:400; margin-left:4px; }
  .pop { margin-left:auto; font-size:12px; color:#93a5b8; text-decoration:none; border:1px solid #3a4a5c; padding:5px 10px; border-radius:6px; }
  .pop:hover { color:#fff; border-color:#5a6f86; }
  iframe { flex:1; border:0; width:100%; background:#f0f2f5; }
</style></head><body>
<header>
  <h1>账单模板整合版</h1>
  <div class="tabs" id="tabs"></div>
  <a class="pop" id="pop" target="_blank" href="#">新窗口打开 ↗</a>
</header>
<iframe id="frame"></iframe>
<script>
const APPS = [
  { id:"wf",   name:"富国",        sub:"Wells Fargo",     base:"/wf/" },
  { id:"ae",   name:"AE",        sub:"American Express", base:"/ae/" },
  { id:"merc", name:"水星C1·SoFi", sub:"Mercury / C1 / SoFi", base:"/merc/" },
  { id:"boa",  name:"BOA", sub:"Personal / Business", base:"/boa/" },
  { id:"chase", name:"大通个人", sub:"Chase Personal", base:"/chase/" },
  { id:"chasebiz", name:"大通商业", sub:"Chase Business", base:"/chasebiz/" },
  { id:"us",  name:"US Bank",   sub:"Personal / Debit", base:"/us/" },
  { id:"usbiz", name:"US Bank 商业", sub:"Business Essentials", base:"/usbiz/" },
  { id:"alliant", name:"Alliant", sub:"Credit Union", base:"/alliant/" },
  { id:"ally",  name:"Ally Bank", sub:"Statement", base:"/ally/" },
];
const tabs = document.getElementById("tabs");
const frame = document.getElementById("frame");
function open(app) {
  tabs.querySelectorAll(".tab").forEach(t => t.classList.toggle("on", t.dataset.id === app.id));
  frame.src = app.base;
  document.getElementById("pop").href = app.base;
  localStorage.setItem("lastApp", app.id);
}
APPS.forEach(a => {
  const el = document.createElement("div");
  el.className = "tab"; el.dataset.id = a.id;
  el.innerHTML = a.name + "<small>" + a.sub + "</small>";
  el.onclick = () => open(a);
  tabs.appendChild(el);
});
open(APPS.find(a => a.id === localStorage.getItem("lastApp")) || APPS[0]);
</script></body></html>"""


def port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.connect_ex(("127.0.0.1", port)) != 0


def spawn(svc, procs):
    env = dict(os.environ)
    env.update(svc["env"])
    env["PYTHONPATH"] = BASE + os.pathsep + env.get("PYTHONPATH", "")
    p = subprocess.Popen(svc["cmd"], cwd=svc["cwd"], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def pump():
        for line in iter(p.stdout.readline, b""):
            sys.stdout.write(f"[{svc['name']}] {line.decode('utf-8', 'replace')}")
            sys.stdout.flush()

    threading.Thread(target=pump, daemon=True).start()
    procs.append((svc, p))


def make_gateway():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class GW(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        # ---- 路由：显式前缀优先，其次按 Referer 里的前缀归属 ----
        def _route(self):
            u = urllib.parse.urlsplit(self.path)
            path = u.path
            target, sub = None, path
            for svc in SERVICES:
                if path == svc["prefix"] or path.startswith(svc["prefix"] + "/"):
                    target, sub = svc, (path[len(svc["prefix"]):] or "/")
                    break
            if target is None:
                ref = urllib.parse.urlsplit(self.headers.get("Referer") or "").path
                for svc in SERVICES:
                    if ref == svc["prefix"] or ref.startswith(svc["prefix"] + "/"):
                        target, sub = svc, path
                        break
            query = ("?" + u.query) if u.query else ""
            return target, sub + query

        def _plain(self, code, text, ctype="text/plain; charset=utf-8"):
            body = text.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _static(self, svc, sub):
            """静态模板目录直伺服（仅 GET/HEAD，路径防穿越）。"""
            import mimetypes
            root = svc["static"]
            path = urllib.parse.unquote(sub.split("?")[0])
            if path.endswith("/"):
                path += "index.html"
            full = os.path.normpath(os.path.join(root, path.lstrip("/")))
            if not full.startswith(root + os.sep):
                return self._plain(403, "forbidden")
            if os.path.isdir(full):
                full = os.path.join(full, "index.html")
            if not os.path.isfile(full):
                return self._plain(404, "not found")
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if full.endswith((".js", ".mjs")):
                ctype = "application/javascript"
            elif full.endswith(".woff2"):
                ctype = "font/woff2"
            with open(full, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _handle(self):
            svc, target_path = self._route()
            if svc is None:
                if urllib.parse.urlsplit(self.path).path in ("/", "/index.html"):
                    body = PAGE.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self._plain(404, "not found")
                return
            if "static" in svc:
                self._static(svc, target_path)
                return
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else None
            fwd = {k: v for k, v in self.headers.items()
                   if k.lower() not in ("host", "connection", "content-length",
                                        "accept-encoding")}
            fwd["Host"] = f"127.0.0.1:{svc['port']}"
            try:
                conn = http.client.HTTPConnection(
                    "127.0.0.1", svc["port"], timeout=300)
                conn.request(self.command, target_path, body=body, headers=fwd)
                resp = conn.getresponse()
                data = resp.read()
            except Exception as e:
                self._plain(502, f"上游服务（{svc['name']}）无响应: {e}")
                return
            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                lk = k.lower()
                if lk in ("transfer-encoding", "connection", "content-length"):
                    continue
                if lk == "location" and v.startswith("/"):
                    v = svc["prefix"] + v  # 上游重定向改写回前缀空间
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if data and self.command != "HEAD":
                self.wfile.write(data)
            conn.close()

        def do_GET(self):
            self._handle()

        def do_POST(self):
            self._handle()

        def do_HEAD(self):
            self._handle()

    return ThreadingHTTPServer((HOST, GATE_PORT), GW)


def main():
    from scripts.assets import missing_assets
    from scripts.seed_examples import seed_examples
    missing = missing_assets()
    if missing:
        print(f"缺少 {len(missing)} 个本机模板资产。先运行：python3 scripts/assets.py import /path/to/整合版")
        sys.exit(1)
    seed_examples()
    procs = []
    # 端口预检：固定端口被占时直接报错（避免子服务静默 +1 漂移串台）；静态模板无端口
    for svc in SERVICES + [{"name": "网关", "port": GATE_PORT}]:
        if svc.get("port") is None:
            continue
        if not port_free(svc["port"]):
            print(f"❌ 端口 {svc['port']} 已被占用（{svc['name']}）。"
                  f"请先停掉占用进程，或检查是否已有整合版在运行。")
            sys.exit(1)

    srv = make_gateway()

    for svc in SERVICES:
        if "static" in svc:
            print(f"▶ 已挂载 {svc['name']}  →  静态直伺服（对外 {svc['prefix']}/）")
            continue
        spawn(svc, procs)
        print(f"▶ 已启动 {svc['name']}  →  内部 127.0.0.1:{svc['port']}（对外 {svc['prefix']}/）")

    shown = "http://127.0.0.1:" if HOST == "127.0.0.1" else "http://<服务器IP>:"
    print("=" * 56)
    print("  账单模板整合版已启动（前台模式，Ctrl+C 全部停止）")
    print(f"    唯一入口:  {shown}{GATE_PORT}")
    for svc in SERVICES:
        print(f"      {svc['name']:<8} {svc['prefix']}/")
    print("=" * 56)

    def stop(*_):
        for _, p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    srv.serve_forever()
    for _, p in procs:
        p.wait(timeout=10)


if __name__ == "__main__":
    main()
