# Mercury（`mercury`）

## 版式

- 企业账户账单：左上 Mercury 行标，`October 2026` + `Oct 01, 2026–Oct 31, 2026 (31 days)` + `All dates in UTC`。
- 左栏公司信息：公司名 / 地址 / `EIN ••1234`（掩码）。
- 右栏 `Account details`（Account Type / Account Number / Routing Number）+ `Account activity`（Beginning Balance / Total withdrawals / Total deposits / Statement balance）。
- `All Transactions /` 表：`Date (UTC) | Description | Type | Amount | End of Day Balance`。
- 页脚：`Banking services provided by Column N.A., Member FDIC` + `Mercury | help@mercury.com | 地址`。

## 表单字段

| 字段 | 类型 | 怎么填 |
| --- | --- | --- |
| `period_label` / `period_start` / `period_end` / `period_days` | str | `October 2026` / `Oct 01, 2026` / `31` |
| `timezone` | str | `All dates in UTC` |
| `company` | str | 公司名（如 `Okafor Design Studio LLC`） |
| `address` | str[2] | `[街, "城市 ST ZIP"]` |
| `ein` | str | 掩码形态 `EIN ••3170` |
| `account_type` / `account_number` / `routing_number` | str | `Checking` / `410737211347` / `121140399`（9 位，过 ABA） |
| `beginning_balance` | number | 期初金额（数字） |
| `transactions` | row[] | `{date: "Dec 9", desc, type, amount: number, balance}` |
| `footer_line1` / `footer_line2` | str | 合作银行声明 / 联系行 |

## 流水行文

| 类型 | Type 列 | 描述 |
| --- | --- | --- |
| ACH 入账 | `ACH In` | **只写公司名**（全大写，不加城市/卡号/日期） |
| ACH 出账 | `ACH Out` | 同上 |
| 大额电汇入账 | `Wire In`（≥$5,000 的贷记） | 公司名 |
| 卡消费 | `Card` | 商户名（如 `AWS EMEA`、`WHOLE FOODS MKT 105`） |
| 费用 | `Fee` | `MONTHLY SERVICE FEE` |
| 利息 | `Interest` | `INTEREST PAYMENT` |
| Zelle | `Zelle` | 对方姓名 |
| ATM | `ATM` | `ATM WITHDRAWAL 城市 ST` |
| 支票 | `Check` | 支票号 |

- 行内**不印卡尾号**；`End of Day Balance` 是当行后的余额，不能留空。
- 金额写法 `-$84.32` / `$1,250.00`。

## 反例

- Description 里塞城市或卡号（Mercury 只写商户）。
- `Routing Number 000000000`。
- Type 列写 `Credit` / `Debit` 而不是 `ACH In/Out`、`Card`、`Wire In`。
