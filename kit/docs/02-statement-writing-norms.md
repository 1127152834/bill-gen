# 流水书写规范（Statement Line Norms）

> 目标：任何一行流水，拿出来都能回答四个问题——**哪一天、谁收的钱、多少钱、凭什么这行长这样**。
> 这份文档分三层：① 通用规矩（所有银行都成立）② 行类词表（这笔钱是什么性质）③ 逐家差异（同一笔钱在不同银行长什么样）。
> 逐家部分与 `kit/contracts/*.json` 一一对应：**契约是机器读的，这份文档是人读的，两边必须同步**（改契约就改这里）。

---

## 一、通用规矩（12 条，缺一条就会露馅）

| # | 规矩 | 为什么 | lint 规则名 |
| --- | --- | --- | --- |
| 1 | 描述符 **全大写**（除少数银行的 `Zelle`/`Card Purchase` 前缀） | 银行核心系统多是大机字符集，从不做大小写混排 | `descriptor-case` |
| 2 | 日期格式逐家固定（`%m/%d`、`%m/%d/%Y`、`%b %-d`），**同一份账单里不许混用** | 混用是模板拼接的典型痕迹 | `date-format` |
| 3 | 金额两位小数，负数用「取款列」或前置 `-`，**不许出现 `+$1,200`** | 负号/列位置本身就是信息 | `amount-precision` |
| 4 | 余额链逐行连续：`上一行余额 + 本行金额 = 本行余额` | 这是会计恒等式，错一位就全崩 | `balance-chain` |
| 5 | 排序：绝大多数**按日期升序**；SoFi 倒序（最新在上） | 每家 EBank 的打印顺序不同，别统一 | `row-order` |
| 6 | 描述符长度逐家不同：Mercury 42、U.S. Bank 36、多数 90 | 超过就被截断，截断位置会暴露模板边界 | `descriptor-length` |
| 7 | 商户名后是否带「城市 州」逐家不同：Chase/BoA/WF/C1/SoFi 带，Mercury/USB 不带 | 见第三节表 | `has_place` / `no_city` |
| 8 | 卡尾 `Card 1234` 只在部分银行出现在**行内**（Chase/BoA） | 其余银行把卡号印在**页眉账户区** | `has_card_tail` / `no_card_tail` |
| 9 | 门店号/分店号是 3–4 位数字，且**同品牌同月不该重复同一个号** | 真实门店号是固定值，但不是随便抄一个 | `store-variety` |
| 10 | 参照号（trace/conf/check no.）格式逐家不同，见第四节 | 参照号是「银行内部系统的指纹」 | `reference-format` |
| 11 | 禁止字面量：`SAMPLE`、`EXAMPLE`、`Lorem`、生成器名（`billgen`、`PyMuPDF`）、**别家银行名** | 一个 `SAMPLE` 水印能毁掉整份材料 | `forbidden-literal` |
| 12 | 不许有前导空格/制表符的填充对齐 | 打印系统靠定位坐标，不靠空格 | `no-leading-padding` |

---

## 二、行类词表（先定性，再决定长什么样）

| 行类 | 语义 | 典型金额特征 | 谁决定金额 |
| --- | --- | --- | --- |
| `card` / `Card Purchase` | 刷卡/线上消费 | 有零头，绝大多数不是整数 | 求解器（类目分布） |
| `atm` | ATM 取现 | 整数（$20 的倍数） | 求解器 |
| `ach_debit` | ACH 扣款（房租、水电、保险、订阅） | 月中固定日，金额稳定 | 求解器（账单周期） |
| `ach_credit` | ACH 入账（工资、平台结算） | 半月/双周一次，金额稳定 | 求解器 |
| `zelle` | 人对人转账 | 整数或整十，带对方姓名 | 求解器 + 选角（姓名） |
| `wire` | 电汇（Fedwire） | 大额、整数、带 IMAD/Trn | 求解器 |
| `check` | 支票 | 整数，带支票号 | 求解器 |
| `fee` | 手续费/月费 | 小额固定（$5/$12/$15/$35） | 契约/银行表 |
| `interest` | 利息 | 小额，可能为负（透支利息） | 求解器 |
| `transfer` | 账户间转账 | 整数，带对方账号尾号 | 求解器 |
| `deposit` | 现金/支票存入 | 整数或整百 | 求解器 |
| `refund` | 退款 | 与某笔消费金额相关（可等于） | 求解器 |

**规矩**：行类决定「哪些字段必填」（见 `04-field-checklist.md`）。导演只给行类与语义字段，**不给金额**。

