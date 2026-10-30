# 字段清单：每一行必须带什么

> 用法：选角（cast）拼描述符之前，先拿这张表检查「字段是否齐」；不齐就不许拼，回导演补。
> 表里的占位符与 `contracts/*.json` 的 `descriptor.<kind>.template` 一一对应。

## 1. 所有行共通

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `date` | `YYYY-MM-DD` | ✅ | 内部统一 ISO；出片时按契约 `date_format` 转 |
| `type` | enum | ✅ | 必须 ∈ 契约 `type_enum`（无 Type 列的银行可省，但内部仍要留） |
| `category` | enum | ✅ | `rent/fuel/grocer/dining/utility/phone/insurance/subscription/shopping/health/income/transfer/fee/interest` |
| `amount` | number | ✅ | 正数=入账，负数=出账；两位小数 |
| `direction` | `credit`/`debit` | ✅ | 与 amount 符号一致 |
| `desc` | string | ✅ | 最终描述符（由 cast 按契约模板拼） |
| `balance` | number | 条件 | 有余额列的银行必填（Chase/BoA 无列但有链路要求另说） |
| `ref` | string | 条件 | 有 Ref 列的银行必填（U.S. Bank） |
| `section` | `deposits`/`withdrawals` | 条件 | BoA 分栏必填 |

## 2. 按行类的专属字段

| 行类 | 额外必填 | 例子 |
| --- | --- | --- |
| `card` | `merchant`、`place{city,state}`（带地点的家）、`last4`（行内带卡尾的家）、`store_no`（可选） | `AMAZON.COM*2H4KL9 SEATTLE WA Card 4499` |
| `atm` | `place`、`amount`（20 的倍数） | `ATM WITHDRAWAL AUSTIN TX` |
| `ach_debit` | `payee`、`co_id`（BoA）、`sec_code`（BoA） | `DES:COMCAST ID:8845 INDN:JOHN DOE CO ID:XXXXX12345 CCD` |
| `ach_credit` | `payer`、`entry_desc`（≤10 字符） | `DES:PAYROLL ID:... INDN:... CO ID:XXXXX12345 PPD` |
| `zelle` | `party`（真人姓名）、`conf`（10 位，BoA 需要） | `Zelle Payment to JORDAN R 23430444423` |
| `wire` | `bank`、`routing`、`holder`、`imad`（22 位）、`trn`（10 位） | `… Imad: 0202MMQFMP2L019146 Trn: 3585096033ES` |
| `check` | `number`（3–4 位）、`payee`（可选） | `Check Paid 12/18 1110` |
| `fee` | `fee_name` | `Monthly Service Fee` |
| `interest` | 无 | `Interest Earned` |
| `transfer` | `counter_account`（后四位） | `To Savings - 1943` |
| `deposit` | `source`（cash/check/mobile） | `Mobile Check Deposit` |
| `refund` | `original_merchant` | `AMAZON.COM REFUND` |

## 3. 抬头 / 页脚（每份账单都要，且逐家不同）

| 位置 | 字段 | 来源 |
| --- | --- | --- |
| 抬头 | `bank.name` / `bank.legal` / `bank.domain` / `bank.phone` | 契约 `bank_identity` |
| 地址块 | `customer.name` + `address{street,city,state,zip}` | 档案（**不许模型编**） |
| 账户区 | `account.number`（按 `row_rules.account_format` 分组）、`routing`、`account_type` | 档案派生的事实表 |
| 页脚 | `bank.footer`（如 Mercury 的 `Banking services provided by Column N.A., Member FDIC`） | 契约 `required_literals` |
| 页码 | `Page k of N`（N 只数账单页，披露页不编号） | 契约 `page_numbering` |

## 4. 三张「不许模型碰」的字段

1. `amount` / `balance` —— 只能由求解器产生；
2. `bank_identity` 里的任何字符串 —— 只能抄契约；
3. `date`（记账日） —— 由求解器的日历规则算，模型只能选「哪一天做这件事」的语义（如「每月 5 号交租」）。

## 5. 交付前的 6 项自检

```
□ 每行 type ∈ 契约枚举
□ 每行 desc 符合 descriptor.template 的占位符
□ 卡行带/不带城市、带/不带卡尾，与契约 rules 一致
□ 金额两位小数；余额链连续
□ 无 forbidden_literals；无前导空格
□ 抬头/页脚/页码与契约 required_literals 一致
```
