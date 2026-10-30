"""虚构行标（brand pack）：把版式里烙死的真实银行 logo 换成场景自己的行名。

两种做法都不能只看表面效果：

* 只盖一块白矩形 —— 原 logo 的图片流还留在 PDF 里，`page.get_images()` 一抓就出来；
* 所以这里用 redaction 把矩形下的图片与文字真正删掉，再插入我们的标。

标本身由 `fitz` 现画：短横/方章 + 词标文字，颜色由行名哈希在固定调色板里取，
所以同一个行名永远得到同一个标。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import fitz

__all__ = [
    "Brand",
    "PLACEMENTS",
    "TEXT_SLOTS",
    "TextSlot",
    "apply",
    "brand_for",
    "brand_of_ledger",
    "wordmark_png",
]

# 每套版式里 logo 所在的矩形（并集：同一模板不同页的落点略有差别，取并集后一页页覆盖）。
# 坐标来自渲染实测（见 tests/test_branding.py 的回归），单位 pt，原点左上。
PLACEMENTS: dict[str, tuple[tuple[float, float, float, float], ...]] = {
    "boa-personal": ((36.0, 46.8, 205.9, 77.0),),
    "boa-business": ((36.0, 46.8, 205.9, 77.0), (378.0, 55.0, 514.8, 80.2)),
    "wellsfargo": ((516.0, 21.8, 564.8, 70.5),),
    "amex": ((50.4, 26.4, 92.6, 69.1), (107.1, 26.6, 172.7, 40.6)),
    "mercury": ((38.0, 38.0, 181.0, 74.0),),
    "capitalone": ((57.6, 21.6, 201.6, 72.0),),
    "sofi": ((470.85, 49.85, 574.40, 77.45),),
    "chase-personal": ((58.1, 35.3, 147.4, 51.8),),
    "chase-business": ((58.1, 35.3, 147.4, 51.8),),
    "usbank-personal": ((72.0, 14.4, 196.8, 47.6),),
    "usbank-business": ((72.0, 14.4, 196.8, 47.6),),
}


# 这几套模板的 logo 是矢量描边（没有图片对象），矩形在场即视为有标
VECTOR_LOGO = frozenset({"mercury", "sofi"})

# 有些模板的 logo 只在首页，续页的同坐标处是表格/余额，画上去会盖住数字。
LOGO_PAGES: dict[str, tuple[int, ...]] = {"sofi": (0,)}

_PALETTE: tuple[tuple[float, float, float], ...] = (
    (0.08, 0.24, 0.45),
    (0.05, 0.32, 0.36),
    (0.20, 0.18, 0.44),
    (0.34, 0.16, 0.22),
    (0.12, 0.30, 0.24),
    (0.25, 0.25, 0.28),
)


@dataclass(frozen=True, slots=True)
class Brand:
    """一个虚拟行的视觉身份（行名、域名、行址、客服电话）。"""

    name: str
    legal: str
    domain: str
    accent: tuple[float, float, float]
    po_box: str = ""
    po_box_alt: str = ""
    city_state_zip: str = ""
    phone_service: str = ""
    phone_es: str = ""
    phone_intl: str = ""
    phone_metro: str = ""
    promo_code: str = ""

    #: 真账单正文里用的短名（Chase 自己的句子写 "visit a Chase branch"，不用法定名）。
    _SHORT_NAMES = {
        "JPMorgan Chase": "Chase",
        "JPMorgan Chase Bank": "Chase",
        "Bank of America": "Bank of America",
        "Wells Fargo Bank": "Wells Fargo",
    }

    @property
    def short(self) -> str:
        """正文里用的短品牌名：`JPMorgan Chase Bank` → `Chase`、`Wells Fargo Bank, N.A.` → `Wells Fargo`。"""
        for full, short in self._SHORT_NAMES.items():
            if self.name.startswith(full):
                return short
        name = self.name.replace(", N.A.", "").replace(", N.A", "").strip()
        for suffix in (" National Association", " Bank", " N.A."):
            if name.endswith(suffix):
                name = name[: -len(suffix)].strip()
        return name or self.name

    @property
    def initials(self) -> str:
        words = [word for word in self.name.replace("-", " ").split() if word]
        if not words:
            return "SB"
        if len(words) == 1:
            return words[0][:2].upper()
        return (words[0][0] + words[1][0]).upper()

    @property
    def wordmark(self) -> str:
        return self.name.upper()

    def field(self, name: str) -> str:
        if name == "city":
            return self.city_state_zip.split(",")[0].strip()
        if name == "city_state":
            city, _, tail = self.city_state_zip.partition(",")
            state = tail.strip().split(" ")[0] if tail.strip() else ""
            return f"{city.strip()}, {state}".strip().rstrip(",")
        if name == "zip4":
            return self.city_state_zip.rsplit(" ", 1)[-1]
        if name == "mail_line":
            return f"{self.po_box_alt}, {self.city_state_zip}" if self.po_box_alt else ""
        return str(getattr(self, name, ""))


def _accent(name: str) -> tuple[float, float, float]:
    seed = hashlib.sha256(name.encode("utf-8")).digest()[0]
    return _PALETTE[seed % len(_PALETTE)]


def promo_code(name: str, domain: str) -> str:
    """促销框右下角那串内部表单码（原版为 `SSM-03-25-0560.B | 7786995`）。"""
    digest = hashlib.sha256(f"promo-code:{name}|{domain}".encode()).hexdigest()
    abbr = "".join(name.upper().split())[:3].ljust(3, "X")
    month = int(digest[0:2], 16) % 12 + 1
    year = int(digest[2:4], 16) % 8 + 24
    return f"{abbr}-{month:02d}-{year}-0560.B | {int(digest[4:11], 16) % 10_000_000:07d}"


def brand_for(name: str, legal: str = "", domain: str = "", **fields: str) -> Brand:
    return Brand(name=name.strip() or "Bank of America",
                 legal=legal.strip(), domain=domain.strip(), accent=_accent(name),
                 promo_code=promo_code(name, domain),
                 **{key: str(value) for key, value in fields.items()})


def brand_of_ledger(ledger) -> Brand:
    """账本里的虚拟行身份 → 行标（含行址与客服电话）。"""
    bank = ledger.bank
    return brand_for(
        bank.name,
        bank.legal,
        bank.domain,
        po_box=bank.po_box,
        po_box_alt=bank.po_box_alt,
        city_state_zip=bank.city_state_zip,
        phone_service=bank.phone_service,
        phone_es=bank.phone_es,
        phone_intl=bank.phone_intl,
        phone_metro=bank.phone_metro,
    )


_CITIES = (("Columbus, OH", "43219", "614"), ("Wilmington, DE", "19801", "302"),
           ("Minneapolis, MN", "55402", "612"), ("Charlotte, NC", "28202", "704"),
           ("Salt Lake City, UT", "84111", "801"))


_CONTACT_KEYS = (
    "po_box", "po_box_alt", "city_state_zip",
    "phone_service", "phone_es", "phone_intl", "phone_metro",
)


def contact_fields(name: str, domain: str) -> dict[str, str]:
    """只按行名/域名派生行址与客服电话（不依赖账本，供直接渲染数据时用）。

    真行（Chase/BofA/U.S. Bank …）先走 `synth.ledger` 里那张真行联系方式表：
    模板数据只带 name/legal/domain 时，若一律拿 555-01xx 与随机城市兜底，
    印出来就是「Chase 的账单、555 的客服号、盐湖城的信箱」这种一眼假。
    """
    from .synth.ledger import real_traits

    traits = real_traits(name)
    if traits:
        return {key: str(traits.get(key, "")) for key in _CONTACT_KEYS}

    digest = hashlib.sha256(f"contact:{name}|{domain}".encode()).digest()
    city, zip_code, area = _CITIES[digest[4] % len(_CITIES)]
    tail = digest[5] % 100
    return {
        "po_box": f"P O Box {40000 + int.from_bytes(digest[6:8], 'big') % 20000}",
        "po_box_alt": f"P O Box {40000 + int.from_bytes(digest[8:10], 'big') % 20000}",
        "city_state_zip": f"{city} {zip_code}",
        "phone_service": f"1-800-555-01{tail:02d}",
        "phone_es": f"1-800-555-01{(tail + 7) % 100:02d}",
        "phone_intl": f"1-800-555-01{(tail + 13) % 100:02d}",
        "phone_metro": f"{area}-555-01{(tail + 19) % 100:02d}",
    }


def brand_from_data(data: dict[str, object]) -> Brand:
    """从模板数据里的 ``bank`` 块造一枚行标（MCP / 编辑器直接用数据渲染时用）。"""
    bank = data.get("bank") if isinstance(data.get("bank"), dict) else {}
    name = str(bank.get("name") or data.get("company") or "Bank of America")
    legal = str(bank.get("legal") or bank.get("legal_na") or "")
    domain = str(bank.get("domain") or "")
    return brand_for(name, legal, domain, **contact_fields(name, domain))


def _canvas(width: float, height: float) -> fitz.Document:
    doc = fitz.open()
    doc.new_page(width=width, height=height)
    return doc


def _fit(text: str, fontname: str, max_width: float, max_height: float) -> float:
    """在给定盒子里塞下文字的最大字号。"""
    size = max_height
    width = fitz.get_text_length(text, fontname=fontname, fontsize=size)
    if width > max_width:
        size *= max_width / width
    return size


def wordmark_png(brand: Brand, width: float, height: float, *, scale: float = 6.0) -> bytes:
    """画一枚标：宽扁画词标，近方画方章，窄长画印章加缩写。"""
    width = max(width, 12.0)
    height = max(height, 8.0)
    ratio = width / height
    doc = _canvas(width, height)
    page = doc[0]
    accent = brand.accent
    if ratio >= 3.0:
        bar = max(height * 0.09, 0.6)
        size = _fit(brand.wordmark, "hebo", width * 0.96, height * 0.64)
        bar_w = min(width, fitz.get_text_length(brand.wordmark, fontname="hebo", fontsize=size))
        page.draw_rect(fitz.Rect(0, height - bar, bar_w, height), color=None, fill=accent)
        page.insert_text(fitz.Point(0, height * 0.60), brand.wordmark, fontsize=size,
                         fontname="hebo", color=accent)
    elif ratio >= 1.7:
        side = height
        page.draw_rect(fitz.Rect(0, 0, side, side), color=None, fill=accent)
        page.insert_text(fitz.Point(side * 0.18, side * 0.70), brand.initials,
                         fontsize=side * 0.46, fontname="hebo", color=(1, 1, 1))
        size = _fit(brand.wordmark, "hebo", width - side * 1.35, height * 0.58)
        if size >= 3:
            page.insert_text(fitz.Point(side * 1.30, height * 0.66), brand.wordmark,
                             fontsize=size, fontname="hebo", color=accent)
    else:
        page.draw_rect(fitz.Rect(0, 0, height, height), color=None, fill=accent)
        page.insert_text(fitz.Point(height * 0.16, height * 0.72), brand.initials,
                         fontsize=height * 0.50, fontname="hebo", color=(1, 1, 1))
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    png = pix.tobytes("png")
    doc.close()
    return png


@dataclass(frozen=True, slots=True)
class TextSlot:
    """版式里烙死的一行行址/客服电话/网址：矩形 + 基线 + 字号 + 文本模板。

    ``template`` 用 ``{field}`` 引用行标字段（``po_box``、``city_state_zip``、``phone_service``…），
    ``align="right"`` 时按矩形右边界对齐（底稿里这些值都是右对齐的）。
    """

    rect: tuple[float, float, float, float]
    origin: tuple[float, float]
    size: float
    template: str
    page: int = 0
    align: str = "left"
    fit: bool = False

    def text(self, brand: Brand) -> str:
        return _SLOT_FIELD.sub(lambda m: brand.field(m.group(1)), self.template)

    def ready(self, brand: Brand) -> bool:
        """任何一个字段为空就整槽跳过，免得印出半截空缺。"""
        return all(brand.field(name) for name in _SLOT_FIELD.findall(self.template))



# 页眉/页脚里位置固定的一行行址、客服电话、网址：矩形 + 基线 + 字号 + 文本模板。
# 坐标实测自渲染结果，单位 pt，原点左上。
TEXT_SLOTS: dict[str, tuple[TextSlot, ...]] = {
    "chase-personal": (
        TextSlot((55.0, 61.0, 200.0, 73.5), (58.8, 70.6), 7.76, "{po_box}"),
        TextSlot((55.0, 73.0, 240.0, 85.0), (58.8, 79.7), 7.76, "{city_state_zip}"),
        # 右边界取底稿里客服电话值列的实际右沿（530.1），不是页边 600：
        # 底稿的 "chase.com" 就压在 CUSTOMER SERVICE INFORMATION 盒内的右列，
        # 右对齐到页边会把域名甩到盒子外面（实测）。
        TextSlot((470.0, 103.0, 530.1, 117.5), (528.6, 113.6), 9.6, "{domain}", align="right"),
        TextSlot((455.0, 116.0, 560.0, 129.0), (465.3, 125.8), 9.6, "{phone_service}"),
        TextSlot((455.0, 128.0, 560.0, 141.0), (465.3, 136.6), 9.6, "{phone_es}"),
        TextSlot((455.0, 140.0, 560.0, 153.0), (465.3, 148.4), 9.6, "{phone_intl}"),
        # 正文 "at chase.com/disclosures or call us…"：底稿把域名拆成十几个定位单元，
        # 替换变长后每个单元还按原 x 落笔，会渲染成 “harb orline-b ank”（实测）。
        # 整行擦掉按 Helvetica 重画；矩形下沿留在上一行之下，否则会擦掉上一行的字。
        TextSlot((34.8, 518.6, 343.0, 527.6), (35.0, 524.9), 7.2,
                 # 路径不能丢：底稿印的是 "at chase.com/disclosures or call us…"，
                 # 早先只换域名，印出来是 "at chase.com or call us…"，少了 /disclosures
                 # ——真账单不会把自家的披露页路径剪掉。
                 "at {domain}/disclosures or call us at the number listed on this statement.", fit=True),
    ),
    "chase-business": (
        TextSlot((55.0, 61.0, 200.0, 73.5), (58.8, 70.6), 7.76, "{po_box}"),
        TextSlot((55.0, 73.0, 240.0, 85.0), (58.8, 79.7), 7.76, "{city_state_zip}"),
        TextSlot((34.8, 575.8, 357.0, 584.6), (35.0, 581.8), 7.2,
                 # 参考件是 "Business Accounts at chase.com/business/disclosures or visit a Chase branch."
                 "Business Accounts at {domain}/business/disclosures or visit a {short} branch.", fit=True),
    ),
    "usbank-personal": (
        TextSlot((70.0, 51.0, 230.0, 61.0), (72.0, 58.17), 7.0, "{po_box}"),
        TextSlot((70.0, 59.0, 300.0, 69.0), (72.0, 66.22), 7.0, "{city_state_zip}"),
        TextSlot((500.0, 161.0, 599.0, 172.0), (504.55, 168.75), 9.0,
                 "To Contact {name}", fit=True),
        TextSlot((378.0, 227.0, 560.0, 238.0), (380.15, 234.9), 9.0,
                 "{name} accepts Relay Calls", fit=True),
        TextSlot((378.0, 200.0, 470.0, 211.0), (380.15, 207.85), 9.0, "{city}", fit=True),
        TextSlot((477.0, 242.0, 600.0, 253.0), (547.6, 249.95), 9.0, "{domain}", fit=True),
        TextSlot((528.0, 175.5, 600.0, 187.0), (532.55, 183.8), 9.0, "{phone_service}", fit=True),
        TextSlot((530.0, 187.5, 600.0, 199.0), (534.05, 195.8), 9.0, ""),
        TextSlot((536.0, 211.5, 600.0, 223.0), (540.1, 219.85), 9.0, "{phone_metro}", fit=True),
        TextSlot((18.0, 746.5, 300.0, 757.0), (20.15, 754.1), 8.0,
                 "call {phone_service}.", fit=True),
        TextSlot((18.0, 430.0, 300.0, 440.0), (20.15, 437.15), 7.0, "{legal}", fit=True),
    ),
    "usbank-business": (
        # 商业版右栏不是个人版那套网格（实测：To Contact 161.8 / 24-Hour Business 179.8 /
        # 服务电话 191.8 / Relay 218.9 / 域名 233.9），坐标按各自原稿单独量。
        TextSlot((70.0, 51.0, 230.0, 61.0), (72.0, 58.17), 7.0, "{po_box}"),
        TextSlot((70.0, 59.0, 300.0, 69.0), (72.0, 66.22), 7.0, "{city_state_zip}"),
        TextSlot((500.0, 156.0, 600.0, 172.0), (504.55, 169.5), 9.0,
                 "To Contact {name}", fit=True),
        TextSlot((520.0, 186.0, 604.0, 202.0), (540.1, 199.5), 9.0,
                 "{phone_service}", fit=True),
        TextSlot((378.0, 214.0, 604.0, 229.0), (380.15, 226.6), 9.0,
                 "{name} accepts Relay Calls", fit=True),
        TextSlot((500.0, 231.0, 604.0, 246.0), (547.6, 241.6), 9.0, "{domain}", fit=True),
        TextSlot((18.0, 746.5, 300.0, 757.0), (20.15, 754.1), 8.0,
                 "call {phone_service}.", fit=True),
        TextSlot((18.0, 430.0, 300.0, 440.0), (20.15, 437.15), 7.0, "{legal}", fit=True),
    ),
    "boa-personal": (
        TextSlot((396.0, 121.0, 560.0, 135.0), (399.6, 131.4), 9.5,
                 "Customer service: {phone_service}"),
        TextSlot((396.0, 140.0, 560.0, 154.0), (399.6, 150.12), 9.5,
                 "En Espa\u00f1ol: {phone_es}"),
        TextSlot((396.0, 185.0, 560.0, 197.5), (399.6, 194.76), 9.5, "{po_box_alt}"),
        TextSlot((396.0, 195.5, 560.0, 208.0), (399.6, 204.84), 9.5, "{city_state_zip}"),
        TextSlot((37.0, 85.0, 200.0, 95.0), (39.6, 92.64), 7.0, "{po_box}"),
        TextSlot((37.0, 94.0, 200.0, 104.0), (39.6, 101.52), 7.0, "{city_state_zip}"),
        # 促销框右下角的内部表单码（底稿用复刻字体画，提取不出文本，只能按坐标盖）
        TextSlot((470.0, 742.0, 552.0, 752.0), (472.7, 748.8), 6.6, "{promo_code}", fit=True),
    ),
    "boa-business": (
        TextSlot((396.0, 121.0, 560.0, 135.0), (399.6, 131.4), 9.5,
                 "Customer service: {phone_service}"),
        TextSlot((396.0, 140.0, 560.0, 154.0), (399.6, 150.12), 9.5,
                 "En Espa\u00f1ol: {phone_es}"),
        TextSlot((396.0, 171.0, 560.0, 184.5), (399.6, 181.08), 9.5, "{po_box_alt}"),
        TextSlot((396.0, 183.0, 560.0, 194.5), (399.6, 191.16), 9.5, "{city_state_zip}"),
        TextSlot((37.0, 85.0, 200.0, 95.0), (39.6, 92.64), 7.0, "{po_box}"),
        TextSlot((37.0, 94.0, 200.0, 104.0), (39.6, 101.52), 7.0, "{city_state_zip}"),
    ),
    "amex": (
        TextSlot((360.0, 628.0, 560.0, 640.0), (362.2, 636.85), 8.0, "{phone_service}"),
        TextSlot((360.0, 642.0, 560.0, 654.0), (362.2, 651.05), 8.0, "{mail_line}"),
    ),
}

_SLOT_FIELD = re.compile(r"\{(\w+)\}")

# 除了页眉里的定点槽位，正文段落里也会出现真实行的号码/信箱（法务与帮助段落）。
# 这些位置不固定，按文字扫描就地替换：找到 → 删掉 → 同字号重写。
LITERALS: dict[str, tuple[tuple[str, str], ...]] = {
    "boa-personal": (("800.432.1000", "{phone_service}"), ("1.800.432.1000", "{phone_service}"),
                     ("1.800.688.6086", "{phone_es}")),
    "boa-business": (("800.432.1000", "{phone_service}"), ("1.800.432.1000", "{phone_service}"),
                     ("1.800.688.6086", "{phone_es}")),
    "usbank-personal": (("800-USBANKS (872-2657)", "{phone_service}"),
                        ("800-872-2657", "{phone_service}"),
                        ("usbank.com/YDAA-upcoming-version", "{domain}/YDAA-upcoming-version"),
                        ("usbank.com/book", "{domain}/book"),
                        ("U.S. Bank, P.O. Box 3528, Oshkosh, WI 54903-3528.",
                         "{legal}, {po_box_alt}, {city_state_zip}."),
                        ("U.S. Bank NA, Member FDIC", "{legal}, Member FDIC"),
                        ("U.S. Bank Attn: Consumer Bureau Dispute Handling (CBDH), "
                         "P.O. Box 3447, Oshkosh, WI 54903-3447.",
                         "{legal} Attn: Consumer Bureau Dispute Handling (CBDH), "
                         "{po_box_alt}, {city_state_zip}."),
                        # 这句话原本在底稿里断成两行：「…or by visiting your local U.S.」/「Bank branch.」
                        # 两个 span 各自换掉，跨行读作「your local bank / branch.」，不再拼出真实行名。
                        ("your local U.S.", "your local bank"),
                        ("Bank branch.", "branch."),
                        # 争议处理地址：「…or write to us at U.S. Bank, EP-MN-WS5D, 60 Livingston」+
                        # 下一行「Ave., St. Paul, MN 55107.」，两行一起换成虚构邮编地址。
                        ("U.S. Bank, EP-MN-WS5D, 60 Livingston",
                         "{legal}, {po_box_alt}"),
                        ("Ave., St. Paul, MN 55107.", "{city_state_zip}."),
                        # 首页页脚：这一 span 里同时有国名「available in U.S. only」（不是行名）
                        # 与「contact a U.S. Bank branch or」，只换后者。
                        ("contact a U.S. Bank branch or", "contact your local branch or"),
                        # 商业版这套散文用的是「U.S. Bank P.O. Box 64991 St. Paul, MN / 55164-9505.」
                        ("U.S. Bank P.O. Box 64991 St. Paul, MN", "{legal}, {po_box_alt}"),
                        ("55164-9505.", "{city_state_zip}."),
                        ("U.S. Bank National Association", "{legal}")),
    "usbank-business": (("800-USBANKS (872-2657)", "{phone_service}"),
                        ("800-872-2657", "{phone_service}"),
                        ("usbank.com/YDAA-upcoming-version", "{domain}/YDAA-upcoming-version"),
                        ("usbank.com/book", "{domain}/book"),
                        ("U.S. Bank, P.O. Box 3528, Oshkosh, WI 54903-3528.",
                         "{legal}, {po_box_alt}, {city_state_zip}."),
                        ("U.S. Bank NA, Member FDIC", "{legal}, Member FDIC"),
                        ("U.S. Bank Attn: Consumer Bureau Dispute Handling (CBDH), "
                         "P.O. Box 3447, Oshkosh, WI 54903-3447.",
                         "{legal} Attn: Consumer Bureau Dispute Handling (CBDH), "
                         "{po_box_alt}, {city_state_zip}."),
                        ("your local U.S.", "your local bank"),
                        ("Bank branch.", "branch."),
                        # 争议处理地址：「…or write to us at U.S. Bank, EP-MN-WS5D, 60 Livingston」+
                        # 下一行「Ave., St. Paul, MN 55107.」，两行一起换成虚构邮编地址。
                        ("U.S. Bank, EP-MN-WS5D, 60 Livingston",
                         "{legal}, {po_box_alt}"),
                        ("Ave., St. Paul, MN 55107.", "{city_state_zip}."),
                        # 首页页脚：这一 span 里同时有国名「available in U.S. only」（不是行名）
                        # 与「contact a U.S. Bank branch or」，只换后者。
                        ("contact a U.S. Bank branch or", "contact your local branch or"),
                        # 商业版这套散文用的是「U.S. Bank P.O. Box 64991 St. Paul, MN / 55164-9505.」
                        ("U.S. Bank P.O. Box 64991 St. Paul, MN", "{legal}, {po_box_alt}"),
                        ("55164-9505.", "{city_state_zip}."),
                        ("U.S. Bank National Association", "{legal}")),
    "sofi": (("1-855-456-7634", "{phone_service}"),
             # 原稿把法务段落的联系地址写成 SoFi 总部：信箱与城市行一起换成虚构地址
             ("2750 East Cottonwood Parkway #300, Cottonwood Heights,",
              "{po_box_alt},"),
             ("Utah 84121 as soon as possible.", "{city_state_zip} as soon as possible.")),
    "wellsfargo": (("Portland, OR", "{city_state}"),
                   ("97228-6995", "{zip4}"),
                   ("Wells Fargo Bank", "{legal}"),
                   ("Wells Fargo", "{name}")),
    "amex": (("1-877-221-2639", "{phone_service}"),
             ("P.O. Box 31492 Salt Lake City, UT 84131", "{mail_line}"),
             ),
}


def _spans(page: fitz.Page) -> list[dict]:
    out: list[dict] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            out.extend(line.get("spans", []))
    return out


#: 各主题「自带字体」的正文绘制器。品牌替换默认用 base-14 Helvetica，但那会在
#: 一份全是嵌入子集字体的账单里凭空多出一个非嵌入字体（`pdffonts` 一眼可见）。
#: 有主题字体就用主题字体画，字宽也用它量。
_THEME_FONTS = {"boa": "Reg"}


def _theme_font(template_id: str) -> tuple[object, str] | None:
    name = _THEME_FONTS.get(template_id.split("-")[0])
    if not name:
        return None
    try:
        from .themes.boa import generate as boa

        return boa, name
    except Exception:  # noqa: BLE001 - 拿不到主题字体就退回 base-14
        return None


def _text_width(template_id: str, text: str, size: float) -> float:
    theme = _theme_font(template_id)
    if theme:
        module, fname = theme
        return module.tlen(text, fname, size)
    return fitz.get_text_length(text, fontname="helv", fontsize=size)


def _draw_text(page, template_id: str, x: float, baseline: float, text: str,
               size: float, color: tuple[float, float, float] = (0, 0, 0)) -> None:
    theme = _theme_font(template_id)
    if theme:
        module, fname = theme
        module.txt(page, x, baseline, text, fname, size, color)
        return
    page.insert_text(fitz.Point(x, baseline), text, fontsize=size,
                     fontname="helv", color=color)


def replace_literals(page: fitz.Page, template_id: str, brand: Brand) -> int:
    """把正文段落里零散出现的真实行号码/信箱换掉，返回处理数量。"""
    pairs = LITERALS.get(template_id, ())
    if not pairs:
        return 0
    spans = _spans(page)
    hits: list[tuple[fitz.Rect, float, float, str, float]] = []
    for literal, template in pairs:
        if not all(brand.field(name) for name in _SLOT_FIELD.findall(template)):
            continue
        text = _SLOT_FIELD.sub(lambda m, _t=template: brand.field(m.group(1)), template)
        for span in spans:
            if literal not in span["text"]:
                continue
            baseline = float(span["origin"][1])
            size = float(span["size"])
            for rect in page.search_for(literal, clip=fitz.Rect(span["bbox"])):
                hits.append((rect, size, baseline, text, _room(page, spans, rect, baseline)))
    if not hits:
        return 0
    for hit in hits:
        page.add_redact_annot(hit[0], fill=(1, 1, 1))
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE,
                          graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                          text=fitz.PDF_REDACT_TEXT_REMOVE)
    for rect, size, baseline, text, room in hits:
        size = size if size >= 3 else 8.0
        # 换字后变长时必须缩到「到同一行下一个 span 为止」的宽度，否则会压住后面的正文
        width = _text_width(template_id, text, size)
        if room > 0 and width > room:
            size *= room / width
        _draw_text(page, template_id, rect.x0, baseline, text, size)
    return len(hits)


def _room(page: fitz.Page, spans: list[dict], rect: fitz.Rect, baseline: float) -> float:
    """同一行里，被替换矩形右侧到下一个 span（或页面右内边）之间还剩多少宽度。"""
    right = page.rect.x1 - 30.0
    for span in spans:
        if abs(float(span["origin"][1]) - baseline) > 2.0:
            continue
        x0 = float(span["bbox"][0])
        if x0 >= rect.x1 - 0.5:
            right = min(right, x0 - 1.0)
    return max(right - rect.x0, 0.0)


def _rect_kind(doc: fitz.Document, rect: fitz.Rect, vector: bool) -> str:
    """判定一个配置矩形里装的是什么：图片标（image）还是文字标（text）。

    以首页为准——首页一定画了标。续页有没有标、标在哪个位置各不相同，
    所以「图片标」只在该页真有图时动手，「文字标」只在该页真有字时动手。
    """
    if vector:
        return "vector"
    if doc.page_count and _logo_boxes(doc[0], rect):
        return "image"
    return "text"


def _has_text(page: fitz.Page, rect: fitz.Rect) -> bool:
    return bool(page.get_text("text", clip=rect).strip())


def _has_art(page: fitz.Page, rect: fitz.Rect) -> bool:
    """矢量标：这一页的这个矩形里有没有描边/填充路径。"""
    return any(fitz.Rect(d["rect"]).intersects(rect) for d in page.get_drawings())


def _logo_boxes(page: fitz.Page, rect: fitz.Rect) -> list[fitz.Rect]:
    """这一页 logo 矩形里真正的图片外框。

    各页 logo 的落点并不完全相同（首页 51.8~77、续页 46.8~72），所以按图片实际
    外框擦、按外框画；矩形里没有图就返回空，避免把正文标题一起擦掉。
    """
    page_area = abs(page.rect)
    boxes = []
    for info in page.get_image_info():
        box = fitz.Rect(info["bbox"])
        if not box.intersects(rect):
            continue
        # 铺满整页的图是「扫描颗粒」一类的底纹，不是 logo：按它的外框擦会把
        # 整页正文一起 redact 掉，所以只认明显小于整页的图。
        if abs(box) > page_area * 0.2:
            continue
        boxes.append(box)
    return boxes


def apply(doc: fitz.Document, template_id: str, brand: Brand, *, redraw_logo: bool = True) -> int:
    """就地替换 logo 与行址/客服电话，返回替换的元素数量（0 表示该模板没有配置）。

    ``redraw_logo=False`` 只换文字槽、保留原稿行标图形：账本用的就是模板自带那家
    银行时，重画的文字词标会把 BOA 的旗、Chase 的八边形换成一行字，反而更假。
    """
    rects = PLACEMENTS.get(template_id, ()) if redraw_logo else ()
    slots = tuple(slot for slot in TEXT_SLOTS.get(template_id, ()) if slot.ready(brand))
    if not rects and not slots:
        return 0
    drawn = 0
    logo_pages = LOGO_PAGES.get(template_id)
    for index, page in enumerate(doc):
        # 只在「这一页这个位置真有一个标」时才动手：否则会把正文标题（如 BOA 的
        # INFORMATION:）当成 logo 擦掉，只留下残字
        page_rects: list[tuple[float, float, float, float]] = []
        page_logo_rects = () if (logo_pages and index not in logo_pages) else rects
        for x0, y0, x1, y1 in page_logo_rects:
            rect = fitz.Rect(x0, y0, x1, y1)
            kind = _rect_kind(doc, rect, template_id in VECTOR_LOGO)
            if kind == "vector":
                if _has_art(page, rect) or _has_text(page, rect):
                    page_rects.append((x0, y0, x1, y1))
            elif kind == "text":
                if _has_text(page, rect):
                    page_rects.append((x0, y0, x1, y1))
            else:
                page_rects.extend(tuple(box) for box in _logo_boxes(page, rect))
        page_slots = [slot for slot in slots if slot.page == index]
        # 正文里的字面量每页都要扫（不止页眉那一页）
        drawn += replace_literals(page, template_id, brand)
        for x0, y0, x1, y1 in page_rects:
            page.add_redact_annot(fitz.Rect(x0, y0, x1, y1), fill=(1, 1, 1))
        for slot in page_slots:
            page.add_redact_annot(fitz.Rect(*slot.rect), fill=(1, 1, 1))
        if not page_rects and not page_slots:
            continue
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE,
                              graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                              text=fitz.PDF_REDACT_TEXT_REMOVE)
        for x0, y0, x1, y1 in page_rects:
            rect = fitz.Rect(x0, y0, x1, y1)
            page.insert_image(rect, stream=wordmark_png(brand, rect.width, rect.height))
            drawn += 1
        for slot in page_slots:
            text = slot.text(brand)
            size = slot.size
            x, y = slot.origin
            if slot.align == "right":
                x = slot.rect[2] - _text_width(template_id, text, size)
            if slot.fit and text:
                room = slot.rect[2] - x
                width = _text_width(template_id, text, size)
                if width > room > 0:
                    size *= room / width
            if not text:
                drawn += 1
                continue
            _draw_text(page, template_id, x, y, text, size)
            drawn += 1
    return drawn
