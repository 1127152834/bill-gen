"""Mercury 账户验证信模板：坐标对齐参考件、字段可改、越界要报错。

参考件是用户给的 `~/Downloads/mercury-bank-letter-7658.pdf`，不在仓库里；
这里把量出来的坐标写成期望值，作用是「以后谁动了版式常量，测试立刻炸」。
"""
from __future__ import annotations

import pytest

import fitz

from billgen.themes.mercury.letter import (
    BASE_PDF,
    DEFAULT_DATA,
    build,
    format_date,
    save,
)

# 参考件里每个 span 的 (基线 y, 起点 x)——量出来的，别在没重量的情况下改。
EXPECTED = {
    "mercury.com": (58.0, 507.91),
    "October 28, 2026": (109.5, 40.0),
    "To Whom It May Concern:": (137.5, 40.0),
    "This letter is to verify that": (165.5, 40.0),
    "Liroa Commerce": (165.5, 153.4),
    "Customer details": (238.5, 40.0),
    "Account signatory": (238.5, 156.0),
    "Zhenjie Li": (238.5, 329.6),
    "Company address": (262.5, 156.0),
    "11119 Freer Avenue": (262.5, 329.6),
    "Temple City, CA 91780": (278.5, 329.6),
    "Account details": (327.5, 40.0),
    "Date account opened": (327.5, 156.0),
    "Bank routing number": (351.5, 156.0),
    "121145433": (351.5, 329.6),
    "Bank account number": (375.5, 156.0),
    "398138140817658": (375.5, 329.6),
    "Bank details": (424.5, 40.0),
    "Name": (424.5, 156.0),
    "Column N.A.": (424.5, 329.6),
    "Address": (448.5, 156.0),
    "1 Letterman Drive, Building A, Suite A4-700": (448.5, 329.6),
    "San Francisco, CA 94129": (464.5, 329.6),
    "Sincerely,": (496.5, 40.0),
    "Jason Zhang": (568.5, 40.0),
    "COO, Mercury": (584.5, 40.0),
    "Banking services provided by Column N.A., Member FDIC.": (769.5, 174.05),
}


def spans(pdf: bytes) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        for block in doc[0].get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    text = span["text"].strip()
                    if text:
                        out.setdefault(text, (round(span["origin"][1], 2), round(span["origin"][0], 2)))
    return out


def full_text(pdf: bytes) -> str:
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        return " ".join(" ".join(page.get_text().split()) for page in doc)


def test_letter_reproduces_reference_geometry():
    """默认数据出片：除正文折行外，每个 span 都落在参考件的坐标上。"""
    got = spans(build())
    for text, (want_y, want_x) in EXPECTED.items():
        assert text in got, f"少了这一段：{text!r}"
        y, x = got[text]
        assert abs(y - want_y) <= 0.05, f"{text!r} 基线 {y} ≠ 参考件 {want_y}"
        assert abs(x - want_x) <= 2.0, f"{text!r} 起点 {x} ≠ 参考件 {want_x}"


def test_letter_page_geometry_and_furniture():
    pdf = build()
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        assert len(doc) == 1
        page = doc[0]
        assert page.rect == fitz.Rect(0, 0, 612, 792)
        # 三条分隔细线还在（y=212/301/398）
        lines = sorted(
            round(d["rect"].y0, 1) for d in page.get_drawings()
            if d["type"] == "s" and abs(d["rect"].x1 - 572.0) < 0.5
        )
        assert lines == [212.0, 301.0, 398.0]
        # 手写签名的矢量路径还在
        assert any(d["rect"].y0 > 500 and d["rect"].x0 < 50 for d in page.get_drawings())


def test_letter_fields_are_editable():
    data = {
        "date": "2026-11-05",
        "company": {
            "name": "Nimbus Robotics Inc.",
            "signatory": "Mei Lin",
            "address": ["88 Harbor Road", "Oakland, CA 94607"],
        },
        "account": {"opened": "2026-03-02", "routing": "121145433", "number": "1234567890123"},
        "signer": {"name": "Dana Wu", "title": "CFO, Mercury", "signature": False},
    }
    pdf = build(data)
    text = full_text(pdf)
    assert "Nimbus Robotics Inc." in text
    assert "Mei Lin" in text
    assert "November 5, 2026" in text          # 日期按人话重排
    assert "March 2, 2026" in text             # 开户日
    assert "Dana Wu" in text and "CFO, Mercury" in text
    # 旧值一个都不许留
    for stale in ("Liroa Commerce", "Zhenjie Li", "Temple City", "Jason Zhang", "COO, Mercury"):
        assert stale not in text, f"旧内容还留在文本层：{stale}"


def test_letter_formatted_amounts_are_stripped_from_numbers():
    """路由号/账号被人塞了千分位也能出，别把 `$` 印在账号上。"""
    pdf = build({"account": {"routing": "121,145,433", "number": "3,981,381,408,176,58"}})
    text = full_text(pdf)
    assert "121145433" in text
    assert "$" not in text
    assert "121,145,433" not in text


def test_letter_keeps_long_company_name_inside_the_margins():
    long_name = "Liroa Commerce International Holdings (Delaware) LLC"
    pdf = build({"company": {"name": long_name}})
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        page = doc[0]
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    assert span["bbox"][2] <= 572.5, f"这行压到右边界了：{span['text']!r}"
    assert long_name in " ".join(full_text(pdf).split())


def test_letter_refuses_values_that_would_overrun_the_layout():
    with pytest.raises(ValueError, match="行"):
        build({"bank": {"address": ["1 One", "2 Two", "3 Three"]}})
    with pytest.raises(ValueError, match="太长"):
        build({"account": {"number": "9" * 200}})


def test_letter_base_has_no_leftover_identity():
    """底稿里不许留参考件的公司名/账号——模板只该印这次填进去的东西。"""
    with fitz.open(BASE_PDF) as doc:
        text = " ".join(" ".join(page.get_text().split()) for page in doc)
    for stale in ("Liroa", "Zhenjie", "11119", "121145433", "398138140817658", "Jason"):
        assert stale not in text, f"底稿里还留着参考件的东西：{stale}"


def test_letter_save_writes_pdf_and_png(tmp_path):
    target = tmp_path / "letter.pdf"
    preview = tmp_path / "letter.png"
    save(str(target), {"company": {"name": "Harbor Labs LLC"}}, png=str(preview))
    assert target.read_bytes().startswith(b"%PDF")
    assert preview.exists() and preview.stat().st_size > 1000


def test_format_date_accepts_iso_and_passthrough():
    assert format_date("2026-10-28") == "October 28, 2026"
    assert format_date("2026/01/05") == "January 5, 2026"
    assert format_date("October 28, 2026") == "October 28, 2026"


def test_mercury_statement_skips_ein_for_personal_accounts():
    """底稿是企业样例才印 EIN；个人账户印一串企业号是硬伤。"""
    from billgen import registry
    from billgen.spec import RenderOptions
    import fitz

    data = registry.get_renderer("mercury").default_data()
    data["ein"] = ""
    pdf = registry.get_renderer("mercury").render(data, RenderOptions(watermark=False))
    with fitz.open(stream=pdf.pdf, filetype="pdf") as doc:
        text = "".join(page.get_text() for page in doc)
    assert "EIN" not in text
