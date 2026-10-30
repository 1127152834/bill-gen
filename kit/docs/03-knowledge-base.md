# 知识库：该存什么、从哪来、怎么用

> 你接 Agent 最缺的不是提示词，是**能给模型当事实依据的本地数据**。
> 原则：**模型负责「像人一样想」，知识库负责「像银行一样准」**。凡是可以查表的，绝不问模型。

## 1. 六张表（这就是全部知识库，别搞几十张）

| 表 | 文件 | 一行长什么样 | 谁用 | 来源 |
| --- | --- | --- | --- | --- |
| 账户档案 | `bank_profiles.json` | `{account, bank, legal, domain, phone, address, routing, account_format, statement_cycle}` | 选账户、编译档案、渲染 | 银行官网/真实账单抬头 |
| 描述符词表 | `descriptors.json` | `{category: [品牌名列表]}` + 州域覆盖 | 选角 | 公开品牌 + 真实账单 |
| 金额/频率分布 | `distributions.json` | `{category: {amount: {p10,p50,p90}, per_month: [min,max], weekday: [...]}}` | 求解器、统计审计 | BLS CE、真实账单统计 |
| 日历 | `calendar.json` | `{date: {holiday, categories, lift}}` | 导演、求解器 | 算法算（复活节/感恩节…），不手写 |
| 地理 | `geo.json` | `{city, state, zip, utilities: {power, gas}, chains_in_scope: [...]}` | 选角、地理审计 | 公开资料 + 真实账单 |
| 禁词 | `forbidden.txt` | 一行一个：`SAMPLE`、`billgen`、别家银行名 | lint、契约审计 | 自己踩过的坑 |

**规模感**：六张表加起来不到 200KB，够覆盖 10 家银行 × 3 年。不要建向量库——账单领域是**结构化查表**，不是语义检索。

## 2. `distributions.json` 怎么来（这是最值钱的一张）

三条渠道，按可信度排：

1. **你自己的真实账单**（最强）：同一类目 12 个月的金额、间隔、星期分布，直接算分位数。
2. **公开统计**：BLS Consumer Expenditure Survey（[2024 年结果](https://www.bls.gov/news.release/cesan.nr0.htm)）给**类目占比**，不给单笔金额；单笔金额用「占比 × 月支出 ÷ 笔数」反推区间。
3. **常识区间**（最弱，兜底）：加油站 $25–90、咖啡 $4–9、正餐 $12–60、超市 $30–220、月租 $900–4000。

格式建议：

```json
{
  "fuel":   {"amount": {"p10": 28.0, "p50": 46.0, "p90": 78.0}, "per_month": [2, 8],  "weekday_hint": "Fri/Sat"},
  "grocer": {"amount": {"p10": 32.0, "p50": 96.0, "p90": 210.0}, "per_month": [3, 9],  "weekday_hint": null},
  "dining": {"amount": {"p10": 11.0, "p50": 24.0, "p90": 62.0}, "per_month": [2, 12], "weekday_hint": "Fri/Sat"}
}
```

**用法**：求解器从分布里抽样（对数正态或分位插值），审计用 KS 检验比对实际产出。**模型只看这些数字的摘要，不许自己编区间。**

## 3. `bank_profiles.json` 必填字段

```json
{
  "account": "chase-personal",
  "bank": "JPMorgan Chase",
  "legal": "JPMorgan Chase Bank, N.A.",
  "domain": "chase.com",
  "phone": "1-800-935-9935",
  "address": "San Antonio, TX 78265-9805",
  "account_format": "masked-last4",
  "statement_cycle": "monthly-calendar",
  "card_tail_in_row": true,
  "type_column": false,
  "sort": "asc"
}
```

这些字段**同时**出现在契约里——契约是权威，这张表只是给人/给提示词看的摘要。改一处必须改两处，所以建议直接从契约生成（`tools/statement_lint.py --dump-profile` 可以生成骨架）。

## 4. 地理表：只需要「能起冲突的三件事」

1. **公用事业**：同城有哪几家电力/燃气公司（Austin: `AUSTIN ENERGY` / `TEXAS GAS SERVICE`；LA 县卫星城: `SOUTHERN CALIFORNIA EDISON` + `SO CAL GAS`；NYC: `CON EDISON`）。
2. **州域连锁**：只有某几个州才有的品牌（`WHATABURGER` 德州、`IN-N-OUT` 西海岸、`BUC-EE'S` 南部、`KROGER` 加州已退出）。
3. **城市写法**：账单上印的是 `TEMPLE CITY CA`（不带逗号、全大写），别写成 `Temple City, California`。

## 5. 日历表：算法优于数据

节日不要手写日期——会算错。用规则算（`kit/schemas` 之外给了一个参考实现思路）：

- 感恩节 = 11 月第 4 个周四；黑五 = +1；网一 = +4
- 复活节 = Computus 算法；母亲节 = 5 月第 2 个周日；父亲节 = 6 月第 3 个周日
- 纪念日 = 5 月最后一个周一；劳动节 = 9 月第 1 个周一；MLK = 1 月第 3 个周一

每个节日挂「影响哪些类目 + 消费倍数」，例：`Thanksgiving → groceries ×1.35, dining ×0.8`、`Black Friday → shopping ×1.9`。

## 6. 外部资料清单（按优先级）

| 资料 | 用途 | 链接 |
| --- | --- | --- |
| BLS Consumer Expenditure Survey | 类目占比、收入档差异 | https://www.bls.gov/cex/ |
| CFPB TISA / Reg DD | 对账单必须有哪些区块 | https://files.consumerfinance.gov/f/documents/102012_cfpb_truth-savings-act-tisa_procedures.pdf |
| 12 CFR 230.6 | 周期对账单披露条目 | https://law.justia.com/cfr/title12/12-3.0.1.1.11.0.8.6.html |
| Nacha 规则（Company Entry Description） | ACH 行描述长度与更新 | https://www.nacha.org/system/files/2020-05/Account-Information-Improvements-Proposed-Modifications-to-the-Rules-combined-May%202020.pdf |
| Visa/Mastercard 商户描述符规范 | 卡行描述符结构与长度 | https://docs.cybersource.com/content/dam/new-documentation/documentation/en/merchant-descriptors/developer/all/so/merchant-descriptors-so-all.pdf |
| Federal Reserve Check 21 | 支票/替代支票参照号 | https://www.federalreserve.gov/secrs/2004/may/20040525/r-1176/r-1176_150_1.pdf |
| 各家银行公开的 sample statement | 逐家排版（最强证据） | 搜 `<bank> sample statement pdf` |

## 7. 知识库的三条纪律

1. **能查表就不问模型**。模型的记忆是概率的，表是确定的。
2. **每条规则带出处**。契约里每条的 `evidence` 指向「哪个 PDF 第几页」或「哪段代码」。没有出处的规则不进契约，只进 `open_questions`。
3. **过期比缺失更危险**。品牌退出某州、银行改版式，都要能被 `--diff` 发现；知识库里每个文件头部写 `updated: YYYY-MM-DD`。
