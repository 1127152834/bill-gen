# 富国银行（`wellsfargo`）字段字典

由 `tools/gen_bank_forms_assets.py` 从引擎的默认数据生成，别手改：改模板的默认数据再重跑。
「必填」= 引擎会读的顶层键；空着的字段会回落成模板自带样例，所以拿不准也**不要**留空。

| 字段路径 | 类型 | 样例 | 说明 |
| --- | --- | --- | --- |
| `_comment` | string | 账户数据。生成: python3 generate.py data.json ../statement.pdf | — |
| `account.number` | number-as-string | 410737211347 | — |
| `account.primary_label` | string | (primary account) | — |
| `account.product` | string | Everyday Checking | — |
| `account.rtn` | number-as-string | 121042882 | 9 位路由号，ABA 校验位正确 |
| `bank.copyright_year` | int | 2026 | — |
| `bank.des_moines` | string | Des Moines, IA 50340 | — |
| `bank.domain` | string | wellsfargo.com | 官网域名，与行名同源的真实形态域名，如 "harborline-bank.com"（别用 .example / 占位域名） |
| `bank.legal` | string | Wells Fargo Bank, N.A. | 法律主体名，通常是 "<行名>, N.A." |
| `bank.legal_na` | string | Wells Fargo Bank, N.A. | 法律主体名（Wells Fargo 版式单独一个字段） |
| `bank.mac_code` | string | MAC F2304-019 | — |
| `bank.name` | string | Wells Fargo | 账单上出现的行名，如 "Bank of America" |
| `bank.nmlsr_id` | number-as-string | 399801 | — |
| `bank.office_code` | number-as-string | 115 | — |
| `bank.phone_es` | string | 1-877-727-2932 | — |
| `bank.phone_main` | string | (1-800-869-3557) | 客服电话，用 555 保留号段 |
| `bank.phone_name` | string | 1-800-TO-WELLS | — |
| `bank.po_box` | string | P.O. Box 6995 | — |
| `bank.po_box2` | string | PO Box 50947 | — |
| `bank.zip_main` | string | 97228-6995 | — |
| `customer.address_lines` | list[string] | 2104 S 1st St | — |
| `customer.name` | string | Maya Okafor | — |
| `fees.paid` | string | $0.00 | — |
| `fees.period` | string | 09/05/2026 - 09/30/2026 | — |
| `fees.standard` | string | $15.00 | — |
| `metadata.author` | string | — | — |
| `metadata.creation_date` | string | D:20261030075718-04'00' | — |
| `metadata.creator` | string | — | 同上，与模板族一致 |
| `metadata.keywords` | string | — | — |
| `metadata.producer` | string | iText® Core 9.5.0 (AGPL version) ©2000-2025 Apryse Group NV | 真实单据供应商，如 "OpenText Output Transformation Engine 16.6.0" |
| `metadata.subject` | string | — | — |
| `metadata.title` | string | Wells Fargo Online Statement | 该行真实出件标题，如 "Chase Online Statement" |
| `statement_date` | string | October 30, 2026 | — |
| `summary.auto` | bool | true | — |
| `summary.begin_amount` | string | $2,460.00 | — |
| `summary.deposits_amount` | string | 1,850.53 | — |
| `summary.ending_amount` | string | $3,670.18 | — |
| `summary.period_end` | date-string | 10/30 | — |
| `summary.period_start` | string | 10/1 | — |
| `summary.withdrawals_amount` | string | -  640.35 | — |
| `total_pages` | int | 5 | — |
| `totals.deposits` | string | $1,850.53 | — |
| `totals.withdrawals` | string | $640.35 | — |
| `transactions` | list[dict] | — | 流水行 {date:'1/4', desc, deposit, withdrawal, balance} |

## 流水行内字段

| 键 | 说明 |
| --- | --- |
| `amount` | 金额。多数模板：存入为正、支出为负；分区表（deposits/withdrawals）里用无符号金额 |
| `balance` | 该笔之后的最新余额，必须与上一行余额加减一致 |
| `date` | 交易日，格式见该模板 notes 里的 date_format |
| `deposit` | 存入金额（无符号），支出行为空串 |
| `desc` | 商户或交易描述，大写更真实；长度上限见 limits.desc_chars |
| `detail` | 交易说明 |
| `extra` | 附加参考号数组，通常一两行短码 |
| `type` | 交易类型，取值域见 types 里的 enum |
| `withdrawal` | 支出金额（无符号），存入行为空串 |

描述字数上限 `34`，每页行数 17。

## 交付形态

这份字段字典描述的是**一个月**的数据对象。选 3 个月就产出 3 份 JSON（`wellsfargo-YYYY-MM.json`），每份只带自己那个月的期初/期末与流水；跨月规则（上期末 = 下期初、月份连续）由 `monthpack` 校验，见 `docs/month-json.md`。
