# Bank of America（`boa-personal` / `boa-business`）

## 版式

- 首页：行标 + 客服信息块 + `Your <产品名>` + 五行 `Account summary` + 无纸化声明 + 促销盒。
- 流水分两张表：`Deposits and other additions` 与 `Withdrawals and other subtractions`，各带小计。
- `show_daily: true` 时印每日余额表（`Daily ledger balance`）。
- business 版在 summary 下多 5 行统计：`# deposits / # withdrawals / items-previous / days / Average ledger balance`。
- 页脚一行表单码：`PULL: B  CYCLE: 12  SPEC: L  DELIVERY: E  TYPE: I  IMAGE: I  BC: CA`。

## 表单字段

| 字段 | 类型 | 怎么填 |
| --- | --- | --- |
| `bank` | obj | `{name, legal, domain}`，`name` 印在页眉 |
| `customer_lines` | str[3] | `[姓名, 门牌+街, "城市 ST ZIP"]`（BoA 是整行带邮编，没有单独 zip 字段） |
| `owner_names` / `owner_line` / `strip_name` | str/str[] | 账户持有人（joint 账户给多人） |
| `account_number` | str | 可带分段：`"4107 3721 1347"` |
| `product` | str | 产品名：`Advantage Plus Banking` / `Advantage SafeBalance Banking` |
| `period` | obj | `{label, begin, end}`：`January 1, 2027 to January 31, 2027` |
| `begin_balance` | str | 纯数字 `"1284.73"` |
| `deposits` / `withdrawals` / `fees` | row[] | 行对象 `{date: "01/04", desc, amount}` |
| `summary` | obj | `{begin, deposits, withdrawals, checks, fees, ending}`；`deposits` 正、`withdrawals` 负；**没有支票时 `checks` 写 `"0.00"`** |
| `totals` | obj | 两张分表的小计（`deposits` / `withdrawals`） |
| `daily` | row[] | 每日余额：`{date, balance}` |
| `stats` | obj | business/自动计算用：`{items_prev, days, avg_ledger}` |
| `qualifications` | obj[] | 免月费条件：`{text, met}`，2–3 条，至少一条 `met: true` |
| `show_daily` / `min_pages` | bool/int | 是否印每日余额表 / 最少页数（促销盒会把页数撑到 4） |

## 流水行文

| 类型 | 写法 | 例 |
| --- | --- | --- |
| ACH | `DES:<公司名≤22> ID:<15位> INDN:<姓名≤22> CO ID:XXXXX##### PPD\|WEB` | `DES:ACME CORP PAYROLL ID:051000014297439 INDN:MAYA OKAFOR CO ID:XXXXX08683 PPD` |
| 卡消费 | `[收单方前缀] 商户 MM/DD Card ####` | `TST* WHOLE FOODS MKT 105 12/11 Card 1347` |
| 支票 | `CHECK 支票号 [备注]` | `CHECK 1110` |
| Zelle | `Zelle payment to <姓名> Conf# <9 位小写字母数字>` | `Zelle payment to JORDAN R Conf# w9wh533i7` |
| ATM | `ATM WITHDRAWAL <序号> <终端/商户> 城市 ST` | `ATM WITHDRAWAL 001 SOUTH TERMINAL AUSTIN TX` |
| 利息 | `INTEREST PAYMENT` | `INTEREST PAYMENT` |

- `DES:` **22 字符内且不切单词**；`INDN:` 22 字符；`ID:` 是 15 位 trace；`CO ID` 一律 `XXXXX` + 5 位。
- **卡尾号印**（`Card 1347`）。
- 收单方前缀按品类：咖啡/轻餐 `SQ *` / `TST* `，亚马逊 `AMZN Mktp US*`，PayPal `PAYPAL *`，加油站 `SP `；超市、网约车、订阅**不加**前缀。

## 反例

- `Checks -0.00`（没有支票却带负号）。
- `DES:ACME CORP PAYROLL DIRE`（把单词切断）。
- 前缀叠前缀：`AMZN Mktp US*AMAZON MKTPLACE`。
- summary 五行与分表小计对不上。
