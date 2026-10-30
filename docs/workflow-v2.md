# 工作流 v2（完整版）：谁在什么时候用契约

> 一句话：**契约不是给人看的文档，是每个阶段的输入约束**。一个账户一份契约（`contracts/<template>.json`），
> 从「选哪个账户」到「出片前最后一次校验」都被同一份契约卡住——阶段之间不许各自解释格式。
>
> 上游设计见 `docs/redesign-life-simulator.md`（为什么这么拆）、`docs/statement-contract.md`（契约三层结构）。

## 0. 全链路（含契约的读写点）

```
档案 profile.json ─┐
                   ├─① 选账户 ──► account.json（bank/template/账户形态）
                   │                 │ 读：contracts/<template>.json（columns / row_rules.account_format）
                   ├─② 档案编译器 ──► persona.json（12–24 个月的生活底数）
                   │                 │ 读：row_rules.account_format（账号分组）、bank_identity（抬头）
                   ├─③ 生活导演(LLM) ─► plan.json（只写事件，不写金额）
                   │                 │ 读：type_enum（这版能印哪些行类）、descriptor（每类行需要哪些字段）
                   ├─④ 求解器(纯代码) ─► ledger.jsonl（金额/日期/余额，构造即闭合）
                   │                 │ 读：row_rules（amount_decimals / order / balance_chain / sections）
                   ├─⑤ 选角(LLM+检索) ► cast.json → rows.json（描述符定稿）
                   │                 │ 读：descriptor（骨架 + rules: no_city / has_card_tail / has_place / max_len）
                   │                 │ 读：forbidden_literals、bank_identity
                   ├─⑥ 审计团(6 agent) ► findings.json（只否决，不改写）
                   │                 │ 工具：contracts.check()（契约审计员的唯一判据）
                   ├─否决 ──────────► 回 ③（带证据行：哪条规则、哪一行、哪份参考件）
                   └─通过 ──⑦ 投影 ──► months/<YYYY-MM>.json + PDF
                                     │ 执行：contracts.conform()（唯一整形点）→ contracts.check()（闸门）
                                     │ 记录：manifest 里存契约 sha256（可追溯是哪版规则出的片）
```

**唯一执行点**：`contracts.conform()` 在 ⑦ 之前跑一次；其它阶段**只读**契约，不许自己裁字符串、自己补城市、自己删卡号。

## 0.5 阶段 ↔ agent 对照

| stage | agent id | 名字 | 读契约 | 改数字 | 状态 |
| --- | --- | --- | --- | --- | --- |
| 0 | `conductor` | 总指挥 | 不读 | 否 | planned |
| 1 | `account-picker` | 选账户 | `kind`/`columns`/`row_rules.account_format`/`bank_identity`/`open_questions` | 否 | planned |
| 2 | `persona-compiler` | 档案编译器 | `row_rules.account_format`/`bank_identity` | 否 | planned |
| 3 | `life-director` | 生活导演 | `type_enum`/`descriptor.*.template` | 否 | planned |
| 4 | `solver` | 求解器 | `row_rules.*` | **是** | planned |
| 5 | `cast-merchants` | 选角 | `descriptor.*`/`card_types`/`forbidden_literals`/`bank_identity` | 否 | planned |
| 6 | `audit-accounting` | 会计审计 | `row_rules.balance_chain` | 否 | planned |
| 6 | `audit-geography` | 地理审计 | `bank_identity` | 否 | planned |
| 6 | `audit-statistics` | 统计审计 | `row_rules.amount_decimals` | 否 | planned |
| 6 | `audit-adversarial` | 反诈审计 | `forbidden_literals` | 否 | planned |
| 6 | `audit-continuity` | 连续性审计 | `row_rules.order`/`sections` | 否 | planned |
| 6 | `audit-contract` | 契约审计 | `type_enum`/`descriptor.*.rules`/`open_questions` | 否 | planned |
| 7 | `renderer` | 投影/渲染 | `descriptor.*.rules`/`type_aliases`/`required_literals` | 否 | partial |
| 8 | `contract-extractor` | 契约提取 | 全部 | 否 | partial |

