#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
American Express Rewards Checking 对账单 —— 演示/测试模板生成器
==============================================================
用途：生成对账单版式 PDF（默认带 SAMPLE 水印，可通过参数关闭）。

对外 API：
  build_pdf(data, dataset_label, with_watermark=True, metadata=None) -> fitz.Document
      传入数据字典，返回 PDF 文档对象（不落盘）。
      with_watermark 控制页面水印；metadata 控制 PDF 元数据（二者互不影响）。

命令行：
  python3 generate_sample_statement.py [--dataset demo|original|jun] [--stress N] [--no-watermark] [--no-encrypt]
  demo     -> AE_sample_statement.pdf（占位样例数据）
  original -> AE_replica_test.pdf（原版样张数据，用于复刻核对）
  jun      -> AE_jun_replica_test.pdf（JUN 3 页原样张，跨页核对）
  --stress N 追加 N 条随机交易 -> AE_sample_statement_stress.pdf
  --no-watermark 关闭水印输出
  --no-encrypt 关闭原稿同款加密（默认输出 Standard V2 R3 128-bit RC4）

水印默认开启：每页页面中央绘制一个 45° 斜向 "SAMPLE" 大字（仅页面
显示层，不写入元数据；元数据由 metadata 参数独立控制）。

坐标体系：所有常量均从原版样张逐项测量（US Letter 612x792pt），
文字定位以 bbox 顶边为准，基线 = top + ascent × size
（Bold ascent≈1.0978，Regular ascent≈1.0778，Italic ascent≈1.0820）。

跨页排版（依 JUN 3 页原稿逐项复刻）：
  - 续页无 "Account Activity (Continued)" 标题，表头直接排在 top=78.85，
    首条交易 top=98.87；
  - 被截断页面在末条内容下方排 8pt 斜体 "Continued on next page"，
    右对齐至 Balance 列右缘（字体自原稿子集提取，仅含所需字形）；
  - 换页判定需为"期末余额行 + 收尾横线"预留空间（行底 + 预留 ≤ 页下限）；
  - 流水跨页时第 1 页底部区块与页脚整体上移 5.67pt；
  - 通知页页眉随页码奇偶切换：0 基偶数页用完整页眉（正文下移 19.845pt）。
