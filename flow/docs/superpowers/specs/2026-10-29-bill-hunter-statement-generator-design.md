# 账单猎手 · 真账单流水生成器 设计文档

- 日期：2026-10-29
- 状态：待审阅
- 范围：一个独立的 TypeScript 小应用（Web 界面 + HTTP API），用 LangGraph 工作流为游戏《账单猎手》生成"真"账单的交易流水数据（JSON）。

## 1. 背景与目标

《账单猎手》每局给玩家 10 份账单，其中 1 份是假的，玩家要把它找出来。本工具只负责生成**真**账单：交易内容要经得起熟悉美国生活的人细看。

成功标准：

1. 每条交易的商户真实存在于人设所在地，价格符合当地当月水平，并且有可追溯的来源链接。
2. 流水的节奏、描述格式、金额细节符合美国银行流水的真实样子。
3. 硬约束（每月条数区间、余额连续、两个账户之间的还款对账）**100% 满足**，由代码保证，不依赖模型自觉遵守。
4. 同一个 seed 和同一份调研缓存，可以复现同一份账单。

## 2. 边界（明确不做的事）

- **不生成任何真实银行的版式、品牌、Logo 或 PDF**，也不接入本机的 `statement-renderer` 服务。账单外观由游戏自己渲染，使用虚构银行名（默认 `Harborline Bank`，通过环境变量 `BANK_DISPLAY_NAME` 配置）。
- 账号和卡号只生成后四位。不生成路由号（routing number），也不生成完整卡号。
- 不生成假账单：假账单由游戏的其他部分负责。
- 第一版不做多用户和权限系统，Web 界面只监听本机。

## 3. 需求

### 3.1 主体类型

**个人（personal）**：一律按**个体户**（self-employed / 1099）处理，**没有工资收入**。

- 字段：姓名、年龄、地址（街道、城市、州、邮编）、职业/行当、家庭情况（单身/已婚/子女数）、喜好。
- 每个字段都可以单独随机，也可以一键全部随机。
- 收入渠道示例：
  - 客户 ACH 打款
  - Stripe、Square、PayPal 净额回款
  - Zelle、Venmo 收款
  - Upwork、Fiverr 提现
  - 网约车和外卖平台周结或即时提现
  - 手机存入支票（`MOBILE DEPOSIT`）
- 收入不规律：有淡旺季，偶尔一笔大单。
- 个体户特有的支出：
  - 联邦季度预缴税（`IRS USATAXPYMT`，4/15、6/15、9/15、1/15 前后）
  - 州预缴税
  - Marketplace 医保
  - 偶尔一笔 SEP-IRA 供款
  - 经营支出与私人消费混在同一账户里，内容按职业决定

**企业（business）**：

- 字段：公司名、实体类型（LLC / S-Corp / Sole Prop）、行业、主营业务描述、成立时间、员工人数、所在地。同样支持随机。
- 给员工发工资（Gusto、ADP）作为**支出**保留。
- 其他典型项目：
  - 支付平台的净额回款
  - 行业供应商（如餐厅从 Sysco 进货）
  - 房租
  - SaaS 订阅（QuickBooks、Google Workspace 等）
  - 商业保险
  - 季度预缴税（EFTPS）
  - 州销售税申报缴纳

### 3.2 账户

可以选择 `checking`、`credit` 或 `both`：

- **checking**（个人为 checking，企业为 business checking）：有收入也有支出，带期初和期末余额。
- **credit**：入账只有还款、退款和返现。账单上有信用额度、账单周期、本期应还、最低还款额、到期日、APR；没有全额还款时产生 `INTEREST CHARGE`。
- **both**：两个账户联动。
  - 支票账户里的 `ONLINE PMT TO CARD ...1234` 与信用卡上的 `PAYMENT - THANK YOU` 金额完全一致，日期相差 0～2 天。
  - 消费分工：日常小额消费和订阅走信用卡；房租、预缴税、Zelle、ATM 走支票账户。

### 3.3 周期与条数

- `months`：1、3 或 6。`endMonth` 默认取最近一个完整月（以今天 2026-10-29 计为 2026-09）。
- **每个账户分别设置**每月条数区间 `{min, max}`。每个月的条数都必须在区间内，这是硬约束。
  - checking 的"每月"指自然月。
  - credit 的"每月"指一个账单周期，例如上月 13 日到本月 12 日。条数按入账日（postedDate）归属到周期。

### 3.4 真实感要求

