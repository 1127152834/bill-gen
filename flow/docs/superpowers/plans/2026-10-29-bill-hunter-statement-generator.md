# 账单猎手 · 真账单流水生成器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一个 TypeScript 应用（Hono API + React 界面），用 LangGraph 工作流为《账单猎手》生成真实可信的美国个人（个体户）/企业 checking 与信用卡交易流水 JSON。

**Architecture:** `packages/core` 放全部领域逻辑：zod 结构、带 seed 的排程引擎、硬规则校验、LangGraph 图（模型负责调研与规划，代码负责排程与金额）。`apps/server` 用 SQLite 保存任务、事件、调研缓存和 checkpoint，并以 SSE 推送进度。`apps/web` 是本机使用的四页界面。

**Tech Stack:** Node ≥ 22、pnpm workspaces、TypeScript（strict）、zod、@langchain/langgraph、@langchain/langgraph-checkpoint-sqlite、@langchain/openai、@langchain/tavily、Hono + @hono/node-server、better-sqlite3、React + Vite + react-router-dom + Tailwind、vitest、fast-check、@testing-library/react。

**Spec:** `docs/superpowers/specs/2026-10-29-bill-hunter-statement-generator-design.md`

## Global Constraints

- 金额全部用整数"分"（`*Cents: number`），任何地方都不使用浮点金额。
- 日期一律为 `YYYY-MM-DD` 字符串、月份为 `YYYY-MM`，计算只在 UTC 下进行（`packages/core/src/date.ts`）。
- 不出现任何真实银行名、Logo、路由号、完整卡号/账号；银行名取 `BANK_DISPLAY_NAME`，默认 `Harborline Bank`；账号只有 `accountLast4`。
- 密钥只从 `.env` 读取：`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`（默认 `gpt-6.1-sol`）、`TAVILY_API_KEY`、`API_KEY`；另有 `BANK_DISPLAY_NAME`、`DATABASE_PATH`（默认 `./data/app.db`）、`HOST`（默认 `127.0.0.1`）、`PORT`（默认 `8797`）、`LLM_STRUCTURED_MODE`（`json_schema` | `function_calling` | `json_mode`，默认 `json_schema`）。`.env` 写入 `.gitignore`，提供 `.env.example`（值为空）。
- 个人人设一律个体户：不生成任何工资入账（`PAYROLL`/`DIRECT DEP` 作为入账描述禁止出现在 personal 结果中）。
- 每个账户每个周期的交易条数必须落在该账户的 `{min, max}` 内；checking 周期为自然月，credit 周期为账单日周期，按 `postedDate` 归属。
- 单任务上限：模型调用 40 次、Tavily 查询 25 次、总时长 10 分钟；调研缓存键 `metroId:category`，有效期 30 天；最多 2 个任务并发；打回重试最多 2 轮。
- 错误响应统一 `{error:{code,message}}`，不含密钥、模型原文或堆栈。
- 界面文字中文；账单内容（描述、memo）英文、描述全大写、长度 ≤ 60 字符。

## Review Focus

1. **跨年窗口**：`endMonth=2026-02, months=6`（2025-09 → 2026-02）——节假日、季度预缴税（2026-01-15）两年都要算对。测试加在 Task 3。
2. **区间收紧**：`{min:8,max:8}` 必须每期恰好 8 条；`max` 低于基础必需条数时 normalize 返回 `InfeasibleRequestError`。测试加在 Task 4、Task 6。
3. **只要信用卡**：没有 checking 时，信用卡还款仍以 `PAYMENT - THANK YOU` 出现（视为外部账户付款），R5 跳过而不是报错。测试加在 Task 6、Task 7。
4. **小城镇地址**：如 `Marfa, TX` 不在都市表内——回退到州级税率，`matchedBy:'state'`，调研范围用州。测试加在 Task 4。
5. **模型输出带围栏**：`json_mode` 下返回 ```` ```json … ``` ```` 或前后有说明文字——宽松解析仍能取出 JSON。测试加在 Task 9。

---

## File Structure

```
package.json  pnpm-workspace.yaml  tsconfig.base.json  .env.example  .gitignore
scripts/probe-llm.ts                 第 0 步：探测代理能力
scripts/smoke.ts                     LIVE_SMOKE 冒烟
skills/*.md                          5 个领域知识文件
packages/core/
  data/metros.json                   50 个人口最多的城市：metroId/city/state/population/combinedSalesTaxRate/colTier
  data/state-tax.json                各州平均合并销售税率
  src/schemas/{request,research,model,output}.ts
  src/date.ts  src/rng.ts  src/calendar.ts  src/geo.ts  src/normalize.ts
  src/schedule/{index,place,amounts,fill,card,balance}.ts
  src/descriptors.ts  src/assemble.ts  src/validate.ts
  src/skills.ts  src/budget.ts  src/llm.ts  src/research.ts  src/persona.ts
  src/graph/{state,deps,build,run}.ts  src/graph/nodes/*.ts  src/prompts/*.ts
  test/**/*.test.ts  test/fixtures/**
apps/server/src/{env,db,events,jobs,auth,app,index}.ts  apps/server/test/*.test.ts
apps/web/src/{api.ts,main.tsx,App.tsx}  apps/web/src/pages/{New,Progress,Result,History}.tsx  apps/web/src/components/*.tsx
```

---

### Task 1: Monorepo 骨架与代理能力探测

**Files:**
- Create: `package.json`, `pnpm-workspace.yaml`, `tsconfig.base.json`, `.gitignore`, `.env.example`, `packages/core/{package.json,tsconfig.json,vitest.config.ts}`, `apps/server/{package.json,tsconfig.json}`, `apps/web/`（`pnpm create vite` react-ts 模板）, `scripts/probe-llm.ts`, `docs/llm-probe.md`
- Test: `packages/core/test/smoke.test.ts`

**Interfaces:**
- Produces: 根脚本 `pnpm test`（所有包 vitest）、`pnpm typecheck`、`pnpm dev`（server + web 并行）、`pnpm probe`、`pnpm smoke`；包名 `@bh/core`、`@bh/server`、`@bh/web`。

- [ ] **Step 1:** `git init`；写 `.gitignore`（`node_modules`、`dist`、`.env`、`data/*.db*`）与 `.env.example`（Global Constraints 中所有变量，值为空或默认值）。
- [ ] **Step 2:** 建 workspace 与三个包；`tsconfig.base.json` 开 `strict`、`noUncheckedIndexedAccess`、`module: NodeNext`。依赖安装当时的最新稳定版，锁文件提交。
- [ ] **Step 3:** 写 `packages/core/test/smoke.test.ts`：`expect(1 + 1).toBe(2)`；运行 `pnpm test`，Expected: 1 passed。
- [ ] **Step 4:** 写 `scripts/probe-llm.ts`：用 `.env` 依次尝试 `LLM_BASE_URL` 与 `LLM_BASE_URL + '/v1'`，对每个可用地址分别测试 `withStructuredOutput(z.object({ok: z.boolean()}), {method})` 的 `jsonSchema`、`functionCalling`、`jsonMode` 三种方法，打印每种成功/失败。
- [ ] **Step 5:** 运行 `pnpm probe`，把结果（可用 baseURL、推荐 `LLM_STRUCTURED_MODE`）写进 `docs/llm-probe.md`，并更新 `.env`（不是 `.env.example`）。Expected：至少一种方法成功；若三种都失败，停下并告知用户。
- [ ] **Step 6:** Commit `chore: scaffold monorepo and probe llm proxy`。

