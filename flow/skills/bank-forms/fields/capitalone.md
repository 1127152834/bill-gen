# 第一资本银行（`capitalone`）字段字典

由 `tools/gen_bank_forms_assets.py` 从引擎的默认数据生成，别手改：改模板的默认数据再重跑。
「必填」= 引擎会读的顶层键；空着的字段会回落成模板自带样例，所以拿不准也**不要**留空。

| 字段路径 | 类型 | 样例 | 说明 |
| --- | --- | --- | --- |
| `account_name` | string | 360 Checking...1347 | — |
| `account_number` | number-as-string | 410737211347 | 完整账号（可 8–17 位数字）；后四位要与账户一致 |
| `account_title` | string | 360 Checking | — |
| `address_lines` | list[string] | Maya Okafor | — |
| `apy` | string | 0.00% | — |
| `bank.domain` | string | capitalone.com | 官网域名，与行名同源的真实形态域名，如 "harborline-bank.com"（别用 .example / 占位域名） |
| `bank.legal` | string | Capital One, N.A. | 法律主体名，通常是 "<行名>, N.A." |
| `bank.name` | string | Capital One | 账单上出现的行名，如 "Bank of America" |
| `closing_date` | string | Oct 31 | — |
| `customer_name` | string | Maya Okafor | — |
| `days_in_cycle` | number-as-string | 31 | — |
| `error_heading` | string | If anything in your statement looks incorrect, please let us | — |
| `error_items` | list[string] | Tell us your name and account number. | — |
| `error_paragraphs` | list[string] | In case of error or questions about your electronic transfer | — |
| `fees_overdraft_period` | float | 0.0 | — |
| `fees_overdraft_ytd` | float | 0.0 | — |
| `fees_return_period` | float | 0.0 | — |
| `fees_return_ytd` | float | 0.0 | — |
| `final_paragraphs` | list[string] | We will investigate your complaint and will correct any erro | — |
| `finance_charges` | float | 0.0 | — |
| `footer_address` | string | P.O. Box 85123, Richmond, VA 23285 | — |
| `footer_phone` | string | 1-888-464-0727 | — |
| `footer_site` | string | capitalone.com | — |
| `headline_month` | string | October 2026 | — |
| `interest_earned` | float | 0.0 | — |
| `note_text` | string | The last four digits of your external accounts may not match | — |
| `opening_balance` | float | 1000.0 | 期初余额（浮点） |
| `overdraft_fees` | float | 0.0 | — |
| `period_range` | string | Oct 01 - Oct 31, 2026 | — |
| `summary_end_label` | string | Oct 31 | — |
| `summary_start_label` | string | Oct 01 | — |
| `tagline` | string | Thanks for saving with Capital One 360® | — |
| `transactions` | list[dict] | — | 流水行 {date:'10/02', desc, amount, id, txid, memo, type} |
| `ytd_interest` | float | 0.0 | — |

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

描述字数上限 `38`，每页行数 24。

## 交付形态

这份字段字典描述的是**一个月**的数据对象。选 3 个月就产出 3 份 JSON（`capitalone-YYYY-MM.json`），每份只带自己那个月的期初/期末与流水；跨月规则（上期末 = 下期初、月份连续）由 `monthpack` 校验，见 `docs/month-json.md`。
