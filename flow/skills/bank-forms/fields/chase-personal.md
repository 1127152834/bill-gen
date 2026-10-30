# 大通银行 · 个人账单（`chase-personal`）字段字典

由 `tools/gen_bank_forms_assets.py` 从引擎的默认数据生成，别手改：改模板的默认数据再重跑。
「必填」= 引擎会读的顶层键；空着的字段会回落成模板自带样例，所以拿不准也**不要**留空。

| 字段路径 | 类型 | 样例 | 说明 |
| --- | --- | --- | --- |
| `account_display` | number-as-string | 410737211347 | 页眉账号显示串 |
| `atm.card_no` | number-as-string | 0000 | — |
| `atm.credits` | number-as-string | 0.00 | — |
| `atm.name` | string | Maya Okafor | — |
| `atm.purchases` | number-as-string | 0.00 | — |
| `atm.withdrawals` | number-as-string | 0.00 | — |
| `auto` | bool | true | true 时引擎按明细重算 summary/totals/daily，别手填合计数 |
| `bank.domain` | string | chase.com | 官网域名，与行名同源的真实形态域名，如 "harborline-bank.com"（别用 .example / 占位域名） |
| `bank.legal` | string | JPMorgan Chase Bank, N.A. | 法律主体名，通常是 "<行名>, N.A." |
| `bank.name` | string | JPMorgan Chase Bank | 账单上出现的行名，如 "Bank of America" |
| `barcode_num` | null | null | — |
| `begin_balance` | number-as-string | 1000.00 | 期初余额，money 字符串，如 "1000.00" |
| `blank_pages` | number-as-string | 0 | — |
| `checks` | list[?] | [] | — |
| `checks_footnote` | string | — | — |
| `customer_lines` | list[string] | Maya Okafor | 持卡人三行地址：姓名 / 街道与门牌 / 城市 州 邮编（写具体值，别写占位） |
| `daily_balance` | list[list] | — | 每日余额 [[MM/DD, 余额]] |
| `deposits` | list[?] | [] | — |
| `electronic_withdrawals` | list[?] | [] | — |
| `encrypt` | bool | false | 是否 RC4 加密 PDF。只看效果时设 false |
| `fees` | list[?] | [] | — |
| `metadata.author` | string | — | — |
| `metadata.creation` | string | D:20270131000000Z | — |
| `metadata.creator` | string | OpenText Output Transformation Engine 16.6.0 | 同上，与模板族一致 |
| `metadata.keywords` | string | — | — |
| `metadata.mod` | string | D:20270131000000Z | — |
| `metadata.producer` | string | OpenText Output Transformation Engine 16.6.0 | 真实单据供应商，如 "OpenText Output Transformation Engine 16.6.0" |
| `metadata.subject` | string | — | — |
| `metadata.title` | string | Chase Online Statement | 该行真实出件标题，如 "Chase Online Statement" |
| `micr.cycle` | number-as-string | 1 | — |
| `micr.dre` | string | 9842 8356 1347 | — |
| `micr.extra` | string | — | — |
| `micr.routing` | number-as-string | 021000021 | 9 位路由号，ABA 校验位必须正确 |
| `msf.lowest_daily` | number-as-string | 1000.00 | — |
| `msf.payment_solutions` | number-as-string | 0.00 | — |
| `other_withdrawals` | list[?] | [] | — |
| `period.end` | string | Jan 31, 2027 | 结束日期，同 start |
| `period.label` | string | January 01, 2027 through January 31, 2027 | 如 "July 1, 2026 to July 31, 2026" |
| `period.start` | string | Jan 1, 2027 | 起始日期，按模板要求（可能只要 "Jan 1, 2027"） |
| `product` | string | Everyday Checking | 产品名，如 "Everyday Checking"；不要用真实银行的产品名，也不要写 Sample/Example |
| `show_atm` | bool | false | — |
| `show_pf` | bool | false | — |
| `template` | string | personal | 模板变体名，必须与所选模板要求一致（如 personal / business / us / usbiz），改错会渲染失败 |
| `transactions` | list[dict] | — | 统一流水，type ∈ deposit / atm / electronic / fee，amount 带符号；优先用它，别同时填分区表 |
| `watermark` | bool | true | 是否加 SAMPLE 水印。合成样例必须保持 true |
| `zip` | number-as-string | 78704 | — |

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

描述字数上限 `44`，每页行数 30。

## 交付形态

这份字段字典描述的是**一个月**的数据对象。选 3 个月就产出 3 份 JSON（`chase-personal-YYYY-MM.json`），每份只带自己那个月的期初/期末与流水；跨月规则（上期末 = 下期初、月份连续）由 `monthpack` 校验，见 `docs/month-json.md`。
