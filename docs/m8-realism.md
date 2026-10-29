# M8 真实化（P0–P3）

目标：把「看起来像」推进到「查起来也像」。所有改动都在 synth 链路，模板默认渲染不动，
所以 32 个 `render_regression` 指纹不变。

## P0 文档画像（`synth/profile.py`）

| 模板 | Producer | Creator |
|---|---|---|
| boa-personal / boa-business | `TargetStream StreamEDS rv1.7.161 for Windows` | `Bank of America` |
| wellsfargo | `iText® Core 9.5.0 (AGPL version) ©2000-2025 iText Group NV` | （空） |
| chase-personal / chase-business | `OpenText Output Transformation Engine 16.6.0` | （空） |
| usbank-personal / usbank-business | `Ricoh Americas Corporation, AFP2PDF v3.2.1` | `AIM, F3` |
| amex | `Smart Communications - SC32` | （空） |
| mercury | `react-pdf` | `react-pdf` |
| capitalone | `openhtmltopdf.com` | （空） |
| sofi | `LibreOffice 7.0` | `Writer` |

- 出件时间：`createdDate = modDate = 周期结束 + 1~3 天`，06:00–22:59，由
  `sha256(seed, 模板, 周期结束日)` 派生，带 `-07'00'` 偏移。原来是周期最后一天
  `00:00:00`，时刻过于整齐。
- 摘掉 `StructTreeRoot` / `MarkInfo`：PyMuPDF 只能把键置成 `null`，键名仍留在
  catalog 里，所以 `synth/profile.py::_rebuild_catalog()` 把 catalog 整个重建成新对象
  （只保留有值的条目），再把 trailer 的 `/Root` 指过去。重建后 chase 的 catalog 只剩
  `/Type` + `/Pages`，与真实样本一致；`tests/test_realism.py` 对 8 个模板逐一断言。
- **不动** title / subject / `SAMPLE` 水印：那是账本自己声明的「这是样例」。

开关：CLI `--no-docinfo`、HTTP `{"docinfo": false}`、`render(..., docinfo=False)`。

## P1 入账日（`synth/realism.py::postings`）

- ACH / FEE / INTEREST / CHECK 只能落在工作日：周末与联邦假日提前到上一个工作日。
- POS / ATM / Zelle 保持原日（真账单里周末刷卡、ATM 取现、即时转账都正常）。
- 提前后按日期重排并**重算余额链**，`期初 + 贷 − 借 = 期末` 恒等式仍成立。
- 生成侧（`flow/`）本来就有这条规则，这里是双重保险；实测三期结果 0 笔需要挪动。

## P2 收单方前缀（`synth/realism.py::descriptors`）

POS 行按哈希补一个收单方前缀（`SQ *`、`TST* `、`PAYPAL *`、`AMZN Mktp US*`、`SP `），
已经带前缀的不重复加。效果：

```
Card Purchase  08/26 SQ *BLICK ART MATERIALS 4896 AUS        Card 1347   -46.52
Card Purchase  08/02 PAYPAL *WHOLEFDS LMR 7925 AUST          Card 1347   -27.39
```

金额尾数（`amounts()`）**没有接进主流程**：改金额会让期末余额与上游汇总对不上，
而这条恒等式正是 `ledger_check_failed` 的判据（拿它抓坏数据比补零头重要）。
要启用得连上游汇总一起改。

## P3 条码 / IMB（`synth/adapters/chase.py::barcode_number`、`usbank.py::imb_serial`）

- Chase 竖排阶梯条码：20 位 = 账号 12 位 + 账期号 2 位 + 6 位流水，引擎会把第 11 位
  改成页码。原来 `barcode_num` 为空，条码里是**底稿原银行**的账号。
- US Bank 智能邮件条码：20 位 IMB 跟踪号 = `00` + `040`（服务类型）+ 6 位客户号 +
  9 位流水，由账号与账期派生；原来留底稿原值。
- chase 条码旁那 4 位小号：底稿把它画成 4 个独立 Type3 单元，ToUnicode 全映射成 `'0'`
  （文本层永远是 `0000`），但字形码换成 1~9 之后**画面**就对上了。`relabel_tracking()`
  只改单元码位，不擦不改字体：条码 `…7573` → 旁边印 `7573`，逐页递增。
  `test_chase_tracking_label_follows_the_barcode_number` 从内容流里读回这 4 个码位比对。

## 验收

```bash
.venv/bin/python -m pytest tests/test_realism.py -q     # 18 条
.venv/bin/python -m pytest -p no:warnings -q            # 493 passed / 16 skipped
.venv/bin/python tools/render_regression.py check       # 32/32，指纹不变
.venv/bin/python tools/brand_audit.py                   # 11 套模板全 OK，KNOWN_GAPS 为空
```

`tests/test_realism.py` 锁住：工作日判定（含独立日调休、感恩节）、ACH 从周六提前到周五、
POS/ATM 留在周末、收单方前缀只加一次、余额链闭合、每个模板的 producer/creator、
出件时间落在「周期后 1~3 天的 6–22 点」、渲染后 docinfo 生效且 SAMPLE 标题保留、
chase 条码号是 20 位且以本账号开头。

## sofi 法务段落（品牌态重排）

`themes/sofi/sofi_generate.py` 的 `LEGAL_PARAS` 是按原稿 baseline 逐行断开写死的
（12 处 `SoFi`、3 处 `sofi.com`、落款 1 处）。虚构行名比 `SoFi Bank` 长，逐行改字必然
撞行，所以品牌态换了一条路：

1. 把同一段的非标题行拼回一整句，替换 `SoFi Insured Deposit Program` → `{name} …`、
   `SoFi Bank, N.A.` → `{legal}`、`SoFi` → `{name}`、`sofi.com` → `{domain}`；
2. 用 `_wrap()` 按版心宽度重排，行数多于原槽位时由 6.0pt 起每次减 0.1pt 重排，直到塞回原行数；
3. 每行仍画在原稿的 y 值上（段落间距不变），标题行单独按可用宽度缩字号；
4. 行内出现域名就按原样式补下划线与 `LINK_URI` 注释。

`ctx.data["bank"]` 不存在时（默认数据、`billgen render sofi`）走原来的逐行路径，
**一个字都不动**，所以 `tools/render_regression.py` 的 32 个指纹不受影响；
`test_sofi_default_render_keeps_the_original_legal_copy` 钉住这一条。

顺带修掉一个只有续页才看得见的 bug：sofi 的 logo 是矢量描边、`_has_art()` 对续页的表格
也判真，于是「HARBORLINE BANK」被画到第 2 页的金额栏上。`branding.LOGO_PAGES = {"sofi": (0,)}`
把 logo 替换限制在首页。