---

### Task 2: 领域数据结构（zod）

**Files:**
- Create: `packages/core/src/schemas/{request,research,model,output}.ts`, `packages/core/src/index.ts`
- Test: `packages/core/test/schemas.test.ts`

**Interfaces:**
- Produces（全部导出 schema 与 `z.infer` 类型）：
  - `CountRange {min:int≥1, max:int≤200}`，refine `min ≤ max`。
  - `Address {street, city, state: /^[A-Z]{2}$/, zip: /^\d{5}$/}`。
  - `PersonalProfile {name, age:18–90, address, occupation, household:{maritalStatus:'single'|'married'|'partnered', children:0–6}, hobbies: string[] ≤8}`。
  - `BusinessProfile {legalName, entityType:'LLC'|'S-Corp'|'Sole Prop', industry, description, founded:'YYYY-MM', employeeCount:0–500, address}`。
  - `type Profile = PersonalProfile | BusinessProfile`。
  - `GenerationRequest {subjectType:'personal'|'business', profile?: Profile, accounts:{checking?:CountRange, credit?:CountRange}（至少一个）, months:1|3|6, endMonth?:'YYYY-MM', seed?:string}`。`profile` 缺省表示由任务先随机生成；`subjectType` 与 profile 形状必须一致。
  - `ResearchQuery {id, category, query, scope:'metro'|'state'}`；`MerchantFact {id, name, chain?, city, state, category, priceItems:{item, priceCents, sourceUrl}[], sourceUrl: url}`。
  - `FinancialModel`（见下）；`Transaction`、`MonthSummary`、`Statement`、`RuleResult`、`AuditFinding`、`GenerationResult` 与 spec §4.5 字段一一对应；`Transaction.type` 取 spec 中 11 种之一。
  - `FinancialModel`：
    ```ts
    { incomeStreams: { id; label; payerName; channel: 'ach_client'|'stripe'|'square'|'paypal'|'zelle'|'venmo'|'platform_payout'|'mobile_deposit'|'card_sales';
        cadence: {kind:'weekly'|'biweekly'; weekday:0-6} | {kind:'monthly'; day:1-28} | {kind:'irregular'; perMonth:CountRange};
        amountCents:{min;max}; seasonality:number[12]; factId?: string }[];
      recurring: { id; label; kind:'rent'|'utility'|'subscription'|'insurance'|'loan'|'tax_estimated'|'payroll'|'vendor'|'other';
        dayRule: {kind:'dayOfMonth'; day:1-28} | {kind:'taxDates'} | {kind:'biweekly'; weekday:0-6; anchor:'YYYY-MM-DD'};
        amount: {kind:'fixed'; cents} | {kind:'seasonal'; minCents; maxCents; peakMonths:number[]};
        account:'checking'|'credit'; priority:1-5; factId; txnType: Transaction['type'] }[];
      discretionaryPool: { factId; weight>0; perMonth:{min;max}; ticketCents:{min;max}; pricing:'basket'|'fuel'|'fixed_menu';
        taxable:boolean; tip:boolean; account:'checking'|'credit'; weekendBias:0-1; location:'local'|'online'|'travel' }[];
      atm?: { perMonth:{min;max}; amountChoicesCents:number[]; feeCents:number };
      openingBalanceCents:number; creditLimitCents?:number; apr?:number; creditOpeningBalanceCents?:number;
      cardPaymentStrategy?:'full'|'partial'; closingDay?:1-28 }
    ```

- [ ] **Step 1:** 写失败测试：
  ```ts
  it('rejects min > max', () => expect(CountRangeSchema.safeParse({min:9,max:8}).success).toBe(false))
  it('requires at least one account', () => expect(GenerationRequestSchema.safeParse({subjectType:'personal', accounts:{}, months:3}).success).toBe(false))
  it('rejects months=2', () => expect(GenerationRequestSchema.safeParse({subjectType:'personal', accounts:{checking:{min:8,max:13}}, months:2}).success).toBe(false))
  it('rejects business profile under personal', ...)            // success === false
  it('accepts a full personal request', ...)                      // success === true
  it('rejects MerchantFact without sourceUrl', ...)
  ```
- [ ] **Step 2:** `pnpm --filter @bh/core test schemas` → FAIL（模块不存在）。
- [ ] **Step 3:** 实现四个 schema 文件并从 `index.ts` 导出。
- [ ] **Step 4:** 测试 PASS；`pnpm typecheck` 通过。
- [ ] **Step 5:** Commit `feat(core): domain schemas`。

---

### Task 3: 日期、随机数、银行日历

**Files:**
- Create: `packages/core/src/{date,rng,calendar}.ts`
- Test: `packages/core/test/calendar.test.ts`, `packages/core/test/rng.test.ts`

**Interfaces:**
- Produces:
  - `date.ts`：`addDays(d: string, n: number): string`、`weekday(d: string): number`（0=周日）、`monthStart(ym)`/`monthEnd(ym)`、`addMonths(ym: string, n: number): string`、`daysBetween(a, b): number`。
  - `rng.ts`：`createRng(seed: string): Rng`，`Rng = { float(): number; int(min: number, max: number): number /*含两端*/; chance(p: number): boolean; pick<T>(xs: readonly T[]): T; weighted<T>(xs: readonly {item: T; weight: number}[]): T; fork(label: string): Rng }`。用 xmur3 哈希 + mulberry32；`fork` 以 `seed + '/' + label` 新建，保证子流互不干扰。
  - `calendar.ts`：
    - `bankHolidays(year): string[]`——美联储规则：New Year、MLK（1 月第 3 个周一）、Presidents（2 月第 3 个周一）、Memorial（5 月最后周一）、Juneteenth、Independence、Labor（9 月第 1 个周一）、Columbus（10 月第 2 个周一）、Veterans、Thanksgiving（11 月第 4 个周四）、Christmas；**周日顺延周一，周六不补休**。
    - `isBankBusinessDay(d)`、`nextBankBusinessDay(d)`（d 本身是工作日则返回 d）。
    - `estimatedTaxDates(taxYear): string[]`——`[Apr 15, Jun 15, Sep 15, 次年 Jan 15]`，遇周末或节假日顺延（IRS 规则）。
    - `type Cycle = {index: number; start: string; end: string; dueDate?: string}`；`checkingCycles(endMonth, months): Cycle[]`（自然月）；`creditCycles(endMonth, months, closingDay): Cycle[]`（第 i 期结束于对应月的 `closingDay`，开始于上期结束次日；`dueDate = end + 25 天` 再顺延到工作日）。