清单在 `agents/manifest.json`（`stage` + `contracts_read` 必填，`tools/check_agents.py` 守着）；`tests/test_workflow_doc.py` 守着这张表与清单不脱节。

## 1. 逐阶段规格

| 阶段 | 谁 | 输入 | 输出 | 读契约的哪些字段 | 能不能改数字 |
| --- | --- | --- | --- | --- | --- |
| ① 选账户 | 代码 + 档案 | `profile.json` | `account.json` | `columns`、`row_rules.account_format`、`kind` | 否 |
| ② 档案编译器 | 代码 + 查表 | `profile.json`、`account.json` | `persona.json` | `row_rules.account_format`、`bank_identity` | 否（只派生） |
| ③ 生活导演 | LLM（large, T≈0.7） | `persona.json`、`account.json`、日历 | `plan.json` | `type_enum`、`descriptor` 的字段占位 | **否**（只写事件） |
| ④ 求解器 | 纯代码 | `plan.json`、`persona.json` | `ledger.jsonl` | `row_rules`（精度/顺序/分栏/余额链） | **是**（唯一产地） |
| ⑤ 选角 | LLM + 检索（medium, T≈0.4） | `ledger.jsonl`、`account.json`、地理库 | `rows.json` | `descriptor`（骨架 + rules）、`forbidden_literals`、`bank_identity` | 否（不改金额，只填名词） |
| ⑥ 审计团 | 6 个 agent（各有工具） | 以上全部 | `findings.json` | 契约审计员用 `check_data()`；其余用分布/地理/连续性判据 | 否（只否决） |
| ⑦ 投影 | 代码 | `rows.json`、`account.json` | 月 JSON + PDF | `conform()` → `check()` → 渲染 | 否（只整形） |

### ① 选账户（契约的第一读者）

- 输入：档案（个人/企业、所在城市州、偏好银行）。
- 决策：`template`（如 `chase-business`）、`kind`（deposit）、账户形态。
- 契约用途：
  - `columns` 决定这个账户能不能表达计划里的行类（例如 BoA 没有 Type 列，事件只能落到「存款/取款」两栏）；
  - `row_rules.account_format` 决定账号怎么印（U.S. Bank 是 `1 964 9173 9161` 分组，不是掩码）；
  - `bank_identity` 决定抬头（行名/法定主体/域名/电话/城市州邮编），后面所有阶段不许再另造。
- 失败处理：档案指定银行但没有契约 → 直接失败（`没有 <template> 的账单契约`），不允许「拿相近的版式凑」。

### ② 档案编译器

- 把档案摊成 12–24 个月的生活底数：收入形态（工资/自雇/混合）、房租、家庭结构、通勤、订阅、保险、车龄。
- 查表规则（代码，不调模型）：职业 → 收入形态与发薪日；收入档 → 信封份额；城市 → 公用事业与税率。
- 契约用途：只读 `account_format` 与 `bank_identity`；**不读描述符**（这时候还没有行）。

### ③ 生活导演（LLM）

- 输出 `plan.json`：事件列表（类型、日期、地点、人物、依赖关系），**没有金额字段**。
- 契约用途（这是「谁会写错」的第一道闸）：
  - `type_enum`：只能从这版式支持的取值里选（Mercury 只有 `Card / ACH Out / ACH In`；Chase 有 `Card Purchase / ACH Debit / Zelle Payment / Check Paid …`）；
  - `descriptor` 的占位符：计划里要带够字段。例如 Chase `zelle = "Zelle Payment to {party}"`，那事件必须给收付款人姓名；BoA `ach = "DES:{des} ID:{ref} INDN:{holder} CO ID:XXXXX{co} {orig}"`，则必须给 `des/ref/holder/orig`。
  - 契约里没有的行类 → 事件写不出来，导演必须换表达方式（例如 BoA 没有卡行 Type 列，就别计划「刷卡消费 + 类型标记」）。