"""

import copy
import datetime
import os
import re
import sys
from dataclasses import dataclass
from datetime import timedelta

import fitz  # PyMuPDF

BASE = os.path.dirname(os.path.abspath(__file__))
FONT_REG = os.path.join(BASE, "fonts", "BentonSansAE-Regular.ttf")
FONT_BOLD = os.path.join(BASE, "fonts", "BentonSansAE-Bold.ttf")
FONT_ITAL = os.path.join(BASE, "fonts", "BentonSansAE-RegularItalic.ttf")  # 原稿子集提取
LOGO = os.path.join(BASE, "assets", "logo.png")

reg = fitz.Font(fontfile=FONT_REG)
bold = fitz.Font(fontfile=FONT_BOLD)
ital = fitz.Font(fontfile=FONT_ITAL)  # 仅含 "Continued on next page" 所需字形
K_BOLD, K_REG, K_ITAL = 1.0978, 1.0778, 1.0820  # 基线系数（自原版 span origin 反推）

BLUE = (0, 0.4353, 0.8118)      # #006FCF
BLACK = (0, 0, 0)
WM_GRAY = (0.60, 0.60, 0.60)

# =====================================================================
# 数据集（CLI 与编辑器预设用）
# =====================================================================
DATASETS = {'demo': {'customer_name': 'ALEX EXAMPLE',
          'address_lines': ['100 EXAMPLE STREET', 'DEMO CITY CA 90000'],
          'account_ending': '0001',
          'account_name': 'Rewards Checking',
          'statement_date': '10/31/2026',
          'period_start': '10/01/2026',
          'period_end': '10/31/2026',
          'days_in_period': 31,
          'interest_rate': '0.00%',
          'apy': '0.00%',
          'interest_paid_ytd': 0.0,
          'beginning_balance': 1000.0,
          'transactions': [{'date': '10/02/2026',
                            'desc': 'Sample payroll credit',
                            'amount': 250.0,
                            'id': 'DEMO0001',
                            'txid': 'DEMO0001',
                            'memo': '',
                            'type': 'Deposit'},
                           {'date': '10/07/2026',
                            'desc': 'Sample grocery purchase',
                            'amount': -35.5,
                            'id': 'DEMO0002',
                            'txid': 'DEMO0002',
                            'memo': '',
                            'type': 'Withdrawal'}]},
 'original': {'customer_name': 'ALEX EXAMPLE',
              'address_lines': ['100 EXAMPLE STREET', 'DEMO CITY CA 90000'],
              'account_ending': '0001',
              'account_name': 'Rewards Checking',
              'statement_date': '10/31/2026',
              'period_start': '10/01/2026',
              'period_end': '10/31/2026',
              'days_in_period': 31,
              'interest_rate': '0.00%',
              'apy': '0.00%',
              'interest_paid_ytd': 0.0,
              'beginning_balance': 1000.0,
              'transactions': [{'date': '10/02/2026',
                                'desc': 'Sample payroll credit',
                                'amount': 250.0,
                                'id': 'DEMO0001',
                                'txid': 'DEMO0001',
                                'memo': '',
                                'type': 'Deposit'},
                               {'date': '10/07/2026',
                                'desc': 'Sample grocery purchase',
                                'amount': -35.5,
                                'id': 'DEMO0002',
                                'txid': 'DEMO0002',
                                'memo': '',
                                'type': 'Withdrawal'}]},
 'jun': {'customer_name': 'ALEX EXAMPLE',
         'address_lines': ['100 EXAMPLE STREET', 'DEMO CITY CA 90000'],
         'account_ending': '0001',
         'account_name': 'Rewards Checking',
         'statement_date': '10/31/2026',
         'period_start': '10/01/2026',
         'period_end': '10/31/2026',
         'days_in_period': 31,
         'interest_rate': '0.00%',
         'apy': '0.00%',
         'interest_paid_ytd': 0.0,
         'beginning_balance': 1000.0,
         'transactions': [{'date': '10/02/2026',
                           'desc': 'Sample payroll credit',
                           'amount': 250.0,
                           'id': 'DEMO0001',
                           'txid': 'DEMO0001',
                           'memo': '',
                           'type': 'Deposit'},
                          {'date': '10/07/2026',
                           'desc': 'Sample grocery purchase',
                           'amount': -35.5,
                           'id': 'DEMO0002',
                           'txid': 'DEMO0002',
                           'memo': '',
                           'type': 'Withdrawal'}]}}

# ---------------------------------------------------------------- 版式常量
PAGE_W, PAGE_H = 612, 792
DESC_X = 112.8
DESC_WRAP_W = 174.0          # 描述列自动换行宽度（原版实测最大行宽）
DESC_WARN_W = 225.0          # 显式断行超过此宽度会侵入金额列（编辑器预警用）
CRED_R, DEB_R, BAL_R = 376.4, 461.4, 560.6   # 三列金额右缘
SUM_R, INT_R = 297.0, 561.1                 # 摘要/利息数值右缘
GAP_NO_ID = 7.5              # 期初余额行 → 首条交易间距（原版 298.9→306.4）

# --- 交易行几何（按 APR/JUN 两份原稿逐行拟合，链式累计误差 ≤0.03pt）---
DESC_STEP = 14.175           # 描述行行距（两份原稿 30+ 行实测拟合）
ID_OFF = 12.46               # 描述末行 top → ID 小字行 top
ADV_ID = 13.38               # ID 行 top → 下一条交易 top
ADV_END_ID = 13.2            # ID 行 top → 期末余额行 top
ADV_NOID = 19.4              # 无 ID 行末行 top → 下一条 top（无实测样本，沿旧值）
ADV_END_NOID = 19.35         # 无 ID 行末行 top → 期末余额行 top（沿旧值）

END_RULE = 11.16             # 期末余额行下缘 → 收尾横线（原版 412.9→424.06）
P1_TABLE_LIMIT = 535.0       # 第 1 页表格区下限（底部区块标题在 547.2）
PN_TABLE_LIMIT = 715.0       # 续页表格区下限

# --- 跨页排版（JUN 原稿 3 页样张实测）---
PN_HEADER_TOP = 78.85        # 续页表头 top（无 "Account Activity (Continued)" 标题）
PN_FIRST_TOP = 98.87         # 续页首条交易 top
CONT_OFF = 14.13             # 页内末条 ID/末行 top → "Continued on next page" top
CONTINUED = "Continued on next page"   # 8pt 斜体，右对齐至 Balance 列右缘
P1_CONT_SHIFT = -5.67        # 流水跨页时第 1 页底部区块 + 页脚整体上移
NOTICE_FULL_DY = 19.845      # 通知页为完整页眉（0 基偶数页）时正文整体下移


@dataclass(frozen=True)
class _RenderContext:
    """单次生成请求的状态；辅助函数显式接收，避免请求间共享数据或文档。"""

    data: dict
    document: fitz.Document


# ---------------------------------------------------------------- 小工具
def money(v):
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def wrap_desc(text, limit=DESC_WRAP_W, size=9.0):
    """按字宽将描述自动换行（数据提供 desc_lines 时不使用）。"""
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if reg.text_length(trial, fontsize=size) <= limit or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def put(page, x, top, text, font, size, color=BLACK, align="l", edge=None):
    """以 bbox 顶边定位写一行字。align: l=左对齐 r=右对齐(edge=右缘) c=居中(edge=中心)"""
    w = font.text_length(text, fontsize=size)
    if align == "r":
        x = edge - w
    elif align == "c":
        x = edge - w / 2
    if font is bold:
        k, fname, ffile = K_BOLD, "AEB", FONT_BOLD
    elif font is ital:
        k, fname, ffile = K_ITAL, "AEI", FONT_ITAL
    else:
        k, fname, ffile = K_REG, "AER", FONT_REG
    page.insert_text(
        (x, top + k * size), text,
        fontname=fname, fontfile=ffile,
        fontsize=size, color=color,
    )


def rule(page, x0, x1, y, color=BLUE, width=1.5):
    page.draw_line(fitz.Point(x0, y), fitz.Point(x1, y), color=color, width=width)


def watermark(page):
    """页面中央绘制一个 45° 斜向 "SAMPLE" 大字水印。

    每页仅此一处（页面几何中心 306,396），最后绘制压在最上层；
    纯页面显示层，不涉及元数据。是否绘制由 build_pdf 的 with_watermark 决定。
    起点常量经 150dpi 像素级校准：旋转后墨迹外接框中心 = 页面中心
    （字形不对称使朴素居中公式偏 ~6pt，故 x 修正 -6.17 / y 系数 0.3683）。
    """
    size = 96
    w = bold.text_length("SAMPLE", fontsize=size)
    mat = fitz.Matrix(1, 0, 0, 1, 0, 0).prerotate(45)
    page.insert_text(
        (306 - w / 2 - 6.17, 396 + 0.3683 * size), "SAMPLE",
        fontname="AEB", fontfile=FONT_BOLD, fontsize=size,
        color=WM_GRAY, fill_opacity=0.45,
        morph=(fitz.Point(306, 396), mat),
    )


DEFAULT_BANK = {
    "name": "American Express",
    "legal": "American Express National Bank",
    "domain": "americanexpress.com",
}


def bank_of(data):
    """行名/法务实体/域名：默认保持原稿字样，数据给了就用数据。"""
    block = dict(DEFAULT_BANK)
    supplied = data.get("bank")
    if isinstance(supplied, dict):
        for key in block:
            value = supplied.get(key)
            if isinstance(value, str) and value.strip():
                block[key] = value.strip()
    return block


def footer(page, top=742.3, bank=None):
    bank = bank or DEFAULT_BANK
    put(page, 306, top, f"Accounts offered by {bank['legal']}. Member FDIC.",
        reg, 9, align="c", edge=306)


def header_full(context, pageno_text):
    """第 1 页页眉：Logo + 标题 + 姓名 + 信息行。"""
    page = context.document.new_page(width=PAGE_W, height=PAGE_H)
    page.insert_image(fitz.Rect(50.4, 26.406, 92.64, 69.126), filename=LOGO)
    bank = bank_of(context.data)
    put(page, 107.1, 22.3, f"{bank['name']}\u00ae Rewards Checking Statement",
        bold, 14, color=BLUE)
    put(page, 107.1, 42.0, context.data["customer_name"], reg, 9)
    put(page, 107.1, 56.0, "Statement Date:", bold, 9)
    put(page, 180.3, 56.1, " " + context.data["statement_date"] + " ", reg, 9)
    put(page, 251.1, 56.0, "Account Ending:", bold, 9)
    put(page, 323.6, 56.1, " " + context.data["account_ending"], reg, 9)
    put(page, 352.3, 56.0, " Account Name:", bold, 9)
    put(page, 426.7, 56.1, context.data["account_name"], reg, 9)
    put(page, 558.4, 28.3, pageno_text, reg, 9, align="r", edge=558.4)
    return page


def header_compact(context, page, pageno_text):
    """第 2 页起的页眉：姓名 + 信息行。"""
    put(page, 50.4, 25.2, context.data["customer_name"], reg, 9)
    put(page, 50.4, 39.2, "Statement Date:", bold, 9)
    put(page, 123.6, 39.3, " " + context.data["statement_date"] + " ", reg, 9)
    put(page, 194.4, 39.2, "Account Ending:", bold, 9)
    put(page, 266.9, 39.3, " " + context.data["account_ending"], reg, 9)
    put(page, 295.6, 39.2, " Account Name:", bold, 9)
    put(page, 370.1, 39.3, context.data["account_name"], reg, 9)
    put(page, 558.4, 28.3, pageno_text, reg, 9, align="r", edge=558.4)


# ---------------------------------------------------------------- 第 1 页区块
def draw_summary(context, page, totals):
    put(page, 50.4, 102.3, "Statement Summary", bold, 11, color=BLUE)
    rule(page, 50.4, 306.0, 118.52)
    put(page, 306.0, 102.3, "Interest Summary", bold, 11, color=BLUE)
    rule(page, 306.0, 561.6, 118.52)

    rows = [
        (125.1, f"Beginning Balance as of {context.data['period_start']}",
         money(context.data["beginning_balance"]), True),
        (139.4, "Total Credits This Period", money(totals["credits"]), False),
        (153.6, "Total Debits This Period", money(totals["debits"]), False),
        (167.6, f"Ending Balance as of {context.data['period_end']}",
         money(totals["ending"]), True),
    ]
    for top, label, value, is_bold in rows:
        f = bold if is_bold else reg
        put(page, 50.4 if is_bold else 67.4, top, label, f, 9)
        put(page, SUM_R, top, value, f, 9, align="r", edge=SUM_R)

    irows = [
        (125.1, "Earned Period", f"{context.data['period_start']} - {context.data['period_end']}", True),
        (139.4, "Days in Statement Period", str(context.data["days_in_period"]), False),
        (153.6, "Interest Rate", context.data["interest_rate"], False),
        (167.8, "Annual Percentage Yield Earned", context.data["apy"], False),
        (181.8, "Interest Earned This Period", money(totals["interest"]), True),
        (198.9, "Interest Paid This Year", money(context.data["interest_paid_ytd"]), False),
    ]
    for top, label, value, is_bold in irows:
        f = bold if is_bold else reg
        put(page, 306.0, top, label, f, 9)
        put(page, INT_R, top, value, f, 9, align="r", edge=INT_R)
    rule(page, 50.4, 561.6, 219.89)


def draw_table_header(page, top=266.5):
    put(page, 50.4, top, "Date", bold, 9)
    put(page, DESC_X, top, "Description", bold, 9)
    put(page, CRED_R, top, "Credits", bold, 9, align="r", edge=CRED_R)
    put(page, DEB_R, top, "Debits", bold, 9, align="r", edge=DEB_R)
    put(page, BAL_R, top, "Balance", bold, 9, align="r", edge=BAL_R)


def draw_row(page, row):
    """row: {top, date, desc_lines, id, credit, debit, balance}"""
    put(page, 50.4, row["top"], row["date"], reg, 9)
    for i, ln in enumerate(row["desc_lines"]):
        put(page, DESC_X, row["top"] + DESC_STEP * i, ln, reg, 9)
    if row.get("id"):
        put(page, DESC_X, row["top"] + DESC_STEP * (len(row["desc_lines"]) - 1) + ID_OFF,
            row["id"], reg, 6)
    if row["credit"] is not None:
        put(page, CRED_R, row["top"], money(row["credit"]), reg, 9,
            align="r", edge=CRED_R)
    if row["debit"] is not None:
        put(page, DEB_R, row["top"], money(row["debit"]), reg, 9,
            align="r", edge=DEB_R)
    put(page, BAL_R, row["top"], money(row["balance"]), reg, 9,
        align="r", edge=BAL_R)


def draw_balance_row(page, top, label, value):
    put(page, 50.4, top, label, bold, 9)
    put(page, BAL_R, top, value, bold, 9, align="r", edge=BAL_R)
    return top + 12.5


def draw_bottom_block(context, page, dy=0.0):
    """第 1 页底部：Account Details + Contact Us 双栏与边框盒。
    dy: 流水跨页时第 1 页整体上移量（P1_CONT_SHIFT）。"""
    put(page, 50.4, 547.2 + dy, "Account Details", bold, 11, color=BLUE)
    rule(page, 50.4, 321.336, 563.475 + dy)
    put(page, 331.56, 547.2 + dy, "Contact Us", bold, 11, color=BLUE)
    rule(page, 331.56, 561.6, 563.475 + dy)

    tops = [604.8, 618.9, 633.1, 647.3]
    for top, ln in zip(tops, [context.data["customer_name"]] + context.data["address_lines"]):
        put(page, 129.8, top + dy, ln, reg, 9)

    # 联系方式盒（原版为 4 条略有出头的独立线段，逐线复刻）
    for (x0, y0, x1, y1) in [
        (355.125, 601.2575, 562.052, 601.2575),
        (561.3435, 600.549, 561.3435, 665.743),
        (355.125, 665.0345, 562.052, 665.0345),
        (355.8335, 600.549, 355.8335, 665.743),
    ]:
        page.draw_line(fitz.Point(x0, y0 + dy), fitz.Point(x1, y1 + dy),
                       color=BLACK, width=1.417)
    b = "24/7 Account Access. World-Class Service"
    put(page, 362.2, 605.4 + dy, b, bold, 8)
    put(page, 362.2 + bold.text_length(b, fontsize=8), 605.6 + dy, ".", reg, 8)
    # 虚构行名的域名可能比原稿长，盒子宽度固定，按可用宽度缩字号（最小 6pt）
    url = f"{bank_of(context.data)['domain']}/rewardscheckingdashboard"
    # 盒子右内边 561.34；留 2pt 余量给原版线条的出头
    width = reg.text_length(url, fontsize=8)  # 用真实字体的度量，别拿 Helvetica 估
    limit = 8.0 * (561.3435 - 3.0 - 362.2) / max(width, 1e-6)
    put(page, 362.2, 619.7 + dy,
        f"{bank_of(context.data)['domain']}/rewardscheckingdashboard", reg,
        max(6.0, min(8.0, limit)))
    put(page, 362.2, 633.9 + dy, "1-877-221-2639", reg, 8)
    put(page, 362.2, 648.1 + dy, "P.O. Box 31492, Salt Lake City, UT 84131", reg, 8)

    # 积分提示盒（同上，4 条线段）
    for (x0, y0, x1, y1) in [
        (355.125, 676.3755, 562.052, 676.3755),
        (561.3435, 675.667, 561.3435, 726.688),
        (355.125, 725.9795, 562.052, 725.9795),
        (355.8335, 675.667, 355.8335, 726.688),
    ]:
        page.draw_line(fitz.Point(x0, y0 + dy), fitz.Point(x1, y1 + dy),
                       color=BLACK, width=1.417)
    for top, ln in zip(
        [680.7, 694.9, 709.0],
        ["To view your point balance or to learn more",
         "about the Membership Rewards\u00ae Program,",
         "visit membershiprewards.com"],
    ):
        put(page, 458.55, top + dy, ln, reg, 8, align="c", edge=458.55)


def draw_notice(page, dy=0.0):
    """Important Notice（固定法律样板文字，按原版断行逐行排）。
    dy: 通知页为完整页眉时的整体下移量（NOTICE_FULL_DY）。"""
    put(page, 50.4, 88.1 + dy, "Important Notice", bold, 11, color=BLUE)
    rule(page, 50.4, 561.6, 104.346 + dy)
    put(page, 50.4, 104.9 + dy,
        "In Case of Errors or Questions About Your Electronic Transfers:", bold, 9)
    body = [
        (50.4, 119.2, "Direct inquiries to 1-877-221-2639 or write us at P.O. Box "
                      "31492 Salt Lake City, UT 84131 as soon as you can, if you think "),
        (50.4, 133.4, "your statement or receipt is wrong or if you need more "
                      "information about a transfer on the statement or receipt. We must "),
        (50.4, 147.6, "hear from you no later than 60 days after we sent you the "
                      "first statement on which the error or problem appeared."),
        (67.4, 160.1, "(1) Tell us your name and account number."),
        (67.4, 176.5, "(2) Describe the error or the transfer you are unsure about, "
                      "and explain as clearly as you can why you believe it is an "),
        (84.4, 187.3, "error or why you need more information."),
        (67.4, 203.8, "(3) Tell us the dollar amount of the suspected error."),
        (50.4, 222.0, "We will investigate your complaint and will correct any error "
                      "promptly. If we take more than 10 business days to do this, we "),
        (50.4, 236.1, "will credit your account for the amount you think is in error, "
                      "so that you will have the use of the money during the time it "),
        (50.4, 250.3, "takes us to complete our investigation."),
    ]
    for x, top, ln in body:
        put(page, x, top + dy, ln, reg, 9)
    rule(page, 50.4, 561.6, 279.437 + dy)


# ---------------------------------------------------------------- 核心 API
META_KEYS = ("title", "author", "subject", "keywords",
             "producer", "creator", "creationDate")

_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")


def normalize_pdf_date(v):
    """把创建时间规范化为 PDF 日期串。

    接受 "YYYY-MM-DD HH:MM:SS"（按 UTC 处理，"现在" 按钮生成的格式）、
    "YYYY-MM-DDTHH:MM:SS"（ISO）或 "D:..." 原生 PDF 日期串；
    无效输入返回 ""（即不写入）。
    """
    v = str(v or "").strip()
    if not v or v.startswith("D:"):
        return v
    m = _DATE_RE.fullmatch(v.rstrip("Zz"))
    if not m:
        return ""
    try:
        datetime.datetime(*(int(x) for x in m.groups()))  # 校验合法性
    except ValueError:
        return ""
    return "D:" + "".join(m.groups()) + "Z"


def pdf_date_to_xmp(v):
    """PDF 日期串 "D:YYYYMMDDHHmmSSZ" -> XMP ISO "YYYY-MM-DDTHH:MM:SSZ"。"""
    m = re.fullmatch(r"D:(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})Z?",
                     str(v or "").strip())
    if not m:
        return ""
    y, mo, d, h, mi, s = m.groups()
    return f"{y}-{mo}-{d}T{h}:{mi}:{s}Z"


# ---------------------------------------------------------------- 元数据默认值
# 与原稿（JUN/APR 实测）Info 字典一致；修改时间(modDate)一律不写。
META_DEFAULTS = {'title': 'American Express Statement',
 'author': 'American Express',
 'subject': '',
 'producer': 'Smart Communications - SC32'}

XMP_KEYS = ("dc:title", "dc:creator", "dc:description", "dc:language",
            "dc:date", "pdf:Producer", "xmp:CreateDate", "xmp:MetadataDate")

# ---------------------------------------------------------------- 加密默认值
# 复刻原稿加密方式与权限：Standard V2 R3 128-bit RC4，权限位 P=-1804
# （允许打印/复制/修改/注释/表单填写，禁止文档组装与高质量打印），
# 用户/属主口令均为空（自由打开）。加密在保存/序列化时生效。
ENCRYPTION_DEFAULTS = {
    "encryption": fitz.PDF_ENCRYPT_RC4_128,
    "owner_pw": '',
    "user_pw": '',
    "permissions": -1804,
}


def save_pdf(document, path, encrypt=True, **kw):
    """保存 PDF；encrypt=True（默认）按原稿同款加密。"""
    kw.setdefault("garbage", 4)
    kw.setdefault("deflate", True)
    opts = dict(ENCRYPTION_DEFAULTS) if encrypt else {}
    opts.update(kw)
    document.save(path, **opts)


def pdf_bytes(document, encrypt=True, **kw):
    """序列化为 bytes；encrypt=True（默认）按原稿同款加密。"""
    kw.setdefault("garbage", 4)
    kw.setdefault("deflate", True)
    opts = dict(ENCRYPTION_DEFAULTS) if encrypt else {}
    opts.update(kw)
    return document.tobytes(**opts)


def statement_date_creation(statement_date, rng=None):
    """按原稿规律生成创建时间：账单日的次日 18:00-19:00，分秒随机（UTC）。

    statement_date 为 MM/DD/YYYY（数据集/编辑器格式）；无法解析返回 ""。
    原稿实测：账单日 06/30/2026 -> D:20260701184435Z。
    """
    import random as _random
    rng = rng or _random
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", str(statement_date or "").strip())
    if not m:
        return ""
    try:
        d0 = datetime.datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return ""
    d1 = d0 + timedelta(days=1)
    return (f"D:{d1.year:04d}{d1.month:02d}{d1.day:02d}"
            f"18{rng.randrange(60):02d}{rng.randrange(60):02d}Z")


def normalize_xmp_date(v):
    """XMP 日期规范化为 ISO（接受可读 / D: / ISO 形式）；无效返回 ""。"""
    pdfd = normalize_pdf_date(v)
    if not pdfd:
        return ""
    if not pdfd.startswith("D:"):
        return str(v).strip()
    return pdf_date_to_xmp(pdfd)


def build_xmp(v):
    """按原稿结构生成 XMP（含 xpacket / pdf:PDFVersion / pdfuaid:part）。

    v 键：dc:title / dc:creator / dc:description / dc:language / dc:date /
    pdf:Producer / xmp:CreateDate / xmp:MetadataDate；空值元素整段省略。
    """
    from xml.sax.saxutils import escape as _esc

    def el(name, val):
        val = str(val or "").strip()
        return f"<{name}>{_esc(val)}</{name}>\n" if val else ""

    dc = (el("dc:description", v.get("dc:description"))
          + el("dc:creator", v.get("dc:creator"))
          + el("dc:title", v.get("dc:title"))
          + el("dc:language", v.get("dc:language"))
          + el("dc:date", v.get("dc:date")))
    pdf = (el("pdf:Producer", v.get("pdf:Producer"))
           + "<pdf:PDFVersion>1.7</pdf:PDFVersion>\n")
    xm = (el("xmp:MetadataDate", v.get("xmp:MetadataDate"))
          + el("xmp:CreateDate", v.get("xmp:CreateDate")))
    parts = []
    if dc:
        parts.append('<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/" '
                     'rdf:about="">\n' + dc + "</rdf:Description>\n")
    if pdf:
        parts.append('<rdf:Description xmlns:pdf="http://ns.adobe.com/pdf/1.3/" '
                     'rdf:about="">\n' + pdf + "</rdf:Description>\n")
    parts.append('<rdf:Description xmlns:pdfuaid="http://www.aiim.org/pdfua/ns/id/" '
                 'rdf:about="">\n<pdfuaid:part>1</pdfuaid:part>\n</rdf:Description>\n')
    if xm:
        parts.append('<rdf:Description xmlns:xmp="http://ns.adobe.com/xap/1.0/" '
                     'rdf:about="">\n' + xm + "</rdf:Description>\n")
    return ("<?xpacket begin=\"\ufeff\" id=\"W5M0MpCehiHzreSzNTczkc9d\"?>\n\n"
            "<x:xmpmeta xmlns:x=\"adobe:ns:meta/\">\n"
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
            + "".join(parts)
            + "</rdf:RDF>\n</x:xmpmeta>\n<?xpacket end=\"r\"?>\n")


def build_pdf(data, dataset_label="custom", with_watermark=True, metadata=None,
              xmp=None):
    """按数据字典生成对账单 PDF，返回 fitz.Document，不写盘。

    with_watermark=True 时每页页面中央绘制一个 45° "SAMPLE" 大字水印；
    水印纯为页面显示层，与元数据完全无关。

    metadata：Info 字典元数据（键见 META_KEYS）。
      - None（默认）→ 预填 META_DEFAULTS（原稿同款标题/作者/主题/生成软件）；
      - 传入字典   → 仅写入非空字段（留空 = 不写入）；
      - creationDate 留空时按原稿规则自动生成：账单日次日 18:00-19:00
        分秒随机（UTC，statement_date_creation）；
      - 修改时间(modDate)一律不写。

    xmp：XMP 元数据字典（键见 XMP_KEYS，即 dc:title / dc:creator /
      dc:description / dc:language / dc:date / pdf:Producer /
      xmp:CreateDate / xmp:MetadataDate）。
      - None（默认）→ 由 Info 值派生（dc:date / xmp:CreateDate /
        xmp:MetadataDate 与 Info creationDate 同一时刻）；
      - 传入字典   → 非空字段覆盖派生默认值，日期规范化为 ISO。

    加密：原稿同款（Standard V2 R3 128-bit RC4，P=-1804，空口令）在
    保存时生效——用 save_pdf() / pdf_bytes() 序列化即默认加密；
    传 encrypt=False 或自行 save 可输出不加密版本。
    """
    context = _RenderContext(data=data, document=fitz.open())
    try:
        return _build_pdf(context, with_watermark, metadata, xmp)
    except BaseException:
        if not context.document.is_closed:
            context.document.close()
        raise


def _build_pdf(context, with_watermark, metadata, xmp):
    """绘制请求；成功后由调用方持有文档，异常由 build_pdf 释放。"""
    data = context.data
    doc = context.document

    txns = data["transactions"]
    totals = {
        "credits": sum(t["amount"] for t in txns if t["amount"] > 0),
        "debits": sum(t["amount"] for t in txns if t["amount"] < 0),
        "interest": sum(t["amount"] for t in txns if t.get("interest")),
        "ending": data["beginning_balance"] + sum(t["amount"] for t in txns),
    }

    # 构造交易行（几何待分页后填充）
    rows, bal = [], data["beginning_balance"]
    for t in txns:
        row = {
            "top": None, "page": None, "date": t["date"],
            "desc_lines": t.get("desc_lines") or wrap_desc(t.get("desc", "")),
            "id": t.get("id"),
            "credit": t["amount"] if t["amount"] > 0 else None,
            "debit": t["amount"] if t["amount"] < 0 else None,
        }
        bal += t["amount"]
        row["balance"] = bal
        rows.append(row)

    # --- 预计算分页（先于任何绘制，保证页码一次成型） ---
    def row_anchor(row, top):
        """行内容锚点：有 ID 为 ID 小字行 top，否则为描述末行 top。"""
        return top + DESC_STEP * (len(row["desc_lines"]) - 1) + (ID_OFF if row["id"] else 0)

    def plan_pagination():
        """返回 (期末余额页码, 期末余额 top, {页码: Continued 标记 top})。

        换页判定（JUN 原稿实测）：某行放入后，若其后紧跟的期末余额行 +
        收尾横线无法容纳在本页下限内，则该行移入下一页；被截断的页面
        在末条内容锚点下方 CONT_OFF 处排 "Continued on next page"。
        """
        markers = {}
        if not rows:  # 无交易：期末行紧随期初行
            return 0, 286.4 + 12.5 + 7.45, markers
        cur, top, first_on_page = 0, 286.4 + 12.5 + GAP_NO_ID, True
        anchor = 0.0
        for row in rows:
            def fits(t):
                a = row_anchor(row, t)
                return a, a + (ADV_END_ID if row["id"] else ADV_END_NOID) \
                    + 12.5 + END_RULE <= (P1_TABLE_LIMIT if cur == 0 else PN_TABLE_LIMIT)
            anchor, ok = fits(top)
            if not ok and not first_on_page:
                markers[cur] = anchor_last + CONT_OFF
                cur += 1
                top = PN_FIRST_TOP
                anchor = row_anchor(row, top)
            row["top"], row["page"] = top, cur
            anchor_last = anchor
            top = anchor + (ADV_ID if row["id"] else ADV_NOID)
            first_on_page = False
        # 期末余额行
        end_page = rows[-1]["page"]
        end_top = anchor_last + (ADV_END_ID if rows[-1]["id"] else ADV_END_NOID)
        if end_top + 12.5 + END_RULE > (P1_TABLE_LIMIT if end_page == 0
                                        else PN_TABLE_LIMIT):
            markers[end_page] = anchor_last + CONT_OFF
            end_page += 1
            end_top = PN_FIRST_TOP
        return end_page, end_top, markers

    end_page, end_top, markers = plan_pagination()
    N = end_page + 2  # 活动表最后一页 + Important Notice 独立页

    # --- 第 1 页 ---
    header_full(context, f"p. 1/{N}")
    draw_summary(context, doc[0], totals)
    put(doc[0], 50.4, 246.9, "Account Activity", bold, 11, color=BLUE)
    rule(doc[0], 50.4, 561.6, 263.165)
    draw_table_header(doc[0])
    draw_balance_row(doc[0], 286.4, f"{data['period_start']} Beginning Balance",
                     money(data["beginning_balance"]))

    # --- 续页：紧凑页眉 + 表头（无标题、无横线，同 JUN 原稿） ---
    for pi in range(1, end_page + 1):
        pg = doc.new_page(width=PAGE_W, height=PAGE_H)
        header_compact(context, pg, f"p. {pi + 1}/{N}")
        draw_table_header(pg, PN_HEADER_TOP)

    # --- 交易行 ---（绘制时按索引取页，避免页对象因文档变更失效）
    for row in rows:
        draw_row(doc[row["page"]], row)

    # --- "Continued on next page"（8pt 斜体，右对齐 Balance 列右缘） ---
    for pno, mtop in markers.items():
        put(doc[pno], 0, mtop, CONTINUED, ital, 8, align="r", edge=BAL_R)

    # --- 期末余额行 + 收尾横线 ---
    end_bottom = draw_balance_row(doc[end_page], end_top,
                                  f"{data['period_end']} Ending Balance",
                                  money(totals["ending"]))
    rule(doc[end_page], 50.4, 561.6, end_bottom + END_RULE)

    # --- 第 1 页底部区块 + 页脚（流水跨页时整体上移，同 JUN 原稿） ---
    dy = P1_CONT_SHIFT if end_page >= 1 else 0.0
    draw_bottom_block(context, doc[0], dy)
    footer(doc[0], 742.3 + dy, bank_of(data))

    # --- Important Notice 独立成页 ---
    # 页眉随页码奇偶切换（JUN 第 3 页完整页眉 / APR 第 2 页紧凑页眉）：
    # 0 基偶数页用完整页眉，且通知正文整体下移 NOTICE_FULL_DY。
    if (N - 1) % 2 == 0:
        header_full(context, f"p. {N}/{N}")           # 创建新页（含 Logo 与 14pt 标题）
        draw_notice(doc[N - 1], NOTICE_FULL_DY)
    else:
        pn = doc.new_page(width=PAGE_W, height=PAGE_H)
        header_compact(context, pn, f"p. {N}/{N}")
        draw_notice(pn)

    # --- 页脚（第 1 页已画）+ 水印（可选） ---
    for i, pg in enumerate(doc):
        if i != 0:
            footer(pg, bank=bank_of(data))
        if with_watermark:
            watermark(pg)

    if metadata is None:
        meta = dict(META_DEFAULTS)
    else:
        meta = {k: str(metadata.get(k) or "").strip()
                for k in META_KEYS if str(metadata.get(k) or "").strip()}
    # 创建时间：留空则按原稿规则自动生成（账单日次日 18-19 点随机 UTC）
    meta["creationDate"] = normalize_pdf_date(meta.get("creationDate", "")) \
        or statement_date_creation(data.get("statement_date"))
    if not meta["creationDate"]:
        meta.pop("creationDate", None)
    meta.pop("modDate", None)  # 修改时间一律不写
    doc.set_metadata(meta)

    # XMP 元数据（结构镜像原稿）：默认由 Info 派生，可用 xmp 字典覆盖
    xv = {
        "dc:title": meta.get("title", ""),
        "dc:creator": meta.get("author", ""),
        "dc:description": meta.get("subject", ""),
        "dc:language": "en",
        "dc:date": pdf_date_to_xmp(meta.get("creationDate", "")),
        "pdf:Producer": meta.get("producer", ""),
        "xmp:CreateDate": pdf_date_to_xmp(meta.get("creationDate", "")),
        "xmp:MetadataDate": pdf_date_to_xmp(meta.get("creationDate", "")),
    }
    if xmp:
        for k in XMP_KEYS:
            v2 = str(xmp.get(k) or "").strip()
            if not v2:
                continue
            if k in ("dc:date", "xmp:CreateDate", "xmp:MetadataDate"):
                v2 = normalize_xmp_date(v2)
                if not v2:
                    continue
            xv[k] = v2
    doc.set_xml_metadata(build_xmp(xv))

    # 嵌入字体名对齐原稿（仅 /BaseFont 名字）
    _rename_embedded_fonts(doc)
    return doc


# ---------------------------------------------------------------- 字体名改写
# 输出 PDF 嵌入字体名与原稿对齐（仅改写 /BaseFont 名字字符串，字体程序、
# 字形、度量均不变）。斜体按需嵌入：仅实际绘制跨页标记时才进 PDF。
# TTF 内部名已同步为原稿干净名（BentonSans Bold / BentonSans Regular /
# BentonSans Regular Italic），/BaseFont 在其上加子集前缀（同原稿分层）。
_FONT_RENAME = {
    "Benton Sans AE Reconstructed Bo": "EAAAAB+BentonSans-Bold",   # 旧内部名（备份兼容）
    "Benton Sans AE Reconstructed Re": "EAAAAC+BentonSans-Regular",
    "BentonSans Bold": "EAAAAB+BentonSans-Bold",
    "BentonSans Regular": "EAAAAC+BentonSans-Regular",
    "BentonSans Regular Italic": "EAAAAA+BentonSans-RegularItalic",
}


def _rename_embedded_fonts(document):
    """把嵌入字体的 /BaseFont 改为原稿同名（仅名字，不改其他任何内容）。"""
    done = set()
    for page in document:
        for f in page.get_fonts(full=True):
            xref = f[0]
            if xref in done:
                continue
            done.add(xref)
            t, v = document.xref_get_key(xref, "BaseFont")
            if t == "name":
                new = _FONT_RENAME.get(v.lstrip("/"))
                if new:
                    document.xref_set_key(xref, "BaseFont", "/" + new)


# ---------------------------------------------------------------- 命令行
def main():
    args = sys.argv[1:]
    dataset = "demo"
    if "--dataset" in args:
        dataset = args[args.index("--dataset") + 1]
        if dataset not in DATASETS:
            raise SystemExit(f"unknown dataset: {dataset} ({'|'.join(DATASETS)})")
    data = copy.deepcopy(DATASETS[dataset])
    output = os.path.join(
        BASE, {"original": "AE_replica_test.pdf",
               "jun": "AE_jun_replica_test.pdf"}.get(dataset, "AE_sample_statement.pdf"))

    if "--stress" in args:  # 追加随机交易，验证分页
        import random
        random.seed(42)
        n = int(args[args.index("--stress") + 1])
        for i in range(n):
            data["transactions"].append({
                "date": f"04/{random.randint(2, 29):02d}/2026",
                "desc": f"Stress Test Transaction {i + 1} Demo Merchant Ref {1000 + i}",
                "id": f"ID {900000000000000 + i}",
                "amount": round(random.uniform(-80, 300), 2),
            })
        output = os.path.join(BASE, "AE_sample_statement_stress.pdf")

    with_watermark = "--no-watermark" not in args
    encrypt = "--no-encrypt" not in args
    d = build_pdf(data, dataset, with_watermark=with_watermark)
    save_pdf(d, output, encrypt=encrypt)
    print(f"OK [{dataset}] -> {output}  pages={d.page_count}"
          f"  watermark={'on' if with_watermark else 'off'}"
          f"  encrypt={'on' if encrypt else 'off'}")


if __name__ == "__main__":
    main()
