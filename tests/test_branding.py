"""品牌替换包：logo 物理替换、行标确定性、文本层残留审计。"""
from __future__ import annotations

import hashlib
import re

import fitz
import pytest

from billgen import audit, branding, registry
from billgen.synth import build_data, from_game_result, render, supported_ids

LEDGER = None


# 每个模板印自己的真行名：账本、logo 重绘、文案替换都跟着走。
BANK_BY_TEMPLATE = {
    "boa-personal": "Bank of America",
    "boa-business": "Bank of America",
    "wellsfargo": "Wells Fargo",
    "amex": "American Express",
    "mercury": "Mercury",
    "capitalone": "Capital One",
    "sofi": "SoFi",
    "chase-personal": "Chase",
    "chase-business": "Chase",
    "usbank-personal": "U.S. Bank",
    "usbank-business": "U.S. Bank",
    "alliant": "Alliant Credit Union",
    "ally": "Ally Bank",
}


def payload(account_type: str = "checking", bank_name: str = "Bank of America") -> dict:
    rows = [
        ("2027-01-04", "ACME PAYROLL DIRECT DEP", 250_000, "credit", "ACH"),
        ("2027-01-06", "CAMDEN LIVING RENT PMT 0106", 120_000, "debit", "ACH"),
        ("2027-01-12", "ATM WITHDRAWAL 0112 TERMINAL", 30_000, "debit", "ATM"),
        ("2027-01-20", "CHECK 1042", 50_000, "debit", "CHECK"),
    ]
    return {
        "id": "brand", "seed": "brand", "createdAt": "2027-02-01T00:00:00.000Z",
        "subject": {"name": "Maya Okafor", "age": 34,
                    "address": {"street": "2104 S 1st St", "city": "Austin",
                                "state": "TX", "zip": "78704"},
                    "occupation": "Designer", "household": "single", "hobbies": []},
        "statements": [{
            "bankName": bank_name, "accountType": account_type,
            "accountLast4": "1347",
            "periodStart": "2027-01-01", "periodEnd": "2027-01-31",
            "months": [{"cycleStart": "2027-01-01", "cycleEnd": "2027-01-31",
                        "openingBalanceCents": 100_000, "closingBalanceCents": 150_000,
                        "totalCreditsCents": 250_000, "totalDebitsCents": 200_000,
                        "transactionCount": len(rows)}],
            "transactions": [
                {"id": f"t{i}", "transactionDate": day, "postedDate": day,
                 "description": desc, "memo": "", "type": kind,
                 "direction": direction, "amountCents": cents}
                for i, (day, desc, cents, direction, kind) in enumerate(rows)
            ],
        }],
        "audit": {"rules": [], "findings": []},
        "usage": {"llmCalls": 0, "tokens": 0, "tavilyQueries": 0},
        "warnings": [], "status": "completed",
    }


@pytest.fixture(scope="module")
def ledger():
    return from_game_result(payload(), account="checking", cycle=0)


def header_images(doc, page, rect):
    """页眉矩形里真正的 logo 图。

    出件前会铺一层近乎透明的扫描颗粒，那是**铺满整页**的位图；按外框相交判断会把
    它也算成 logo，所以只收明显小于整页的图。
    """
    page_area = abs(page.rect)
    found = []
    for info in page.get_image_info(xrefs=True):
        box = fitz.Rect(info["bbox"])
        if not box.intersects(rect) or abs(box) > page_area * 0.2:
            continue
        found.append((info["xref"], box))
    return found


def _samples(png: bytes) -> tuple[int, int, bytes]:
    """图片流会被 PyMuPDF 重编码，比像素而不是比容器字节。"""
    pix = fitz.Pixmap(png)
    return pix.width, pix.height, pix.samples


def _mean_abs_difference(a: bytes, b: bytes) -> float:
    if len(a) != len(b):
        return 255.0
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / max(len(a), 1)


