# 账单表单填写技能（bank-forms）

给「要出某一家银行账单」的模型用：**先按版式找到对应的 reference，再照着字段表逐条填。**

这个技能解决的是「同一个账本，填进不同银行表单时，字段名、日期写法、行文、列语义全都不一样」
的问题——填错字段名，渲染会直接回落成模板自带的占位数据；行文填成通用格式，出件就不像那家银行。

## 什么时候用

- 要把一份账本落进某个版式（`<bank>-personal` / `<bank>-business`）时；
- 要判断某条流水在这家银行该怎么写时；
- 要在出片前自检「这家银行特有的列有没有填满」时。

## 怎么用（三步）

1. **定位**：按下面的索引找到这家银行的 reference 文件，读它，别读别家的。
2. **对表**：reference 里的「表单字段」表 = 这套版式的全部可填字段，字段名必须一字不差。
   一个字段都不要新造；不确定就留空（引擎有默认值），不要写 `N/A`、`-`、`unknown`。
3. **照行文**：reference 的「流水行文」一节给出这家银行的写法。同一账本在不同银行读起来必须不同。

## 索引

| 版式 | reference | 版式特征（一眼区分） |
| --- | --- | --- |
| `boa-personal` | `boa.md` | 存/取两张分表 + `Account summary` 五行 + 每日余额表 |
| `boa-business` | `boa.md` | 同上，多 5 行统计（# deposits / # withdrawals / average ledger balance） |
| `chase-personal` | `chase.md` | 单表四列 + `CHECKING SUMMARY` 盒 + MICR 行 |
| `chase-business` | `chase.md` | 同上，summary 多 `INSTANCES` 列（笔数）+ 月费豁免说明 |
| `usbank-personal` | `usbank.md` | `Uni-Statement` + `Ref Number` 列 + ACH 两行续行 + IMB 条码 |
| `usbank-business` | `usbank.md` | 同上，标题为 `BUSINESS ESSENTIALS CHECKING` |
| `wellsfargo` | `wellsfargo.md` | 存/取分列 + `Ending daily balance` 列 + 6 页（含披露页） |
| `capitalone` | `capitalone.md` | `CATEGORY` 列（品类词，不是 Credit/Debit） |
| `sofi` | `sofi.md` | `TYPE` 列（Direct Payment / Card Purchase…）+ `Transaction ID:` 续行 |
| `mercury` | `mercury.md` | `Type` 列（ACH In / Card / Wire In）+ `End of Day Balance` |

## 通用硬规则（所有版式都适用，详见 `references/common.md`）

- 金额：字符串，**不带 `$`、不带千分位**，负数是 `-120.00`（U.S. Bank 例外：分表里用 `120.00-`）。
- 日期：每家格式不同（`01/04`、`Jan 4, 2027`、`Oct 01 - Oct 31, 2026`），照 reference 抄，不要自创。
- 身份：人名、门牌+街名、城市+州缩写+ZIP 必须是具体值。
- 商户：只能来自账本 facts。不要新编商户、价格、银行、路由号。
- 禁止出现的词：`SAMPLE`、`EXAMPLE`、`DEMO`、`TEST`、`DUMMY`、`YOUR NAME`、`PLACEHOLDER`——lint 会直接阻断。
- 行序：按日期升序；同日按入账先后。
- 合计：`期初 + 贷 − 借 = 期末`，分表合计与 summary 行必须一致；不要为了凑数改金额。

## 样例文件

`assets/<template>.json` 是这套版式的**完整可填空对象**（由引擎默认数据导出，字段名与嵌套结构就是最终形态）。
填之前先看它的形状；填完后再对照字段表确认没有漏项。

## 自检清单

- [ ] 字段名与 reference 完全一致，没有新键。
- [ ] 这家银行特有的列（CATEGORY / TYPE / Ref Number / End of Day Balance / ID）都填满了。
- [ ] 卡尾号：该印的印（Chase/BoA），不该印的没印（C1/SoFi/Mercury/Amex/WF/USB）。
- [ ] 金额闭合、分表合计与 summary 一致。
- [ ] 没有占位词、没有 `…`、没有 `- $`（符号后带空格）、没有全零路由号。
