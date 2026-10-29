# SoFi（`sofi`）

## 版式

- 页眉三栏：`Primary Account Holder`（姓名 + `Member since …`）/ `Address` / `Account Number` + `Monthly Statement Period`；右上角 SoFi 行标。
- 灰底摘要块：`Current Balance` / `Monthly Interest Paid` / `Annual Percentage Yield Earned` / `Beginning Balance` / `Year-to-date Interest Paid`。
- 流水表：`DATE | TYPE | DESCRIPTION | AMOUNT | BALANCE`——**TYPE 列是账户动作，不是金额方向**。
- 每条流水的第二行是 `Transaction ID: <号码>`。
- 底部 `Contact Information` 三栏（网站/地址/电话），图标是矢量线稿。
- 页脚：`©2026 SoFi Bank, N.A. All rights reserved.` / `SoFi Bank, N.A. Member FDIC`。

## 表单字段

| 字段 | 类型 | 怎么填 |
| --- | --- | --- |
| `holder_name` | str | `Maya Okafor` |
| `member_since` | str | `Member since Mar 15, 2025` |
| `address_lines` | str[4] | `[街, "城市 ST ZIP", "", ""]` |
| `account_number` | str | 完整账号 |
| `statement_period` | str | `10/01/2026 - 10/31/2026` |
| `account_type` / `account_last4` | str | `Checking` / `0001` |
| `current_balance` / `current_as_of` | number/str | `1214.50` / `10/31/2026` |
| `monthly_interest` / `apy` / `beginning_balance` / `ytd_interest` / `beginning_as_of` | number/str | 利息一般 `0.00`（活期） |
| `balance_note` | str | `Current balances include the amount of interest paid.` |
| `transactions` | row[] | `{date: "10/07/2026", type, desc, txid, amount: number}` |
| `contact_website` / `contact_address` / `contact_phone` | str/str[] | `www.sofi.com` / `SoFi Bank, N.A.` 三行地址 / `(855) 456-SOFI (7634)` |
| `legal_footer` / `footnote1` | str | 法务与脚注原文 |

## 流水行文

| 类型 | TYPE 列 | 描述 |
| --- | --- | --- |
| 卡消费 | `Card Purchase` | `商户 城市 ST`（如 `WHOLE FOODS MKT 105 AUSTIN TX`） |
| ACH 借记（账单/还款） | `Direct Payment` | 公司名（`CHASE CREDIT CRD EPAY`） |
| ACH 贷记 | `Deposit` | 公司名或 `STRIPE TRANSFER` |
| 利息 | `Interest Earned` | `Interest earned` |
| 支票 | `Check` | 支票号 |
| 转账（内部） | `Transfer` | `To Savings - 1943`（目标账户尾号） |
| ATM | `Withdrawal` | 终端描述 |
| 费用 | `Fee` | 费用名 |

- 行内**不印卡尾号**。
- `Transaction ID:` 是 3 位前缀 + 8 位数字的形态（`132-445765001`），由引擎派生。
- 金额写法 `-$86.42` / `$3,125.80`。

## 反例

- TYPE 列只写 `Deposit` / `Withdrawal`（丢掉 `Direct Payment`、`Interest Earned` 这些真件语义）。
- `sofi .com`（字距表被误用导致中间多空格）。
- 页头三行地址用底稿的旧基线，撑出 42pt 空档。
