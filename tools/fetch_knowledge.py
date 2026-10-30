#!/usr/bin/env python3
"""把外部依据拉到本地，做成可离线检索的知识库。

用法:
    python tools/fetch_knowledge.py                 # 拉全部（跳过 24 小时内已拉过的）
    python tools/fetch_knowledge.py --only chase,boa
    python tools/fetch_knowledge.py --force         # 无视缓存全部重拉
    python tools/fetch_knowledge.py --check         # 只比对是否变过（不写文件）
    python tools/fetch_knowledge.py --report        # 打印现状表

设计:
  - `knowledge/sources.json` 是登记簿：每条有 id / 主题 / 类型 / url / 抓取时间 / sha256 / 状态。
  - `knowledge/raw/<id>.txt` 是离线正文（PDF 用 PyMuPDF 抽文本，HTML 去标签）。
  - 只存「引用片段 + 元数据」，不存整份受版权保护的 PDF。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import html as _html
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB = ROOT / "knowledge"
SOURCES = KB / "sources.json"
RAW = KB / "raw"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)
MAX_CHARS = 120_000
TIMEOUT = 30


# ---------------------------------------------------------------- 抓取与抽文本
def _get(url: str) -> tuple[int, bytes, str]:
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",  # eCFR API 不带这行会回 406
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            if resp.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return resp.status, body, ctype
    except urllib.error.HTTPError as exc:
        return exc.code, b"", ""
    except Exception as exc:  # 网络层问题也记下来，不抛
        return 0, b"", f"{type(exc).__name__}: {exc}"


def _html_text(raw: bytes) -> str:
    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError:
        html = raw.decode("latin-1", "replace")
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = (
        html.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n{3,}", "\n\n", html))


def _pdf_text(raw: bytes) -> str:
    try:
        import fitz  # PyMuPDF
    except Exception:
        return ""
    out: list[str] = []
    try:
        doc = fitz.open(stream=raw, filetype="pdf")
    except Exception:
        return ""
    for page in doc:
        out.append(page.get_text("text"))
    doc.close()
    return "\n".join(out)


def extract_text(raw: bytes, ctype: str) -> str:
    if raw[:4] == b"%PDF" or "pdf" in ctype.lower():
        text = _pdf_text(raw)
        if text.strip():
            return text
    if "html" in ctype.lower() or raw[:200].lstrip().startswith(b"<"):
        return _html_text(raw)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", "replace")


def _squash(text: str) -> str:
    text = _html.unescape(text)  # eCFR XML 里 § 是 &#xA7; 实体
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:MAX_CHARS]


# ---------------------------------------------------------------- 登记簿
def load_sources() -> dict:
    return json.loads(SOURCES.read_text("utf-8"))


def save_sources(doc: dict) -> None:
    SOURCES.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", "utf-8")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fdic_url(item: dict) -> str:
    """FDIC BankFind：银行法定名称/地址/电话/官网，账单抬头要用的一手数据。"""
    if item.get("cert"):
        query = urllib.parse.urlencode(
            {
                "filters": f"CERT:{item['cert']}",
                "fields": "NAME,CITY,STALP,ADDRESS,ZIP,PHONE,WEBADDR,CERT,RSSDHID",
                "limit": "1",
                "format": "json",
            }
        )
        return "https://api.fdic.gov/banks/institutions?" + query
    query = urllib.parse.urlencode(
        {
            "search": f"name:{item['search']}",
            "fields": "NAME,CITY,STALP,ADDRESS,ZIP,PHONE,WEBADDR,CERT,RSSDHID",
            "limit": str(item.get("limit") or 3),
            "format": "json",
        }
    )
    return "https://api.fdic.gov/banks/institutions?" + query


def _fdic_text(raw: bytes) -> str:
    data = json.loads(raw.decode("utf-8"))
    rows = [r.get("data", {}) for r in data.get("data", [])]
    keep = [
        {k: r.get(k) for k in ("NAME", "ADDRESS", "CITY", "STALP", "ZIP", "PHONE", "WEBADDR", "CERT", "RSSDHID")}
        for r in rows
    ]
    return json.dumps(keep, ensure_ascii=False, indent=2)


def fetch_one(item: dict, *, force: bool, check_only: bool) -> dict:
    sid = item["id"]
    if item.get("mode") == "fdic":
        item["url"] = fdic_url(item)
    target = RAW / f"{sid}.txt"
    if not force and target.exists() and not check_only:
        age = time.time() - target.stat().st_mtime
        if age < 24 * 3600:
            item["status"] = item.get("status", "ok")
            item["cached"] = True
            return item
    status, body, ctype = _get(item["url"])
    item["http_status"] = status
    item["content_type"] = ctype.split(";")[0].strip() or None
    if status != 200 or not body:
        item["status"] = "unreachable" if status in (0, 403, 404, 410, 500) else "error"
        item["checked_at"] = now()
        item.pop("cached", None)
        return item
    text = _squash(_fdic_text(body) if item.get("mode") == "fdic" else extract_text(body, ctype))
    digest = hashlib.sha256(body).hexdigest()
    item["bytes"] = len(body)
    item["chars"] = len(text)
    item["sha256"] = digest
    item["fetched_at"] = now()
    item["status"] = "ok" if text else "empty"
    item.pop("cached", None)
    if check_only:
        old = target.read_text("utf-8") if target.exists() else ""
        item["changed"] = hashlib.sha256(old.encode()).hexdigest() != hashlib.sha256(
            _raw_header(item, text).encode()
        ).hexdigest()
        return item
    if not text:
        return item
    RAW.mkdir(parents=True, exist_ok=True)
    target.write_text(_raw_header(item, text), "utf-8")
    return item


def _raw_header(item: dict, text: str) -> str:
    return (
        f"source: {item['id']}\n"
        f"topic: {item.get('topic', '')}\n"
        f"kind: {item.get('kind', '')}\n"
        f"url: {item['url']}\n"
        f"fetched_at: {item.get('fetched_at', '')}\n"
        f"content_type: {item.get('content_type', '')}\n"
        f"sha256: {item.get('sha256', '')}\n"
        "--- 正文（外链原文摘录，只作离线检索用）---\n\n" + text + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把外部依据拉到本地知识库")
    ap.add_argument("--only", default="", help="只处理这些 id（逗号分隔，可写主题前缀）")
    ap.add_argument("--force", action="store_true", help="无视缓存重拉")
    ap.add_argument("--check", action="store_true", help="只比对是否变过，不写文件")
    ap.add_argument("--report", action="store_true", help="打印现状表")
    args = ap.parse_args(argv)

    KB.mkdir(parents=True, exist_ok=True)
    doc = load_sources()
    if args.report:
        rows = doc["sources"]
        ok = sum(1 for r in rows if r.get("status") == "ok")
        print(f"{len(rows)} 条来源，{ok} 条已落地 → knowledge/raw/")
        for r in rows:
            flag = {"ok": "✓", "empty": "∅"}.get(r.get("status"), "✗")
            print(f"  {flag} {r['id']:<38} {r.get('http_status') or '-':>4}  {r.get('chars') or 0:>7} 字  {r['url'][:70]}")
        return 0

    only = [x.strip() for x in args.only.split(",") if x.strip()]
    changed: list[str] = []
    for item in doc["sources"]:
        if only and not any(item["id"] == o or item["id"].startswith(o) for o in only):
            continue
        before = item.get("sha256")
        item = fetch_one(item, force=args.force, check_only=args.check)
        if args.check and item.get("changed"):
            changed.append(item["id"])
        elif args.check and before != item.get("sha256"):
            changed.append(item["id"])
        mark = "✓" if item.get("status") == "ok" else "✗"
        print(f"{mark} {item['id']:<38} {str(item.get('http_status') or '-'):>4}  {item.get('chars') or 0:>7} 字")
    doc["updated_at"] = now()
    if not args.check:
        save_sources(doc)
    if args.check:
        print("有变化的：" + (", ".join(changed) if changed else "无"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
