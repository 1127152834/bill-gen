# 银行账单的「真」体现在哪里

这份文档是 `billgen` 的拟真清单：**哪些规律是真账单里稳定出现的**、我们靠什么实现、哪些只是看起来像但其实会露馅。

每条规律都标注了「依据」（底稿 PDF 词法 / 公开样本 / 供应商习惯）与「落点」（代码位置）。

---

## 1. 页面家具（每一页都要有，缺一个就不像）

| 元素 | 真件形态 | 落点 |
| --- | --- | --- |
| 抬头 | 行名 + 产品名（`Your Advantage Plus Banking` / `Everyday Checking`） | 各主题 `generate*.py` |
| 账期 | `January 1, 2027 to January 31, 2027`，与账户周期严格对齐 | `synth/ledger.py` `period` |
| 账号 | 真件多印全号或掩码（`xxxxxxxx1347`），不印 `0000…` | `ledger.AccountRef` |
| 页码 | `Page 1 of 4`；只有账单页参与计数，披露页另算 | `usbank` 的 `Page 1 of 2` 就是这种口径 |
| 客服块 | 电话 / 网址 / 信箱 / En Español，且是**这家行自己的** | `branding.contact_fields()` + `ledger.real_traits()` |
| 无纸化声明、余额保护、促销盒 | 每家有固定句式，随产品名派生 | 各主题 `_promo()` |
| 页脚表单码 | `PULL: B  CYCLE: 12  SPEC: L  DELIVERY: E  TYPE: I  IMAGE: I  BC: CA` | `themes/boa/generate.py::_cycle_ctrls` |
| MICR 行 | `⑆路由⑆ 账号⑈ 序号⑈ 0000 AT 00` | `themes/chase` MICR 四段 |
| 条码 / QR | 跟踪号派生，尺寸 600dpi 级别 | 各主题 |

**反例（我们踩过）**：`Page 1 of 2` 出现在 4 页文档上；客服电话是 `1-800-555-01xx`；信箱地址随机生成 —— 这三样任意一个出现，行家一眼就知道是拼的。

---

## 2. 时间规律

- **账期**：自然月或行方固定周期（Chase 常见 12/25–1/24），周期长度 28–31 天。
- **出件日**：周期结束后 1–3 个工作日（`createdAt`），不是当天。
- **固定账单**：房租、水电、保险、订阅落在 `dayOfMonth` 锚点上，跨月不漂移。
- **工资**：每月 1/15 两次，或每 14 天（biweekly）——`place.ts` 的 `biweekly` 从锚点双向展开。
- **利息**：周期最后一天入账。
- **周末**：借记卡消费照常入账（真件如此），不要画蛇添足地把周末行挪到周一。
- **同日多行**：银行按入账顺序列，不排序金额。

**落点**：`flow/packages/core/src/schedule/place.ts`（`recurringDates`）、`amounts.ts`、`noise.ts`。

---

## 3. 金额规律

- 房租/水电/月费：整数或 `.00`；订阅常见 `.99`。
- 卡消费：带零头（`.42`、`.87`），**不要**整元。
- ATM：整十（20/40/100/200）+ 单独的 `ATM SURCHARGE 3.00`。
- 支票：整数。
- 合计必须闭合：`期初 + 贷 − 借 = 期末`，且分表合计与 summary 行一致 —— `synth/lint.py` 会拦。

---

## 4. 描述符（最容易被看穿的地方）

### 4.1 通用字段宽度（NACHA）

| 字段 | 宽度 | 例 |
| --- | --- | --- |
| Company Name | 16 | `ACME CORP` |
| Company Entry Description | 10 | `PAYROLL` |
| Individual Name (INDN) | 22 | `MAYA OKAFOR` |
| Trace Number (ID) | 15 | `051000014297439` |
| CO ID | `XXXXX` + 5 位公司号 | `CO ID:XXXXX42831` |

超宽就**硬切**，不印省略号 —— 真账单里没有 `…`。

### 4.2 每家自己的行文

