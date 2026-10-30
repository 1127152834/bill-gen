"""从 Mercursion 参考信里擦掉所有可变内容，得到一张「空白信纸」底稿。

用法：

    .venv/bin/python tools/make_mercury_letter_base.py \
        --src ~/Downloads/mercury-bank-letter-7658.pdf \
        --out src/billgen/themes/mercury/assets/letter/verification_letter_blank.pdf

保留的（版式家具）：顶部浅蓝条、Mercury 徽标与词标、三条分隔细线、
三个蓝色小节标题、行标签、以及页脚那行 Column 说明。
（`To Whom It May Concern:` 与 `Sincerely,` 也算可变内容，一起擦掉重画。）
默认连手写签名一起保留（默认数据就是写信的那位），渲染时若换了签署人，
模板会用白块把签名盖掉；要连矢量一起干净，加 `--strip-signature` 重做底稿。
擦掉的（可变内容）：日期、正文里的公司名与整段正文、各行的值、签署人姓名与头衔。

为什么要擦：模板工具只应该印「这次填进去的东西」。旧值留在文本层里，
search/复制 还能捞到别人的公司名与账号——那是比肉眼更严重的泄漏。
"""
from __future__ import annotations

import argparse
from pathlib import Path

import fitz

# 文本层要清空的区域（x0, y0, x1, y1）。都避开了 y=212/301/398 的三条分隔线。
TEXT_ZONES = [
    (36.0, 100.0, 260.0, 116.0),    # 日期
    (36.0, 128.0, 320.0, 144.0),    # To Whom It May Concern:
    (36.0, 486.0, 220.0, 504.0),    # Sincerely,
    (36.0, 155.0, 576.0, 193.0),    # 正文两行（含内嵌公司名）
    (326.0, 230.0, 560.0, 246.0),   # Account signatory 的值
    (326.0, 254.0, 560.0, 286.0),   # 公司地址两行
    (326.0, 318.0, 520.0, 334.0),   # 开户日
    (326.0, 342.0, 470.0, 358.0),   # 路由号
    (326.0, 366.0, 520.0, 382.0),   # 账号
    (326.0, 415.0, 520.0, 431.0),   # Bank details / Name 的值
    (326.0, 439.0, 576.0, 471.0),   # Bank details / Address 两行
    (36.0, 560.0, 300.0, 592.0),    # 签署人姓名 + 头衔
]

# 手写签名（黑色矢量路径）。单独一趟擦，避免误伤分隔线。
SIGNATURE_ZONE = (36.0, 505.0, 132.0, 556.0)


def build(src: Path, out: Path, *, strip_signature: bool = False) -> None:
    doc = fitz.open(src)
    page = doc[0]

    for rect in TEXT_ZONES:
        page.add_redact_annot(fitz.Rect(rect))
    page.apply_redactions(
        images=fitz.PDF_REDACT_IMAGE_NONE,
        graphics=fitz.PDF_REDACT_LINE_ART_NONE,
        text=fitz.PDF_REDACT_TEXT_REMOVE,
    )

    if strip_signature:
        page.add_redact_annot(fitz.Rect(SIGNATURE_ZONE))
        page.apply_redactions(
            images=fitz.PDF_REDACT_IMAGE_NONE,
            graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
            text=fitz.PDF_REDACT_TEXT_NONE,
        )

    doc.set_metadata({})  # 别把参考件的 producer/标题带出去
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out, garbage=4, deflate=True)
    doc.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--strip-signature", action="store_true",
                    help="连手写签名的矢量路径一起擦掉")
    args = ap.parse_args()
    build(Path(args.src).expanduser(), Path(args.out),
          strip_signature=args.strip_signature)
    print(f"空白信纸已生成：{args.out}")


if __name__ == "__main__":
    main()