- [ ] **Step 1:** 写失败测试：
  ```ts
  expect(bankHolidays(2026)).toEqual(['2026-01-01','2026-01-19','2026-02-16','2026-05-25','2026-06-19','2026-09-07','2026-10-12','2026-11-11','2026-11-26','2026-12-25'])  // 7/4 是周六，不补
  expect(bankHolidays(2025)).toContain('2025-07-04')
  expect(nextBankBusinessDay('2026-09-05')).toBe('2026-09-08')       // 周六 → 跳过周日和 Labor Day
  expect(estimatedTaxDates(2026)).toEqual(['2026-04-15','2026-06-15','2026-09-15','2027-01-15'])
  expect(checkingCycles('2026-02', 6).map(c => c.start)).toEqual(['2025-09-01','2025-10-01','2025-11-01','2025-12-01','2026-01-01','2026-02-01'])  // Review Focus 1
  expect(estimatedTaxDates(2025)[3]).toBe('2026-01-15')              // 跨年窗口内的 1 月预缴
  const c = creditCycles('2026-09', 3, 12); expect(c.map(x => [x.start, x.end])).toEqual([['2026-06-13','2026-07-12'],['2026-07-13','2026-08-12'],['2026-08-13','2026-09-12']])
  expect(c[2].dueDate).toBe('2026-10-07')
  ```
  `rng.test.ts`：同 seed 序列相同；`int(3,5)` 一万次只出现 3/4/5 且三者都出现；`fork('a')` 与 `fork('b')` 序列不同。
- [ ] **Step 2:** 运行 → FAIL。
- [ ] **Step 3:** 实现三个文件。
- [ ] **Step 4:** 运行 → PASS。
- [ ] **Step 5:** Commit `feat(core): dates, seeded rng, bank calendar`。

---

### Task 4: 地理数据与 normalize

**Files:**
- Create: `packages/core/data/metros.json`, `packages/core/data/state-tax.json`, `packages/core/src/{geo,normalize}.ts`
- Test: `packages/core/test/normalize.test.ts`

**Interfaces:**
- Consumes: Task 2 schemas、Task 3 `calendar`/`rng`。
- Produces:
  - `lookupLocation(city: string, state: string): LocationInfo`，`LocationInfo = {metroId: string; metroName: string; state: string; combinedSalesTaxRate: number; colTier: 'low'|'mid'|'high'; matchedBy: 'city'|'state'}`；未命中城市时 `metroId = 'state-' + state`。
  - `type MetroRow = {metroId: string; metroName: string; city: string; state: string; population: number; combinedSalesTaxRate: number; colTier: 'low'|'mid'|'high'}`；`pickWeightedMetro(rng: Rng): MetroRow`（按人口加权）。
  - `normalizeRequest(req: GenerationRequest, today: string): NormalizedRequest`，`NormalizedRequest = {request: GenerationRequest & {seed: string; endMonth: string}; location: LocationInfo; closingDay?: number; cycles: {checking?: Cycle[]; credit?: Cycle[]}; periodStart: string; periodEnd: string; holidays: string[]; taxDates: string[]}`。
  - `class InfeasibleRequestError extends Error { code = 'INFEASIBLE_REQUEST' }`。
- 数据：`metros.json` 收录美国人口前 50 城市；税率取自 Tax Foundation 最新一期城市合并销售税率；`state-tax.json` 取同一来源的各州平均合并税率。文件头 `_source` 字段记录来源 URL 与期次。
- 规则：
  - `endMonth` 缺省 = `today` 所在月的上一个月。
  - `seed` 缺省 = `crypto.randomUUID()`。
  - 有 credit 时 `closingDay = createRng(seed).fork('closing').int(1, 28)`。
  - `taxDates` = 窗口覆盖的每个年份 y 的 `estimatedTaxDates(y-1)` 与 `estimatedTaxDates(y)` 取并集，再筛选出落在 `[periodStart, periodEnd]` 内的日期。
  - `holidays` = 窗口覆盖年份的 `bankHolidays` 并集。
  - 基础必需条数：checking 为 2，若同时有 credit 再 +1；credit 为 1。任一账户的 `max` 小于基础必需条数时抛 `InfeasibleRequestError`，message 中给出所需最小值。

- [ ] **Step 1:** 写失败测试：
  ```ts
  expect(lookupLocation('Austin','TX')).toMatchObject({combinedSalesTaxRate:0.0825, matchedBy:'city'})
  expect(lookupLocation('Chicago','IL').combinedSalesTaxRate).toBe(0.1025)
  expect(lookupLocation('Portland','OR').combinedSalesTaxRate).toBe(0)
  expect(lookupLocation('Marfa','TX')).toMatchObject({matchedBy:'state', metroId:'state-TX'})          // Review Focus 4
  expect(normalizeRequest(req({months:3}), '2026-10-29').request.endMonth).toBe('2026-09')
  expect(() => normalizeRequest(req({accounts:{checking:{min:1,max:2}, credit:{min:5,max:9}}}), '2026-10-29')).toThrow(InfeasibleRequestError)  // Review Focus 2
  expect(normalizeRequest(req({seed:'s1', accounts:{credit:{min:5,max:9}}}), '2026-10-29').closingDay).toBe(normalizeRequest(req({seed:'s1', accounts:{credit:{min:5,max:9}}}), '2026-10-29').closingDay)
  ```
- [ ] **Step 2:** FAIL → **Step 3:** 实现 → **Step 4:** PASS。
- [ ] **Step 5:** Commit `feat(core): geo lookup and request normalization`。

---

### Task 5: 排程引擎——发生次数与金额

**Files:**
- Create: `packages/core/src/schedule/{place,amounts}.ts`
- Test: `packages/core/test/schedule-place.test.ts`

**Interfaces:**
- Consumes: `FinancialModel`、`NormalizedRequest`、`Rng`、`calendar`。
- Produces:
  - `type ScheduledTxn = {id: string; account: 'checking'|'credit'; transactionDate: string; postedDate: string; type: Transaction['type']; direction: 'credit'|'debit'; amountCents: number; merchantKey: string; source: 'recurring'|'income'|'discretionary'|'card_payment'|'interest'|'fee'|'atm'; priority: number /* 1 最不可删，5 最可删；discretionary 为 5 */; factId?: string; location?: 'local'|'online'|'travel'}`
  - `recurringOccurrences(model, norm, rng): ScheduledTxn[]`、`incomeOccurrences(model, norm, rng): ScheduledTxn[]`（金额乘 `seasonality[month-1]`）、`postingDate(txnDate: string, type: Transaction['type'], rng): string`
  - `discretionaryAmount(item, taxRate, rng): number`、`fuelAmount(pricePerGallonCents, rng): number`