- 模型 Tier：large，温度 0.7（要多样性），但输出受 JSON Schema + 契约枚举双重约束。

### ④ 求解器（纯代码，构造即闭合）

- 四类不变量：期初 + 流水 = 期末；余额链逐行连续；类目总额落在信封区间；事件间隔/金额服从分布（`distributions.json`）。
- 契约用途：`amount_decimals`（两位小数）、`order`（SoFi 倒序打印，最新在上）、`sections`（BoA 的存/取两栏）、`balance_chain`。
- **不读描述符**：求解器只碰数字与日期，碰描述符就会开始「顺手拼字符串」，那就回到了治标。

### ⑤ 选角（LLM + 检索）

- 输入：`ledger.jsonl` 的每一行（类目 + 金额 + 日期 + 城市）。
- 输出：真实商户名、门店号、地点、Zelle 对方姓名、ACH 对方机构。
- 契约用途（写描述符的**唯一**允许的位置）：
  - `descriptor.<kind>.template`：骨架（Chase 卡行 `{merchant} {city} {state} Card {last4}`；Mercury 卡行只有 `{merchant}`）；
  - `descriptor.<kind>.rules`：`no_city`（Mercury 不许带持卡人城市）、`has_card_tail`（Chase 行尾必须有 `Card 1234`）、`has_place`（Capital One / SoFi 必须带城市州）、`no_payment_tail`（ACH 不许带 `PAYMENT` 尾巴）、`upper`；
  - `descriptor.<kind>.max_len`：描述符长度上限；
  - `forbidden_literals`：不许写 `SAMPLE`、行名串台（`Bank of America` 出现在 Mercury 单子上）；
  - `bank_identity`：电话/域名/地址一律取契约，不许模型自己编。
- 检索约束：商户必须在人所在城市半径内（或线上品牌按注册地）；同一品牌门店号一月内不重复到假。

### ⑥ 审计团（6 个 agent，只否决）

| agent | 判据 | 工具 | 典型 findings |
| --- | --- | --- | --- |
| 会计 | 逐行余额链、期初+流水=期末、摘要分项 | 纯代码 | `行 12 余额断链：$1,204.31 应为 $1,204.30` |
| 地理 | 商户与市政/公用事业是否同城同州；州域连锁 | 地理库 + `web_search` | `Austin 户头出现 Con Edison` |
| 统计 | 类目占比、金额分布、间隔、星期效应、圆整率 | `distributions.json` + KS/卡方 | `餐饮 6 笔集中在 $40–43，长尾缺失` |
| 反诈 | 金额档、同店连刷、整数额、月初集中、节日错位 | 规则集 | `同一门店一个月刷 5 次且金额相同` |
| 连续性 | 跨月：上期末=下期初、订阅不跳月、车龄/保险周期 | 账本前三个月 | `Netflix 11 月缺失但 10/12 月都有` |
| 契约 | `contracts.check_data()` 全绿；`open_questions` 是否被触碰 | `contracts.py` | `Type 列出现裸 ACH`、`卡行带城市` |

- 否决必须**带证据行**：`{stage, rule, row_index/row_id, expected, actual, evidence_ref}`，例如
  `{"rule":"descriptor-card-tail","row":37,"expected":"… Card 3217","actual":"… AUSTIN TX","evidence":"contracts/chase-personal.json#descriptor.card"}`。
- 回退目标：格式/描述符类 → ⑤（选角重挑）；计划类（事件类型不可能、依赖冲突） → ③；金额分布类 → ④。
- **审计员不改写**：`can_write_numbers: false` 是硬约束，改了就分不清「谁错了」。

### ⑦ 投影（渲染前的唯一整形点）

