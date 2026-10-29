# American Express（`amex`）

## 版式

- 页眉：行标 + `American Express® Rewards Checking Statement` + `Statement Date` / `Account Ending` / `Account Name`。
- 上半页双栏：`Statement Summary`（期初/贷/借/期末）与 `Interest Summary`（利息期、天数、APY、本年利息）。
- `Account Activity` 表：`Date | Description | Credits | Debits | Balance`，**贷记与借记分两列**。
- 每条流水的第二行是 `ID <15 位>`（续行，缩进对齐描述列）。
- 页脚：`Accounts offered by American Express National Bank. Member FDIC.` + `p. 1/2`。

## 表单字段

| 字段 | 类型 | 怎么填 |
| --- | --- | --- |
| `customer_name` | str | 全大写 `MAYA OKAFOR` |
| `address_lines` | str[2] | 全大写：`["2104 S 1ST ST", "AUSTIN TX 78704"]` |
| `account_ending` | str | 4 位尾号 `8410` |
| `account_name` | str | `Rewards Checking` |
| `statement_date` / `period_start` / `period_end` | str | `10/31/2026` / `10/01/2026` |
| `days_in_period` | int | 账期天数 |
| `interest_rate` / `apy` | str | `0.01%`（活期一般很低，不要写 4.00%） |
| `interest_paid_ytd` | number | 本年累计利息，元 |
| `beginning_balance` | number | 期初，**数字类型**（不是字符串） |
| `transactions` | row[] | `{date: "10/04/2026", desc, amount: number, id?: "ID …"}` |

## 流水行文

| 类型 | 写法 | 例 |
| --- | --- | --- |
| 卡消费 | `DEBIT CARD PURCHASE 商户 城市 ST`（日期在 DATE 列，不重复） | `DEBIT CARD PURCHASE WHOLE FOODS MKT 105 AUSTIN TX` |
| ACH 贷记 | `ACH CREDIT 公司名` | `ACH CREDIT ACME CORP PAYROLL` |
| ACH 借记 | `ACH Debit 公司名` | `ACH Debit VERIZON WIRELESS AUTOPAY` |
| 利息 | `INTEREST PAID` | `INTEREST PAYED`→ 用 `INTEREST PAID` |

- 行内**不印卡尾号**；身份靠 `Account Ending`。
- 每条流水的 `id` 写 `ID <12–15 位数字>`，由引擎派生；模型不必自己编。
- 描述宽度约 40 字符，超长折到第二行（这套版式允许折行）。

## 反例

- 把日期写进描述（`DEBIT CARD PURCHASE 10/13 …`）——Amex 的日期在 DATE 列。
- 借记写进 Credits 列。
- `ID` 行缺失。