def _thumbnail(png: bytes, size=(48, 16)) -> bytes:
    """把图缩成固定大小再比：只关心「是不是同一枚标」，不关心重采样细节。"""
    from PIL import Image

    pix = fitz.Pixmap(png)
    mode = "RGB" if pix.n == 3 else "L"
    image = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
    return image.convert("L").resize(size, Image.LANCZOS).tobytes()


def _same_mark(left: bytes, right: bytes) -> bool:
    return _mean_abs_difference(_thumbnail(left), _thumbnail(right)) < 12.0


def _region(pdf: bytes, rect, dpi: int = 96) -> bytes:
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        return doc[0].get_pixmap(clip=fitz.Rect(*rect), dpi=dpi).samples


@pytest.mark.parametrize("template_id", sorted(branding.PLACEMENTS))
def test_every_mapped_template_actually_replaces_logo(template_id):
    renderer = registry.get_renderer(template_id)
    result = renderer.render(renderer.default_data(), renderer.spec.default_options)
    placements = branding.PLACEMENTS[template_id]
    brand = branding.brand_for("Harborline Bank", "Harborline Bank, N.A.", "harborline-bank.com")

    with fitz.open(stream=result.pdf, filetype="pdf") as doc:
        before = [header_images(doc, doc[0], fitz.Rect(*placements[0]))]
        replaced = branding.apply(doc, template_id, brand)
        # 续页不一定有标（sofi 只在首页、boa 的 logo 只出现在部分页），
        # 所以只要求「首页的每个矩形都被处理」，不按页数乘系数
        assert replaced >= len(placements)
        after = header_images(doc, doc[0], fitz.Rect(*placements[0]))
        assert after, "替换后该矩形里必须只剩我们自己的标"
        for xref, bbox in after:
            info = doc.extract_image(xref)
            if info is None:
                continue
            assert _same_mark(info["image"],
                              branding.wordmark_png(brand, bbox.width, bbox.height)), \
                "该矩形里应当只剩我们自己的标"
        assert before[0] != after, "原 logo 必须被物理删除（图片流换了人）"


def test_wordmark_is_deterministic():
    brand = branding.brand_for("Harborline Bank")
    first = branding.wordmark_png(brand, 169.9, 25.2)
    second = branding.wordmark_png(brand, 169.9, 25.2)
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
    assert first[:8] == b"\x89PNG\r\n\x1a\n"


def test_different_banks_get_different_marks():
    a = branding.wordmark_png(branding.brand_for("Harborline Bank"), 169.9, 25.2)
    b = branding.wordmark_png(branding.brand_for("Ridgeline Credit Union"), 169.9, 25.2)
    assert a != b


def test_wordmark_fits_inside_the_box():
    """文字不能溢出矩形：先前手工系数让词标被裁成 "HARBORLINE BAN"。"""
    brand = branding.brand_for("Harborline Bank")
    width, height = 169.9, 25.2
    png = branding.wordmark_png(brand, width, height)
    with fitz.open(stream=png, filetype="png") as doc:
        page = doc[0]
        rect = page.rect
        longest = max((span["bbox"][2] for block in page.get_text("dict")["blocks"]
                       for line in block.get("lines", []) for span in line.get("spans", [])),
                      default=0)
        assert longest <= rect.width + 0.5
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    assert span["bbox"][3] <= rect.height + 0.5
        assert width / height >= 3.0


