#!/usr/bin/env python3
"""字体体检 — 出件前的最后一道闸。

判据只看「PDF 里真实发生了什么」，不看代码意图：

1. ``unembedded``  字体没有 FontFile/FontFile2/FontFile3 → 阅读器拿系统字体顶替，
                   换台机器就是另一个字形，还会被一眼看出「Base-14 没嵌」。
2. ``notdef``      正文用到的字符不在该字体 cmap 里 → 渲染器画 .notdef/豆腐块。
3. ``no-tounicode`` 缺 ToUnicode → 选中复制是乱码，文本提取失败。
4. ``type3``       Type3 位图字体 → 真账单不会出现。
5. ``duplicate``   同一 BaseFont 被重复嵌入多个对象 → 体积膨胀 + 提取工具命名混乱。
6. ``declared``    与 parity 基线声明的字体名单不一致 → 版式已漂移。

用法::

    python tools/font_audit.py _build/fontaudit
    python tools/font_audit.py _build/fontaudit --baseline src/billgen/parity/baseline.json
    python tools/font_audit.py some.pdf --json

退出码：有 ERROR 级问题返回 1。
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from pathlib import Path

import fitz

SUBSET = re.compile(r"^[A-Z]{6}\+")
ERROR, WARN, INFO = "ERROR", "WARN", "INFO"

# PDF 规范要求阅读器必须提供的 14 个标准字体：不嵌入也不会缺字，
# 只是字形由阅读器自己挑（各平台长得不完全一样）。真正会「乱」的是
# 非 Base-14 又没嵌入的字体——阅读器只能拿别的字体顶替，度量对不上。
BASE14 = {
    "courier", "courier-bold", "courier-oblique", "courier-boldoblique",
    "helvetica", "helvetica-bold", "helvetica-oblique", "helvetica-boldoblique",
    "times-roman", "times-bold", "times-italic", "times-bolditalic",
    "symbol", "zapfdingbats",
}


def _norm(name: str) -> str:
    """字体名归一：span 里是「Myriad Pro Bold」，get_fonts 里是 MyriadPro-Bold。"""
    return re.sub(r"[^0-9a-z]", "", name.lower())


def _is_base14(name: str) -> bool:
    return _norm(SUBSET.sub("", name)) in {_norm(b) for b in BASE14}


def _cmap_chars(buf: bytes) -> set[str] | None:
    """字体对象里真实可用的字符集合；拿不到（纯 CFF/位图）返回 None。"""
    from fontTools.ttLib import TTFont

    try:
        font = TTFont(io.BytesIO(buf), lazy=True)
    except Exception:
        return None
    try:
        chars: set[str] = set()
        if "cmap" not in font:
            return None
        for table in font["cmap"].tables:
            if table.isUnicode():
                chars.update(chr(c) for c in table.cmap)
        return chars
    finally:
        try:
            font.close()
        except Exception:
            pass


def _has_tounicode(doc: fitz.Document, xref: int) -> bool:
    try:
        kind, value = doc.xref_get_key(xref, "ToUnicode")
    except Exception:
        return False
    return kind != "null" and bool(value)


def audit_pdf(path: Path, declared: list[str] | None = None) -> dict:
    report: dict = {"file": str(path), "fonts": [], "issues": []}
    doc = fitz.open(path)
    try:
        seen: dict[str, list[int]] = {}
        used: dict[str, set[str]] = {}
        pages_using: dict[str, set[int]] = {}
        page_seen: dict[tuple[int, str], list[int]] = {}

        for pno, page in enumerate(doc, start=1):
            for info in page.get_fonts(full=True):
                xref, ext, ftype, basefont, resname = info[0], info[1], info[2], info[3], info[4]
                clean = SUBSET.sub("", basefont)
                seen.setdefault(clean, []).append(xref)
                # 同一页上同一字体出现两次才是真重复；跨页各写一份是 PDF 常态
                page_seen.setdefault((pno, clean), []).append(xref)
                pages_using.setdefault(resname, set()).add(pno)
            raw = page.get_text("rawdict")
            for block in raw.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        key = _norm(SUBSET.sub("", span.get("font", "")))
                        bucket = used.setdefault(key, set())
                        for ch in span.get("chars", []):
                            c = ch.get("c")
                            if c and not c.isspace():
                                bucket.add(c)

        resname_to_base: dict[str, str] = {}
        for pno, page in enumerate(doc, start=1):
            for info in page.get_fonts(full=True):
                resname_to_base[info[4]] = info[3]

        xref_done: set[int] = set()
        for pno, page in enumerate(doc, start=1):
            for info in page.get_fonts(full=True):
                xref, ext, ftype, basefont, resname = info[0], info[1], info[2], info[3], info[4]
                if xref in xref_done:
                    continue
                xref_done.add(xref)
                clean = SUBSET.sub("", basefont)
                embedded = ext not in ("n/a", "", None)
                has_subset_prefix = bool(SUBSET.match(basefont))
                tounicode = _has_tounicode(doc, xref)
                glyphs = None
                chars = None
                if embedded:
                    try:
                        _n, _e, _s, buf = doc.extract_font(xref)
                        if buf:
                            chars = _cmap_chars(buf)
                            glyphs = len(chars) if chars else None
                    except Exception:
                        chars = None

                # 该字体对象实际承载的字符（按资源名归集，含所有页）
                used_here = set(used.get(_norm(clean), set()))

                missing = sorted(used_here - chars) if chars is not None else []

                entry = {
                    "xref": xref, "basefont": basefont, "clean": clean, "ext": ext,
                    "type": ftype, "encoding": info[5], "embedded": embedded,
                    "subset_prefix": has_subset_prefix, "tounicode": tounicode,
                    "cmap_chars": len(chars) if chars else None,
                    "used_chars": len(used_here),
                    "missing": missing[:20],
                    "missing_count": len(missing),
                }
                report["fonts"].append(entry)

                if not embedded and ftype != "Type3":   # Type3 的字形在 CharProcs 里，本来就没有 FontFile
                    if _is_base14(basefont):
                        report["issues"].append({
                            "level": INFO, "kind": "base14", "font": basefont, "xref": xref,
                            "detail": f"{ftype} 是 Base-14 标准字体，不嵌入也不会缺字；字形随阅读器略有差异",
                        })
                    else:
                        report["issues"].append({
                            "level": ERROR, "kind": "unembedded", "font": basefont, "xref": xref,
                            "detail": f"{ftype} 未嵌入（ext={ext}）且不是 Base-14，阅读器只能替换字体，排版会飘",
                        })
                if ftype == "Type3":
                    report["issues"].append({"level": INFO, "kind": "type3", "font": basefont,
                                             "xref": xref, "detail": "Type3 自带字形，不会缺字；放大时发糊（位图轮廓）"})
                if missing:
                    report["issues"].append({
                        "level": ERROR, "kind": "notdef", "font": basefont, "xref": xref,
                        "detail": f"{len(missing)} 个字符不在字体里，会画成豆腐块/notdef：{''.join(missing[:20])!r}",
                    })
                if embedded and not tounicode:
                    report["issues"].append({
                        "level": WARN, "kind": "no-tounicode", "font": basefont, "xref": xref,
                        "detail": "缺 ToUnicode，选中复制/文本提取会乱",
                    })

        for (pno, clean), xrefs in sorted(page_seen.items()):
            if len(xrefs) > 1:
                report["issues"].append({
                    "level": WARN, "kind": "duplicate", "font": clean, "xref": xrefs,
                    "detail": f"第 {pno} 页同一字体嵌入 {len(xrefs)} 份",
                })

        produced = sorted(seen)
        report["produced"] = produced
        if declared is not None:
            want = sorted({SUBSET.sub("", d) for d in declared})
            want_norm = {_norm(f) for f in want}
            prod_norm = {_norm(f) for f in produced}
            missing_f = [f for f in want if _norm(f) not in prod_norm]
            extra_f = [f for f in produced if _norm(f) not in want_norm]
            report["declared"] = {"missing": missing_f, "extra": extra_f}
            for f in missing_f:
                report["issues"].append({"level": WARN, "kind": "declared", "font": f,
                                         "detail": "基线声明了但产物里没有（可能是原稿自己的怪名或按数据才出现的 CJK 字体）"})
            for f in extra_f:
                report["issues"].append({"level": WARN, "kind": "declared", "font": f,
                                         "detail": "产物多出基线未声明的字体"})
    finally:
        doc.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PDF 字体体检")
    ap.add_argument("target", nargs="+", help="PDF 文件或目录")
    ap.add_argument("--baseline", help="parity 基线 JSON（按 id 取 declared 名单）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--quiet", action="store_true", help="只打问题行")
    args = ap.parse_args(argv)

    baseline = {}
    if args.baseline:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))

    files: list[Path] = []
    for t in args.target:
        p = Path(t)
        if p.is_dir():
            files.extend(sorted(p.glob("*.pdf")))
        elif p.is_file():
            files.append(p)

    reports = []
    bad = 0
    for f in files:
        declared = None
        entry = baseline.get(f.stem)
        if isinstance(entry, dict) and entry.get("fonts"):
            declared = entry["fonts"]
        rep = audit_pdf(f, declared)
        reports.append(rep)
        errors = [i for i in rep["issues"] if i["level"] == ERROR]
        warns = [i for i in rep["issues"] if i["level"] == WARN]
        if errors:
            bad += 1
        if not args.quiet or rep["issues"]:
            flag = "FAIL" if errors else ("WARN" if warns else " OK ")
            print(f"[{flag}] {f.name}  fonts={len(rep['fonts'])} error={len(errors)} warn={len(warns)}")
            for i in rep["issues"]:
                print(f"       {i['level']:5} {i['kind']:12} {i['font']:44} {i['detail']}")
            if not args.quiet:
                for fo in rep["fonts"]:
                    print(f"       · {fo['clean'][:40]:40} xref={fo['xref']:<4} "
                          f"emb={'Y' if fo['embedded'] else 'N'} sub={'Y' if fo['subset_prefix'] else '-'} "
                          f"cmap={fo['cmap_chars']} used={fo['used_chars']}")

    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))

    print(f"\n{len(files)} 个文件，{bad} 个有 ERROR。")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
