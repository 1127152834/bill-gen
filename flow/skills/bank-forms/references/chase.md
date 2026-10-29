# Chase（`chase-personal` / `chase-business`）

## 版式

- 单表四列：`DATE | DESCRIPTION | AMOUNT | BALANCE`；描述列里再分两个子列（类型 + 商户信息）。
- 页眉右侧 `CUSTOMER SERVICE INFORMATION` 盒（网址/电话/西语线/国际线），盒右沿与金额列右沿对齐。
- 底部一行 MICR：`⑆路由⑆ 账号⑈ 序号⑈ 0000 AT 00`；右侧竖条码是 20 位跟踪号。
- business 版 summary 多一列 `INSTANCES`（笔数），并有月费豁免说明段。

## 表单字段

| 字段 | 类型 | 怎么填 |
| --- | --- | --- |
| `begin_balance` | str | 期初余额，纯数字 `"4820.55"` |
| `customer_lines` | str[3] | `[姓名, 门牌+街, "城市 ST"]`；**邮编单独放 `zip`** |
| `zip` | str | 5 位邮编；`customer_lines[2]` 里不要再带一遍 |
| `account_display` | str | 完整账号（10–17 位） |
| `bank` | obj | `{name, legal, domain}`，`name` 会印在页眉左上 |
| `period` | obj | `{label, start, end}`：`January 01, 2027 through January 31, 2027` / `Jan 1, 2027` |
| `micr` | obj | `{routing, dre, cycle, extra}`；`dre` 形如 `9842 8356 1347`（账号分段 + 尾号） |
| `barcode_num` | str/null | 20 位跟踪号；留 null 由引擎派生 |
| `msf` | obj | 月费豁免判定用：`{lowest_daily, payment_solutions}` |
| `atm` | obj | 借记卡汇总：`{name, card_no, withdrawals, purchases, credits}` |
| `deposits` / `checks` / `electronic_withdrawals` / `other_withdrawals` / `fees` | row[] | 行对象见下 |
| `daily_balance` | [date, amount][] | 每日余额（当日最后一笔后的余额） |

行对象：`{date: "01/04", desc: "...", amount: "500.00", type?: "deposit"}`；`amount` 纯数字，正负由方向决定。

## 流水行文

| 类型 | 写法 | 例 |
| --- | --- | --- |
| 卡消费 | `Card Purchase` + 子列 `MM/DD 商户 [城市 ST]` + `Card ####` | `Card Purchase 12/11 WHOLE FOODS MKT 105 Card 1347` |
| ACH | `ACH Credit` / `ACH Debit` + `MM/DD 公司名` | `ACH Debit 12/10 CAMDEN PROPERTY MGMT RENT` |
| 支票 | `Check Paid` + `MM/DD` + 支票号 | `Check Paid 12/18 1110` |
| Zelle | `Zelle Payment to 对方姓名` + 11 位数字号 | `Zelle Payment to JORDAN R 23430444423` |
| ATM | `ATM Withdrawal` + `MM/DD 终端描述` + 授权号 | `ATM Withdrawal 12/17 ATM TERMINAL 7014 AUS` |
| 费用/利息 | `Fee` / `Interest Paid` + `MM/DD` | `Fee 12/31 MONTHLY SERVICE FEE` |

- **卡尾号印**（4 位），这是 Chase 的辨识点。
- 企业版行文与个人版同一套；只有电汇（`TRANSFER`，大额）才追加 `Imad: … Trn: …`。
- 描述超宽硬切，不折行、不加省略号。

## 反例

- 把 `customer_lines[2]` 写成 `Austin TX 78704` 同时又给 `zip` → 页面出现两遍邮编。
- 卡行只有商户名没有 `Card ####`。
- 企业版流水退回成裸商户名（没有 `ACH Credit` / `Card Purchase` 前缀）。
- 用 `SAMPLE PAYROLL` 占位。