def test_synth_render_keeps_the_original_logo_for_the_same_bank(ledger):
    """账本就是模板自带那家：保留原稿行标（带图形），只换文字槽。

    重画的词标只有字没有图形（BOA 的旗、Chase 的八边形都在原图里），
    换上去反而更像「拿字拼的假标」，所以同一家银行时不动 logo。
    """
    first = render("boa-personal", ledger).pdf
    second = render("boa-personal", ledger).pdf
    assert first == second
    plain = render("boa-personal", ledger, brand=False).pdf
    rect = fitz.Rect(*branding.PLACEMENTS["boa-personal"][0])
    with fitz.open(stream=first, filetype="pdf") as doc, \
            fitz.open(stream=plain, filetype="pdf") as raw:
        branded_marks = [doc.extract_image(x)["image"] for x, _ in header_images(doc, doc[0], rect)]
        original = [raw.extract_image(x)["image"] for x, _ in header_images(raw, raw[0], rect)]
    assert original, "底稿页眉本来就该有行标图"
    assert branded_marks == original


def test_synth_render_redraws_the_logo_for_a_different_bank():
    """换成别的银行：logo 必须重画成那家的词标，不能留着原稿的行标。"""
    other = from_game_result(payload(), account="checking", cycle=0, bank_name="Harborline Bank")
    pdf = render("boa-personal", other).pdf
    rect = fitz.Rect(*branding.PLACEMENTS["boa-personal"][0])
    brand = branding.brand_of_ledger(other)
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        images = header_images(doc, doc[0], rect)
        assert images
        # 重画后页眉里应该有那家的词标；原稿行标图应已被 redaction 抹掉
        assert any(_same_mark(doc.extract_image(xref)["image"],
                              branding.wordmark_png(brand, bbox.width, bbox.height))
                   for xref, bbox in images)


def test_brand_can_be_switched_off(ledger):
    """默认模板渲染（回归指纹）不吃品牌替换，只有 synth 链路默认打开。"""
    branded = render("chase-personal", ledger).pdf
    plain = render("chase-personal", ledger, brand=False).pdf
    assert branded != plain
    renderer = registry.get_renderer("chase-personal")
    raw = renderer.render(build_data("chase-personal", ledger),
                          renderer.spec.default_options).pdf
    with fitz.open(stream=raw, filetype="pdf") as a, fitz.open(stream=plain, filetype="pdf") as b:
        assert a[0].get_text() == b[0].get_text()


@pytest.mark.parametrize("template_id", sorted(supported_ids()))
def test_no_placeholder_bank_text(branded_pdf, template_id):
    """模板换成整合版后印真行名：审计方向反过来了，查的是虚构占位名。"""
    pdf = branded_pdf(template_id)
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        flat = audit.normalize(" ".join(page.get_text() for page in doc))
    assert audit.placeholder_gaps(template_id, audit.scan_placeholders(flat)) == {}


def test_brand_split_across_lines_is_still_caught():
    """真实账单会断行：`your local U.S.` + `Bank branch.` 只扫原文本会漏掉。"""
    pages = ["contact your local U.S.", "Bank branch or call us", "held at SoFi", "Bank, N.A."]
    assert audit.scan_text("".join(pages)) == {}  # 直接拼起来正好把词切断
    assert audit.scan_pages(pages) == {"U.S. Bank": 1, "SoFi": 1}


@pytest.mark.parametrize("template_id", sorted(supported_ids()))
def test_real_brand_text_is_present(branded_pdf, template_id):
    """账面必须出现对应银行的真名/域名，而不是占位名。"""
    with fitz.open(stream=branded_pdf(template_id), filetype="pdf") as doc:
        flat = audit.normalize(" ".join(page.get_text() for page in doc))
    hits = audit.scan_text(flat)
    assert hits, f"{template_id} 文本层没有任何真实行名"
    assert not audit.scan_placeholders(flat), audit.scan_placeholders(flat)


@pytest.fixture(scope="module")
def branded_pdf():
    """按模板自己的账户类型渲染一次，供文本审计复用。"""
    cache: dict[str, bytes] = {}

    def build(template_id: str) -> bytes:
        if template_id not in cache:
            account = "business_checking" if "business" in template_id else "checking"
            bank = BANK_BY_TEMPLATE.get(template_id, "Bank of America")
            # 显式点名行名：这些审计测的就是「重绘后文本可读」，不点名则模板原样保留。
            led = from_game_result(payload(account, bank), account=account, cycle=0, bank_name=bank)
            cache[template_id] = render(template_id, led).pdf
        return cache[template_id]

    return build


