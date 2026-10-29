# U.S. Bank（`usbank-personal` / `usbank-business`）

## 版式

- 页眉：`usbank` 行标 + `Uni-Statement` + 账号/账期（右上角竖排四行）。
- 左侧 IMB 条码（USPS 智能邮件条码）+ 一串 `106481000<账号片段> ER` 编码行。
- `INFORMATION YOU SHOULD KNOW` 披露段 → `SAFE DEBIT ACCOUNT`（business 版为 `BUSINESS ESSENTIALS CHECKING`）摘要块。
- 流水分 `Deposits / Credits` 与 `Other Withdrawals` 两张表，各带 `Total …` 小计行。
- 每张表列 = `Date | Description of Transaction | Ref Number | Amount`；金额用**尾随负号**（`120.00-`）。
- 末尾 `Balance Summary` 三列（Date / Ending Balance）。
- 页脚：`Page N of M`（只数账单页，披露页不计数）。

## 表单字段

| 字段 | 类型 | 怎么填 |
| --- | --- | --- |
| `account_display` | str | 分段账号：`"1 482 7396 0514"` |
| `customer_lines` | str[3] | `[姓名, 门牌+街, "城市 ST ZIP"]` |
| `period` | obj | `{label, start, end}`：`January 01, 2027 through January 31, 2027` / `Jan 1, 2027` |
| `begin_balance` | str | 纯数字 `"1000.00"` |
| `imb_serial` | str | IMB 条码的 11 位串（引擎可派生，留空亦可） |
| `deposits` / `withdrawals` | row[] | 主行 + `extra` 续行，见下 |
| `summary` | obj | `{begin, deposits, withdrawals, ending}`；金额带千分位是这套版式的习惯（`"1,000.00"`） |
| `daily` | row[] | `Balance Summary` 的日终余额 |
| `days` | str | 账期天数 |

行对象：

```json
{"date": "Jan 4", "type": "Deposit", "detail": "ACME CORP PAYROLL PPD",
 "extra": ["REF=051000014297439Y00", "PAYROLL 086830302"], "amount": "500.00", "ref": "..."}
```

`extra` 每项用 `|` 分隔列（第一段左列、第二段右列），0–2 条。

## 流水行文

| 类型 | 写法 |
| --- | --- |
| ACH | 主行 = 公司名；续行 1 `REF=<15 位 trace>Y00`；续行 2 `PAYROLL <10 位公司号>`（借记为 `PAYMENT`） |
| 卡消费 | 商户 + `城市 ST`（如 `H-E-B 0611 AUSTIN TX`），**不印卡尾号** |
| 支票 | 主行 `CHECK`，`ref` 列 = 支票号（`1110`） |
| Zelle | 续行 `On 01/09/27 PMT|ID=USB…` |
| ATM | `ATM TERMINAL 7014 AUS` |
| 费用/利息 | `ref` 列给 10 位号 |

- 金额一律**尾随负号**：`120.00-`、`2,150.00-`。
- 描述列宽度有限，超长硬切。

## 反例

- `Statement Period: an 01, 2027`（`Jan` 的字形缺失被裁掉——字体补丁要跟上）。
- `Other Withdrawals 20.00-`（`-120.00` 掉了首位数字）。
- 金额写成 `-$120.00`（这套版式是尾负号）。