- 规则：
  - `merchantKey`：discretionary 和带 factId 的 recurring 用 `factId`；没有 factId 的 recurring 用其 `id`；income 用 stream `id`；其余固定为 `card-payment`、`interest`、`fee-overdraft`、`fee-atm`、`atm`。
  - POS/card 交易：入账日 = 交易日后的第 1～2 个银行工作日，周末交易至少顺延到周一；ACH/ZELLE/PAYMENT 遇非工作日顺延。
  - `taxDates` 规则的预缴税只出现在 `norm.taxDates` 中落入周期的日期。
  - recurring 的 `fixed` 金额每次完全相同；`seasonal` 在 `peakMonths` 取区间上 40%，其余月取下 60%。
  - 计税金额 = `round(base × (1 + taxRate))`；小费 = 税前金额 × `rng` 取 15–22%，并有 50% 概率把总额向上取整到整美元。
  - 加油：加仑数 8.000–16.000，保留 3 位小数。
  - `base` 的分位随机，不出现整美元（固定价格项目除外）。

- [ ] **Step 1:** 写失败测试：
  ```ts
  it('posts Saturday POS on Monday or later', () => expect(weekday(postingDate('2026-09-12','POS', rng))).toBeGreaterThanOrEqual(1))
  it('fixed subscription identical every month', ...)          // 3 个月金额 Set.size === 1，日期的"日"相同或为顺延后的工作日
  it('tax payment only on tax dates in window', ...)            // endMonth 2026-09, months 6 → 只有 2026-04-15、2026-06-15、2026-09-15
  it('discretionary amount within ticket*(1+tax)*(1.22)+100', ...)  // 1000 次全部在界内
  it('basket amounts are rarely whole dollars', ...)            // 1000 次中 amountCents % 100 === 0 的比例 < 5%
  it('weekly platform payout appears ~4-5 times per month', ...)
  ```
- [ ] **Step 2:** FAIL → **Step 3:** 实现 → **Step 4:** PASS。
- [ ] **Step 5:** Commit `feat(core): schedule occurrences and amounts`。

---

### Task 6: 排程引擎——填充、信用卡联动、余额、条数保证

**Files:**
- Create: `packages/core/src/schedule/{fill,card,balance,index}.ts`
- Test: `packages/core/test/schedule.property.test.ts`, `packages/core/test/schedule.test.ts`, `packages/core/test/fixtures/models/{freelancer,rideshare,cafe-business}.json`

**Interfaces:**
- Consumes: Task 5 全部导出。
- Produces:
  - `schedule(model: FinancialModel, norm: NormalizedRequest, rng: Rng): ScheduleOutput`，其中 `ScheduleOutput = {txns: ScheduledTxn[]; balances: {checking?: {openingCents: number; byTxnId: Record<string, number>}; credit?: {perCycle: {statementBalanceCents: number; minimumDueCents: number; interestChargedCents: number}[]}}; notes: string[]}`
  - `class ScheduleInfeasibleError extends Error { code = 'SCHEDULE_INFEASIBLE'; target = 'lifeModel' }`
- 算法（按以下顺序，每个账户每个周期执行）：
  1. `target = rng.int(min, max)`。
  2. 放入必需项：recurring、income、信用卡还款、利息。
  3. 必需项多于 `target` 时，按 priority 从 5 往 1 删除（priority ≤ 2 的不删）。仍超过 `max` 就抛 `ScheduleInfeasibleError`。
  4. `fill`：按 weight 加权抽取 discretionary，遵守 `perMonth.max`；池子用尽仍不足就忽略 `perMonth.max` 继续抽；`atm` 按其 `perMonth` 加入，ATM 手续费作为同日的 FEE 交易，计入条数。
  5. `card`：每个 credit 周期结束后，在 `dueDate - rng.int(3,10)` 天由 checking 付款（`full` 付全额；`partial` 付 30–70% 且不少于最低还款额）；credit 侧入账日为付款日 +0～2 天。没有 checking 时只生成 credit 侧（Review Focus 3）。`partial` 时下一期结账日加 `INTEREST`，金额 = 上期未还余额 × apr / 12。最低还款额 = max(3500, 余额的 1% + 利息)，余额不足 3500 时为全额。
  6. `balance`：checking 按 `postedDate`（同日先入后出）滚动余额。出现负数时，先把当期最晚的、日期可以后移的 discretionary 后移（仍须在本周期内）；仍为负则加一笔 `FEE`（`OVERDRAFT FEE`，3500 分）并删除当期一笔 priority 5 的交易，保持条数不变。
  7. 最后重新统计每个周期的条数，补足或删除 discretionary 直到落在 `[min, max]`，最多循环 5 次；仍不满足就抛 `ScheduleInfeasibleError`。
- `cardPaymentStrategy` 缺省为 `full`；`creditOpeningBalanceCents` 缺省为 0。

- [ ] **Step 1:** 写属性测试（fast-check，每条 200 次，对三份 fixture 模型、任意 seed、任意 `min∈[3,30]`、`max∈[min,min+15]`、`months∈{1,3,6}`、任意账户组合）：
  ```ts
  // 每个账户每个周期的条数 ∈ [min, max]
  // checking：opening + Σ(credit) − Σ(debit) === 最后一条的余额，且相邻余额差 === 该笔金额
  // 所有 postedDate ∈ [periodStart, periodEnd + 3 天]，transactionDate ∈ [periodStart, periodEnd]
  // both 时：每笔 checking card_payment 都有一笔金额相同的 credit PAYMENT，日期差 ∈ [0,2]
  // 同一 recurring fixed 项每次金额相同
  // 同 seed 两次 schedule 结果深度相等
  ```
- [ ] **Step 2:** 写示例测试：`{min:8,max:8}` 下每期恰好 8 条（Review Focus 2）；只有 credit 时生成 `PAYMENT` 且没有 checking 侧交易（Review Focus 3）；把 `openingBalanceCents` 设为 0 且收入很低时，出现 `OVERDRAFT FEE` 且条数仍在区间内；priority 1 的项目多于 `max` 时抛 `ScheduleInfeasibleError`。
- [ ] **Step 3:** 运行 → FAIL。
- [ ] **Step 4:** 实现 `fill.ts`、`card.ts`、`balance.ts`、`index.ts`。
- [ ] **Step 5:** 运行 → PASS（属性测试无反例）。
- [ ] **Step 6:** Commit `feat(core): scheduler with count guarantee, card linkage, balances`。

---

### Task 7: 描述渲染、组装与硬规则校验

**Files:**
- Create: `packages/core/src/{descriptors,assemble,validate}.ts`
- Test: `packages/core/test/descriptors.test.ts`, `packages/core/test/validate.test.ts`

