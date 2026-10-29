#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AE 对账单模板 · 本地可视化编辑器
================================
在浏览器里填写测试数据 → 实时预览（自动刷新）→ 保存/下载 PDF。

运行：python3 editor.py [--port 8765] [--no-browser]
依赖：pymupdf（生成器同款），其余为 Python 标准库。
说明：SAMPLE 水印默认开启，可在"输出"卡片中勾选关闭（生成器 with_watermark 参数）。
"""

import base64
import hashlib
import json
import os
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from runtime_paths import data_path
from billgen.server.state import BrowserSessions, RENDER_LOCK, browser_session

BASE = os.path.dirname(os.path.abspath(__file__))
from billgen.themes.amex import generate_sample_statement as gen

# Docker/服务器环境可用环境变量覆盖：
#   AE_HOST     监听地址（默认 127.0.0.1；容器内设 0.0.0.0）
#   AE_SAVE_DIR "保存 PDF 到本目录" 的目标目录（默认项目目录；容器内挂载卷）
HOST = os.environ.get("AE_HOST", "127.0.0.1")
SAVE_DIR = os.environ.get("AE_SAVE_DIR", data_path("AE"))
PV_DIR = data_path("AE/pv_png")     # 预览页 PNG（内容哈希寻址，immutable 缓存）

LOCK = RENDER_LOCK
SESSIONS = BrowserSessions()

# 字段 → 渲染字体（用于字符覆盖检查；both = 该字段同时出现在 Regular/Bold 文本中）
SCALAR_FONTS = {
    "customer_name": "reg",
    "account_ending": "reg",
    "account_name": "reg",
    "statement_date": "reg",
    "period_start": "both",
    "period_end": "both",
    "interest_rate": "reg",
    "apy": "reg",
}
FIELD_LABELS = {
    "customer_name": "客户姓名", "account_ending": "账号尾号",
    "account_name": "账户名称", "statement_date": "账单日",
    "period_start": "周期起", "period_end": "周期止",
    "interest_rate": "利率", "apy": "APY",
    "address_lines": "地址", "transactions": "交易",
}


# ---------------------------------------------------------------- 数据处理
def parse_payload(j):
    """把编辑器 JSON 转为生成器数据字典；非法输入抛 ValueError。"""
    def s(v):
        return "" if v is None else str(v)

    def f(v, what):
        try:
            return float(str(v).replace(",", "") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"{what} 不是有效数字: {v!r}")

    def i(v, what):
        try:
            return int(str(v) or 0)
        except (TypeError, ValueError):
            raise ValueError(f"{what} 不是有效整数: {v!r}")

    txns = []
    for n, t in enumerate(j.get("transactions", []), 1):
        desc_raw = s(t.get("desc", ""))
        item = {
            "date": s(t.get("date", "")),
            "id": s(t.get("id", "")),
            "amount": round(f(t.get("amount", 0), f"第 {n} 条交易金额"), 2),
        }
        if t.get("interest"):
            item["interest"] = True
        if "\n" in desc_raw:          # 多行 = 显式断行；单行 = 自动换行
            item["desc_lines"] = desc_raw.split("\n")
        else:
            item["desc"] = desc_raw
        txns.append(item)

    return {
        "customer_name": s(j.get("customer_name")),
        "address_lines": [s(x) for x in j.get("address_lines", [])][:4],
        "account_ending": s(j.get("account_ending")),
        "account_name": s(j.get("account_name")),
        "statement_date": s(j.get("statement_date")),
        "period_start": s(j.get("period_start")),
        "period_end": s(j.get("period_end")),
        "days_in_period": i(j.get("days_in_period"), "账期天数"),
        "interest_rate": s(j.get("interest_rate")),
        "apy": s(j.get("apy")),
        "interest_paid_ytd": round(f(j.get("interest_paid_ytd"), "年内已付利息"), 2),
        "beginning_balance": round(f(j.get("beginning_balance"), "期初余额"), 2),
        "transactions": txns,
    }


def _missing_chars(text, spec):
    fonts = [gen.reg, gen.bold] if spec == "both" else [gen.reg]
    return sorted({c for c in str(text)
                   if c != " " and not all(f.has_glyph(ord(c)) for f in fonts)})


def build_warnings(data):
    """字符覆盖与排版预警（不阻断生成）。

    返回 (汇总消息列表, 字段级问题字典)。
    字段级 key 与编辑器 DOM 对应：标量字段为其 id，交易行为 "txn:{索引}:{date|id|desc}"。
    """
    warns, issues = [], {}

    def mark(key, text):
        issues[key] = f"{issues[key]}；{text}" if key in issues else text

    def font_label(spec):
        return "Regular + Bold" if spec == "both" else "Regular"

    for key, spec in SCALAR_FONTS.items():
        miss = _missing_chars(data[key], spec)
        if miss:
            msg = f"缺字形字符 {''.join(miss)!r}（{font_label(spec)}，会显示为方框）"
            warns.append(f"{FIELD_LABELS[key]}（{key}）包含{msg}")
            mark(key, msg)
    if len(data["address_lines"]) > 3:
        warns.append("地址超过 3 行：模板地址区固定 4 行（姓名+3），多余行不会渲染")
    for n, ln in enumerate(data["address_lines"][:3], 1):
        miss = _missing_chars(ln, "reg")
        if miss:
            msg = f"缺字形字符 {''.join(miss)!r}（Regular）"
            warns.append(f"地址第 {n} 行包含{msg}")
            mark(f"addr{n}", msg)

    for n, t in enumerate(data["transactions"], 1):
        for fld in ("date", "id"):
            miss = _missing_chars(t[fld], "reg")
            if miss:
                msg = f"缺字形字符 {''.join(miss)!r}（Regular）"
                warns.append(f"交易 #{n} {fld} 包含{msg}")
                mark(f"txn:{n - 1}:{fld}", msg)
        lines = t.get("desc_lines")
        if lines:
            for li, ln in enumerate(lines, 1):
                miss = _missing_chars(ln, "reg")
                if miss:
                    msg = f"第 {li} 行缺字形 {''.join(miss)!r}（Regular）"
                    warns.append(f"交易 #{n} 描述{msg}")
                    mark(f"txn:{n - 1}:desc", msg)
                w = gen.reg.text_length(ln, fontsize=9)
                if w > gen.DESC_WARN_W:
                    msg = (f"第 {li} 行宽度 {w:.0f}pt 超过 "
                           f"{gen.DESC_WARN_W:.0f}pt，会侵入金额列")
                    warns.append(f"交易 #{n} 描述{msg}")
                    mark(f"txn:{n - 1}:desc", msg)
        else:
            miss = _missing_chars(t.get("desc", ""), "reg")
            if miss:
                msg = f"缺字形字符 {''.join(miss)!r}（Regular）"
                warns.append(f"交易 #{n} 描述包含{msg}")
                mark(f"txn:{n - 1}:desc", msg)
    return warns, issues


_COVERAGE = None


def font_coverage():
    """两款字体的已覆盖字符集（懒加载缓存），供浏览器端即时检查。"""
    global _COVERAGE
    if _COVERAGE is None:
        from fontTools.ttLib import TTFont
        cov = {}
        for key, path in (("reg", gen.FONT_REG), ("bold", gen.FONT_BOLD)):
            cmap = TTFont(path).getBestCmap()
            cov[key] = "".join(sorted(chr(cp) for cp in cmap))
        _COVERAGE = cov
    return _COVERAGE


def compute_totals(data):
    txns = data["transactions"]
    return {
        "credits": sum(t["amount"] for t in txns if t["amount"] > 0),
        "debits": sum(t["amount"] for t in txns if t["amount"] < 0),
        "interest": sum(t["amount"] for t in txns if t.get("interest")),
        "ending": data["beginning_balance"] + sum(t["amount"] for t in txns),
    }


# ---------------------------------------------------------------- 元数据
def parse_metadata(j):
    """编辑器元数据 JSON -> 生成器 metadata 字典。

    留空字段不写入；creationDate 复用生成器的 normalize_pdf_date
    规范化（"现在" 按钮生成 "YYYY-MM-DD HH:MM:SS" UTC）。
    返回 (meta, (警告列表, 字段问题字典))；非法时间给出字段级提示并忽略。
    """
    meta = {k: str(j.get(k) or "").strip()
            for k in gen.META_KEYS if str(j.get(k) or "").strip()}
    warns, issues = [], {}
    cd = meta.get("creationDate")
    if cd:
        norm = gen.normalize_pdf_date(cd)
        if norm:
            meta["creationDate"] = norm
        else:
            del meta["creationDate"]
            msg = ("创建时间不是有效日期" if cd[:2].isdigit() and "-" in cd
                   else "时间格式应为 YYYY-MM-DD HH:MM:SS（UTC）")
            warns.append(f"元数据{msg}，已忽略")
            issues["md_creationDate"] = msg
    return meta, (warns, issues)


def parse_xmp(j):
    """编辑器 XMP JSON -> 生成器 xmp 字典。

    仅保留非空字段；三个日期（dc:date / xmp:CreateDate / xmp:MetadataDate）
    经 normalize_xmp_date 规范化为 ISO，非法值给出字段级提示并忽略。
    返回 (xmp, (警告列表, 字段问题字典))。
    """
    out = {k: str(j.get(k) or "").strip() for k in gen.XMP_KEYS
           if str(j.get(k) or "").strip()}
    warns, issues = [], {}
    field_id = {"dc:date": "x_dc_date", "xmp:CreateDate": "x_create_date",
                "xmp:MetadataDate": "x_meta_date"}
    for k in ("dc:date", "xmp:CreateDate", "xmp:MetadataDate"):
        if k in out:
            iso = gen.normalize_xmp_date(out[k])
            if iso:
                out[k] = iso
            else:
                del out[k]
                msg = "日期格式应为 YYYY-MM-DD HH:MM:SS（UTC）"
                warns.append(f"XMP {k} 格式无效，已忽略")
                issues[field_id[k]] = msg
    return out, (warns, issues)


# ---------------------------------------------------------------- 页面
HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>AE 对账单模板 · 测试数据编辑器</title>
<style>
  :root { --blue:#006fcf; --line:#e3e8ee; --bg:#f5f7fa; --red:#d64545; --ok:#2e9e5b; }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.5 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
         background:var(--bg); color:#1c2733; }
  header { background:#fff; border-bottom:1px solid var(--line); padding:12px 20px;
           display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
  header h1 { font-size:17px; margin:0; color:var(--blue); }
  .badge { background:#fff3f3; color:var(--red); border:1px solid #ffd6d6;
           border-radius:6px; padding:2px 10px; font-size:12px; }
  .badge.blue { background:#eef6ff; color:var(--blue); border-color:#cfe6ff; }
  main { display:grid; grid-template-columns: 470px 1fr; gap:16px; padding:16px 20px;
         align-items:start; }
  @media (max-width: 1080px){ main { grid-template-columns: 1fr; } }
  .card { background:#fff; border:1px solid var(--line); border-radius:10px;
          padding:14px 16px; margin-bottom:14px; }
  .card h2 { font-size:14px; margin:0 0 10px; color:var(--blue);
             border-bottom:1px solid var(--line); padding-bottom:6px; }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:8px 12px; }
  label { display:block; font-size:12px; color:#5b6b7c; margin-bottom:2px; }
  input[type=text], input[type=number], textarea {
    width:100%; border:1px solid var(--line); border-radius:6px; padding:6px 8px;
    font:inherit; background:#fff; }
  input:focus, textarea:focus { outline:2px solid #bfe0ff; border-color:var(--blue); }
  .invalid { border-color:var(--red) !important; background:#fff8f8; }
  .fielderr { color:var(--red); font-size:11px; line-height:1.3; margin-top:2px; }
  .txn { border:1px solid var(--line); border-radius:8px; padding:10px; margin-bottom:10px;
         background:#fbfcfe; }
  .txn .row { display:grid; grid-template-columns: 110px 130px 1fr auto; gap:8px;
              align-items:end; margin-bottom:6px; }
  .txn .check { display:flex; align-items:center; gap:4px; font-size:12px; color:#5b6b7c; }
  .del { border:none; background:#ffecec; color:var(--red); border-radius:6px;
         padding:5px 10px; cursor:pointer; }
  .btn { border:1px solid var(--blue); background:#fff; color:var(--blue);
         border-radius:7px; padding:6px 14px; cursor:pointer; font:inherit; }
  .btn.primary { background:var(--blue); color:#fff; }
  .btn.ghost { border-color:var(--line); color:#5b6b7c; }
  .btn:hover { filter:brightness(0.96); }
  .btnrow { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }
  #warnings div { color:var(--red); font-size:13px; padding:2px 0; }
  #warnings .allok { color:var(--ok); }
  #totals { display:flex; gap:8px; flex-wrap:wrap; }
  .chip { background:#eef6ff; border:1px solid #cfe6ff; color:#17456e; border-radius:6px;
          padding:3px 10px; font-size:12px; }
  #preview .pv { margin-bottom:14px; text-align:center; }
  #preview img { width:100%; max-width:640px; border:1px solid var(--line);
                 border-radius:4px; box-shadow:0 2px 10px rgba(20,40,80,.08); }
  #preview img.retry { opacity:.4; }   /* 加载重试中的半透明提示 */
  #preview .pn { font-size:12px; color:#5b6b7c; margin-top:4px; }
  #preview .note { font-size:12px; color:#b8860b; margin-bottom:8px; }
  #status { font-size:12px; color:#5b6b7c; }
  .hint { font-size:12px; color:#8794a3; margin-top:4px; }
  #previewWrap { position:sticky; top:12px; }
</style>
</head>
<body>
<header>
  <h1>AE 对账单模板 · 测试数据编辑器</h1>
  <span class="badge" id="wmBadge">SAMPLE 水印：开启</span>
  <span class="badge blue" id="status">就绪</span>
</header>

<main>
  <section>
    <div class="card">
      <h2>预设</h2>
      <div class="btnrow">
        <button class="btn" onclick="loadPreset('demo')">载入 demo 占位数据</button>
        <button class="btn" onclick="loadPreset('original')">载入 original 数据（复刻核对用）</button>
        <button class="btn" onclick="loadPreset('jun')">载入 jun 数据（跨页排版核对用）</button>
      </div>
    </div>

    <div class="card">
      <h2>客户与账户</h2>
      <div class="grid2">
        <div><label>客户姓名 customer_name</label><input type="text" id="customer_name"></div>
        <div><label>账号尾号 account_ending</label><input type="text" id="account_ending"></div>
        <div style="grid-column:1/-1"><label>账户名称 account_name</label>
          <input type="text" id="account_name"><div class="hint">默认含前导空格，与原版字距一致</div></div>
        <div><label>地址行 1</label><input type="text" id="addr1"></div>
        <div><label>地址行 2</label><input type="text" id="addr2"></div>
        <div><label>地址行 3</label><input type="text" id="addr3"></div>
        <div></div>
      </div>
    </div>

    <div class="card">
      <h2>账期与利率</h2>
      <div class="grid2">
        <div><label>账单日 statement_date</label><input type="text" id="statement_date" placeholder="MM/DD/YYYY"></div>
        <div><label>账期天数 days_in_period</label><input type="number" id="days_in_period"></div>
        <div><label>周期起 period_start</label><input type="text" id="period_start"></div>
        <div><label>周期止 period_end</label><input type="text" id="period_end"></div>
        <div><label>利率 interest_rate</label><input type="text" id="interest_rate" placeholder="1.00%"></div>
        <div><label>APY apy</label><input type="text" id="apy" placeholder="1.00%"></div>
        <div><label>期初余额 beginning_balance</label><input type="number" step="0.01" id="beginning_balance"></div>
        <div><label>年内已付利息 interest_paid_ytd</label><input type="number" step="0.01" id="interest_paid_ytd"></div>
      </div>
    </div>

    <div class="card">
      <h2>交易明细</h2>
      <div id="txns"></div>
      <div class="btnrow">
        <button class="btn primary" onclick="addTxn()">+ 添加交易</button>
        <button class="btn ghost" onclick="random10()">随机加 10 条测试交易</button>
      </div>
      <div class="hint">描述单行 = 按 174pt 自动换行；含换行（回车）= 固定断行（等价于 desc_lines）。</div>
    </div>

    <div class="card">
      <h2>元数据 Metadata</h2>
      <div class="grid2">
        <div style="grid-column:1/-1"><label>标题 title</label>
          <input type="text" id="md_title"></div>
        <div><label>作者 author</label><input type="text" id="md_author"></div>
        <div><label>主题 subject</label><input type="text" id="md_subject"></div>
        <div><label>PDF 生成器 producer</label><input type="text" id="md_producer"></div>
        <div><label>创建软件 creator</label><input type="text" id="md_creator"></div>
        <div><label>描述 keywords</label><input type="text" id="md_keywords"></div>
        <div><label>创建时间（UTC） creationDate</label>
          <div style="display:flex;gap:6px">
            <input type="text" id="md_creationDate" placeholder="YYYY-MM-DD HH:MM:SS">
            <button class="btn ghost" style="white-space:nowrap" onclick="fillNow()">现在</button>
          </div>
        </div>
        <div></div>
      </div>
      <div class="hint">字段留空则不写入（creator/keywords 原稿亦无）；创建时间默认按
        账单日次日 18-19 点分秒随机（UTC）自动生成，修改账单日会重新生成；
        修改时间(modDate)一律不写入。</div>
    </div>

    <div class="card">
      <h2>XMP 元数据</h2>
      <div class="grid2">
        <div style="grid-column:1/-1"><label>标题 dc:title</label>
          <input type="text" id="x_dc_title"></div>
        <div style="grid-column:1/-1"><label>作者 dc:creator</label>
          <input type="text" id="x_dc_creator"></div>
        <div style="grid-column:1/-1"><label>描述 dc:description</label>
          <input type="text" id="x_dc_description"></div>
        <div><label>语言 dc:language</label><input type="text" id="x_dc_language"></div>
        <div><label>PDF 生成器 pdf:Producer</label>
          <input type="text" id="x_pdf_producer"></div>
        <div><label>日期 dc:date</label><input type="text" id="x_dc_date"></div>
        <div><label>创建时间 xmp:CreateDate</label>
          <input type="text" id="x_create_date"></div>
        <div><label>元数据时间 xmp:MetadataDate</label>
          <input type="text" id="x_meta_date"></div>
        <div></div>
      </div>
      <div class="hint">留空字段由 Info 元数据自动派生（结构同原稿）；三个日期默认与
        「元数据」创建时间保持同一时刻（生成/修改账单日/点"现在"时自动同步），可手动覆盖。</div>
    </div>

    <div class="card">
      <h2>输出</h2>
      <div class="grid2">
        <div style="grid-column:1/-1" class="check">
          <input type="checkbox" id="wm" checked>
          <label for="wm" style="margin:0">输出 SAMPLE 水印（内部使用可关闭）</label>
        </div>
        <div style="grid-column:1/-1" class="check">
          <input type="checkbox" id="enc" checked>
          <label for="enc" style="margin:0">输出加密（同原稿 Standard V2 R3 128-bit RC4）</label>
        </div>
        <div style="grid-column:1/-1"><label>保存文件名</label>
          <input type="text" id="fname" value="AE_sample_statement.pdf"></div>
      </div>
      <div class="btnrow">
        <button class="btn primary" onclick="savePdf()">保存 PDF 到模板目录</button>
        <button class="btn" onclick="location.href='/api/download'">下载 PDF</button>
        <button class="btn ghost" onclick="reveal('finder')">在 Finder 中显示</button>
        <button class="btn ghost" onclick="reveal('open')">用默认应用打开</button>
      </div>
      <div class="hint" id="saveMsg"></div>
    </div>

    <div class="card">
      <h2>检查结果（字符覆盖 / 排版预警）</h2>
      <div id="warnings"><div class="allok">尚未生成</div></div>
    </div>
  </section>

  <section id="previewWrap">
    <div class="card">
      <h2>实时预览 <span id="pages" style="color:#5b6b7c;font-weight:400"></span></h2>
      <div id="totals"></div>
      <div id="preview" style="margin-top:10px"><div class="hint">修改左侧内容后自动刷新…</div></div>
    </div>
  </section>
</main>

<script>
let randCounter = 0;
const debounceMap = {};
function debounce(key, fn, ms){ clearTimeout(debounceMap[key]); debounceMap[key] = setTimeout(fn, ms); }

function collect(){
  const g = id => document.getElementById(id).value;
  const txns = [...document.querySelectorAll('#txns .txn')].map(card => ({
    date: card.querySelector('.t-date').value,
    desc: card.querySelector('.t-desc').value,
    id: card.querySelector('.t-id').value,
    amount: parseFloat(card.querySelector('.t-amt').value || '0') || 0,
    interest: card.querySelector('.t-int').checked
  }));
  return {
    customer_name: g('customer_name'),
    address_lines: [g('addr1'), g('addr2'), g('addr3')],
    account_ending: g('account_ending'),
    account_name: g('account_name'),
    statement_date: g('statement_date'),
    period_start: g('period_start'),
    period_end: g('period_end'),
    days_in_period: g('days_in_period'),
    interest_rate: g('interest_rate'),
    apy: g('apy'),
    interest_paid_ytd: g('interest_paid_ytd'),
    beginning_balance: g('beginning_balance'),
    transactions: txns,
    watermark: document.getElementById('wm').checked,
    encrypt: document.getElementById('enc').checked,
    metadata: {
      title: g('md_title'), author: g('md_author'), subject: g('md_subject'),
      keywords: g('md_keywords'), producer: g('md_producer'), creator: g('md_creator'),
      creationDate: g('md_creationDate')
    },
    xmp: {
      'dc:title': g('x_dc_title'), 'dc:creator': g('x_dc_creator'),
      'dc:description': g('x_dc_description'), 'dc:language': g('x_dc_language'),
      'pdf:Producer': g('x_pdf_producer'), 'dc:date': g('x_dc_date'),
      'xmp:CreateDate': g('x_create_date'), 'xmp:MetadataDate': g('x_meta_date')
    }
  };
}

function txnCard(t, i){
  return `<div class="txn">
    <div class="row">
      <div><label>日期</label><input type="text" class="t-date" value="${esc(t.date)}" placeholder="MM/DD/YYYY"></div>
      <div><label>金额（负=借方）</label><input type="number" step="0.01" class="t-amt" value="${t.amount ?? 0}"></div>
      <div><label>交易 ID（6pt 小字行）</label><input type="text" class="t-id" value="${esc(t.id)}"></div>
      <div class="check"><input type="checkbox" class="t-int" ${t.interest?'checked':''}>利息</div>
    </div>
    <label>描述</label>
    <textarea class="t-desc" rows="2">${esc(t.desc)}</textarea>
    <div class="btnrow" style="margin-top:6px">
      <button class="del" onclick="delTxn(this)">删除 #${i+1}</button>
    </div>
  </div>`;
}
function esc(s){ return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;'); }

function renderTxns(txns){
  document.getElementById('txns').innerHTML = txns.map((t,i)=>txnCard(t,i)).join('') ||
    '<div class="hint">暂无交易（期末余额 = 期初余额）</div>';
}

function apply(d){
  const set = (id,v)=>document.getElementById(id).value = v ?? '';
  set('customer_name', d.customer_name); set('account_ending', d.account_ending);
  set('account_name', d.account_name);
  const a = d.address_lines || [];
  set('addr1', a[0]); set('addr2', a[1]); set('addr3', a[2]);
  set('statement_date', d.statement_date); set('period_start', d.period_start);
  set('period_end', d.period_end); set('days_in_period', d.days_in_period);
  set('interest_rate', d.interest_rate); set('apy', d.apy);
  set('interest_paid_ytd', d.interest_paid_ytd);
  set('beginning_balance', d.beginning_balance);
  renderTxns((d.transactions||[]).map(t => ({
    date: t.date||'', id: t.id||'', amount: t.amount??0, interest: !!t.interest,
    desc: t.desc_lines ? t.desc_lines.join('\n') : (t.desc||'')
  })));
}

function money(v){ const s = Math.abs(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}); return v<0?`-$${s}`:`$${s}`; }

/* ---------- 字符覆盖即时检查 ---------- */
const FONT_SPEC = {
  customer_name:'reg', account_ending:'reg', account_name:'reg',
  statement_date:'reg', period_start:'both', period_end:'both',
  interest_rate:'reg', apy:'reg', addr1:'reg', addr2:'reg', addr3:'reg'
};
let COV = null;
async function loadCoverage(){
  const r = await fetch('/api/coverage'); COV = await r.json();
}
function missingChars(text, spec){
  if(!COV || !text) return [];
  const out = [];
  for(const c of String(text)){
    if(c === ' ') continue;
    const ok = spec === 'both'
      ? (COV.reg.includes(c) && COV.bold.includes(c)) : COV.reg.includes(c);
    if(!ok && !out.includes(c)) out.push(c);
  }
  return out;
}
function keyFor(el){
  if(el.id) return el.id;
  const card = el.closest('.txn');
  if(card){
    const i = [...document.querySelectorAll('#txns .txn')].indexOf(card);
    if(el.classList.contains('t-date')) return `txn:${i}:date`;
    if(el.classList.contains('t-desc')) return `txn:${i}:desc`;
    if(el.classList.contains('t-id'))   return `txn:${i}:id`;
  }
  return null;
}
function elForKey(key){
  if(key.startsWith('txn:')){
    const parts = key.split(':');   // txn : i : field
    const card = document.querySelectorAll('#txns .txn')[+parts[1]];
    return card ? card.querySelector('.t-' + parts[2]) : null;
  }
  return document.getElementById(key);
}
function markEl(el, msg){
  if(!el) return;
  el.classList.add('invalid');
  let err = el.parentElement.querySelector('.fielderr');
  if(!err){ err = document.createElement('div'); err.className = 'fielderr';
            el.parentElement.appendChild(err); }
  err.textContent = msg;
}
function clearEl(el){
  if(!el) return;
  el.classList.remove('invalid');
  const err = el.parentElement.querySelector('.fielderr');
  if(err) err.remove();
}
function clearAllMarks(){
  document.querySelectorAll('.invalid').forEach(el => el.classList.remove('invalid'));
  document.querySelectorAll('.fielderr').forEach(el => el.remove());
}
function applyFieldIssues(issues){
  Object.entries(issues || {}).forEach(([k, m]) => markEl(elForKey(k), m));
}
function instantCheck(el){
  const key = keyFor(el);
  if(!key) return;
  const spec = key.startsWith('txn:') ? 'reg' : FONT_SPEC[key];
  if(!spec){ clearEl(el); return; }
  clearEl(el);
  const miss = missingChars(el.value, spec);
  if(miss.length)
    markEl(el, `含字体缺字形的字符: ${miss.join(' ')}${spec==='both' ? '（需 Regular+Bold 都有）' : '（Regular）'}`);
}

async function build(){
  // 焦点保持：文本输入进行中不生成预览（输完整个值才算一次输入）；
  // 勾选框/按钮即时生效。失焦由 focusout 监听立即触发
  const ae = document.activeElement;
  if(ae && ((ae.tagName === 'INPUT' && (ae.type === 'text' || !ae.type || ae.type === 'number'))
      || ae.tagName === 'TEXTAREA') && document.querySelector('main').contains(ae)){
    debounce('build', build, 500);
    return;
  }
  document.getElementById('status').textContent = '生成中…';
  try{
    const r = await fetch('/api/build', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(collect())});
    const j = await r.json();
    if(!j.ok){
      document.getElementById('warnings').innerHTML = `<div>生成失败：${esc(j.error)}</div>`;
      document.getElementById('status').textContent = '出错';
      return;
    }
    const box = document.getElementById('preview');
    // 页级增量刷新：哈希寻址 + img 复用——内容未变的页不换 src（浏览器缓存秒出、
    // 无白闪），变化的页单独重载；首次渲染移除占位提示
    const h0 = box.querySelector(':scope > .hint');
    if (h0) h0.remove();
    const note = box.querySelector(':scope > .note');
    if (note) note.remove();
    if (j.truncated)
      box.insertAdjacentHTML('afterbegin', `<div class="note">共 ${j.pages} 页，仅预览前 8 页</div>`);
    let wraps = [...box.querySelectorAll(':scope > .pv')];
    while (wraps.length < j.hashes.length) {
      const w = document.createElement('div');
      w.className = 'pv';
      w.innerHTML = '<img loading="lazy"><div class="pn"></div>';
      box.appendChild(w);
      wraps.push(w);
    }
    while (wraps.length > j.hashes.length) wraps.pop().remove();
    wraps.forEach((w, i) => {
      const img = w.querySelector('img');
      const src = `/pv/${j.hashes[i]}.png`;
      img.onload = () => img.classList.remove('retry');
      img.onerror = () => {
        img.classList.add('retry');
        setTimeout(() => { img.removeAttribute('src'); img.src = src; }, 800);
      };
      if (!img.src.endsWith(src)) img.src = src;
      w.querySelector('.pn').textContent = `第 ${i + 1} / ${j.pages} 页`;
    });
    document.getElementById('pages').textContent = `（共 ${j.pages} 页）`;
    const w = document.getElementById('warnings');
    w.innerHTML = j.warnings.length
      ? j.warnings.map(x=>`<div>⚠ ${esc(x)}</div>`).join('')
      : '<div class="allok">✓ 字符覆盖与排版检查全部通过</div>';
    const t = j.totals;
    document.getElementById('totals').innerHTML =
      `<span class="chip">贷方合计 ${money(t.credits)}</span>` +
      `<span class="chip">借方合计 ${money(t.debits)}</span>` +
      `<span class="chip">利息入账 ${money(t.interest)}</span>` +
      `<span class="chip">期末余额 ${money(t.ending)}</span>`;
    clearAllMarks();                       // 服务端权威标记（含超宽等排版检查）
    applyFieldIssues(j.fieldIssues);
    document.getElementById('status').textContent = '预览已更新';
  }catch(e){
    document.getElementById('status').textContent = '连接失败';
  }
}

function addTxn(){ renderTxns(collect().transactions.concat([{date:'',desc:'',id:'',amount:0}])); buildSoon(); }
function delTxn(btn){
  const cards=[...document.querySelectorAll('#txns .txn')];
  const idx = cards.indexOf(btn.closest('.txn'));
  const tx = collect().transactions; tx.splice(idx,1); renderTxns(tx); buildSoon();
}
function random10(){
  const tx = collect().transactions;
  for(let i=0;i<10;i++){
    randCounter++;
    tx.push({ date: `04/${String(2+(randCounter%28)).padStart(2,'0')}/2026`,
      desc: `Random Test Transaction ${randCounter} Demo Merchant Ref ${1000+randCounter}`,
      id: `ID ${String(700000000000000+randCounter)}`,
      amount: +(Math.random()*300-80).toFixed(2), interest:false });
  }
  renderTxns(tx); buildSoon();
}
function buildSoon(){ debounce('build', build, 700); }

/* ---------- 元数据默认值与日期规则 ---------- */
const gv = id => document.getElementById(id).value;
const META_DEFAULTS = {
  "md_title": "",
  "md_author": "",
  "md_subject": "",
  "md_producer": "",
  "x_dc_title": "",
  "x_dc_creator": "",
  "x_dc_description": "",
  "x_dc_language": "",
  "x_pdf_producer": ""
};
function fillMetaDefaults(){
  for (const [id, v] of Object.entries(META_DEFAULTS)){
    const el = document.getElementById(id);
    if (el && !el.value) el.value = v;
  }
}
function fmtUTC(d){
  return d.toISOString().slice(0, 19).replace('T', ' ');
}
function ruleCreationDate(){
  /* 原稿规律：账单日的次日 18:00-19:00，分秒随机（UTC） */
  const m = (gv('statement_date') || '').trim().match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (!m) return '';
  const d = new Date(Date.UTC(+m[3], +m[1] - 1, +m[2] + 1, 18,
                              Math.floor(Math.random() * 60),
                              Math.floor(Math.random() * 60)));
  return fmtUTC(d);
}
function syncXmpDates(){
  const v = gv('md_creationDate');
  ['x_dc_date', 'x_create_date', 'x_meta_date'].forEach(
    id => document.getElementById(id).value = v);
}
function regenCreationDate(){
  const v = ruleCreationDate();
  if (v){
    document.getElementById('md_creationDate').value = v;
    syncXmpDates();
  }
}
function fillNow(){
  document.getElementById('md_creationDate').value = fmtUTC(new Date());
  syncXmpDates();
  buildSoon();
}

function syncWmBadge(){
  const on = document.getElementById('wm').checked;
  const b = document.getElementById('wmBadge');
  b.textContent = on ? 'SAMPLE 水印：开启' : 'SAMPLE 水印：已关闭';
  b.style.background = on ? '#fff3f3' : '#eef6ff';
  b.style.color = on ? 'var(--red)' : 'var(--blue)';
  b.style.borderColor = on ? '#ffd6d6' : '#cfe6ff';
}

async function loadPreset(name){
  const r = await fetch('/api/presets'); const j = await r.json();
  apply(j[name]); regenCreationDate(); build();
}
async function savePdf(){
  const r = await fetch('/api/save', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({filename: document.getElementById('fname').value})});
  const j = await r.json();
  document.getElementById('saveMsg').textContent = j.ok ? `已保存：${j.path}` : `保存失败：${j.error}`;
}
async function reveal(action){
  const r = await fetch('/api/reveal', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({action})});
  const j = await r.json();
  if(!j.ok) document.getElementById('saveMsg').textContent = j.error;
}

document.querySelector('main').addEventListener('input', e => { instantCheck(e.target); buildSoon(); });
// 焦点离开任一文本输入 → 立即按完整值生成
document.querySelector('main').addEventListener('focusout', () => buildSoon());
document.querySelector('main').addEventListener('change', e => { instantCheck(e.target); buildSoon(); });
document.getElementById('wm').addEventListener('change', syncWmBadge);
document.getElementById('statement_date').addEventListener('change', regenCreationDate);
syncWmBadge();
fillMetaDefaults();
loadCoverage();
loadPreset('demo');
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- HTTP 服务
class Handler(BaseHTTPRequestHandler):
    sessions = SESSIONS

    def end_headers(self):
        if getattr(self, "_session_cookie", None):
            self.send_header("Set-Cookie", self._session_cookie)
            self._session_cookie = None
        super().end_headers()

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(f"[editor] {self.command} {self.path}")

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self):
        body = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @browser_session
    def do_GET(self):
        if self.path == "/":
            self._html()
        elif self.path.startswith("/pv/") and self.path.endswith(".png"):
            # 预览页 PNG：路径=内容哈希（无查询串），可永久缓存；文件名取 basename 防穿越
            name = os.path.basename(self.path.split("?")[0])
            fp = os.path.join(PV_DIR, name)
            if os.path.isfile(fp):
                with open(fp, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "public, max-age=86400, immutable")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
        elif self.path == "/api/presets":
            self._json(gen.DATASETS)
        elif self.path == "/api/coverage":
            cov = font_coverage()
            self._json({"reg": cov["reg"], "bold": cov["bold"]})
        elif self.path == "/api/download":
            with LOCK:
                pdf = self.session.pdf
            if not pdf:
                self._json({"ok": False, "error": "尚未生成，请先编辑内容"}, 400)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition",
                             'attachment; filename="AE_sample_statement.pdf"')
            self.send_header("Content-Length", str(len(pdf)))
            self.end_headers()
            self.wfile.write(pdf)
        else:
            self.send_response(204)
            self.end_headers()

    @browser_session
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception as e:
            self._json({"ok": False, "error": f"请求解析失败: {e}"}, 400)
            return

        if self.path == "/api/build":
            try:
                data = parse_payload(body)
            except ValueError as e:
                self._json({"ok": False, "error": str(e)}, 400)
                return
            warnings, field_issues = build_warnings(data)
            totals = compute_totals(data)
            with_watermark = bool(body.get("watermark", True))
            encrypt = bool(body.get("encrypt", True))
            meta, (md_warns, md_issues) = parse_metadata(body.get("metadata") or {})
            xv, (x_warns, x_issues) = parse_xmp(body.get("xmp") or {})
            warnings.extend(md_warns)
            warnings.extend(x_warns)
            field_issues.update(md_issues)
            field_issues.update(x_issues)
            try:
                with LOCK:
                    d = gen.build_pdf(data, "editor", with_watermark=with_watermark,
                                      metadata=meta, xmp=xv)
                    pages = d.page_count
                    pdf = gen.pdf_bytes(d, encrypt=encrypt)
                    # 预览页：内容哈希寻址落盘（响应只带哈希；未变化页走 immutable
                    # 浏览器缓存，不再每次全量 base64 回传）
                    hashes = []
                    os.makedirs(PV_DIR, exist_ok=True)
                    for i, page in enumerate(d):
                        if i >= 8:
                            break
                        png = page.get_pixmap(dpi=96).tobytes("png")
                        h = hashlib.md5(png).hexdigest()[:12]
                        fp = os.path.join(PV_DIR, h + ".png")
                        if not os.path.exists(fp):
                            tmp = fp + ".tmp.png"
                            with open(tmp, "wb") as f:
                                f.write(png)
                            os.replace(tmp, fp)
                        hashes.append(h)
                    self.session.hashes = list(hashes)
                    referenced = SESSIONS.referenced_hashes()
                    for f in os.listdir(PV_DIR):      # 清理不再引用的页（防累积）
                        if f.endswith(".png") and f[:-4] not in referenced:
                            try:
                                os.remove(os.path.join(PV_DIR, f))
                            except OSError:
                                pass
                    self.session.pdf = pdf
                    self.session.path = None
                    d.close()
            except Exception as e:
                self._json({"ok": False, "error": f"生成失败: {e}"}, 500)
                return
            self._json({"ok": True, "pages": pages, "hashes": hashes,
                        "truncated": pages > 8, "warnings": warnings,
                        "fieldIssues": field_issues, "totals": totals})

        elif self.path == "/api/save":
            with LOCK:
                pdf = self.session.pdf
            if not pdf:
                self._json({"ok": False, "error": "尚未生成，请先编辑内容"}, 400)
                return
            fname = os.path.basename(str(body.get("filename") or "").strip())
            if not fname or fname.startswith("."):
                fname = "AE_sample_statement.pdf"
            if not fname.lower().endswith(".pdf"):
                fname += ".pdf"
            os.makedirs(SAVE_DIR, exist_ok=True)
            path = os.path.join(SAVE_DIR, fname)
            try:
                with open(path, "wb") as f:
                    f.write(pdf)
            except OSError as e:
                self._json({"ok": False, "error": f"写入失败: {e}"}, 500)
                return
            with LOCK:
                self.session.path = path
            self._json({"ok": True, "path": path})

        elif self.path == "/api/reveal":
            action = body.get("action")
            with LOCK:
                path = self.session.path
            if not path or not os.path.exists(path):
                self._json({"ok": False, "error": "尚未保存过文件"}, 400)
                return
            if sys.platform != "darwin":
                self._json({"ok": False, "error": "仅支持 macOS"}, 400)
                return
            cmd = ["open", "-R", path] if action == "finder" else ["open", path]
            subprocess.Popen(cmd)
            self._json({"ok": True})

        else:
            self._json({"ok": False, "error": "unknown endpoint"}, 404)


def main():
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1]) if "--port" in args else 8765
    srv = ThreadingHTTPServer((HOST, port), Handler)
    srv.daemon_threads = True
    shown_host = "127.0.0.1" if HOST == "0.0.0.0" else HOST
    url = f"http://{shown_host}:{port}"
    print(f"[editor] 本地编辑器已启动: {url}  (Ctrl+C 退出)")
    if "--no-browser" not in args:
        def _open():
            try:
                webbrowser.open(url)
            except Exception:
                pass  # 无浏览器环境（如 Docker/无头服务器）
        threading.Timer(0.6, _open).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[editor] 已退出")


if __name__ == "__main__":
    main()