---

## 三、逐家差异（11 套，照抄这张表就不会串台）

### 3.1 JPMorgan Chase（个人 / 企业）

| 项 | 个人 | 企业 |
| --- | --- | --- |
| 结构 | `Date │ Description │ Amount │ Balance` | 同 |
| 日期 | `%m/%d` | 同 |
| 卡行 | `{商户} {城市} {州} Card {后四位}`，例：`AMAZON.COM*2H4KL9 SEATTLE WA Card 4499` | 同（企业也带卡尾） |
| ACH | `{收款方}`，大写 | 同 |
| Zelle | `Zelle Payment to {姓名} {conf#}` | 同 |
| 支票 | `Check Paid {mm/dd} {支票号}` | 同 |
| 电汇 | `Online Domestic Wire Transfer Via: {银行}/{路由号} A/C: {户名} … Imad: {22位} Trn: {10位}`，**仅借记电汇** | 同 |
| 卡尾位置 | 行内 | 行内 |
| 特殊 | 只有行标签，没有 Type 列；续页有 1pt 隐形标记（来源于真实样本） | 磁码四段含 `DRE 703 219 28826 NNNNNNNNNNN`（个人）/ `802 219 05926`（企业） |

### 3.2 Bank of America（个人 / 企业）

| 项 | 内容 |
| --- | --- |
| 结构 | **两栏**：`Deposits and other additions` / `Withdrawals and other subtractions`，各自 `Date │ Description │ Amount`，无余额列 |
| 卡行 | `{商户} {mm/dd} Card {后四位}`，例：`TST* WHOLEFDS MKT 105 12/11 Card 1347`（**商户里常带 `TST*`/`SQ *` 这类收单标识**） |
| ACH | NACHA 风格拼接：`DES:{描述} ID:{ID} INDN:{户名} CO ID:XXXXX{5位} {SEC码}`，例：`DES:PAYROLL ID:1234567890 INDN:JOHN DOE CO ID:XXXXX12345 PPD` |
| Zelle | `Zelle payment to {姓名} Conf# {10位}`（注意 BoA 用 `payment` 小写 p） |
| 支票 | `CHECK {号} {说明}` |
| 特殊 | 无 Type 列；`CO ID` 固定 5 位数字；**没有参考 PDF**，这些规则来自适配器实现，`open_questions` 已标注「必须拿真实账单核对」 |

### 3.3 Wells Fargo

| 项 | 内容 |
| --- | --- |
| 结构 | `Date │ Description │ Amount │ Balance`，**取款/存款分列**（金额列带方向） |
| 日期 | `%m/%d` |
| 卡行 | `Card Purchase {mm/dd} {商户} {城市} {州}`（**日期在描述里再出现一次**，不带卡尾） |
| ATM | `ATM Withdrawal {mm/dd} {地点}` |
| 支票 | `Check {号} {说明}` |
| Zelle | `Zelle {对方姓名}` |
| 特殊 | 卡号只出现在页眉 `Account number …` 区；无 Type 列 |

### 3.4 Capital One（360 Checking）

| 项 | 内容 |
| --- | --- |
| 结构 | `Date │ Description │ Category │ Amount │ Balance` |
| 日期 | `%m/%d` |
| 卡行 | `{商户} {地点}`（必须在描述里带城市/州或线上标识），**不带卡尾** |
| ACH | 直接写 NACHA 文本（`{nacha_text}`），不加额外前缀 |
| Category 列 | 不是交易类型，是**消费分类**（`Dining`/`Groceries`/`Gas`…），与行类不是一回事 |
| 特殊 | 页眉印 `360 Checking …5521`；`capitalone.com` 必须出现 |

### 3.5 SoFi

| 项 | 内容 |
| --- | --- |
| 结构 | `Date │ Description │ Type │ Amount │ Balance` |
| 日期 | `%m/%d/%Y`（**唯一用四位年的**） |
| 排序 | **倒序（最新在上）**——这是 SoFi 的标志，别的家别学 |
| 卡行 | `{商户} {地点}`，不带卡尾 |
| 内部转账 | `To Savings - {后四位}` / `From Savings - {后四位}` |
| Type 列 | `Card Purchase / Withdrawal / Check / Fee / Interest Earned / Transfer / Direct Payment / Deposit` |
| 特殊 | 页眉 `Checking Account - 4287`；必须出现 `SoFi Bank, N.A.`、`Cottonwood Heights, Utah 84121` |