**Interfaces:**
- Consumes: `ScheduleOutput`、`NormalizedRequest`、`FinancialModel`、`MerchantFact[]`。
- Produces:
  - `type DescriptorTemplate = {merchantKey: string; template: string; memo: string}`；模板占位符只有 `{STORE}`、`{CITY}`、`{ST}`、`{MMDD}`、`{REF}`、`{LAST4}`。
  - `renderDescriptor(t: DescriptorTemplate, ctx: {txn: ScheduledTxn; fact?: MerchantFact; linkedLast4?: string}, rng: Rng): string`：`{STORE}` 是每个 merchantKey 固定的 4 位数（由 `rng.fork(merchantKey)` 生成）；`{CITY}` 大写并截断到 13 字符；`{REF}` 每笔交易随机 10 位大写字母数字。输出整体大写、压缩连续空格、截断到 60 字符。
  - `assembleStatements(out: ScheduleOutput, templates: DescriptorTemplate[], norm, model, facts, bankName: string, rng): Statement[]`：business 的 checking 账户类型为 `business_checking`；`accountLast4` 由 `rng.fork('acct-'+account)` 生成；`MonthSummary` 按 spec §4.5 填写；`evidence` 取自 fact。
  - `runRules(statements: Statement[], ctx: {norm: NormalizedRequest; model: FinancialModel; facts: MerchantFact[]}): RuleResult[]`，`RuleResult = {ruleId: RuleId; passed: boolean; violations: {transactionIds: string[]; message: string}[]; target: 'schedule'|'lifeModel'}`。
  - `RuleId` 及 target：
    - `R1_count_in_range`（schedule）
    - `R2_balance_continuity`（schedule）
    - `R3_dates_in_period`（schedule）
    - `R4_recurring_consistency`（schedule）
    - `R5_card_payment_match`（schedule；没有 checking 时 `passed: true` 并跳过，Review Focus 3）
    - `R6_amount_within_range`（lifeModel；discretionary 金额 ≤ `ticketCents.max × (1+tax) × 1.22 + 100` 且 ≥ `ticketCents.min`）
    - `R7_geo_consistency`（lifeModel；`local` 交易的商户州必须等于人设州；`travel` 占 debit 不超过 10%）
    - `R8_no_duplicates`（schedule；同日、同描述、同金额视为重复）
    - `R9_evidence_present`（lifeModel；所有 discretionary 和带 factId 的 recurring 必须有 `evidence.sourceUrl`）
    - `R10_no_payroll_income_personal`（lifeModel；personal 的 credit 方向描述不得匹配 `/PAYROLL|DIRECT DEP|SALARY/`）

- [ ] **Step 1:** 写失败测试：
  - `renderDescriptor({template:'TST* JOES PIZZA {STORE} {CITY} {ST}'}, …)` 对同一商户两次调用 `{STORE}` 相同；Austin 输出包含 `AUSTIN TX`；长度 ≤ 60。
  - `validate.test.ts`：为 R1–R10 各构造一份最小的"坏" `Statement[]`，断言只有对应规则 `passed === false`；再用 Task 6 fixture 跑完 schedule → assemble 得到的好账单，断言全部通过。
- [ ] **Step 2:** FAIL → **Step 3:** 实现 → **Step 4:** PASS。
- [ ] **Step 5:** Commit `feat(core): descriptor rendering, assembly, hard rules`。

---

### Task 8: skills 知识文件与加载

**Files:**
- Create: `skills/{bank-descriptors,self-employed-patterns,industry-cost-structures,us-calendar-seasonality,consumer-habits}.md`, `packages/core/src/skills.ts`
- Test: `packages/core/test/skills.test.ts`

**Interfaces:**
- Produces: `type SkillName = 'bank-descriptors'|'self-employed-patterns'|'industry-cost-structures'|'us-calendar-seasonality'|'consumer-habits'`；`loadSkills(dir: string): SkillStore`，`SkillStore = {get(names: SkillName[]): string}`（按传入顺序拼接，每段以 `## skill: <name>` 开头）。
- 节点与 skill 的对应关系（`SKILLS_BY_NODE` 常量）：
  - planResearch → self-employed-patterns、industry-cost-structures
  - lifeModel → self-employed-patterns、industry-cost-structures、us-calendar-seasonality、consumer-habits
  - descriptors → bank-descriptors
  - audit → 全部 5 个
- 内容要求：
  - 每个文件 150–400 行，写成可以直接执行的规则和例子，不写空泛描述。
  - `bank-descriptors.md` 至少覆盖：POS/CHECKCARD、ACH 入账与扣款、Square（`SQ *`）、Toast（`TST*`）、Amazon、Zelle/Venmo/PayPal、IRS USATAXPYMT、ATM、利息与费用、信用卡还款两侧的写法。
  - `self-employed-patterns.md` 至少覆盖 12 个行当。
  - `industry-cost-structures.md` 至少覆盖 10 个行业。
  - 每个文件末尾列出参考来源 URL。
  - **不得写入任何真实银行的版式或品牌描述。**

- [ ] **Step 1:** 写失败测试：`get(['bank-descriptors'])` 包含 `## skill: bank-descriptors` 和 `TST*`；`SKILLS_BY_NODE.audit` 的长度为 5；读取不存在的 skill 名时抛错。
- [ ] **Step 2:** FAIL → **Step 3:** 用 Tavily 或网页查资料，写好 5 个 md 文件，并实现 `skills.ts` → **Step 4:** PASS。
- [ ] **Step 5:** Commit `feat: realism skills knowledge base`。

---

### Task 9: 模型客户端、预算与宽松解析

**Files:**
- Create: `packages/core/src/{budget,llm}.ts`, `packages/core/test/helpers/fakeLlm.ts`
- Test: `packages/core/test/llm.test.ts`

