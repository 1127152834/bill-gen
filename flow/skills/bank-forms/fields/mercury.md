# 水星银行（`mercury`）字段字典

由 `tools/gen_bank_forms_assets.py` 从引擎的默认数据生成，别手改：改模板的默认数据再重跑。
「必填」= 引擎会读的顶层键；空着的字段会回落成模板自带样例，所以拿不准也**不要**留空。

| 字段路径 | 类型 | 样例 | 说明 |
| --- | --- | --- | --- |
| `account_number` | number-as-string | 410737211347 | 完整账号（可 8–17 位数字）；后四位要与账户一致 |
| `account_type` | string | Checking | — |
| `address` | list[string] | 2104 S 1st St | — |
| `bank.domain` | string | mercury.com | 官网域名，与行名同源的真实形态域名，如 "harborline-bank.com"（别用 .example / 占位域名） |
| `bank.legal` | string | Column N.A. | 法律主体名，通常是 "<行名>, N.A." |
| `bank.name` | string | Mercury | 账单上出现的行名，如 "Bank of America" |
| `beginning_balance` | float | 1000.0 | — |
| `company` | string | Okafor Design Studio LLC | 公司名（商业账户） |
| `ein` | string | EIN ••3170 | 雇主识别号，用 12-3456789 形态的虚构值 |
| `footer_line1` | string | Banking services provided by Column N.A., Member FDIC | — |
| `footer_line2` | string | Mercury \| help@mercury.com \| 2261 Market Street, Suite 868 | — |
| `period_days` | number-as-string | 31 | — |
| `period_end` | string | Oct 31, 2026 | — |
| `period_label` | string | October 2026 | — |
| `period_start` | string | Oct 01, 2026 | — |
| `routing_number` | number-as-string | 121140399 | — |
| `timezone` | string | All dates in UTC | — |
| `transactions` | list[dict] | — | 流水行 {date:'Oct 2', desc, amount, id, txid, memo, type, dir:'in'|'out'} |

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

这份字段字典描述的是**一个月**的数据对象。选 3 个月就产出 3 份 JSON（`mercury-YYYY-MM.json`），每份只带自己那个月的期初/期末与流水；跨月规则（上期末 = 下期初、月份连续）由 `monthpack` 校验，见 `docs/month-json.md`。