| 维度 | 要求 |
|---|---|
| 商户 | 只用 Tavily 调研到的、当地真实存在的商户；每条交易都有 `evidence`（来源链接 + 价格依据） |
| 描述 | 符合美国银行流水格式，例如 `POS DEBIT`、`ACH DEBIT`、`SQ *`、`TST*`、`AMZN Mktp US*`、`ZELLE TO/FROM`、`CHECKCARD MMDD`，带门店编号和城市/州缩写；同一商户每次出现的描述一致 |
| 时间 | 周末刷卡周一入账；账单扣款遇节假日顺延到下一个工作日；工作日和周末的消费习惯不同；节日效应（Thanksgiving、Black Friday、开学季）；水电费随季节波动 |
| 金额 | 含当地销售税的零头；餐厅加 15–20% 小费；加油金额 = 当地油价 × 加仑数；ATM 取款为 20 的倍数并收手续费；固定订阅每月金额一致 |
| 一致性 | 余额连续；不凭空透支（如果透支，必须出现对应的 overdraft fee）；地理位置与人设一致，偶尔出现合理的外地消费 |

## 4. 架构

### 4.1 项目结构（pnpm monorepo）

```
packages/core    LangGraph 工作流、排程引擎、校验器、zod 数据结构（核心逻辑，可独立测试）
skills/          领域知识 markdown，按需注入到各节点
apps/server      Hono API + SSE；SQLite 存任务、结果、调研缓存和 checkpoint
apps/web         React + Vite：填参数、看进度、预览账单、下载 JSON
```

### 4.2 外部依赖

- **模型**：`@langchain/openai` 的 `ChatOpenAI`，`configuration.baseURL` 指向 `LLM_BASE_URL`，模型为 `LLM_MODEL=gpt-6.1-sol`，key 存在 `LLM_API_KEY`。
- **调研**：`@langchain/tavily`，key 存在 `TAVILY_API_KEY`。
- **工作流**：`@langchain/langgraph`，使用 SQLite checkpointer。
- **密钥**：所有密钥只放在 `.env`（已加入 gitignore）；仓库里提供 `.env.example`。服务端 API key 存在 `API_KEY`，游戏服务端通过 `X-API-Key` 请求头携带。

**第 0 步验证**：确认代理接口的 baseURL 路径（是否需要 `/v1`），以及是否支持 JSON schema 结构化输出和 tool calling。验证结果决定 4.4 节里的降级策略是否启用。

### 4.3 skills（领域知识）

| 文件 | 内容 | 注入的节点 |
|---|---|---|
| `bank-descriptors.md` | 各类交易在美国银行流水上的描述格式和常见支付平台前缀 | descriptors、audit |
| `self-employed-patterns.md` | 各行当个体户的收入渠道、节奏、淡旺季和经营支出 | planResearch、lifeModel、audit |
| `industry-cost-structures.md` | 各行业企业的收入构成、成本结构、典型供应商 | planResearch、lifeModel、audit |
| `us-calendar-seasonality.md` | 联邦节假日、预缴税日期、消费季节性 | lifeModel、audit |
| `consumer-habits.md` | 卡和支票账户的使用分工、小费习惯、订阅、地域差异 | lifeModel、audit |

skills 可以单独修改和扩充，改了不用动代码。

### 4.4 工作流节点

```
normalize → planResearch → research×N（Send 并行）→ lifeModel → schedule → descriptors → validate → audit → finalize
                                                      ↑            ↑           ↑                         │
                                                      └────────────┴───────────┴────── 按问题类型打回 ─────┘
```

1. **normalize**（代码）：
   - 用 zod 校验输入。
   - 推导出都市区、州和地方合计销售税率、生活成本档位、账单周期内的节假日。
   - 检查参数是否有解，例如条数上限是否小于必有的固定支出条数。无解直接返回 400 并说明原因。

2. **planResearch**（模型）：根据人设和 skills 生成调研问题清单，每个问题都带城市和品类，例如：
   - "affordable grocery stores Austin TX"
   - "average electric bill Austin TX 2026"
   - "DoorDash driver weekly earnings Austin"

3. **research**（Tavily，并行）：
   - 先查缓存。缓存键为 `metro + category`，有效期 30 天。
   - 没命中就调用 Tavily，再由模型提取为 `MerchantFact[]`：`{id, name, chain?, city, state, category, priceItems[{item, price, sourceUrl}], sourceUrl}`。
   - **没有 sourceUrl 的条目直接丢弃。**