**Interfaces:**
- Produces:
  - `class Budget { constructor(limits = {llmCalls: 40, tavilyQueries: 25, ms: 600_000}, now = Date.now); chargeLlm(tokens?: number): void; chargeTavily(): void; usage(): {llmCalls: number; tokens: number; tavilyQueries: number} }`；超限时抛 `BudgetExceededError {code: 'BUDGET_EXCEEDED'}`。
  - `type LlmNode = 'persona'|'planResearch'|'extractFacts'|'lifeModel'|'descriptors'|'audit'`；`type LlmMessage = {role: 'system'|'user'; content: string}`；`interface StructuredLlm { invoke<T>(node: LlmNode, schema: z.ZodType<T>, messages: LlmMessage[]): Promise<T> }`。
  - `type LlmEnv = {baseUrl: string; apiKey: string; model: string; structuredMode: 'json_schema'|'function_calling'|'json_mode'}`。
  - `createOpenAiStructuredLlm(env: LlmEnv, budget: Budget): StructuredLlm`：
    - 按 `LLM_STRUCTURED_MODE` 选择 `withStructuredOutput` 的 method；`json_mode` 时用 `parseJsonLoose` 后再 zod 解析。
    - 遇到 429、5xx 或超时（单次 120s）时退避 1s、2s、4s，最多重试 3 次。
    - zod 解析失败时，把错误信息作为一条 user 消息追加后重试 1 次，仍失败就抛 `LlmOutputError`。
    - 每次真实调用都执行 `budget.chargeLlm`。
  - `parseJsonLoose(text: string): unknown`：去掉 ```` ```json ```` 围栏，截取第一个 `{` 或 `[` 到与之配对的结尾。
  - `test/helpers/fakeLlm.ts`：`createFakeLlm(responses: Partial<Record<LlmNode, unknown[]>>): StructuredLlm & {calls: {node: LlmNode; messages: unknown}[]}`，按节点依次返回预设结果，并经过 schema 解析。

- [ ] **Step 1:** 写失败测试：
  ```ts
  expect(parseJsonLoose('Here you go:\n```json\n{"ok":true}\n```\nThanks')).toEqual({ok:true})   // Review Focus 5
  expect(parseJsonLoose('{"a":{"b":[1,2]}} trailing')).toEqual({a:{b:[1,2]}})
  // Budget：第 41 次 chargeLlm 抛 BudgetExceededError；用注入的 now 让时间超过 600000ms 后也抛错
  // 重试：用 msw 或注入的 fetch 返回两次 429、第三次 200 → 成功，且共发出 3 次请求
  // 修复：第一次返回不合 schema，第二次合规 → 成功，第二次请求的 messages 中包含 zod 错误文本
  ```
- [ ] **Step 2:** FAIL → **Step 3:** 实现 → **Step 4:** PASS。
- [ ] **Step 5:** Commit `feat(core): structured llm client with budget, retry, repair`。

---

### Task 10: 调研（Tavily + 缓存 + 事实提取）

**Files:**
- Create: `packages/core/src/research.ts`, `packages/core/src/prompts/extractFacts.ts`
- Test: `packages/core/test/research.test.ts`, `packages/core/test/fixtures/tavily/*.json`

**Interfaces:**
- Consumes: `StructuredLlm`、`Budget`、`ResearchQuery`、`MerchantFact`。
- Produces:
  - `interface SearchClient { search(query: string): Promise<{title: string; url: string; content: string}[]> }`；`createTavilyClient(apiKey: string): SearchClient`（`@langchain/tavily`，`maxResults: 5`，`searchDepth: 'advanced'`）。
  - `interface ResearchCache { get(key: string, now: number): {facts: MerchantFact[]; fetchedAt: number; stale: boolean} | null; set(key: string, facts: MerchantFact[], now: number): void }`（超过 30 天时 `stale = true`）；core 提供 `createMemoryCache()`，server 提供 SQLite 实现。
  - `researchOne(q: ResearchQuery, loc: LocationInfo, deps: {search; llm; cache; budget; now}): Promise<{facts: MerchantFact[]; warnings: string[]}>`
- 流程：
  1. 缓存未过期 → 直接返回。
  2. 否则调用 search，失败时用过期缓存并加警告。
  3. 有结果 → 交给 `llm.invoke('extractFacts', z.array(MerchantFactSchema), …)` 提取。
  4. 丢弃没有 `sourceUrl` 的条目，以及 `sourceUrl` 不在本次搜索结果 URL 中的条目（防止模型编造来源）。
  5. 提取结果为空且 `scope === 'metro'` → 把城市名换成州名再查一次。
  6. 仍为空 → 返回空数组，并加警告 `category <x> dropped: no sources`。
  7. 写入缓存，键为 `${loc.metroId}:${q.category}`。

- [ ] **Step 1:** 写失败测试（假 search + fakeLlm + memory cache）：
  - 缓存命中时不调用 search；
  - 过期缓存加上 search 抛错 → 返回过期数据并带警告；
  - 来源不在搜索结果中的条目被丢弃；
  - metro 查不到 → 第二次查询用州名；
  - 两次都为空 → 警告中包含 `dropped`；
  - 每次 search 都执行了 `budget.chargeTavily`。
- [ ] **Step 2:** FAIL → **Step 3:** 实现 → **Step 4:** PASS。
- [ ] **Step 5:** Commit `feat(core): research with tavily, cache, fact extraction`。

---

### Task 11: 模型节点与提示词

**Files:**
- Create: `packages/core/src/prompts/{planResearch,lifeModel,descriptors,audit,persona}.ts`, `packages/core/src/graph/nodes/{planResearch,lifeModel,descriptors,audit}.ts`, `packages/core/src/persona.ts`
- Test: `packages/core/test/nodes.test.ts`

**Interfaces:**
- Consumes: `StructuredLlm`、`SkillStore`、`SKILLS_BY_NODE`、schemas、`pickWeightedMetro`。
- Produces（每个节点都是纯函数 `(state, deps) => Partial<GraphState>`，GraphState 定义见 Task 12）：
  - `planResearchNode`：输出 `ResearchQuery[]`，数量在 6–14 之间（超出截断）；query 必须包含城市名或州名。
  - `lifeModelNode`：输出 `FinancialModel`。用代码做后置检查：每个 `factId` 都存在于 facts 中；personal 不得有 `kind: 'payroll'` 的 recurring，`channel` 也不得表示工资；只有 checking 的请求里不得出现 `account: 'credit'` 的项目，反之亦然。检查失败就把错误追加到消息中，重新调用一次（这次重试计入预算，不计入打回轮次）。
  - `descriptorsNode`：对 `schedule` 结果中每个不同的 `merchantKey` 生成一个 `DescriptorTemplate`，一次调用批量完成。
  - `auditNode`：输出 `AuditFinding[]`，`AuditFinding = {severity: 'low'|'medium'|'high'; transactionIds: string[]; problem: string; suggestedTarget: 'lifeModel'|'schedule'|'descriptors'}`。输入是账单的紧凑表格（每行：日期、描述、金额、余额），不传 evidence 全文。
  - `randomPersona(subjectType, partial: Partial<Profile> | undefined, deps: {llm; rng}): Promise<PersonalProfile|BusinessProfile>`：先用代码 `pickWeightedMetro` 选城市（partial 里给了地址就用给定的），再由模型补全其余字段，已给出的字段原样保留。
- 提示词要求：
  - 系统提示包含：对应的 skills 文本；人设与 location（税率、生活成本档位）；"只能使用提供的 facts，不得编造商户"；"个人一律是个体户，没有工资"；输出语言为英文。
  - 每个 prompt 文件导出 `build<Node>Messages(input): LlmMessage[]`。

- [ ] **Step 1:** 写失败测试（fakeLlm）：
  - planResearch 返回 20 条时截断到 14 条；
  - lifeModel 第一次返回引用了不存在 factId 的模型 → 第二次调用的消息中包含该 factId → 采用第二次的结果；
  - personal 的 lifeModel 返回 payroll → 被拒绝；
  - descriptors 每个 merchantKey 恰好调用 1 次（批量）；
  - `randomPersona(partial={name:'Dana Ruiz'})` 返回的结果保留该 name；
  - 所有节点的 system 消息都包含 `SKILLS_BY_NODE` 中对应 skill 的标题。
- [ ] **Step 2:** FAIL → **Step 3:** 实现 → **Step 4:** PASS。
- [ ] **Step 5:** Commit `feat(core): llm nodes and prompts`。

---

### Task 12: LangGraph 组装、路由与端到端（假依赖）

**Files:**
- Create: `packages/core/src/graph/{state,deps,build,run}.ts`, `packages/core/src/graph/nodes/{normalize,research,schedule,validate,finalize}.ts`
- Test: `packages/core/test/graph.e2e.test.ts`, `packages/core/test/fixtures/e2e/*.json`

**Interfaces:**
- Consumes: 前面所有任务。
- Produces:
  - `GraphDeps = {llm: StructuredLlm; search: SearchClient; cache: ResearchCache; skills: SkillStore; budget: Budget; bankName: string; today: string; now: () => number}`。
  - `GraphState`（`Annotation.Root`）字段：`request`、`norm`、`queries`、`facts`（concat reducer）、`model`、`scheduleOut`、`templates`、`statements`、`ruleResults`、`findings`、`rounds`（number）、`retryTarget`、`warnings`（concat reducer）。
  - `type ProgressEvent = {type: 'node_start'|'node_end'; node: string} | {type: 'research'; query: string; sources: string[]} | {type: 'rules'; results: RuleResult[]} | {type: 'retry'; target: string; reason: string} | {type: 'warning'; message: string}`。
  - `buildGraph(deps: GraphDeps, checkpointer?: BaseCheckpointSaver)`。
  - `runGeneration(request: GenerationRequest, deps: GraphDeps, opts: {threadId: string; checkpointer?: BaseCheckpointSaver; onEvent?: (e: ProgressEvent) => void; resume?: boolean}): Promise<GenerationResult>`。
- 边与路由：
  - `normalize → planResearch → (Send 每个 query 到 research) → lifeModel → schedule → descriptors → validate`。
  - validate：有失败规则且 `rounds < 2` 时，`rounds+1` 并打回失败规则的 target（只要有一条 target 是 lifeModel，就去 lifeModel）；否则进入 audit。
  - audit：有 high 级别问题且 `rounds < 2` 时，`rounds+1` 并按 `suggestedTarget` 打回（优先级 lifeModel > schedule > descriptors）；否则进入 finalize。
  - 打回到 schedule 时，rng 使用 `fork('schedule-r'+rounds)`，保证新一轮的结果不同。
  - `ScheduleInfeasibleError` 视同 target 为 lifeModel 的失败。
  - 超过轮次后 finalize：状态为 `completed_with_warnings`，失败规则和 high 问题写入 warnings。
  - `BudgetExceededError` 由 `runGeneration` 抛出给调用方。
  - `GenerationResult.usage` 取自 `budget.usage()`。

- [ ] **Step 1:** 写失败的端到端测试（fakeLlm + 假 search + memory cache + `MemorySaver`）：
  1. Austin 个体户设计师，3 个月，both（checking 8–13、credit 20–35）→ 结果通过 `GenerationResultSchema` 校验；R1–R10 全部通过；事件序列以 `node_start normalize` 开头、`node_end finalize` 结尾。
  2. 第一次 lifeModel 的 fixture 让 R6 失败 → 出现 `retry` 事件，target 为 lifeModel，最终通过。
  3. audit 连续三次返回 high → 最多 2 轮打回，最终 `completed_with_warnings`。
  4. 预算 `llmCalls: 3` → 抛 `BudgetExceededError`。
  5. 同 seed 同 fixture 跑两次，`statements` 深度相等。
- [ ] **Step 2:** FAIL → **Step 3:** 实现 → **Step 4:** PASS。
- [ ] **Step 5:** Commit `feat(core): langgraph pipeline with routing and e2e tests`。

---

### Task 13: Server——存储、任务队列、生成接口与 SSE

**Files:**
- Create: `apps/server/src/{env,db,events,jobs,auth,app,index}.ts`
- Test: `apps/server/test/generations.test.ts`

**Interfaces:**
- Consumes: `runGeneration`、`GraphDeps`、`ResearchCache`、`createOpenAiStructuredLlm`、`createTavilyClient`、`loadSkills`、`randomPersona`。
- Produces:
  - `loadEnv(): Env`（用 zod 校验，缺少必填项时启动失败并指出变量名，但不打印变量值）。
  - `openDb(path): Db`，包含表 `generations(id TEXT PK, subject_type, status, request_json, result_json, error_json, created_at, updated_at)`、`events(generation_id, seq INTEGER, event_json, PRIMARY KEY(generation_id, seq))`、`research_cache(key TEXT PK, facts_json, fetched_at INTEGER)`；`createSqliteCache(db): ResearchCache`。
  - `createJobRunner({db, makeDeps, concurrency: 2, bus}): JobRunner`，`JobRunner = {enqueue(id: string): void; resume(id: string): void; markInterruptedOnStartup(): void}`。如果请求里没有 profile，先调用 `randomPersona`，把补全后的请求写回 `request_json`。checkpointer 使用同一个库文件的 `SqliteSaver`，`threadId` 等于任务 id。
  - `EventBus`：写入 events 表，同时推送给订阅者。
  - `requireApiKey`：loopback 地址（`127.0.0.1`、`::1`）的请求直接放行，用于本机 Web 界面；其他来源必须带正确的 `X-API-Key`，否则返回 401 `UNAUTHORIZED`。key 比较用 `crypto.timingSafeEqual`。
  - `createApp(deps: {db; runner; bus}): Hono`，路由：
    - `POST /api/generations`：用 zod 校验 → `normalizeRequest` 预检（`InfeasibleRequestError` 返回 400 `INFEASIBLE_REQUEST`）→ 写库 → enqueue → 返回 `202 {id}`。
    - `GET /api/generations/:id`
    - `GET /api/generations/:id/events`：SSE，先补发 `Last-Event-ID` 之后的历史事件，再推送实时事件；任务结束时发送 `event: done`。
    - `POST /api/generations/:id/resume`：只有 `interrupted` 状态允许，否则返回 409。
  - 状态值与 spec §5 一致；`BudgetExceededError` 对应 `failed`，`error_json = {code: 'BUDGET_EXCEEDED', message}`。此时从 checkpoint 读取最后的 state，如果已有 `statements` 就写入 `result_json`（保存已有结果）。
  - `makeDeps(env)`：`LLM_FAKE=1` 时注入 Task 12 的 fixture 依赖（fakeLlm、假 search），供界面开发和 Task 16 手动验证使用；否则注入真实客户端。

- [ ] **Step 1:** 写失败测试（`app.request`，用 `makeDeps` 注入 Task 12 的 fake 依赖，数据库用 `:memory:`）：
  - 非 loopback 地址且不带 key → 401；
  - 非法 body → 400 `VALIDATION_ERROR`；
  - `max` 太小 → 400 `INFEASIBLE_REQUEST`；
  - 合法请求 → 202，轮询状态直到 `completed`，结果符合 schema；
  - SSE 依次收到 `node_start` 和 `done`，带 `Last-Event-ID: 3` 重连只收到 seq > 3 的事件；
  - 第 3 个任务在前两个完成前保持 `queued`；
  - 把一条记录手工设为 `running` 后调用 `markInterruptedOnStartup` → 状态变为 `interrupted`，resume 后最终 `completed`；
  - 错误响应体中不包含 `LLM_API_KEY` 的值。
- [ ] **Step 2:** FAIL → **Step 3:** 实现 → **Step 4:** PASS。
- [ ] **Step 5:** Commit `feat(server): jobs, generation api, sse, auth`。

---

### Task 14: Server——随机人设、批量、导出、列表、删除

**Files:**
- Modify: `apps/server/src/app.ts`
- Test: `apps/server/test/misc-routes.test.ts`

**Interfaces:**
- Produces 以下路由：
  - `POST /api/personas/random {subjectType, partial?}` → `200 Profile`
  - `POST /api/batches {count: 1–10, subjectType, accounts, months, endMonth?}` → `202 {ids: string[]}`（每个任务都不带 profile，由任务自己随机）
  - `GET /api/generations/:id/export.json`：只有 completed 或 completed_with_warnings 的任务可导出，响应头 `Content-Disposition: attachment; filename="statement-<id>.json"`；其他状态返回 409。
  - `GET /api/generations?subjectType=&status=&limit=50`：按时间倒序，每项包含 `{id, subjectType, status, displayName, createdAt}`。
  - `DELETE /api/generations/:id`：返回 204，同时删除事件和 checkpoint；任务不存在也返回 204；正在运行的任务返回 409。

- [ ] **Step 1:** 写失败测试：`count: 11` 返回 400；`count: 3` 返回 3 个 id，且它们最终都带上了随机 profile；导出未完成的任务返回 409；列表筛选正确；删除后再 GET 返回 404。
- [ ] **Step 2:** FAIL → **Step 3:** 实现 → **Step 4:** PASS。
- [ ] **Step 5:** Commit `feat(server): personas, batches, export, list, delete`。

---

### Task 15: Web——新建页（三步向导）

**Files:**
- Create: `apps/web/src/{api.ts,App.tsx}`, `apps/web/src/pages/New.tsx`, `apps/web/src/components/{ProfileForm,AccountRangeInput}.tsx`, `apps/web/vite.config.ts`（开发时把 `/api` 代理到 `http://127.0.0.1:8797`）
- Test: `apps/web/src/pages/New.test.tsx`

**Interfaces:**
- Consumes: `@bh/core` 导出的 schemas，前端复用同一套校验。
- Produces:
  - `api.ts`：`createGeneration(req): Promise<{id}>`、`randomPersona(subjectType, partial?)`、`getGeneration(id)`、`listGenerations(q)`、`deleteGeneration(id)`、`exportUrl(id)`、`subscribeEvents(id, onEvent): () => void`（基于 EventSource）。
  - 路由：`/new`、`/g/:id`（根据状态显示进度或结果）、`/history`；`/` 重定向到 `/new`。
- 页面行为：
  - 第 1 步：选个人或企业。
  - 第 2 步：每个字段旁有 🎲 按钮，调用 `randomPersona` 时把其他已填字段作为 `partial` 传入，只替换这一个字段；顶部"全部随机"传空的 partial。
  - 第 3 步：两个复选框（支票账户、信用卡），勾选后显示该账户的条数区间（滑块加数字输入）；月数为 1/3/6 单选；结束月份默认上个月。
  - 表单校验失败时，在对应字段下方显示中文错误（例如"最小值不能大于最大值"）；400 `INFEASIBLE_REQUEST` 的 message 显示在表单顶部；提交成功后跳转到 `/g/:id`。

- [ ] **Step 1:** 写失败测试（testing-library，mock `api.ts`）：min 填 9、max 填 8 时显示"最小值不能大于最大值"且提交按钮禁用；两个账户都不勾选时禁用提交；点击姓名旁的 🎲 后只有姓名被替换。
- [ ] **Step 2:** FAIL → **Step 3:** 实现 → **Step 4:** PASS。
- [ ] **Step 5:** Commit `feat(web): new generation wizard`。

---

### Task 16: Web——进度、结果、历史

**Files:**
- Create: `apps/web/src/pages/{Progress,Result,History}.tsx`, `apps/web/src/components/{Timeline,StatementView,TransactionRow,AuditPanel}.tsx`
- Test: `apps/web/src/pages/Result.test.tsx`

**Interfaces:**
- Consumes: Task 15 的 `api.ts`、core 的 `GenerationResult` 类型。
- 页面行为：
  - **进度**：时间线按 9 个节点排列，进行中的节点高亮；research 事件显示查询词和来源链接（新窗口打开）；retry 事件显示打回目标和原因；收到 `done` 后切换到结果页。
  - **结果**：
    - 每个账户一个标签页。
    - 账单抬头显示 `bankName`、账户类型、`****last4`、账单周期。
    - 每月一张汇总卡片：期初、收入、支出、期末；信用卡另显示应还、最低还款额、到期日。
    - 交易表格列：入账日、描述（等宽字体）、金额（收入绿色、支出默认色）、余额。
    - 点击一行展开 memo、商户、evidence 链接。
    - 侧栏显示 R1–R10 的结果和审计问题，按严重程度排序。
    - 按钮：下载 JSON、同 seed 复现（用相同的 request 和 seed 新建任务）、换 seed 重新生成（去掉 seed）。
  - **历史**：表格加类型和状态筛选，提供打开和删除（删除前确认）。
  - 金额显示用 `Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD'})`，日期显示为 `MM/DD`。

- [ ] **Step 1:** 写失败测试（用 Task 12 端到端测试产出的 fixture）：渲染两个标签页；第一行显示的金额格式为 `$1,234.56`；点击行后出现 evidence 链接；"换 seed 重新生成"提交的请求里没有 `seed`。
- [ ] **Step 2:** FAIL → **Step 3:** 实现 → **Step 4:** PASS。
- [ ] **Step 5:** 运行 `pnpm dev`，在浏览器打开 `http://localhost:5197`，用 fake 模式（`LLM_FAKE=1` 时 server 注入 Task 12 的 fixture 依赖）走完新建、进度、结果、历史全流程，并在 375px 宽度下检查没有横向滚动。
- [ ] **Step 6:** Commit `feat(web): progress, result, history pages`。

---

### Task 17: 真实环境冒烟

**Files:**
- Create: `scripts/smoke.ts`, `docs/smoke-report.md`

**Interfaces:**
- Consumes: 运行中的 server（真实 `.env`）。

- [ ] **Step 1:** 写 `scripts/smoke.ts`：仅在 `LIVE_SMOKE=1` 时运行。依次提交两个任务：
  - 个人：随机人设，1 个月，both（checking 8–13、credit 15–25）。
  - 企业：Austin 咖啡馆，LLC，12 人，1 个月，checking 20–30。

  轮询直到任务结束，打印状态、规则结果、usage，并把结果 JSON 存到 `data/smoke/`。
- [ ] **Step 2:** 运行 `LIVE_SMOKE=1 pnpm smoke`。Expected：两个任务都是 `completed` 或 `completed_with_warnings`，R1–R10 全部通过，单个任务 `llmCalls ≤ 40`、`tavilyQueries ≤ 25`。
- [ ] **Step 3:** 人工抽查每份账单的 10 条交易：商户是否真实存在于该城市（点开 evidence 核对）、描述格式是否可信、金额是否合理。结论写进 `docs/smoke-report.md`。发现的问题记为对 skills 或提示词的修改项，不在本任务内修复。
- [ ] **Step 4:** Commit `chore: live smoke run and report`。