1. `contracts.conform(template, data)`：按契约去城市 / 去 `PAYMENT` 尾巴 / 裁长度 / 归一 Type（返回计数，写进 `job-summary.json`）；
2. `contracts.check(template, data)`：不过就**拒绝出片**，把 findings 原样抛回 ⑥；
3. 渲染：`registry.get_renderer(template).render(data, RenderOptions(watermark=False))`；
4. `manifest.json` 记录 `contract_sha256`：出问题能追到「当时用的是哪版契约」。

## 2. 契约字段 → 消费者矩阵

| 契约字段 | ① 选账户 | ② 编译器 | ③ 导演 | ④ 求解器 | ⑤ 选角 | ⑥ 审计 | ⑦ 投影 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `kind` / `columns` | ✅ 决策 | — | ✅ 校验可行性 | — | — | ✅ | ✅ |
| `type_enum` / `type_aliases` | — | — | ✅ 只能选这些 | — | ✅ 落盘前归一 | ✅ | ✅ 归一 |
| `card_types` | — | — | — | — | ✅ 判是不是卡行 | ✅ | ✅ |
| `descriptor.*.template` | — | — | ✅ 知道要哪些字段 | — | ✅ 拼描述符 | ✅ | — |
| `descriptor.*.rules` | — | — | — | — | ✅ | ✅ | ✅ 整形 |
| `descriptor.*.max_len` | — | — | — | — | ✅ 截断前判断 | ✅ | ✅ 截断 |
| `forbidden_literals` | — | — | — | — | ✅ | ✅ | ✅ |
| `required_literals` | — | — | — | — | ✅ 抬头/页脚 | ✅ | ✅ |
| `row_rules.amount_decimals` | — | — | — | ✅ | — | ✅ | ✅ |
| `row_rules.order` | — | — | — | ✅（SoFi 倒序） | — | ✅ | ✅ |
| `row_rules.sections` | ✅ 可行性 | — | ✅ | ✅（BoA 两栏） | — | ✅ | ✅ |
| `row_rules.account_format` | ✅ | ✅ | — | — | — | ✅ | ✅ |
| `bank_identity` | ✅ | ✅ | — | — | ✅ | ✅ | ✅ |
| `open_questions` | ✅ 警告 | — | — | — | — | ✅ 触碰即拒 | ✅ 记录 |

## 3. 契约的生命周期

| 阶段 | 动作 | 命令 |
| --- | --- | --- |
| 新增/换参考件 | 从参考件提取，并与人工规则合并 | `PYTHONPATH=.:src .venv/bin/python tools/contract_extract.py --templates <id>` |
| 人工补规则 | 改 `contracts/curated/<theme>.json`（**必须带 evidence 与 why**） | 再跑一次提取器 |
| 漂移检测 | 参考件换版后，规则该跟着变 | `… --diff`（CI 里跑，有差异就红） |
| 出片追溯 | manifest 记录契约 sha256 | 自动 |

## 4. 三道闸门（缺一不可）

1. **计划闸**（③ 之后）：`plan.json` 里每个事件的行类 ∈ `type_enum`，占位符字段齐全；不齐就让导演重写，别进求解器。
2. **交付闸**（⑦ 之前）：`conform()` 之后再 `check()`，非空就拒出片。
3. **回归闸**（仓库级）：`tests/test_contract_extract.py` 保证「一家账户一份契约 + 一份 skill」「每条规则有出处」「提取确定」；`tools/realism_rules.py` 保证版式层不漂。

## 5. 现在还没有的部分（别假装有）

- ① 选账户目前是档案里指定的 `bank`，没有「按职业/城市推荐账户」的逻辑；
- ⑤ 选角还没有接真实 POI 检索（现在靠 `prompts.py::MERCHANT_*` 词表 + 州域表）；
- ⑥ 审计团的 6 个 agent 只有提示词与判据，没有接线跑（`agents/manifest.json` 里 `status: planned` 待补）；
- `distributions.json` 还没建（统计审计现在用的是 envelope 区间，不是真实分布）。
