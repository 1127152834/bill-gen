# 美国合众银行 · 个人账单（`usbank-personal`）字段字典

由 `tools/gen_bank_forms_assets.py` 从引擎的默认数据生成，别手改：改模板的默认数据再重跑。
「必填」= 引擎会读的顶层键；空着的字段会回落成模板自带样例，所以拿不准也**不要**留空。

| 字段路径 | 类型 | 样例 | 说明 |
| --- | --- | --- | --- |
| `account_display` | string | 1 482 7396 0514 | 账单上显示的账号字符串，可用掩码 |
| `auto` | bool | true | true 时引擎按明细重算 summary/totals/daily，别手填合计数 |
| `bank.domain` | string | usbank.com | 官网域名，与行名同源的真实形态域名，如 "harborline-bank.com"（别用 .example / 占位域名） |
| `bank.legal` | string | U.S. Bank National Association | 法律主体名，通常是 "<行名>, N.A." |
| `bank.name` | string | U.S. Bank | 账单上出现的行名，如 "Bank of America" |
| `begin_balance` | number-as-string | 1000.00 | 期初余额，money 字符串，如 "1000.00" |
| `customer_lines` | list[string] | Maya Okafor | 持卡人三行地址：姓名 / 街道与门牌 / 城市 州 邮编（写具体值，别写占位） |
| `daily` | list[list] | — | 每日余额 [[Jan 4, 余额]] |
| `days` | number-as-string | 31 | — |
| `deposits` | list[dict] | — | 存入行 {date:'Jan 4', type, detail, extra[], amount} |
| `encrypt` | bool | false | 是否 RC4 加密 PDF。只看效果时设 false |
| `imb_serial` | number-as-string | 00041073721 | 票据序列号，版式固定长度 |
| `metadata.author` | string | — | — |
| `metadata.creation` | string | D:20270131000000Z | — |
| `metadata.creator` | string | Ricoh Americas Corporation, AFP2PDF v3.2.1 | 同上，与模板族一致 |
| `metadata.keywords` | string | — | — |
| `metadata.mod` | string | D:20270131000000Z | — |
| `metadata.producer` | string | Ricoh Americas Corporation, AFP2PDF v3.2.1 | 真实单据供应商，如 "OpenText Output Transformation Engine 16.6.0" |
| `metadata.subject` | string | — | — |
| `metadata.title` | string | U.S. Bank Online Statement | 该行真实出件标题，如 "Chase Online Statement" |
| `metadata.x_creator` | string | Ricoh Americas Corporation, AFP2PDF v3.2.1 | — |
| `metadata.x_desc` | string | — | — |
| `metadata.x_producer` | string | Ricoh Americas Corporation, AFP2PDF v3.2.1 | — |
| `metadata.x_title` | string | U.S. Bank Online Statement | — |
| `metadata.xmptk` | string | Ricoh Americas Corporation, AFP2PDF v3.2.1 | — |
| `period.end` | string | Jan 31, 2027 | 结束日期，同 start |
| `period.label` | string | January 01, 2027 through January 31, 2027 | 如 "July 1, 2026 to July 31, 2026" |
| `period.start` | string | Jan 1, 2027 | 起始日期，按模板要求（可能只要 "Jan 1, 2027"） |
| `summary.begin` | string | 1,000.00 | — |
| `summary.deposits` | number-as-string | 500.00 | — |
| `summary.ending` | string | 1,380.00 | — |
| `summary.withdrawals` | number-as-string | -120.00 | — |
| `template` | string | us | 模板变体名，必须与所选模板要求一致（如 personal / business / us / usbiz），改错会渲染失败 |
| `watermark` | bool | true | 是否加 SAMPLE 水印。合成样例必须保持 true |
| `withdrawals` | list[dict] | — | 扣款行，同上 |

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

描述字数上限 `40`，每页行数 28。

## 交付形态

这份字段字典描述的是**一个月**的数据对象。选 3 个月就产出 3 份 JSON（`usbank-personal-YYYY-MM.json`），每份只带自己那个月的期初/期末与流水；跨月规则（上期末 = 下期初、月份连续）由 `monthpack` 校验，见 `docs/month-json.md`。