| 银行 | 卡行 | ACH 行 | 支票 | Zelle | ATM |
| --- | --- | --- | --- | --- | --- |
| Chase | `Card Purchase 12/11 商户 Card 1347` | `ACH Debit 12/10 商户` | `Check Paid 12/18 CHECK 1110` | `Zelle Payment 名字 12 位号` | `ATM Withdrawal 12/17 ATM TERMINAL 7014 AUS` |
| BoA | `TST* 商户 12/11 Card 1347` | `DES:… ID:… INDN:… CO ID:XXXXX##### PPD\|WEB` | `CHECK 1110` | `Zelle payment to 名字 Conf# w9wh533i7` | `ATM WITHDRAWAL 001 商户 AUSTIN TX` |
| U.S. Bank | `商户 AUSTIN TX`（Ref 列配 10 位号） | 主行 + `REF=<trace>Y00` + `PAYROLL ####` 两行续行 | Ref 列印支票号 | `On 12/16/26 PMT\|ID=USB…` | `ATM TERMINAL 7014 AUS` |
| Wells Fargo | `Card Purchase 12/11 商户 CITY ST` | `商户`（存/取各一列） | `Check 1110` | `Zelle Payment To 名字` | `ATM Withdrawal 12/17 ATM 7014 AUS` |
| Amex | `DEBIT CARD PURCHASE 商户 CITY ST`（`ID` 第二行） | `ACH CREDIT/Debit 商户` | — | — | — |
| Capital One | `商户 CITY ST` + **CATEGORY 品类词** | `商户` | `CHECK 1100` | `Zelle Payment 名字` | `ATM 7014 AUS` |
| SoFi | `商户 CITY ST` + TYPE `Card Purchase` | TYPE `Direct Payment`（借记）/ `Deposit`（贷记） | TYPE `Check` | TYPE `Transfer` | TYPE `Withdrawal`，内部转账 `To Savings - 1943` |
| Mercury | 只印商户名（`AWS EMEA`） | TYPE `ACH In/Out`，≥$5,000 贷记记 `Wire In` | TYPE `Check` | TYPE `Zelle` | `ATM WITHDRAWAL CITY ST` |

### 4.4 卡尾号：只有部分银行印在流水行里

| 银行 | 行内表现 | 依据 |
| --- | --- | --- |
| Chase | `Card Purchase 12/11 商户 Card 1347`、`De Card 4499` | 底稿 `chase/assets/chase_static_personal.pdf` 原文 |
| Bank of America | `TST* 商户 12/11 Card 1347` | BoA 主题示例数据（来自真实账单格式） |
| Wells Fargo | 行内**不印**，卡号在页眉账户区 | WF 版式为「存/取分列」，无卡号列 |
| Capital One | 不印；账户尾号在页眉 `360 Checking...5521` | `C1-Personal-Statement-SAMPLE.pdf` |
| SoFi | 不印；页眉 `Checking Account - 4287`，**内部转账才印目标账户** `To Savings - 1943` | `SoFi-Statement-SAMPLE.pdf` |
| Mercury | 不印；Type 列写 `Card` | `Demo-Bank-Statement-SAMPLE.pdf` |
| Amex | 不印；第二行给 `ID …` | Amex 样张 |
| U.S. Bank | 不印；类型进 Type 列，号进 Ref 列 | 底稿已清洗，按现版式保留 |

**结论**：卡尾号不是「全都一样」的东西 —— 有的行印（Chase/BoA），有的靠页眉账户尾号区分（C1/SoFi/WF/Mercury），
所以在 `line_styles` 里它是**按银行开关**的，不是全局参数。

### 4.3 城市州

- 线上/全国品牌印**注册地**：`NETFLIX.COM LOS GATOS CA`、`APPLE.COM/BILL CUPERTINO CA`、`STEAM GAMES BELLEVUE WA`。
- 实体商户印**持卡人所在城市**：`H-E-B 0611 AUSTIN TX`。
- 实现：`synth/line_styles.py::merchant_location()`（品牌表 + 兜底）。

