# 真账单的流水版式：11 家银行的「行语法」拆解

样本来源：`/Users/tian/Downloads/真账单/`（AE、Alliantcu、BOA、Mercury、USbank、ally、c1、sofi、大通、富国，共 30 份 PDF），
逐页用 PyMuPDF 取 `words` 带上 x 坐标还原列位置；Mercury 是矢量描边无文字层，按 140dpi 渲染后目视读。

结论先说：**「只有描述」和「必须带流水号」不是两种风格，而是同一条行语法里可选的槽位**。
真实账单的行 = 主行（日期列 + 描述列 + 金额列 + 余额列，四者可缺）+ 0~3 条续行（挂在描述列下方，左列/右列各一个槽）。
流水号就住在续行里，或住在描述列的第二个固定子列里。

---

## 1. 一览：每家的列与续行

| 银行 | 列 | 余额列 | 续行/子列 | 行的「身份信息」 |
| --- | --- | --- | --- | --- |
| BOA 存款 | Date @36 / Description @91 / Amount @550 | 无 | 描述可折行（`PPD` 单独一行） | `DES:` `ID:` `INDN:` `CO ID:`；Zelle 带 `Conf# w9wh533i7` |
| Chase 个人存款 | Date @35 / Description @70（内含子列 @155）/ Amount @460 / Balance @532 | 有 | 描述单行 + 固定子列 | Zelle 11 位交易号；卡交易 `Card 4499` |
| Chase 商业存款 | Date / Description / Amount（支票段多 CHECK NO. 与 DATE PAID，`^` 标已付） | 无 | 存款行第二行是入账序号 | 电汇 `Imad: <22>` + `Trn: <7>Es` |
| Chase 信用卡 | Date of Transaction / Merchant Name or Transaction Description / $ Amount | 无 | 外币消费 3 行：本币额、`MM/DD 币种`、`原币 X 汇率 (EXCHG RATE)` | 无独立号，商户名即身份 |
| Wells Fargo | Date / Check Number / Description / Deposits / Withdrawals / Ending daily balance | 有（逐日） | 描述内嵌 `MMDD` + 参考串（`260714 M6092`、`Z25991151`） | 支票号列；ACH 串在描述里 |
| U.S. Bank 存款 | Date / Description of Transaction / Ref Number@409 / Amount | Balance Summary 页尾三列 | 续行左 @70.6、右 @221.8 | Zelle：`On 09/19/26 PMT` + `ID=USBkw7lsGba2`；ACH：`REF=<15位>` + 公司名 + 公司号；手续费行 Ref 列 `0600029652` |
| Capital One 存款 | DATE / DESCRIPTION / **CATEGORY**@342 / AMOUNT / BALANCE | 有 | 无 | Category 列只填 `Credit` / `Debit` |
| SoFi 存款 | DATE / TYPE / DESCRIPTION / AMOUNT / BALANCE | 有 | 每条一行续行 | `Transaction ID: 133-12773001` |
| Amex 存款 | Date / Description / Credits / Debits / Balance | 有 | 描述折 2~4 行 | `ID 009002519159756`（15 位 ACH trace） |
| Amex 信用卡 | Date / Description / 外币额 / 金额 | 无 | 商户名后跟 14 位参考号；`*` 标记过账日 | 参考号独占一行 |
| Mercury | Date (UTC) / Description / **Type**（带图标）/ Amount / End of Day Balance | 有，但**只在当日最后一笔** | 无 | Type 词表：`ACH In` `Transfer Out` `Wire` |
| Ally | Date / Description / Credits / Debits / Balance | 有 | 描述折行（含 `$TRANSFER` 噪声行） | 无 |
| Alliant | Posting Date / Transaction Description / Deposit / Withdrawal / Balance | 有 | 无 | 空列用 `–--------` 占位 |

---

## 2. 四类「流水号」，各有各的长相

这是最值得抄进生成器的部分。真账单的号不是随便一串数字，长度和前缀都有惯例：

