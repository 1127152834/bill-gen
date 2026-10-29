# M3 · 联系信息槽位化：行址、客服电话、域名

M2 把 logo 换成了自家的标，但**页眉的另一半还是真银行的**：`P O Box 44959`、
`Indianapolis, IN 46244 - 4959`、`1-800-935-9935`、`801-USBANKS (872-2657)`、
`usbank.com`…… 一个假行名配真行址，比不换 logo 更容易被看出来。M3 把这些槽位也交给账本。

## 1. 三种残留，三种处理

| 形态 | 例子 | 处理 |
| --- | --- | --- |
| 独立文字行（可定位） | chase 的 P.O. Box / 城市行 / 三个电话；boa 的 `Customer service:` 行 | `TEXT_SLOTS`：redact 后按基线重画 |
| 正文里的固定串 | usbank `usbank.com/YDAA-upcoming-version`、`U.S. Bank National Association`、wf `Wells Fargo Bank, P.O. Box 6995, Portland, OR 97228-6995` | `LITERALS`：文字层扫描 + 按原 bbox 原位替换 |
| 图像 / 矢量美术 | logo（M2 已处理） | `PLACEMENTS` |

槽位与字面量都**每页执行**。踩过的坑：早期 `apply()` 在「本页没有槽位」时 `continue`，
于是只有第 1 页页眉被换，第 2、3 页的 `usbank.com` 一个没动——页数一多就露馅。

## 2. 派生：一个行名，一整套联系信息

`synth/ledger.py` 的 `BankIdentity` 现在带整套派生值，全部由银行名 + 种子稳定算出：

| 字段 | 形态 | 说明 |
| --- | --- | --- |
| `po_box` / `po_box_alt` | `P O Box 41793` / `P O Box 48827` | 主址与汇款址，两行不重复 |
| `city_state_zip` | `Charlotte, NC 28202` | 从 5 个虚构总部城市里按摘要选 |
| `phone_service` | `1-800-555-0015` | `555-0100..0199` 是北美保留的虚构号段 |
| `phone_es` | `1-800-555-0035` | 西语线 |
| `phone_intl` / `phone_metro` | `1-800-555-0075` / `704-555-0119` | 国际线与本地线，区号随城市 |

`synth/adapters/_common.py` 的 `bank_contact()` 也从这套值取，不再各自 `digits()` 造邮编——
之前 wf 会出现「Portland, OR 28202-8596」这种城市与邮编对不上的组合。

## 3. 排版：不许溢出，也不许撞行

三个约束，三种手段：

- **右对齐**：`TextSlot(align="right")` 按矩形右边界反推起点（chase 的 `Web site:` 值、
  右栏电话）。
- **缩字号**：`TextSlot(fit=True)` 与 `replace_literals()` 都会在文本比原槽宽时按
  `room / width` 等比缩小。`replace_literals()` 的 `room` 取「被替换矩形到同一行下一个
  span 的距离」（`_room()`），而不是矩形自身宽度——否则换长的串会压住后面的正文
  （usbank 的 `usbank.com/YDAA-upcoming-version` 曾直接盖住 `by calling 24-Hour Banking`）。
- **换行安全**：`replace_literals()` 只替换字面量本身占据的矩形，同一 span 里的后续文字不动。

字号反解必须用**真实字体度量**：`fitz.get_text_length(..., "helv")` 与
`reg.text_length(...)` 能差 10%（amex 域名行因此顶出地址盒）；底稿的 Type3 字体字号
`0.24` 也不是 pt，得按 bbox 反推（chase 实测 = 9.6pt）。

## 4. 覆盖范围（11 个后端模板）

| 模板 | 槽位 | 字面量 |
| --- | --- | --- |
| boa-personal / boa-business | 左侧 P.O. Box + 城市；右侧客服线、西语线、汇款址、域名 | `800.432.1000` 等旧号 |
| chase-personal / chase-business | P.O. Box、城市行、域名（右对齐）、三条电话 | 产品名大小写两种形态 |
| usbank-personal / usbank-business | P.O. Box、城市行、`To Contact …`、`… accepts Relay Calls`、城市、域名、`{legal}`、两条电话、页脚 `call …` | `usbank.com/*`、`800-USBANKS (872-2657)`、`Oshkosh` 两处信箱、`U.S. Bank NA, Member FDIC`、`your local U.S.` / `Bank branch.` 跨行拆分 |
| wellsfargo | — | `Wells Fargo Bank`、`Portland, OR`、`97228-6995` |
| amex | 客服线、`mail_line` | — |
| mercury / capitalone / sofi | 数据字段本就干净，只剩 sofi 预断行法务段落 | — |

## 5. 当前残留（`audit.KNOWN_GAPS`）

**空。** 11 套模板的文本层不再放行任何真实品牌字样（M8）。

| 曾登记 | 处理 |
| --- | --- |
| usbank `U.S. Bank` × 2（`your local U.S.` + `Bank branch.` 跨行拆分） | 两个 span 各自换成 `your local bank` / `branch.`，跨行读作一句 |
| usbank `contact a U.S. Bank branch or`（首页页脚） | 换成 `contact your local branch or`；同一 span 里的国名 `available in U.S. only` 不动 |
| sofi `SoFi` × 12、`sofi.com` × 3（预断行法务段落） | 品牌态把整段拼回去重新断行，由长到短试字号塞回原行数（见 [m8-realism.md](m8-realism.md#sofi-法务段落)） |

BOA 促销框与法务段落里的商品名（`Balance Connect™`、`SafeBalance Banking®`、`SmartAsset`、
`Merrill`）已在 [M4](m4-promo-copy.md) 处理完。

新增残留会让 `tools/brand_audit.py` 退出码 1，`tests/test_branding.py` 同步变红。

## 6. 验证

```bash
.venv/bin/python -m pytest tests/test_branding.py -q          # 品牌 + 联系信息
.venv/bin/python -m pytest -p no:warnings -q                  # 全量
.venv/bin/python tools/brand_audit.py                         # 11 模板审计
.venv/bin/python tools/render_regression.py check             # 32 默认渲染指纹
```

默认模板渲染路径不调用 `branding.apply()`，所以 `render_regression` 的指纹与 M2 一致：
品牌/联系信息替换只发生在 synth 链路（`brand=True`，可用 `--no-brand` / `{"brand": false}` 关）。

## 7. 正文里的域名：整行重画（M5 补）

底稿把 `at chase.com/disclosures or call us…` 这一行按「定位单元」拆成十几个元素
（`h` `a` `r` `b` `or` `lin` …），每个单元都带自己的 x。替换成 `harborline-bank.com`
之后单元数变多、字宽变长，但每个单元仍按原 x 落笔，于是渲染成
`harb orline-b ank.example`——文本层看着是对的，画面上是断开的。

修法：把这一行当成一个 `TextSlot`，整行擦掉改用 Helvetica 按原基线重画。
`tests/test_branding.py::test_chase_body_domain_is_redrawn_as_one_run` 锁住三件事：

1. 文本层是一整句话（`at {domain} or call us at the number listed on this statement.`）；
2. 不再有 `harb orline` 这种碎字；
3. **上一行不能缺字**——`page.apply_redactions()` 是按「矩形与字形相交」删字的，
   槽位矩形一旦压到上一行（那一行的 bbox 下沿只比本行字形带高 2pt），
   `Fee on this account (if applicable), or if you have any questions…` 就会被一起擦掉。
   实测踩过两次，最终把矩形下沿定在 `y0-1.2` 之上（`518.6`）才稳。

`chase-business` 同理（那一行本身是一个 span，整句重画）。