def test_brand_tokens_use_word_boundaries():
    """PURCHASE 里含 CHASE，但不算品牌残留。"""
    assert audit.scan_text("POS PURCHASE AUTHORIZED") == {}
    assert audit.scan_text("CHASE COLLEGE CHECKING") == {"Chase": 1}
    assert audit.scan_text("visit usbank.com/book") == {"usbank.com": 1, "USbank": 1}


# 底稿里这些是真实行的行址与客服电话，属于「行标」的一部分，也必须换掉
# 各模板账单上必须出现的真行联系方式（只比数字，模板对分隔符的写法各不相同）
REAL_CONTACT_DIGITS = {
    "boa-personal": ("4321000", "25118", "33622"),
    "boa-business": ("4321000", "25118", "33622"),
    "chase-personal": ("9359935",),
    "usbank-personal": ("8722657", "3528"),
    "usbank-business": ("8722657", "3528"),
    "amex": ("5284800", "981540"),
}


# 促销框与法务段落里的商品名/评奖来源（只出现在 BOA 版式里）
REAL_PRODUCT_COPY = {
    "boa-personal": ("Balance Connect", "Business Fundamentals", "SafeBalance",
                     "SmartAsset", "Merrill"),
}


@pytest.mark.parametrize("template_id", sorted(REAL_PRODUCT_COPY))
def test_real_product_copy_is_replaced(branded_pdf, template_id):
    with fitz.open(stream=branded_pdf(template_id), filetype="pdf") as doc:
        text = "".join(page.get_text() for page in doc)
    for token in REAL_PRODUCT_COPY[template_id]:
        assert token not in text, f"{template_id} 仍印着原版商品名 {token}"


@pytest.mark.parametrize("template_id", sorted(REAL_CONTACT_DIGITS))
def test_real_contact_details_are_kept(branded_pdf, template_id):
    """品牌态不再把真行联系方式换成 555 号段，账单上印的就是真客服线与真信箱。"""
    with fitz.open(stream=branded_pdf(template_id), filetype="pdf") as doc:
        text = "".join(page.get_text() for page in doc)
    digits = re.sub(r"\D", "", text)
    for literal in REAL_CONTACT_DIGITS[template_id]:
        assert literal in digits, f"{template_id} 少了真行联系方式 {literal}"


def test_brand_fields_compose_and_skip_when_empty():
    brand = branding.brand_for("Harborline Bank", po_box="P O Box 41793",
                               po_box_alt="P O Box 48827", city_state_zip="Charlotte, NC 28202")
    assert brand.field("mail_line") == "P O Box 48827, Charlotte, NC 28202"
    slot = branding.TextSlot((0, 0, 10, 10), (0, 0), 8.0, "Customer service: {phone_service}")
    assert slot.ready(brand) is False, "字段为空时整槽跳过，不能印出半截空缺"


def test_text_slots_stay_inside_the_page():
    for template_id, slots in branding.TEXT_SLOTS.items():
        assert slots, template_id
        for slot in slots:
            x0, y0, x1, y1 = slot.rect
            assert 0 <= x0 < x1 <= 612, (template_id, slot.rect)
            assert 0 <= y0 < y1 <= 792, (template_id, slot.rect)


def test_known_gaps_are_documented_per_template():
    for template_id, tokens in audit.KNOWN_GAPS.items():
        assert template_id in supported_ids()
        assert tokens
        for token in tokens:
            assert any(token.lower() == known.lower() for known in audit.BRAND_TOKENS)


def test_no_known_gaps_remain():
    """M8 之后 11 套模板的文本层都不再放行任何真实品牌字样。"""
    assert audit.KNOWN_GAPS == {}