| 场景 | 真实样例 | 结构 |
| --- | --- | --- |
| ACH 入账 | `REF=262640167906930Y00`；Amex `ID 009002519159756` | 前 8 位是 ODFI 路由号，后 7 位是序号 → 15 位；`Y00` 之类是追加的批次尾缀 |
| Fedwire 电汇 | `Imad: 0102Mmqfmp2M014221`、`Trn: 3448736002Es` | IMAD 固定 22 位、以 `MMDD` 开头；`Trn` 是 7~10 位序号 + 后缀 |
| Zelle | `Conf# w9wh533i7`；`ID=USBkw7lsGba2`；`31179996497` | 9~11 位小写字母数字，银行自己加前缀（USB…） |
| 卡交易 | `Card 4499`、`#000758857`、`Ticket Number: 5262188822309` | 卡末四位 / 6~9 位授权号 / 13 位票号 |
| 支票 | 独立 `CHECK NO.` 列 + `DATE PAID` | 号在列里，不在描述里 |
| 内部交易号 | SoFi `133-12773001`、Amex 信用卡 `87398742700075` | `批次-序号` 或纯 14 位 |

公开资料佐证：ACH trace number 的前 8 位是 ODFI 路由号（[moov-io/ach](https://pkg.go.dev/github.com/moov-io/ach@v1.63.0)、[ramp 的 trace number 说明](https://next.ramp.com/blog/ach-trace-number)）；
Fedwire 的 IMAD 是 22 字符标识、`Trn` 是报文序号（[Fedwire Funds 格式参考](http://www.frbservices.org/campaigns/remittance/files/fedwire_funds_format_reference_guide.pdf)）。
两家银行印同一个号的不同片段，所以**同一个 ACH 在 A 行印 15 位、在 B 行只印后 7 位**是正常的。

---

## 3. 行语法的细节（决定「像不像」的地方）

**日期列会拆成两个 span。** US Bank、Chase 的 `Sep 8` 是 `Sep`@20.1 + `8`@43.9 两段，单数字日右移 23.8pt；`Sep 22` 才整体一段。C1 用 `Jun 17`，SoFi 用 `Aug 31, 2026`，WF 用 `7/14`，Amex 用 `04/10/2026`——**日期格式是行级属性，不是全局属性**。

**金额列三件事。** ①`$` 单独一格（US Bank @495.4，数值右对齐到 589）；②负数用后缀 `-`（`20.00-`）而不是前缀（Chase 是 `-226.83` 前缀，两种都存在）；③只有首行或合计行带 `$`，其余裸数字。

**余额列未必逐行。** Mercury 的 End of Day Balance 只在当日最后一笔出现（同一天第二笔起留空）；Wells Fargo 叫 `Ending daily balance`；U.S. Bank 干脆不逐行，页尾给 `Balance Summary` 的三组「日期 + 期末」。

**描述折行是常态。** BOA 的 ACH 拆成 `DES:CASHREWARD ID:LI INDN:0000…` + `PPD`；Amex 的 ACH 拆成 `AMEX EPAYMENT ACH PMT *****0019` / `X9186 SHI CAO External - WELLS FARGO BANK NA (MINNESOTA)` / `ID 009002519159756`。折行的左边界与描述列对齐（@101~113），不是页面左边距。

**分组与小计。** BOA 分 `Deposits and other additions` / `Withdrawals and other subtractions` / `Service fees` 三段，每段末尾一行 `Total …`；Chase 分五段（含 `Checks Paid` 的 `CHECK NO.` + `DATE PAID` 双列）；US Bank 按 `Other Deposits` / `Other Withdrawals` 分段并给 `Total`。

**空单元格有占位。** Alliant 的 Withdrawal 空着时印 `–--------`；BOA 的 `Checks -0.00`；Chase 支票段的 `^` 是「已清算」标记。

**颜色与图标。** Mercury 入账绿色、出账深色，Type 列前面还有方向图标（`←` `⇄` `✈`）；这是纯视觉信号，不影响文字层。

---

## 4. M4 之前我们和真账单差在哪（已全部补齐）

`tools/rowspec_check.py` 会把每个模板渲染一遍，逐行比对「该有的槽位是否为空」。
下面这张表是 M4 开工前的差距，现在每一行都已落地：

| 模板 | 当时的问题 | 真账单 | 现在 |
| --- | --- | --- | --- |
| amex | 每行 `ID BG260701001` | `ID 009002519159756`（15 位，前 8 位是路由号） | ✅ `ID {ach_trace}` |
| sofi | `Transaction ID: BG260701001` | `133-12773001` | ✅ `{batch}-{seq}` |
| usbank | Ref 列全空、无续行 | Zelle 有 `ID=USB…` 续行、ACH 有 `REF=` 续行、手续费行填 Ref | ✅ 三处都填 |
| chase 个人 | 描述单串 | `Card Purchase` 与 `10/13 Arcteryx…` 分占两个子列 | ✅ 固定子列 + 按像素裁中段 |
| chase 商业 | 无 Imad/Trn | 电汇行必带 `Imad:` + `Trn:` | ✅ ≥$1000 的 debit 行追加 |
| c1 | 无 CATEGORY | `Credit` / `Debit` | ✅ `category` 字段 |
| mercury | Type 用 `ACH Out` 等自造词 | `ACH In` / `Transfer Out` / `Wire` | ✅ 词表归一 |
| wf | 支票号列空 | `Check Number` 列 | ✅ 支票号并入描述 |
| boa | 描述纯商户名 | `DES:` `INDN:` `CO ID:` 结构化描述符 | ✅ 自动折行 |

---

## 5. 实现（M4 已落地）

1. **账本层**：`LedgerRow.reference`（该行对外可见的号），由 `from_game_result()` 里的
   `_attach_references()` 按类型一次性算好——CHECK 票号、ACH trace、ZELLE conf、
   POS `last4:auth`、FEE/INTEREST 10 位、其余 14 位内部号。行键
   `f"{seed}:{date}:{amount_cents}:{description}"`，同一账本重复构建得到同一批号。
2. **`synth/refs.py`**：`ach_trace(routing, seed)`（15 位，前 8 位 = 路由号）、
   `fedwire_imad(when, seed)`（22 字符，`MMDD` 开头）、`fedwire_trn`、
   `zelle_conf`（9 位小写字母数字）、`zelle_payment_id`、`card_auth`（6 位）、
   `internal_id`（14 位）、`sofi_txid`（`\d{3}-\d{8}`）、`amex_reference`（15 位）、
   `check_number`。全部 sha256 派生、确定性。
3. **适配器只做「把号摆到该在的槽」**：
   - `amex` → `id = "ID " + reference`
   - `sofi` → `txid = sofi_txid(...)`
   - `usbank` → Zelle 续行 `On MM/DD/YY PMT|ID=USB…`、ACH 续行 `REF=…Y00|PAYMENT`、
     手续费行 `ref`
   - `chase-personal` → `类型标签 + 固定子列 + 日期 + 商户 + 号`，宽度用
     `fitz.get_text_length` 量到 336pt 以内（超了裁中段），否则会压到金额列
   - `chase-business` → debit 且 ≥$1000 追加 `Imad: … Trn: …`
   - `capitalone` → `category: Credit|Debit`
   - `mercury` → Type 词表 `ACH In / ACH Out / Transfer Out / Card / ATM Withdrawal / Check / Fee / Interest`
   - `wellsfargo` → 支票号并入描述（模板没有独立 Check Number 列可渲染）
   - `boa` → `DES:{商户} ID:{trace} INDN:{持卡人} CO ID:{5 位} {PPD|WEB}`，
     Zelle `Zelle payment to … Conf# …`，POS `… MM/DD Card {last4}`，`CHECK {no}`
4. **正文域名整行重画**：chase 的两句正文（`at chase.com/disclosures or call us…`、
   `Business Accounts at chase.com/business/disclosures …`）按 `TextSlot` 整行擦除后
   用 Helvetica 重画，消掉底稿「按单元定位」造成的 `harb orline-b ank` 断字。
5. **测试**：`tests/test_synth_refs.py`（14 条）锁住每种号的形状与确定性，以及每个
   适配器「该填的槽真的填了」；`test_chase_body_domain_is_redrawn_as_one_run` 锁住整行重画
   且上一行不缺字。fixture 是 `tests/fixtures/synth_result.json`。

验收：`pytest` 433 passed / 16 skipped、`ruff`、`mypy`（18 文件）、
`tools/render_regression.py check` 32 指纹、`tools/brand_audit.py` 全绿。