### 3.6 Mercury

| 项 | 内容 |
| --- | --- |
| 结构 | `Date (UTC) │ Description │ Type │ Amount │ End of Day Balance` |
| 日期 | `%b %-d`（例 `Sep 3`，无前导零），账单头写 `All dates in UTC` |
| 卡行 | **只有商户名**（`RALPHS 7585`），不许带城市、不许带卡尾，长度 ≤ 42 |
| ACH | `{收款方}`，去 `PAYMENT` 尾巴 |
| ATM | `ATM WITHDRAWAL {城市} {州}` |
| Type 列 | 只有三个值：`Card` / `ACH Out` / `ACH In` |
| 特殊 | 企业账户印 EIN（`EIN ••1234`），**个人账户不印**；页脚 `Banking services provided by Column N.A., Member FDIC` |

### 3.7 American Express（信用卡账单口径）

| 项 | 内容 |
| --- | --- |
| 结构 | `Date │ Description │ Amount`（信用卡无余额列，只有 `Amount`），另有 `Payments & Credits` 与 `New Charges` 分区 |
| 卡行 | `{商户} {城市} {州}`，**第二行是 `ID {参照号}`**（例 `ID 3202458712`） |
| 参照号 | 10 位数字，逐笔唯一 |
| 特殊 | 无卡尾、无余额链；有「Minimum Payment Due / Payment Due Date」区块 |

### 3.8 U.S. Bank（个人 / 企业）

| 项 | 内容 |
| --- | --- |
| 结构 | `Date │ Description │ Type │ Ref │ Amount`（**没有余额列**） |
| 日期 | `%m/%d` |
| 卡行 | `{商户} {地点}`，≤ 36 字，全大写，不带卡尾 |
| Type 列 | `Purchase / ATM / ACH / Zelle / Check / Fee / Interest / Transfer / Payment / Deposit / Refund` |
| Ref 列 | 7 位数字参照号 |
| 账号印法 | `1 964 9173 9161`（**1-3-4-4 分组，不是掩码**） |
| 特殊 | 披露页与空白页**不编号**，`Page k of N` 的 N 只数账单页 |

---

## 四、参照号 / 标识符规范

| 名称 | 出现位置 | 格式 | 说明 |
| --- | --- | --- | --- |
| `Card 1234` | Chase / BoA 卡行行尾 | 4 位 | 卡号后四位；同一账单内**同一个卡号始终一致** |
| Zelle `Conf#` | BoA、部分 Zelle 描述 | 10 位数字 | 逐笔唯一 |
| ACH `Trace Number` | ACH 明细/授权表 | 15 位（8 位路由 + 7 位序号） | 真实账单通常不在行内印，别乱加 |
| ACH `Company Entry Description` | 入账行的公司描述 | 上限 10 字符（NACHA 规则，2026 起有更新） | 例 `PAYROLL`、`VENDOR PMT` |
| Fedwire `IMAD` | Chase 电汇行 | 22 位字母数字（日期+来源+序号） | 只有电汇行才有；`Trn` 是 10 位 |
| Check number | 支票行 | 3–4 位 | 与支票影像上的号一致 |
| Amex `ID` | 卡消费第二行 | 10 位数字 | 逐笔唯一 |
| U.S. Bank `Ref` | Ref 列 | 7 位数字 | 逐笔唯一 |
| SoFi 内部转账 | 描述尾部 | 后四位 | `To Savings - 1943` |

**总规矩**：参照号**要么完全按真实格式生成，要么干脆不印**。半真半假（长度不对、字母数字混排不对）比没有更可疑。

---

## 五、常见错法（我在真实产物里抓到的，按出现频率排）