def test_sofi_legal_page_keeps_the_real_bank_name(branded_pdf):
    """sofi 的 12 处 SoFi 全在预断行的法务段落里：品牌态要拼回去重排，且行名保持真实。"""
    with fitz.open(stream=branded_pdf("sofi"), filetype="pdf") as doc:
        text = "\n".join(page.get_text() for page in doc)
    assert "SoFi" in text
    assert "Insured Deposit Program" in text
    assert "Member of FDIC" in text
    assert "sofi.com/banking/fdic/sidpterms" in text.replace(" ", "")
    # 真行址与客服线：原稿总部街址换成账单信箱，城市行保留
    assert "1-855-456-7634" in text
    assert "Cottonwood Heights, UT 84047-4197" in text
    assert "2750 East Cottonwood Parkway" not in text
    assert re.search(r"P O Box \d{4}, [A-Za-z .]+, [A-Z]{2} \d{5}", text), text[-400:]
    assert not audit.scan_placeholders(text)
    # 法务段落没有溢出边界：最宽的一行仍落在版心内
    with fitz.open(stream=branded_pdf("sofi"), filetype="pdf") as doc:
        legal = doc[-1]
        for block in legal.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                assert line["bbox"][2] <= 600.0, line["bbox"]


def test_sofi_default_render_keeps_the_original_legal_copy():
    """默认数据没有 bank：法务段落必须逐字保持原稿，指数字节比对不漂。"""
    from billgen import registry

    renderer = registry.get_renderer("sofi")
    result = renderer.render(renderer.default_data(), renderer.spec.default_options)
    with fitz.open(stream=result.pdf, filetype="pdf") as doc:
        text = "\n".join(page.get_text() for page in doc)
    assert "SoFi Insured Deposit Program" in text
    assert "SoFi Bank, N.A. Member of FDIC and Equal Housing Lender" in text


def test_usbank_prose_carries_the_real_bank_name(branded_pdf):
    """跨行拆分的两句（`your local U.S.` + `Bank branch.`）要连起来读得通。"""
    for template_id in ("usbank-personal", "usbank-business"):
        with fitz.open(stream=branded_pdf(template_id), filetype="pdf") as doc:
            text = "\n".join(page.get_text() for page in doc)
        assert "U.S. Bank" in text, template_id
        assert "usbank.com" in text.replace(" ", ""), template_id
        assert not audit.scan_placeholders(text), template_id


def test_usbank_personal_footer_points_at_the_fictional_branch(branded_pdf):
    with fitz.open(stream=branded_pdf("usbank-personal"), filetype="pdf") as doc:
        text = doc[0].get_text()
    assert "contact your local branch or" in text
    assert "your local bank" in text


def test_usbank_business_right_column_is_drawn_once(branded_pdf):
    """商业版右栏槽位曾按个人版坐标错放，导致原文与虚构名同时出现。"""
    with fitz.open(stream=branded_pdf("usbank-business"), filetype="pdf") as doc:
        text = doc[0].get_text()
    assert text.count("accepts Relay Calls") == 1
    assert "24-Hour Business" in text


def test_chase_body_domain_is_redrawn_as_one_run():
    """底稿把正文里的域名拆成十几个定位单元，替换变长后会渲染成 "harb orline-b ank"。

    品牌包把这一行整行重画成一句 Helvetica，所以文本层应当是一句完整的话，
    而不是一堆碎字；同时上一行不能被 redaction 连带擦掉。
    """
    book = from_game_result(payload(bank_name=BANK_BY_TEMPLATE["chase-personal"]),
                            account="checking", cycle=0)
    for template_id in ("chase-personal",):
        result = render(template_id, book)
        with fitz.open(stream=result.pdf, filetype="pdf") as doc:
            text = " ".join(page.get_text() for page in doc)
        assert "at chase.com or call us at the number listed on this statement." in text
        assert "harb orline" not in text
        assert "Fee on this account (if applicable), or if you have any questions" in text