4. **lifeModel**（模型 + skills）：输出 `FinancialModel`：
   - `incomeStreams`：渠道、频率、金额分布、季节系数、入账账户。
   - `recurring`：名称、日期规则、精确金额或季节区间、走哪个账户、`factId`。
   - `discretionaryPool`：商户 `factId`、权重、每月频次、单笔金额区间、是否加小费、走哪个账户。
   - `openingBalance`、`creditLimit`、`apr`、还款策略（全额或部分）。

   每个商户都必须引用一个存在的 `factId`，否则视为编造并驳回。

5. **schedule**（纯代码，使用带 seed 的随机数生成器）：
   1. 每个账户每月在 `[min, max]` 区间内随机定一个目标条数。
   2. 按顺序填充：固定扣款 → 收入 → 信用卡还款（联动时）→ 按权重补日常消费，直到达到目标条数。
   3. 应用日历规则：周末和节假日的入账顺延、交易日与入账日分开。
   4. 计算金额：在价格区间内取值，再乘以税率、加小费、保留零头。以"分"为单位的整数存储。
   5. 计算每一条之后的余额。余额不足时，先推迟可推迟的支出；无法避免的透支要生成 overdraft fee。
   6. 如果固定支出已经超过当月上限，按优先级合并或去掉最低优先级的项目。**保证最终条数在区间内。**

6. **descriptors**（模型）：
   - 按**商户**而不是逐条生成描述模板，例如 `TST* {NAME} {STORE} {CITY} {ST}`，由代码填入具体信息。
   - 同时生成英文备注 `memo`。

7. **validate**（代码，硬规则）：
   - 每月条数在区间内
   - 余额连续
   - 日期都在账单周期内
   - 固定扣款每月金额和日期一致
   - 联动账户的还款两边对得上
   - 金额落在对应 fact 的价格区间内（允许含税和小费）
   - 地理位置一致
   - 没有重复交易
   - 每条交易都有 evidence

8. **audit**（模型 + skills）：通读整份账单，输出 `AuditFinding[] {severity: low|medium|high, transactionIds, problem, suggestedTarget: lifeModel|schedule|descriptors}`。

9. **路由**：
   - validate 失败：打回 schedule；如果是 FinancialModel 本身导致的，打回 lifeModel。
   - audit 有 high 级别问题：打回 suggestedTarget 指向的节点。
   - 最多重试 2 轮。超过后带着警告输出，任务状态记为 `completed_with_warnings`。

10. **finalize**：组装输出、保存结果、推送完成事件。

### 4.5 输出结构

```ts
type GenerationResult = {
  id: string; seed: string; createdAt: string;
  subject: PersonalProfile | BusinessProfile;
  statements: Statement[];
  audit: { rules: RuleResult[]; findings: AuditFinding[] };
  usage: { llmCalls: number; tokens: number; tavilyQueries: number };
};

type Statement = {
  bankName: string;                       // 虚构银行
  accountType: "checking" | "business_checking" | "credit";
  accountLast4: string;
  periodStart: string; periodEnd: string; // 覆盖所选的全部月份
  months: MonthSummary[];                 // 每个账单周期一份
  checking?: { openingBalanceCents: number; closingBalanceCents: number };  // 整个区间的首尾余额
  credit?: { creditLimitCents: number; apr: number };
  transactions: Transaction[];
};

type MonthSummary = {
  cycleStart: string; cycleEnd: string;   // checking 按自然月；credit 按账单日周期（如每月 12 日结账）
  openingBalanceCents: number; closingBalanceCents: number;
  totalCreditsCents: number; totalDebitsCents: number; transactionCount: number;
  credit?: { statementBalanceCents: number; minimumDueCents: number; dueDate: string;
             interestChargedCents: number };
};

type Transaction = {
  id: string;
  transactionDate: string; postedDate: string;
  description: string;                    // 银行格式描述
  memo: string;                           // 英文备注
  type: "POS" | "ACH" | "ZELLE" | "CHECK" | "ATM" | "FEE" | "INTEREST" | "TRANSFER" | "PAYMENT" | "REFUND" | "DEPOSIT";
  direction: "credit" | "debit";
  amountCents: number;
  runningBalanceCents?: number;           // 只有 checking 有
  merchant?: { name: string; category: string; city: string; state: string };
  evidence?: { factId: string; sourceUrl: string; note: string };
};
```

## 5. API

鉴权：在请求头 `X-API-Key` 中携带 `.env` 里的 `API_KEY`。Web 界面只监听 `127.0.0.1`。