| # | 错法 | 为什么一眼假 | 修法 |
| --- | --- | --- | --- |
| 1 | 卡行带持卡人城市（`NETFLIX.COM AUSTIN TX`） | 线上商户没有门店地点 | 契约 `no_city` + conform |
| 2 | 同一家公用事业一个月两笔 | 电费月结一次 | 去重，只留大额 |
| 3 | 加油 $4.56 | 量级像买水 | 类目金额区间（$24–95） |
| 4 | 房租 $275 / 收入 $275×4 | 与档案收支档矛盾 | 信封约束 |
| 5 | 同一顿饭 $43.65 连四笔 | 模型抄自己 | 同类目金额不撞值 |
| 6 | `POS PURCHASE POS PURCHASE AMAZON` | 前缀叠加 | 描述符模板单一来源 |
| 7 | Type 列出现裸 `ACH` | 枚举里没有这个值 | 类型归一 + lint |
| 8 | 商家消费被写成 `ACH Out` | 刷卡不是 ACH | 按类目强制 `Card` |
| 9 | 别州公用事业（Austin 户头出现 Con Edison） | 地理矛盾 | 城市→供应商表 |
| 10 | 商户名来自搜索标题（`AVERAGE GROCER`） | 不是品牌名 | 选角只从品牌词表取 |
| 11 | 描述符前十个空格 | 用空格做对齐 | 坐标定位，不用空格 |
| 12 | 补出来的行缺字段（渲染 `KeyError: dir`） | 没照抄同组行的固定字段 | 补行时复制同组行骨架 |
| 13 | 12 月账单出现 `CHAMPAGNE BAR` | 占位商户 | 品牌词表里换真实商户 |
| 14 | 收入方写 `CLIENT PAYMENT` | 通用名不是付款方 | 按职业给真实付款方 |
| 15 | 节日错位（感恩节在 11/26，消费在 11/20） | 日历算错 | 用算法算节日，不手写日期 |
| 16 | 页码 `Page 1 of 4` 把披露页也算进去 | 银行只数账单页 | `page_numbering: statement-pages-only` |
| 17 | 个人账户印 EIN | 企业才有 | 按账户类型分支 |
| 18 | 水印 `SAMPLE` 印在成品上 | 交付件与样张混淆 | 默认不盖水印 |
| 19 | 同品牌同门店号一个月刷 5 次 | 门店号是固定的但如果同店高频，金额也该接近 | 门店号轮换 + 金额抖动 |
| 20 | 余额链按「打印顺序」而不是「记账顺序」累加 | 一排序就断 | 排序后再重算余额 |

---

## 六、怎么从真实样本推出你家银行的规则（提取流程）

1. **找样本**：真实账单 PDF（你自己手里的），至少 3 个月、含所有行类。
2. **抽文本层**：`pdftotext -layout` 或 PyMuPDF `get_text("words")`，**保留坐标**。
3. **认列**：按 x 坐标聚类，得到 `date/description/type/ref/amount/balance` 的实际列位置。
4. **认行类**：取描述首词/前两词，统计频次（≥3 次且是纯字母短语才算类型，别把 `Beginning Balance` 认成类型）。
5. **写规则**：每条规则必须能指到「样本第几页第几行」——这就是 `evidence`。
6. **落地契约**：写进 `contracts/<account>.json`（字段见 `kit/schemas/contract.schema.json`），跑 `statement_lint.py`。
7. **防腐坏**：样本换版后跑 `--diff`，规则漂了就红。

---

## 七、外部依据（这些不是拍脑袋）

- 卡组织对账单描述符的字符与结构限制（商户名 + 城市/州/国家 + 后缀，常见 25 字符量级）：[Cybersource 商户描述符规范](https://docs.cybersource.com/content/dam/new-documentation/documentation/en/merchant-descriptors/developer/all/so/merchant-descriptors-so-all.pdf)、[Bank of America 商户描述符说明](https://merchanthelp.bankofamerica.com/kb/upload_files/6d6a79b35c4203fd3235b964d73a1822.pdf)
- NACHA 的 `Company Entry Description` 字段长度与 2026 规则更新：[Nacha 规则修改说明](https://www.nacha.org/system/files/2020-05/Account-Information-Improvements-Proposed-Modifications-to-the-Rules-combined-May%202020.pdf)、[银行侧解读](https://www.mtb.com/content/dam/mtb-web/treasury-center-learning-center/pdfs/mtb-ach-2026-rule-change-company-entry-description.pdf)
- 对账单必须披露什么（周期、费用、余额口径）：[12 C.F.R. § 230.6 Periodic statement disclosures](https://law.justia.com/cfr/title12/12-3.0.1.1.11.0.8.6.html)、[CFPB TISA 手册](https://files.consumerfinance.gov/f/documents/102012_cfpb_truth-savings-act-tisa_procedures.pdf)
- 支票截断与替代支票的参照号：Federal Reserve [Check 21 / substitute check 说明](https://www.federalreserve.gov/secrs/2004/may/20040525/r-1176/r-1176_150_1.pdf)
- 类目占比的分布基准（做信封与分布审计用）：[BLS Consumer Expenditures 2024](https://www.bls.gov/news.release/cesan.nr0.htm)

> 这些链接只解决「格式与合规」的常识；**逐家的具体排版只能靠真实样本**，别拿别家的规则套。
