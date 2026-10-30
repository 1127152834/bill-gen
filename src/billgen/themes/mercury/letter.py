"""Mercury 账户验证信（account verification letter）模板。

版式全部按参考件 `~/Downloads/mercury-bank-letter-7658.pdf` 量出来的坐标复刻：
A4/US-Letter 612×792、页眉徽标、三条分隔细线、三节表格、页脚说明。
可变内容（日期、公司名、签署人、地址、开户日、路由号、账号、银行信息）全部由
`data` 决定，字体用 ArcadiaText（360 正文 / 480 强调），见 `assets/letter/`。

为什么用「空白信纸 + 重绘文字」而不是直接改参考件：
参考件的文本层里留着别人的公司名和账号，只覆盖不擦除，search/复制 还能捞到；
`tools/make_mercury_letter_base.py` 先把那些值从内容流里真正删掉，得到干净底稿。

用法：

    from billgen.themes.mercury.letter import build, DEFAULT_DATA
    pdf = build()                       # 用默认数据
    pdf = build({"company": {"name": "Acme LLC"}})   # 只改一个字段

命令行（改内容最省事的方式）：

    python -m billgen.themes.mercury.letter --data letter.json --out letter.pdf --png letter.png
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date as _date, datetime
from typing import Any, Iterable

import fitz

BASE = os.path.dirname(os.path.abspath(__file__))
LETTER_DIR = os.path.join(BASE, "assets/letter")
BASE_PDF = os.path.join(LETTER_DIR, "verification_letter_blank.pdf")
FONT_TEXT = os.path.join(LETTER_DIR, "ArcadiaText-360.ttf")
FONT_EMPH = os.path.join(LETTER_DIR, "ArcadiaText-480.ttf")

# ---- 版式常量（全部量自参考件，别凭感觉改）---------------------------------
PAGE_W, PAGE_H = 612.0, 792.0
LEFT = 40.0
RIGHT = 572.0
VALUE_X = 329.6          # 「值」列的左边界
LABEL_X = 156.0          # 行标签的左边界
SIZE = 10.0

DATE_BL = 109.5          # 日期
SALUTE_BL = 137.5        # To Whom It May Concern:
BODY_BL = 165.5          # 正文首行
BODY_STEP = 16.0
BODY_W = RIGHT - LEFT

SECTION_BL = {"customer": 238.5, "account": 327.5, "bank": 424.5}
ROW_STEP = 24.0          # 同一节里下一行标签的基线间距
LINE_STEP = 16.0         # 同一个值换行的基线间距

SINCERE_BL = 496.5
SIGNER_BL = 568.5
TITLE_BL = 584.5
FOOTER_BL = 769.5
FOOTER_X = 174.05

C_TEXT = (0x36 / 255, 0x36 / 255, 0x44 / 255)      # #363644
C_MUTED = (0x71 / 255, 0x71 / 255, 0x8E / 255)     # #71718E
C_FOOTER = (0x53 / 255, 0x54 / 255, 0x61 / 255)    # #535461

# 签名区（参考件里那笔手写体：40.3,511.0 - 116.0,551.3）
SIG_RECT = (38.0, 507.0, 122.0, 555.0)

# 正文模板。公司名单独成 run，用强调字体，宽度按实际算，不许留空洞。
BODY_HEAD = "This letter is to verify that "
BODY_TAIL = (
    " is a customer of Column N.A., Member FDIC, through Mercury Technologies, Inc. "
    '("Mercury"). Mercury is providing this letter in its capacity as a service provider.'
)

# 参考件（`~/Downloads/mercury-bank-letter-7658.pdf`）的页脚是静态的，底稿里已经印着；
# 数据里的 footer 与它相同就别再画一遍，否则文本层会出现两遍同一句话。
BASE_FOOTER = "Banking services provided by Column N.A., Member FDIC."

DEFAULT_DATA: dict[str, Any] = {
    "date": "October 28, 2026",
    "salutation": "To Whom It May Concern:",
    "company": {
        "name": "Liroa Commerce",
        "signatory": "Zhenjie Li",
        "address": ["11119 Freer Avenue", "Temple City, CA 91780"],
    },
    "account": {
        "opened": "October 28, 2026",
        "routing": "121145433",
        "number": "398138140817658",
    },
    "bank": {
        "name": "Column N.A.",
        "address": ["1 Letterman Drive, Building A, Suite A4-700", "San Francisco, CA 94129"],
    },
    "signer": {"name": "Jason Zhang", "title": "COO, Mercury", "signature": True},
    "footer": "Banking services provided by Column N.A., Member FDIC.",
    "watermark": False,
}

# 值列能用的宽度，以及每个字段最多几行（超了就缩字号，再超就报错）
VALUE_W = RIGHT - VALUE_X
MAX_LINES = {
    "signatory": 1,
    "company_address": 2,
    "opened": 1,
    "routing": 1,
    "number": 1,
    "bank_name": 1,
    "bank_address": 2,
}

# 报错时说人话：给人看的是「公司地址」，不是 JSON 里的键名
FIELD_LABELS = {
    "signatory": "账户签署人",
    "company_address": "公司地址",
    "opened": "开户日期",
    "routing": "路由号",
    "number": "账号",
    "bank_name": "银行名",
    "bank_address": "银行地址",
}


# ---- 小工具 -----------------------------------------------------------------
def _font(path: str) -> fitz.Font:
    key = f"_font_{path}"
    cached = getattr(_font, key, None)
    if cached is None:
        cached = fitz.Font(fontfile=path)
        setattr(_font, key, cached)
    return cached


def _width(text: str, size: float, font_path: str) -> float:
    return _font(font_path).text_length(text, size)


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def _money_free(text: str) -> str:
    """别让 `$` / 千分位混进账号、路由号这类字段。"""
    return text.replace("$", "").replace(",", "").strip()


def format_date(value: Any) -> str:
    """`2026-10-28` / `2026/10/28` → `October 28, 2026`；已经是人话就原样返回。"""
    text = _as_text(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            stamp = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return f"{stamp.strftime('%B')} {stamp.day}, {stamp.year}"
    return text


def _lines(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part for part in value.split("\n") if part.strip()]
    if isinstance(value, Iterable):
        return [_as_text(item) for item in value if _as_text(item).strip()]
    return [_as_text(value)]


def deep_merge(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    """把用户给的零散字段并进默认数据（只给 `company.name` 也行）。"""
    out = json.loads(json.dumps(base))
    if not override:
        return out
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _wrap(runs: list[tuple[str, str]], size: float, width: float) -> list[list[tuple[str, str, float]]]:
    """按词贪心折行。返回 [[(词, 字体, 词前空格宽), …], …]。

    中英混排不在这里处理：这是英文信，折行只认空格。
    """
    lines: list[list[tuple[str, str, float]]] = [[]]
    used = 0.0
    for text, font in runs:
        for token in text.split():
            # 词间空格统一由「当前词的字体」算一次，别让 run 边界的空格叠成两个

            space = _width(" ", size, font) if lines[-1] else 0.0
            need = _width(token, size, font)
            if lines[-1] and used + space + need > width:
                lines.append([])
                used = 0.0
                space = 0.0
            lines[-1].append((token, font, space))
            used += space + need
    return [line for line in lines if line]


def _fit_size(text: str, font: str, size: float, width: float, *, floor: float = 7.0) -> float:
    """单行字段放不下就一路缩字号，缩到 floor 还不行就交给调用方报错。"""
    while size > floor and _width(text, size, font) > width:
        size -= 0.25
    return size


def _draw_runs(page: fitz.Page, x: float, baseline: float, line: list[tuple[str, str, float]],
               size: float, color: tuple[float, float, float]) -> None:
    for word, font, space in line:
        if space:
            x += space
        page.insert_text((x, baseline), word, fontname=os.path.basename(font).split(".")[0],
                         fontfile=font, fontsize=size, color=color)
        x += _width(word, size, font)


def _draw_line(page: fitz.Page, x: float, baseline: float, text: str, font: str,
               size: float, color: tuple[float, float, float]) -> None:
    page.insert_text((x, baseline), text, fontname=os.path.basename(font).split(".")[0],
                     fontfile=font, fontsize=size, color=color)


# ---- 骨架 -------------------------------------------------------------------
def _load_base() -> bytes:
    with open(BASE_PDF, "rb") as handle:
        return handle.read()


def _base_text() -> str:
    """底稿里已经印好的静态文字（判断「这句话底稿有没有」用，结果缓存）。"""
    cached = getattr(_base_text, "_cache", None)
    if cached is None:
        with fitz.open(stream=_load_base(), filetype="pdf") as doc:
            cached = " ".join(doc[0].get_text().split())
        _base_text._cache = cached
    return cached


def _document() -> fitz.Document:
    return fitz.open(stream=_load_base(), filetype="pdf")


def build(data: dict[str, Any] | None = None, *, watermark: bool | None = None) -> bytes:
    """按 data 出一封信，返回 PDF 字节。data 只写要改的字段即可。"""
    doc = _document()
    page = doc[0]
    merged = deep_merge(DEFAULT_DATA, data)

    company = merged.get("company") or {}
    account = merged.get("account") or {}
    bank = merged.get("bank") or {}
    signer = merged.get("signer") or {}

    # 日期
    _draw_line(page, LEFT, DATE_BL, format_date(merged.get("date")), FONT_TEXT, SIZE, C_MUTED)

    # 称呼
    _draw_line(page, LEFT, SALUTE_BL, _as_text(merged.get("salutation")), FONT_TEXT, SIZE, C_TEXT)

    # 正文（公司名换强调字体，折行按真实宽度算）
    runs = [
        (BODY_HEAD, FONT_TEXT),
        (_as_text(company.get("name")).strip(), FONT_EMPH),
        (BODY_TAIL, FONT_TEXT),
    ]
    body_size = SIZE
    lines = _wrap(runs, body_size, BODY_W)
    while len(lines) > 3 and body_size > 8.5:
        body_size -= 0.5
        lines = _wrap(runs, body_size, BODY_W)
    if len(lines) > 3:
        raise ValueError("公司名太长：正文折行超过 3 行，会压到下面的分隔线")
    for index, line in enumerate(lines):
        _draw_runs(page, LEFT, BODY_BL + index * BODY_STEP, line, body_size, C_TEXT)

    # 三节表格：标签是底稿里印好的，这里只画值
    rows: list[tuple[str, str, list[str]]] = [
        ("customer", "signatory", [_as_text(company.get("signatory")).strip()]),
        ("customer", "company_address", _lines(company.get("address"))),
        ("account", "opened", [format_date(account.get("opened"))]),
        ("account", "routing", [_money_free(_as_text(account.get("routing")))]),
        ("account", "number", [_money_free(_as_text(account.get("number")))]),
        ("bank", "bank_name", [_as_text(bank.get("name")).strip()]),
        ("bank", "bank_address", _lines(bank.get("address"))),
    ]
    baseline = dict(SECTION_BL)
    for section, key, values in rows:
        values = [value for value in values if value]
        limit = MAX_LINES[key]
        if not values:
            baseline[section] += ROW_STEP
            continue
        size = SIZE
        wrapped: list[list[str]] = []
        for value in values:
            size = min(size, _fit_size(value, FONT_TEXT, size, VALUE_W))
        wrapped = values
        if len(wrapped) > limit:
            raise ValueError(
                f"{FIELD_LABELS[key]}给了 {len(wrapped)} 行，版式只放得下 {limit} 行（超了会压到分隔线）"
            )
        while _width(max(wrapped, key=len), size, FONT_TEXT) > VALUE_W and size > 7.0:
            size -= 0.25
        if _width(max(wrapped, key=len), size, FONT_TEXT) > VALUE_W:
            raise ValueError(
                f"{FIELD_LABELS[key]}的值太长，缩到 7pt 仍放不进值列：{max(wrapped, key=len)!r}"
            )
        top = baseline[section]
        for index, value in enumerate(wrapped):
            _draw_line(page, VALUE_X, top + index * LINE_STEP, value, FONT_TEXT, size, C_TEXT)
        baseline[section] = top + ROW_STEP + LINE_STEP * (len(wrapped) - 1)

    # 签署
    _draw_line(page, LEFT, SINCERE_BL, "Sincerely,", FONT_TEXT, SIZE, C_TEXT)
    if not signer.get("signature", True):
        page.draw_rect(fitz.Rect(SIG_RECT), color=None, fill=(1.0, 1.0, 1.0))
    _draw_line(page, LEFT, SIGNER_BL, _as_text(signer.get("name")), FONT_EMPH, SIZE, C_TEXT)
    _draw_line(page, LEFT, TITLE_BL, _as_text(signer.get("title")), FONT_TEXT, SIZE, C_TEXT)

    # 页脚
    footer = _as_text(merged.get("footer"))
    if footer != BASE_FOOTER:
        # 底稿里那行是参考件的静态页脚，已经印好了；不等才擦掉重画，
        # 否则同一句话会进两次文本层（搜 FDIC 命中两条，复制出来也是两遍）。
        if BASE_FOOTER in _base_text():
            page.add_redact_annot(fitz.Rect(FOOTER_X - 2.0, FOOTER_BL - 11.0, RIGHT, FOOTER_BL + 4.0))
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                                  graphics=fitz.PDF_REDACT_LINE_ART_NONE)
        if footer:
            _draw_line(page, FOOTER_X, FOOTER_BL, footer, FONT_TEXT, SIZE, C_FOOTER)

    show_watermark = merged.get("watermark") if watermark is None else watermark
    if show_watermark:
        page.insert_text((170.0, 700.0), "SAMPLE", fontname="helv", fontsize=64.0,
                         color=(0.93, 0.93, 0.95), rotate=0)

    # 参考件的 PDF 元数据是 react-pdf 生成的（producer/creator 都是 "react-pdf"，
    # title/author 为空）；不设的话 PyMuPDF 会留空，`pdfinfo` 一看就知道换了工具。
    doc.set_metadata({"producer": "react-pdf", "creator": "react-pdf",
                      "title": "", "author": "", "subject": "", "keywords": ""})

    pdf = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return pdf


def save(path: str, data: dict[str, Any] | None = None, *, watermark: bool | None = None,
         png: str | None = None) -> str:
    """落盘；给 `png` 就顺手出一张预览图。"""
    pdf = build(data, watermark=watermark)
    with open(path, "wb") as handle:
        handle.write(pdf)
    if png:
        with fitz.open(stream=pdf, filetype="pdf") as doc:
            doc[0].get_pixmap(dpi=144).save(png)
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="Mercury 账户验证信：按 JSON 改内容后出 PDF")
    ap.add_argument("--data", help="JSON 文件；只写要改的字段即可")
    ap.add_argument("--out", default="mercury-verification-letter.pdf")
    ap.add_argument("--png", help="同时导出一张 PNG 预览")
    ap.add_argument("--watermark", action="store_true", help="加 SAMPLE 水印（默认不加）")
    ap.add_argument("--show-defaults", action="store_true",
                    help="把默认数据打成 JSON 打到标准输出，拿去当模板改")
    args = ap.parse_args()

    if args.show_defaults:
        print(json.dumps(DEFAULT_DATA, ensure_ascii=False, indent=2))
        return

    payload: dict[str, Any] = {}
    if args.data:
        with open(args.data, encoding="utf-8") as handle:
            payload = json.load(handle)
    save(args.out, payload, watermark=True if args.watermark else None, png=args.png)
    print(f"已生成：{args.out}" + (f"（预览 {args.png}）" if args.png else ""))


if __name__ == "__main__":
    main()
