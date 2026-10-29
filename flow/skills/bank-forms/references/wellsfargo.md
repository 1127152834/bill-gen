# Wells Fargo（`wellsfargo`）

## 版式

- 首页是封面性质：行标 + 地址 + `You and Wells Fargo` + 安全提示；**没有账务摘要**。
- 第二页起：月度服务费摘要 → `Account activity` 表（`Date | Check Number | Description | Deposits/Additions | Withdrawals/Subtractions | Ending daily balance`）。
- `Check Number` 列只对支票行有值，其余留空（不要写 `-`）。
- 页脚：`Page N of M`；末页是披露/联系方式页。

## 表单字段

| 字段 | 类型 | 怎么填 |
| --- | --- | --- |
| `bank` | obj | `{name, legal_na, domain, phone_name, phone_number, ...}` |
| `customer` | obj | `{name, address_lines: [街, "城市 ST ZIP"]}` |
| `statement_date` | str | `October 30, 2026`（期末 + 1~3 个工作日） |
| `total_pages` | int | 与渲染页数一致 |
| `account` | obj | `{product, number, primary_label, rtn, ...}`；`rtn` 是 9 位路由号 |
| `summary` | obj | `{period_start: "10/1", period_end: "10/30", begin_amount, deposits_amount, withdrawals_amount, ending_amount, ...}` 展示格式带 `$` 与千分位 |
| `transactions` | row[] | `{date: "12/9", desc, deposit?, withdrawal?, balance}` |
| `totals` | obj | `{deposits, withdrawals}`，展示格式 |
| `fees` | obj | 月度服务费摘要 |
| `metadata` | obj | 输出用（title/producer），不是表单字段 |

## 流水行文

| 类型 | 写法 | 例 |
| --- | --- | --- |
| 卡消费 | `Card Purchase MM/D 商户 城市 ST` | `Card Purchase 12/11 WHOLE FOODS MKT 105 AUSTIN TX` |
| ATM | `ATM Withdrawal MM/D 商户 城市 ST` | `ATM Withdrawal 12/17 ATM 7014 AUS` |
| 支票 | `Check <号码> 备注` | `Check 1110` |
| 其他 ACH | 裸公司名 | `CAMDEN PROPERTY MGMT RENT` |
| Zelle | `Zelle Payment To <姓名>` | `Zelle Payment To Jordan R` |

- 存/取**分列**：一笔只出现在 `deposit` 或 `withdrawal` 其中一列，另一列留空字符串（不是 0）。
- `balance` 是当行入账后的日终余额。
- 行内**不印卡尾号**（卡号在页眉账户区）。
- 描述列宽约 58 字符（8pt），超长硬切。

## 反例

- 首页塞账务摘要（WF 真件首页是封面）。
- `Check Number` 列给非支票行填 `-` 或 `0000`。
- 同一笔同时出现在 deposit 与 withdrawal。
- 流水 6 页摊 20 行。
