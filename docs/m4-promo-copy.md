# M4 · 促销文案与商品名：把「自家产品」也换掉

M3 之后，行标、行址、客服电话、域名、邮编都是场景银行的；但 BOA 版式的促销框和法务段落里
还留着原版的**产品名与评奖来源**——`Balance Connect™`、`Business Fundamentals® Checking`、
`SafeBalance Banking®`、`SmartAsset`、`Merrill`。一行假行名配一串真产品名，等于自报家门。

## 1. 为什么不用 redact，而是走 `build_subs`

BOA 的静态版式是**逐字坐标**（每个 span 带 `chars_x`，逐字符定位写进 PDF，1:1 复刻原稿）。
对这类 span 做redaction + 重画，只换掉字面量矩形里的几个字，同一行后面的字符仍按原坐标待着，
换长的词就会压字、换短的会留洞。

模板本身已经有一条正确的路：`build_subs(d)` 产生替换表，`sub_text()` 命中后**整行重画**
（`txt(..., fit_x1=s["x1"])`），横向缩放补齐原行宽。M4 只是把促销文案接进这条路径：

```python
# src/billgen/themes/boa/generate.py
_PROMO_KEYS = (
    ("Balance Connect™", "overdraft_product"),
    ("Business Fundamentals® Checking", "business_checking"),
    ("Business Fundamentals®", "business_checking_short"),
    ("Best Banks for Small Business", "award_name"),
    ("SmartAsset.com", "award_domain"),
    ("SmartAsset", "award_source"),
    ("SafeBalance Banking®", "safebalance_product"),
    ("SSM-03-25-0560.B", "form_code"),
    ("Merrill", "brokerage_brand"),
)
```

`d["promo"]` 给了值才替换，没给就保持原稿——`render_regression` 的 32 个默认渲染指纹
因此一动不动，替换只发生在 synth 链路。

## 2. 派生值（`synth/adapters/boa.py::_promo`）

| 位置 | 原稿 | 现在 |
| --- | --- | --- |
| 透支保护产品 | `Balance Connect™` | `{行名} Balance Guard™` |
| 商业支票账户 | `Business Fundamentals® Checking` | `{行名} Business Checking` |
| 评奖来源 | `by SmartAsset*` | `by Small Business Review*` |
| 评奖域名 | `* SmartAsset.com 2025` | `* {域名} 2025` |
| 法务段落 | `SafeBalance Banking®` | 账本里的 `product`（如 `Everyday Checking`） |
| 法务段落 | `Merrill credit card` | `{行名} credit card` |
| 促销框右下码 | `SSM-03-25-0560.B \| 7786995` | `{缩写}-{月}-{年}-0560.B \| {7 位}` |

评奖来源用虚构刊物名，域名用场景银行自己的域名——两者都不指向任何真实机构。

## 3. 一个例外：那串表单码只能盖

`SSM-03-25-0560.B | 7786995` 用复刻字体画，**提取不出文本**（没有 ToUnicode），
所以 `sub_text` 匹配不到、`brand_audit` 也看不见它。这串只能按坐标处理：
`TEXT_SLOTS["boa-personal"]` 里加一条 `(470, 742, 552, 752)` 的槽，字号 6.6、`fit=True`。

教训：文字层扫描不是万能的，肉眼复核仍然是最后一道关。

## 4. 审计与测试

- `audit.BRAND_TOKENS` 增加 `Balance Connect`、`Business Fundamentals`、`SafeBalance`、
  `SmartAsset`、`Merrill`——以后再冒出来就红。
- `tests/test_branding.py::test_real_product_copy_is_replaced` 断言 BOA 五个商品名不出现在文本层。

```bash
.venv/bin/python -m pytest -p no:warnings -q      # 418 passed / 16 skipped
.venv/bin/python tools/brand_audit.py             # 11 模板，只剩 sofi/usbank 的已登记项
```

## 5. 仍然保留的真实品牌（供应链，不是银行身份）

账单正文里出现**真实商户**是必须的：`AMAZON MKTPLACE`、`H-E-B #2664`、`NETFLIX.COM`、
`AUSTIN ENERGY`——这些是持卡人日常消费的对手方，替换掉反而不真实。品牌审计只审**银行自身身份**
（行名、域名、行址、客服电话、自家产品名），不碰商户描述符。