| 方法与路径 | 说明 |
|---|---|
| `POST /api/personas/random` | `{subjectType, partial?}`，随机生成人设，已填的字段保留 |
| `POST /api/generations` | 创建生成任务，返回 `202 {id}` |
| `GET /api/generations/:id` | 查询状态（`queued`、`running`、`completed`、`completed_with_warnings`、`failed`、`interrupted`）和结果 |
| `GET /api/generations/:id/events` | SSE 实时进度：节点开始/结束、调研查询、找到的来源、校验结果、打回原因 |
| `GET /api/generations/:id/export.json` | 下载 `GenerationResult` |
| `POST /api/generations/:id/resume` | 从 checkpoint 继续已中断的任务 |
| `GET /api/generations` | 历史列表，可按类型和状态筛选 |
| `POST /api/batches` | `{count ≤ 10, subjectType, accounts, months, ranges}`，批量创建随机人设的任务 |
| `DELETE /api/generations/:id` | 删除任务 |

- 最多同时执行 2 个任务，其余排队。
- 错误统一返回 `{error:{code,message}}`，不包含密钥或模型原始输出。

## 6. Web 界面

1. **新建**（三步）：
   - 第一步：选个人还是企业。
   - 第二步：填写资料，每个字段旁有 🎲 按钮单独随机，顶部有"全部随机"。
   - 第三步：选账户类型和月数，每个账户分别设置条数区间（滑块加数字输入），表单实时校验。
2. **进度**：工作流时间线，当前节点高亮；能看到调研查询和来源链接；被打回时显示原因。
3. **结果**：
   - 每个账户一个标签页，按虚构银行的账单样式预览：账户头信息、每月汇总、交易明细。
   - 点击某条交易，展开 evidence。
   - 侧栏显示审计报告。
   - 操作：下载 JSON、用同一 seed 复现、换 seed 重新生成。
4. **历史**：所有生成记录，可以打开或删除。

界面语言为中文，账单内容为英文。

## 7. 错误处理

| 情况 | 处理方式 |
|---|---|
| 模型超时、429、5xx | 指数退避，最多重试 3 次 |
| 模型输出不符合 zod 结构 | 把校验报错发回模型修正 1 次；仍失败则该节点失败 |
| 代理不支持 JSON schema | 降级为 JSON mode + zod 解析 |
| Tavily 失败或额度用完 | 使用过期缓存并标记警告；查不到时从城市放宽到州再查一次；仍查不到就去掉该品类，并写进审计报告 |
| 参数无解 | normalize 阶段返回 400 并说明原因 |
| 服务重启 | 进行中的任务标记为 `interrupted`，可以通过 resume 接口继续 |
| 成本上限 | 每个任务最多 40 次模型调用、25 次 Tavily 查询、总时长 10 分钟；超过后终止任务，并保存已有结果 |

每个任务都记录 token 用量和 Tavily 查询次数。

## 8. 测试（vitest）

1. **排程引擎属性测试**（fast-check）：对任意 seed 和任意合法的条数区间，以下规则永远成立：每月条数在区间内、余额连续、日期在账单周期内、联动账户的还款对得上、固定扣款每月一致。
2. **日历规则**：2026 年联邦节假日、周末入账顺延、季度预缴税日期。
3. **校验器**：每条规则都配一份手工构造的"坏账单"，确认能被拦下。
4. **整条工作流**：用假模型和录制好的 Tavily 结果跑完整流程，结果固定可重复；包括打回重做的路径。
5. **API**：用 Hono 的 `app.request` 测试各接口和错误码。
6. **真实环境冒烟测试**（需要设置环境变量 `LIVE_SMOKE=1` 才运行）：用真实的接口和 Tavily，个人和企业各生成一份一个月的账单，由人工检查真实感。

## 9. 已确认的决策

| 决策 | 结论 |
|---|---|
| 联网调研 | Tavily，结果按 metro + category 缓存 30 天 |
| 工作流方案 | 方案 A：模型负责调研和规划，代码负责排程和计算金额，再经过规则校验和模型审计 |
| 个人人设 | 一律为个体户，没有工资收入 |
| 企业发薪 | 作为支出保留 |
| 账户类型 | checking、credit、两者联动 |
| 条数区间 | 每个账户分别设置，硬约束 |
| 交付形式 | Web 界面 + HTTP API，输出 JSON |
| 账单外观 | 虚构银行，由游戏渲染；不接入 statement-renderer |
