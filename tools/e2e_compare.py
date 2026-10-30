#!/usr/bin/env python3
"""端到端对比：同一份账本 → 每套模板出片，再和仓库里的参考件逐项比对。

参考件（`reference`）就是做版式时用的那份 PDF：Chase/US Bank 的静态底稿、Capital One /
Mercury / SoFi 的官方 SAMPLE。没有参考 PDF 的主题（BoA / Amex / Wells Fargo）用
`layout_*.json` 里记着的元素位置做结构比对。

用法：
    .venv/bin/python tools/e2e_compare.py --result /tmp/gen-personal.json
    .venv/bin/python tools/e2e_compare.py --result r.json --templates chase-personal,sofi
    .venv/bin/python tools/e2e_compare.py --result r.json --png      # 同时导出并排图

输出：`_build/e2e/<template>.pdf|png`、`docs/e2e/report.md`、`docs/e2e/img/*-compare.png`
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from billgen import registry  # noqa: E402
from billgen.synth import from_game_result  # noqa: E402
from billgen.synth import bridge  # noqa: E402

OUT = ROOT / "_build/e2e"
DOCS = ROOT / "docs/e2e"

#: 每套模板用来对照的参考件；`None` = 仓库里没有这份 PDF（改用 layout json 做结构比对）。
REFERENCES: dict[str, str | None] = {
    "boa-personal": None,
    "boa-business": None,
    "chase-personal": "src/billgen/themes/chase/assets/chase_static_personal.pdf",
    "chase-business": "src/billgen/themes/chase/assets/chase_static_business.pdf",
    "usbank-personal": "src/billgen/themes/usbank/assets/us_static_personal.pdf",
    "usbank-business": "src/billgen/themes/usbank/assets/usbiz_static.pdf",
    "capitalone": "src/billgen/themes/capitalone/C1-Personal-Statement-SAMPLE.pdf",
    "mercury": "src/billgen/themes/mercury/Demo-Bank-Statement-SAMPLE.pdf",
    "sofi": "src/billgen/themes/sofi/SoFi-Statement-SAMPLE.pdf",
    "wellsfargo": None,
}

#: 这套版式必须出现的区块标题（缺一个就不像这家了）。
SECTION_LABELS = {
    "boa-personal": ["Account summary", "Deposits and other additions", "Withdrawals and other subtractions"],
    "boa-business": ["Account summary", "Deposits and other additions"],
    "chase-personal": ["CHECKING SUMMARY", "CUSTOMER SERVICE INFORMATION", "TRANSACTION DETAIL"],
    "chase-business": ["CHECKING SUMMARY", "DEPOSITS AND ADDITIONS"],
    # `Account Summary` 这行小标题以前被擦掉就没再补（2026-10-30 补回），
    # 列进必检清单，免得再漏
    "usbank-personal": ["Uni-Statement", "SAFE DEBIT ACCOUNT", "Account Summary", "Deposits / Credits", "Other Withdrawals"],
    "usbank-business": ["Business Statement", "BUSINESS ESSENTIALS CHECKING", "Account Summary"],
    "capitalone": ["TOTAL ENDING BALANCE", "Account Summary", "Cashflow Summary"],
    "mercury": ["Account details", "Account activity", "All Transactions"],
    "sofi": ["Primary Account Holder", "Transaction Details", "Contact Information"],
    "wellsfargo": ["Monthly service fee summary", "Account activity"],
}

#: 参考件里的占位/他人身份，绝不能出现在我们出的件里。
LEAK_PATTERNS = [
    r"XUXING LI",
    r"GIRARD ST",
    r"\bSAMPLE\b",
    r"\bEXAMPLE\b",
    r"000000000000",
]

DATE_PATTERNS = {
    "MM/DD": r"\b\d{2}/\d{2}\b",
    "MM/DD/YYYY": r"\b\d{2}/\d{2}/\d{4}\b",
    "Mon D": r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{1,2}\b",
    "Mon DD, YYYY": r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{2}, \d{4}\b",
    "Month D, YYYY": r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4}\b",
}


@dataclass
class Facts:
    """一份 PDF 的客观指标。"""

    path: str
    pages: int
    page_size: tuple[float, float]
    fonts: set[str] = field(default_factory=set)
    images: list[float] = field(default_factory=list)  # 有效 DPI
    ink: list[float] = field(default_factory=list)  # 每页墨迹覆盖率（%）
    text: str = ""
    date_formats: set[str] = field(default_factory=set)

    @property
    def min_dpi(self) -> float | None:
        return min(self.images) if self.images else None


def analyze(path: Path) -> Facts:
    doc = fitz.open(path)
    page0 = doc[0]
    fonts: set[str] = set()
    images: list[float] = []
    ink: list[float] = []
    text_parts: list[str] = []
    for page in doc:
        for name in {f[3] for f in page.get_fonts()}:
            fonts.add(name.split("+")[-1])
        for info in page.get_images(full=True):
            xref = info[0]
            pix = doc.extract_image(xref)
            w, h = pix["width"], pix["height"]
            rects = page.get_image_rects(xref)
            if not rects:
                continue
            wid = max(r.width for r in rects)
            if wid > 0 and w > 0:
                images.append(round(w / (wid / 72.0), 1))
        pixmap = page.get_pixmap(dpi=72)
        samples = pixmap.samples
        nonwhite = sum(1 for b in samples if b < 245)
        ink.append(round(100.0 * nonwhite / max(1, len(samples)), 1))
        text_parts.append(page.get_text())
    text = "\n".join(text_parts)
    dates = {label for label, pattern in DATE_PATTERNS.items() if re.search(pattern, text)}
    return Facts(
        path=str(path.relative_to(ROOT)) if str(path).startswith(str(ROOT)) else str(path),
        pages=len(doc),
        page_size=(round(page0.rect.width, 1), round(page0.rect.height, 1)),
        fonts=fonts,
        images=images,
        ink=ink,
        text=text,
        date_formats=dates,
    )



def _skip_reason(template: str) -> str:
    """空错误串时给一句人话：`alliant`/`ally` 是浏览器端 pdf-lib 模板，Python 没有出片路径。"""
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "src"))
    try:
        from billgen import registry

        spec = registry.get_spec(template)
    except Exception:
        return "无法取到模板定义"
    if getattr(spec, "kind", "") == "static-web":
        return "浏览器端 pdf-lib 模板，Python 侧无出片路径（跳过）"
    return f"{template} 无法在 Python 侧渲染"


def render_template(template_id: str, result: dict[str, Any], *, watermark: bool = True) -> Path:
    out = OUT / f"{template_id}.pdf"
    renderer = registry.get_renderer(template_id)
    # 版式没有「账户类型」字段：企业版走 business_checking，其余交给账本自己挑第一个账户。
    account = "business_checking" if renderer.spec.variant == "business" else ""
    ledger = from_game_result(result, account=account, watermark=watermark, template=template_id)
    rendered = bridge.render(template_id, ledger)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(rendered.pdf)
    return out


def diff_lines(gen: Facts, ref: Facts, template_id: str) -> list[tuple[str, str, str, str]]:
    """(方面, 出片, 参考件, 判定)。判定只有 OK / DIFF / INFO。"""
    rows: list[tuple[str, str, str, str]] = []

    def add(aspect: str, ours: Any, theirs: Any, verdict: str) -> None:
        rows.append((aspect, str(ours), str(theirs), verdict))

    add("页面尺寸", gen.page_size, ref.page_size, "OK" if gen.page_size == ref.page_size else "DIFF")
    add("页数", gen.pages, ref.pages, "OK" if gen.pages == ref.pages else "INFO")
    shared = gen.fonts & ref.fonts
    add("字体（共有）", ", ".join(sorted(shared))[:80] or "无", f"参考共 {len(ref.fonts)} 种", "OK" if shared else "DIFF")
    add("字体（仅出片）", ", ".join(sorted(gen.fonts - ref.fonts))[:80] or "无", "—", "INFO")
    if ref.min_dpi is not None:
        verdict = "OK"
        if gen.min_dpi is None:
            verdict = "OK"  # 全矢量（图标重绘过），比位图更清晰
        elif gen.min_dpi < ref.min_dpi - 5:
            verdict = "DIFF"
        add("最低图效 DPI", gen.min_dpi if gen.min_dpi is not None else "全矢量", ref.min_dpi, verdict)
    add("第 1 页墨量 %", gen.ink[0] if gen.ink else None, ref.ink[0] if ref.ink else None,
        "OK" if abs((gen.ink[0] if gen.ink else 0) - (ref.ink[0] if ref.ink else 0)) <= 6 else "INFO")

    for label in SECTION_LABELS.get(template_id, []):
        add(f"区块标题 {label}", "有" if label.lower() in gen.text.lower() else "缺",
            "有" if label.lower() in ref.text.lower() else "—",
            "OK" if label.lower() in gen.text.lower() else "DIFF")

    leaks = [p for p in LEAK_PATTERNS if re.search(p, gen.text)]
    add("参考件身份/占位泄漏", ", ".join(leaks) or "无", "—", "OK" if not leaks else "DIFF")
    add("日期格式", ", ".join(sorted(gen.date_formats)) or "无", ", ".join(sorted(ref.date_formats)) or "无",
        "OK" if gen.date_formats & ref.date_formats else "INFO")
    return rows


def side_by_side(gen_pdf: Path, ref_pdf: Path, out_png: Path) -> None:
    from PIL import Image

    def pages(path: Path, limit: int = 2) -> list[Image.Image]:
        doc = fitz.open(path)
        out = []
        for page in list(doc)[:limit]:
            pix = page.get_pixmap(dpi=100)
            out.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
        return out

    ours, theirs = pages(gen_pdf), pages(ref_pdf)
    height = max(sum(i.height for i in ours), sum(i.height for i in theirs))
    width = max(i.width for i in ours) + max(i.width for i in theirs) + 24
    canvas = Image.new("RGB", (width, height), "white")
    y = 0
    for img in ours:
        canvas.paste(img, (0, y))
        y += img.height
    y = 0
    for img in theirs:
        canvas.paste(img, (max(i.width for i in ours) + 24, y))
        y += img.height
    out_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_png)




def _to_business(result: dict[str, Any]) -> dict[str, Any]:
    """把一份个人结果改写成企业结果，只用于版式对比。

    离线 fixture 的 lifeModel 只有个人字段（incomeStreams / creditLimitCents …），
    跑 `subjectType=business` 会在 lifeModel 阶段被判无效。这里复用同一批交易，
    把主体换成公司、账户换成 business_checking，用于检查企业版式的排版与行文。
    """
    cloned = copy.deepcopy(result)
    subject = cloned.get("subject") or {}
    company = "Okafor Design Studio LLC"
    subject.update({"subjectType": "business", "name": company, "legalName": company})
    cloned["subject"] = subject
    rolled = []
    for statement in cloned.get("statements") or []:
        if statement.get("accountType") == "checking":
            statement["accountType"] = "business_checking"
            statement["accountName"] = "Business Checking"
            rolled.append(statement)
    cloned["statements"] = rolled or cloned.get("statements")
    return cloned


def layout_spans(template_id: str, layout: dict[str, Any]) -> list[dict[str, Any]]:
    """把 layout json 归一成 [{text, x, bl}]，用于「位置是否还在原稿位置」的比对。"""
    out: list[dict[str, Any]] = []
    if template_id.startswith("boa"):
        page = layout.get("p1") or {}
        for span in page.get("spans") or []:
            text = (span.get("text") or "").strip()
            if text:
                out.append({"text": text, "x": float(span["x"]), "bl": float(span["bl"])})
    elif template_id == "wellsfargo":
        for entry in (layout.get("spans") or {}).get("0") or []:
            text = (entry[0] or "").strip()
            if text:
                out.append({"text": text, "x": float(entry[3]), "bl": float(entry[4])})
    return out


def compare_layout(page, ref_spans: list[dict[str, Any]], tol: float = 2.5) -> tuple[int, list[str]]:
    """参考件每一段文字在出片第 1 页里找同文本，比较 x / 基线偏移。"""
    ours = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                text = "".join(ch["c"] for ch in span["chars"]).strip()
                if text:
                    # origin[1] 才是基线（bbox 底边含降部，和 layout 的 bl 不同口径）
                    ours.append((text, (span["bbox"][0], span["origin"][1])))
    bad, notes = 0, []
    for ref in ref_spans:
        text_ref = ref["text"].strip()
        if len(text_ref) < 2 and not text_ref.isalnum():
            # 孤立的 `-`/`•`：出片里同字符出现在别处（金额负号、列表符号），
            # 撞出来的「漂移」没有意义，直接不比。
            continue
        hit = next((bbox for text, bbox in ours if text == ref["text"]), None)
        if hit is None:
            continue  # 合成数据换了文案（客户名/金额），不算版式漂移
        dx, dbl = abs(hit[0] - ref["x"]), abs(hit[1] - ref["bl"])
        if dx > tol or dbl > tol:
            bad += 1
            if len(notes) < 6:
                notes.append(f"{ref['text'][:24]!r} 偏移 x{dx:+.1f} 基线{dbl:+.1f}")
    return bad, notes


def layout_reference(template_id: str) -> dict[str, Any] | None:
    """没有参考 PDF 时，退回 layout json：元素位置就是原稿位置。"""
    candidates = {
        "boa-personal": "src/billgen/themes/boa/layout_personal.json",
        "boa-business": "src/billgen/themes/boa/layout_business.json",
        "wellsfargo": "src/billgen/themes/wellsfargo/layout_static.json",
        }
    rel = candidates.get(template_id)
    if not rel:
        return None
    path = ROOT / rel
    return json.loads(path.read_text()) if path.exists() else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", required=True, help="langgraph 产出的 result JSON")
    ap.add_argument("--templates", default="", help="逗号分隔；默认全部可渲染模板")
    ap.add_argument("--png", action="store_true", help="同时导出并排对比图")
    ap.add_argument("--no-watermark", action="store_true", help="出片不带 SAMPLE 水印（对比用）")
    ap.add_argument("--label", default="", help="报告文件名后缀：个人账本与 企业账本 各存一份，别互相覆盖")
    ap.add_argument(
        "--as-business",
        action="store_true",
        help="把个人账本改写成企业账本（离线 fixture 只有个人模型，企业版式靠这条路径做版式对比）",
    )
    args = ap.parse_args()

    result = json.loads(Path(args.result).read_text())
    result = result.get("result", result)  # 允许直接喂 /api/generations 的整包
    if args.as_business:
        result = _to_business(result)
    if "statements" not in result:
        result = result.get("result") or result

    wanted = [t for t in args.templates.split(",") if t] or list(REFERENCES)
    OUT.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "img").mkdir(parents=True, exist_ok=True)

    report: list[str] = ["# 端到端对比：出片 vs 参考件", ""]
    report.append(f"- 账本期数：{len(result.get('statements') or [])} 份")
    report.append(f"- 水印：{'关' if args.no_watermark else '开'}")
    report.append("")

    for template in wanted:
        print(f"== {template}")
        try:
            pdf = render_template(template, result, watermark=not args.no_watermark)
        except Exception as err:  # 账户类型不匹配等
            reason = str(err) or _skip_reason(template)
            print(f"   跳过：{reason}")
            report.append(f"## {template}\n\n跳过：`{reason}`\n")
            continue
        ours = analyze(pdf)
        print(f"   {ours.pages}p  fonts={len(ours.fonts)}  ink={ours.ink}")

        ref_rel = REFERENCES.get(template)
        # 有些版式既没有参考 PDF 也没有 layout json（引擎纯代码绘制），
        # 那就别印「0 段文字」，否则读者会以为跑过了结构比对。
        if not ref_rel and layout_reference(template) is None:
            report.append("无参考件（无 PDF、无 layout json）：本轮只做自检指标与区块标题检查。")
            report.append("")
        report.append(f"## {template}")
        report.append("")
        if ref_rel:
            ref_path = ROOT / ref_rel
            if ref_path.exists():
                theirs = analyze(ref_path)
                rows = diff_lines(ours, theirs, template)
                report.append(f"参考件：`{ref_rel}`（{theirs.pages}p，{theirs.page_size[0]}×{theirs.page_size[1]}）")
                report.append("")
                report.append("| 方面 | 出片 | 参考件 | 判定 |")
                report.append("| --- | --- | --- | --- |")
                for aspect, a, b, verdict in rows:
                    report.append(f"| {aspect} | {a} | {b} | {verdict} |")
                diffs = [r for r in rows if r[3] == "DIFF"]
                report.append("")
                report.append(f"差异：{len(diffs)} 项" + ("（" + "、".join(r[0] for r in diffs) + "）" if diffs else ""))
                if args.png:
                    target = DOCS / "img" / f"{template}-compare.png"
                    side_by_side(pdf, ref_path, target)
                    report.append(f"\n![{template}](img/{template}-compare.png)")
            else:
                report.append(f"参考件缺失（{ref_rel}）")
        else:
            layout = layout_reference(template)
            ref_spans = layout_spans(template, layout) if layout else []
            if ref_spans:
                report.append(f"无参考 PDF，改用 layout json 做结构比对（{len(ref_spans)} 段文字）")
            else:
                # 引擎纯代码绘制、没有 layout 底稿：说清楚「没做结构比对」，
                # 别印一个「0 段」让人误以为比对过且全过。
                report.append("无参考件（无 PDF、无 layout 底稿）：本轮只做自检指标与区块标题检查。")
            report.append("")
            if ref_spans:
                with fitz.open(pdf) as doc:
                    bad, notes = compare_layout(doc[0], ref_spans)
                report.append(f"- 版式漂移：{bad} 段（容差 2.5pt）")
                report.extend(f"  - {n}" for n in notes)
            report.append(f"- 页数：{ours.pages}；墨量：{ours.ink}；字体：{', '.join(sorted(ours.fonts))[:120]}")
        report.append("")
        print()

    name = f"report-{args.label}.md" if args.label else "report.md"
    (DOCS / name).write_text("\n".join(report) + "\n")
    print(f"报告：{DOCS/name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