---

## 5. 每家独有的列（留空就是穿帮）

| 模板 | 独有列 |
| --- | --- |
| Capital One | `CATEGORY`（Groceries / Dining / Bills & Utilities / Income / Transfers / Cash / Merchandise / Entertainment / Transportation / Health Care / Travel / Insurance） |
| SoFi | `TYPE`（Deposit / Direct Payment / Card Purchase / Withdrawal / Check / Fee / Interest Earned / Transfer） + `Transaction ID:` 续行 |
| Mercury | `Type` + `End of Day Balance`；`Date (UTC)` |
| U.S. Bank | `Ref Number` |
| Amex | Credits / Debits 双列 + `ID` 行 |
| Wells Fargo | Deposits/Additions 与 Withdrawals/Subtractions 分列 + `Ending daily balance` |

---

## 6. 截断

- 真账单**硬切**：`AMAZON MKTPLACE PMTS AMZN.COM/BILL WA` 超宽就停在字符边界，不带 `…`。
- 列宽换算：8pt MyriadPro 下 259pt ≈ 58 字符（Wells Fargo 描述列），40 字符（BoA 允许折行）。
- 落点：`synth/adapters/_common.py::clip`。

---

## 7. 反模式清单（出现即假）

- `SAMPLE` / `EXAMPLE` / `DEMO` / `DUMMY` 任何位置（`synth/lint.py` 已阻断）
- `1-800-555-01xx`、随机城市信箱
- 路由号 `000000000`、ACH trace `00000000…`
- `AMZN Mktp US*AMAZON MKTPLACE…`（叠前缀）
- `… …`（双省略号）
- `Checks -0.00`（零值带负号）
- CATEGORY 列写 `Credit` / `Debit`
- 金额写成 `- $86.42`（符号后带空格）、`+ $3,125.80`（正号）
- 摘要页与流水页数字不一致、分表合计与 summary 不一致

---

## 8. 元数据

| 模板 | title | producer |
| --- | --- | --- |
| BoA | `Bank of America - Online Banking Statement` | `TargetStream StreamEDS rv1.7.161 for Windows` |
| Chase | `Chase Online Statement` | `OpenText Output Transformation Engine 16.6.0` |
| U.S. Bank | `U.S. Bank Online Statement` | `Ricoh Americas Corporation, AFP2PDF v3.2.1` |
| Capital One | `Capital One Statement` | `openhtmltopdf.com`（含 XMP） |
| SoFi | `SoFi Bank Statement` | `LibreOffice 7.0` |
| Mercury | `Mercury Statement` | `react-pdf` |
| Wells Fargo | `Wells Fargo Online Statement` | `iText® Core 9.5.0 (AGPL version) ©2000-2025 Apryse Group NV` |

字体必须用各家品牌字（BoA 的 `Connections`/`HigherStandards`、Amex 的 `BentonSans`、WF 的 `MyriadPro`…），
**不能**出现 macOS 兜底字体（`Hiragino Sans GB`、`PingFang`）——那是生成环境的指纹。

---

## 9. 代码映射

| 规律 | 位置 |
| --- | --- |
| 行文按银行分家 | `src/billgen/synth/line_styles.py` |
| 描述符截断 | `src/billgen/synth/adapters/_common.py::clip` |
| NACHA 字段宽度 | `src/billgen/synth/adapters/boa.py::_descriptor` |
| 城市州 | `line_styles.py::merchant_location` |
| 支票号 / trace / Zelle 号 | `src/billgen/synth/refs.py`、`ledger.py::_attach_references` |
| 合计闭合 | `src/billgen/synth/lint.py` |
| 占位词拦截 | `src/billgen/synth/lint.py::placeholder_hits` |
| 知识库（给模型的规范） | `flow/skills/bank-descriptors.md` §15 |
| 补全提示词 | `flow/packages/core/src/graph/nodes/render.ts` |
| MCP 契约 | `src/billgen/mcp/contract.py::_COMMON_RULES` |
