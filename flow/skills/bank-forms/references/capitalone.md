# Capital One（`capitalone`）

## 版式

- 首页：大行标 + 客户地址 + `Your account statement` + `Here's your <月份> bank statement.` + 深蓝色块 `TOTAL ENDING BALANCE IN ALL ACCOUNTS`。
- `Account Summary`（账户 × 期初/期末）与 `Cashflow Summary`（利息/透支/手续费）双栏。
- 流水表：`DATE | DESCRIPTION | CATEGORY | AMOUNT | BALANCE`——**CATEGORY 是品类词，不是 Credit/Debit**。
- 首行是 `Opening Balance`。
- 页脚图标行：`capitalone.com | 1-800-227-4825 | 地址 | MEMBER FDIC | EQUAL HOUSING LENDER`。

## 表单字段

| 字段 | 类型 | 怎么填 |
| --- | --- | --- |
| `customer_name` | str | 首字母大写 `Maya Okafor` |
| `address_lines` | str[4] | `[姓名, 街, "城市 ST ZIP", ""]`——姓名在第一行，末项留空 |
| `tagline` | str | `Your account statement`（不要加 ®） |
| `headline_month` | str | `October 2026` |
| `period_range` | str | `Oct 01 - Oct 31, 2026` |
| `summary_start_label` / `summary_end_label` | str | `Oct 01` / `Oct 31` |
| `account_name` / `account_title` | str | `Checking` / `360 Checking` |
| `account_number` | str | 完整账号（表头会截成尾号显示） |
| `apy` / `days_in_cycle` | str | `0.10%` / `31` |
| `opening_balance` / `ytd_interest` / `interest_earned` | number | 数字类型 |
| `overdraft_fees` / `finance_charges` / `fees_*_period` / `fees_*_ytd` | number | 没有就 `0` |
| `transactions` | row[] | `{date: "Oct 07", desc, category, amount: number, balance}` |
| `footer_site` / `footer_phone` / `footer_address` | str | 页脚三件套 |
| `note_text` / `error_heading` / `error_paragraphs` / `error_items` / `final_paragraphs` | str/str[] | 法务段，照真账单句式写，不要写「示例数据」 |

## 流水行文

| 类型 | 写法 | CATEGORY |
| --- | --- | --- |
| 卡消费 | `商户 城市 ST` | `Groceries` / `Dining` / `Gas & Automotive` / `Merchandise` / `Bills & Utilities` / `Entertainment` / `Travel` / `Health Care` / `Transportation` / `Insurance` |
| ACH 工资 | 公司名（保留 NACHA 原样） | `Income` |
| ACH 账单 | 公司名 | `Bills & Utilities` |
| Zelle / 转账 | `Zelle Payment 姓名` | `Transfers` |
| ATM | `ATM 终端号 城市码` | `Cash` |
| 支票 | `CHECK 号码` | `Other` |
| 费用 | `MONTHLY SERVICE FEE` 之类 | `Fees` |

- 金额写法：`-$86.42` / `$3,125.80`（**没有正号、符号后不留空格**）。
- 行内**不印卡尾号**（账户靠页眉 `360 Checking...5521`）。
- CATEGORY 判不出来时：贷记 `Income`，借记 `Other`。

## 反例

- CATEGORY 列写 `Credit` / `Debit`（把金额方向当品类）。
- 金额写成 `- $86.42` 或 `+ $3,125.80`。
- `Sample account statement®` 这种残留占位 + 乱挂的商标号。
