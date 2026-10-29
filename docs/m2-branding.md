# M2 · 品牌替换包：让行标、行名、域名说同一件事

M1 解决了「数据可信」，但版式里的**品牌美术**还是原版复刻资产：虚构行名 `Harborline Bank`
配上真银行的 logo，一眼就自相矛盾。M2 把这层补齐。

## 1. 三条链路，三种改法

| 残留形态 | 例子 | 处理方式 |
| --- | --- | --- |
| 图片 logo | boa `logo.png`、wf `logo.jpeg`、chase/usbank 底稿里的图片流 | `billgen.branding.apply()` 物理替换 |
| 数据字段 | `bank{name,legal,domain}`、amex 标题/页脚/域名 | 改成数据驱动，数据给了就覆盖 |
| 烙死的字符串 | usbank 底稿里的 `usbank.com`、sofi 法务段落 | M3 起改为槽位/字面量替换（见 [m3-contact.md](m3-contact.md)）；M8 后 `audit.KNOWN_GAPS` 已清空 |

第三条不做「看起来差不多」的猜测，而是**显式记账**：谁还残留、残留几处写死在代码里，
新增残留会让测试立刻变红。

## 2. logo 替换：删掉，而不是盖住

```python
page.add_redact_annot(rect, fill=(1, 1, 1))
page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE,
                      graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                      text=fitz.PDF_REDACT_TEXT_REMOVE)
page.insert_image(rect, stream=wordmark_png(brand, rect.width, rect.height))
```

只盖白矩形的话，原 logo 的图片流仍留在文件里，`page.get_images()` 一抓就出来；
redaction 才是真的删除。`tests/test_branding.py` 断言替换后该矩形里**只剩我们自己的标**。

### 行标怎么画

`wordmark_png()` 用 PyMuPDF 现画，按矩形宽高比分三档：

| 宽高比 | 形态 | 用在哪 |
| --- | --- | --- |
| ≥ 3.0 | 词标 + 底部 accent 粗条 | boa、chase、usbank、capitalone、mercury |
| 1.7 – 3.0 | 方章（首字母白字）+ 右侧词标 | amex |
| < 1.7 | 只画方章 | wellsfargo（正方形红标位置） |

字号由 `fitz.get_text_length` 反解，塞满 96% 宽、64% 高的盒子——早期用手工系数时
`HARBORLINE BANK` 被裁成 `HARBORLINE BAN`，现在的断言直接量文字外框。

颜色来自行名 sha256 在六色板里取，同一个行名永远同一枚标。

### 坐标表

`branding.PLACEMENTS` 是实测并集（pt，原点左上）。同一模板不同页落点略有偏移时取并集，
逐页覆盖即可：boa `(36, 46.8, 205.9, 77.0)`；wellsfargo `(516, 21.8, 564.8, 70.5)`（每页都有）；
amex `(50.4, 26.4, 92.6, 69.1)` + 词标文字区 `(107.1, 26.6, 172.7, 40.6)`；
mercury `(38, 38, 181, 74)`（`logo.pdf` 是矢量，`get_image_info()` 抓不到，坐标直接取自源码）；
capitalone `(57.6, 21.6, 201.6, 72)`；sofi `LOGO_RECT`；chase `(58.1, 35.3, 147.4, 51.8)`；
usbank `(72, 14.4, 196.8, 47.6)`。

## 3. 数据驱动的品牌字段

- **amex**：新增 `DEFAULT_BANK` 与 `bank_of(data)`，标题、页脚 `Accounts offered by … Member FDIC.`、
  域名行三处都读数据；数据没给就保持原稿字样（`render_regression` 的 32 个指纹不受影响）。
  页脚有三处调用，其中两处原来漏传，导致第 2/3 页仍印 American Express。
- **chase**：`build_subs()` 里域名必须先于行名——否则 `Chase.com` 先被 `Chase → 行名` 吃掉，
  只剩 `Harborline Bank.com`；产品名同时存在 `Chase College Checking` / `CHASE COLLEGE CHECKING`
  两种形态，两种都要换。
- 其余模板的 `bank{name,legal,domain}` 本来就是数据驱动，只是缺 logo 替换。

## 4. 开关与默认值

| 入口 | 默认 | 关闭方式 |
| --- | --- | --- |
| `synth.render()` / `render_from_result()` | 替换 | `brand=False` |
| CLI `billgen synth render` | 替换 | `--no-brand` |
| `POST /api/v1/synth/render` | 替换 | `{"brand": false}` |
| 模板默认渲染（`billgen render`、回归指纹） | **不替换** | — |

默认渲染保持原样，是「重构不改版式」的硬约束：品牌替换只属于 synth 链路。

## 5. 审计门禁

```bash
.venv/bin/python tools/brand_audit.py                 # 11 个模板
.venv/bin/python tools/brand_audit.py --from result.json
```

词边界正则匹配（`(?<![A-Za-z0-9])token(?![A-Za-z0-9])`），`PURCHASE` 不会被算成 `CHASE`。
输出分「剩余 / 已知」，出现未登记残留退出码 1。当前状态：

| 模板 | 剩余 | 已知 |
| --- | --- | --- |
| boa-personal/business、wellsfargo、amex、mercury、capitalone、chase-personal/business | 无 | — |
| sofi | 无 | `SoFi` × 12、`sofi.com` × 3（预断行法务段落） |
| usbank-personal | 无 | `U.S. Bank` × 2（M3 之后只剩散文与国名误报，见 [m3-contact.md](m3-contact.md)） |
| usbank-business | 无 | `U.S. Bank` × 3 |

## 6. 验证

```bash
.venv/bin/python -m pytest tests/test_branding.py -q   # 29 条
.venv/bin/python -m pytest -p no:warnings -q           # 417 passed / 16 skipped
.venv/bin/python tools/render_regression.py check      # 32 fixed cases
```

`tests/test_branding.py` 覆盖：11 个模板逐个「矩形里只剩我们的标」、行标确定性、
不同行名不同标、词标不溢出、synth 链路端到端确定性、`brand=False` 回退、
11 个模板的文本层残留审计、词边界正则、`KNOWN_GAPS` 与模板 id 的自洽（M8 起该表为空，`test_no_known_gaps_remain` 钉住）。

### 一次有意的指纹变更

chase 域名替换顺序修复后，默认数据渲染从 `Sample Bank.com` 变成 `example.invalid`，
`render_regression` 的 8 个 chase 个人版指纹随之刷新（`--reason` 里记了原因）。
这是唯一一处默认版式变化，其余 24 个指纹未动。
