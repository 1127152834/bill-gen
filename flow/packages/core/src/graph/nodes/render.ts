/**
 * The render node: the LangGraph side of the bridge to billgen's MCP server.
 *
 *   statements ──draft_from_ledger──▶ 模板数据草稿
 *              ──(model fills)─────▶ 数据
 *              ──validate─────────▶ 错误 → 回灌重试（最多 MAX_FILL_ATTEMPTS 轮）
 *              ──render───────────▶ PDF 路径 + 指纹
 *
 * The model never touches pixels: it gets a contract (skeleton, field notes, rules, limits) and a
 * draft produced by the deterministic ledger adapter, edits the JSON, and the server answers with
 * structured errors. Rendering failures never fail the job — they become warnings on the result.
 */
import { z } from 'zod';
import type { GenerationResult, MonthFile, RenderArtifact, Statement } from '../../schemas/index.js';
import type { Emit, GraphDeps } from '../deps.js';
import type { GraphStateUpdate, GraphStateValue } from '../state.js';
import { sharedMcpPool, type McpStdioClient } from '../../mcp/client.js';

/** Rounds of "draft → fill → validate" per template before giving up on that template. */
const MAX_FILL_ATTEMPTS = 3;
/** 同时出片的模板数。 */
const RENDER_CONCURRENCY = 2;

/** `BILLGEN_TEMPLATES=auto` asks the recommender instead of a hand-written list. */
const AUTO_TEMPLATES = 'auto';
/** Templates kept per statement when `auto` is on. */
const AUTO_TEMPLATE_LIMIT = 2;

/** Ask billgen which layouts fit this statement (deterministic scoring, no model call). */
async function recommendFor(
  client: McpStdioClient,
  result: GenerationResult,
  statement: Statement,
  limit: number,
): Promise<{ candidates: z.infer<typeof RecommendSchema>['candidates']; errors: string[] }> {
  try {
    const answer = await client.callTool<z.infer<typeof RecommendSchema>>('recommend_templates', {
      result,
      account: statement.accountType,
      cycle: -1,
      limit,
    });
    return { candidates: answer.ok ? answer.candidates : [], errors: answer.ok ? [] : (answer.errors ?? []) };
  } catch (err) {
    return { candidates: [], errors: [`recommend failed (${err instanceof Error ? err.message : String(err)})`] };
  }
}

/** Billgen's `template_contract` payload (only the parts the node uses). */
const ContractSchema = z.object({
  template: z.object({ id: z.string(), display_name: z.string(), kind: z.string() }),
  data: z.record(z.string(), z.unknown()),
  types: z.record(z.string(), z.string()),
  notes: z.record(z.string(), z.string()),
  rules: z.array(z.string()),
  limits: z.record(z.string(), z.unknown()),
});
type Contract = z.infer<typeof ContractSchema>;

/** `month_write` 的应答：这一份的结论 + 并进去之后的整批结论。 */
const MonthWriteSchema = z.object({
  ok: z.boolean().optional(),
  errors: z.array(z.string()).optional(),
  month: z.string().optional(),
  path: z.string().optional(),
  rows: z.number().optional(),
  bytes: z.number().optional(),
  sha256: z.string().optional(),
  structure: z.array(z.record(z.string(), z.unknown())).optional(),
  content: z.array(z.record(z.string(), z.unknown())).optional(),
  batch: z.object({
    ok: z.boolean().optional(),
    months: z.array(z.string()).default([]),
    failed: z.array(z.string()).default([]),
    problems: z.array(z.record(z.string(), z.unknown())).default([]),
  }).optional(),
});
type MonthWriteAnswer = z.infer<typeof MonthWriteSchema>;

const ValidateSchema = z.object({
  ok: z.boolean(),
  errors: z.array(z.string()),
  warnings: z.array(z.string()),
  pages: z.number(),
  bytes: z.number(),
});

const RenderSchema = z.object({
  ok: z.boolean(),
  path: z.string().optional(),
  pages: z.number().optional(),
  bytes: z.number().optional(),
  sha256: z.string().optional(),
  brand_leftovers: z.record(z.string(), z.number()).optional(),
  errors: z.array(z.string()).optional(),
});

const RecommendSchema = z.object({
  ok: z.boolean(),
  candidates: z
    .array(
      z.object({
        template: z.string(),
        display_name: z.string(),
        score: z.number(),
        reasons: z.array(z.string()),
      }),
    )
    .default([]),
  errors: z.array(z.string()).optional(),
});

const DraftSchema = z.object({
  ok: z.boolean(),
  data: z.record(z.string(), z.unknown()).optional(),
  ledger: z.record(z.string(), z.unknown()).optional(),
  errors: z.array(z.string()).optional(),
});

/** What the model answers for the `render` node: the complete template data object. */
export const FilledDataSchema = z.record(z.string(), z.unknown());

export type { RenderArtifact };

/** Only deposit-account layouts exist server-side; credit statements cannot be rendered here. */
const RENDERABLE_ACCOUNTS = new Set(['checking', 'business_checking']);

function ledgerBrief(statement: Statement): string {
  const months = statement.months
    .map(
      (m) =>
        `${m.cycleStart}..${m.cycleEnd}: open ${(m.openingBalanceCents / 100).toFixed(2)} → close ${(m.closingBalanceCents / 100).toFixed(2)}, credits ${(m.totalCreditsCents / 100).toFixed(2)}, debits ${(m.totalDebitsCents / 100).toFixed(2)}, ${m.transactionCount} rows`,
    )
    .join('\n');
  const rows = statement.transactions
    .slice(0, 40)
    .map(
      (t) =>
        `${t.transactionDate} ${t.direction === 'credit' ? '+' : '-'}${(t.amountCents / 100).toFixed(2)} ${t.description}`,
    )
    .join('\n');
  return `bank: ${statement.bankName}\naccount: ${statement.accountType} ****${statement.accountLast4}\nperiod: ${statement.periodStart}..${statement.periodEnd}\n${months}\nrows:\n${rows}`;
}

function fillMessages(
  contract: Contract,
  draft: Record<string, unknown>,
  brief: string,
  bankSkill = '',
): {
  role: 'system' | 'user';
  content: string;
}[] {
  const notes = Object.entries(contract.notes)
    .map(([path, note]) => `- ${path}: ${note}`)
    .join('\n');
  const types = Object.entries(contract.types)
    .map(([path, type]) => `- ${path}: ${type}`)
    .join('\n');
  const rules = contract.rules.map((rule) => `- ${rule}`).join('\n');
  return [
    {
      role: 'system',
      content: [
        `You fill the data object for a synthetic bank statement layout ("${contract.template.display_name}").`,
        'Return the COMPLETE data object, not a diff. Keep every key you did not need to change.',
        '',
        'Hard rules:',
        rules,
        '- 人名、地址、公司、账号、路由号必须是具体值；SAMPLE / EXAMPLE / DEMO / TEST / DUMMY / 占位地址一律不行。',
        '- Only reuse merchants from the ledger; never invent a merchant, a price or a bank.',
        '',
        'Field notes (path: meaning):',
        notes,
        '',
        'Field types (path: type):',
        types,
        `Description limit: ${String(contract.limits.desc_chars ?? 38)} characters; rows per page: ${String(contract.limits.rows_per_page ?? 24)}.`,
        // 这家银行的表单填写规范（字段表 + 行文 + 反例）。只给这一次要出片的银行，
        // 别家的行文不要混进来，否则模型会把两家风格搅在一起。
        bankSkill ? `\n${bankSkill}` : '',
      ]
        .filter(Boolean)
        .join('\n'),
    },
    {
      role: 'user',
      content: [
        'Here is the statement ledger the data must describe:',
        brief,
        '',
        'Here is the draft the deterministic adapter produced (a valid starting point; amounts are already closed):',
        JSON.stringify(draft, null, 2),
        '',
        'Fill it so a banker or landlord reading the PDF sees an ordinary statement:',
        '- Dates and period labels in exactly this layout\'s format (see the draft); copy them instead of inventing a new style.',
        '- Every identity field (account holder, address lines, company, EIN) comes from the ledger and stays concrete:',
        '  a real-looking personal name, street number + street name, city + two-letter state + ZIP. Never write',
        '  SAMPLE, EXAMPLE, DEMO, TEST, DUMMY, lorem ipsum, "John Doe", "123 Main St", "00000", or a masked account number',
        '  where the layout prints a full one.',
        '- Transaction rows must keep the wording of THIS bank, not one generic house style. The draft already has the',
        '  right shape; do not rewrite it into a universal format: Chase prefixes the type and puts the date + card last4 in',
        '  the text, BoA prints the raw NACHA fields (DES:/INDN:/CO ID:/PPD|WEB), U.S. Bank keeps a Ref column and two',
        '  continuation lines for ACH, Wells Fargo prefixes "Card Purchase 12/11 … CITY ST", Amex prints',
        '  "DEBIT CARD PURCHASE …", Capital One fills the CATEGORY column with a merchant class (Groceries, Dining,',
        '  Bills & Utilities, Income…, never Credit/Debit), SoFi uses specific TYPE words (Deposit, Direct Payment,',
        '  Card Purchase, Interest Earned) with a Transaction ID line, Mercury shows ACH In/Out, Card, Wire, Fee.',
        '  Fill every column the layout has: an empty CATEGORY or Ref column is a tell. Keep the merchant names the ledger',
        '  already has; do not invent new ones and do not pad with placeholder cities.',
        '- Product names, promo copy and the service-information block belong to this bank; reuse the draft\'s wording',
        '  unless the ledger makes it wrong.',
        '- Metadata title is the real title this bank prints on its statements (e.g. "Chase Online Statement"),',
        '  producer/creator are the real statement vendors, never "SAMPLE".',
        '- No leftover placeholder text anywhere: no "Sample checking", no "Your Name", no empty memo fields that the',
        '  layout shows as a dash.',
        'Do not change the arithmetic, the row count, or the dates themselves.',
      ].join('\n'),
    },
  ];
}

/**
 * 一个月一份账单。
 *
 * 美国的银行只给当期出一张对账单（这 30 份真实样本也都是「一人一月一个 PDF」），所以这里按
 * 账期把结果拆开：3 个月的请求 = 3 个文件，而不是把它们排进同一个 PDF。每份只带自己那个月
 * 的期初/期末与流水，模型补全也是按月做，某个月失败不影响其它月份。
 */
async function renderMonthly(
  deps: GraphDeps,
  emit: Emit,
  client: McpStdioClient,
  templateId: string,
  result: GenerationResult,
  statement: Statement,
  recommend?: { reasons: string[]; score: number },
  bankName = '',
): Promise<{ artifacts: RenderArtifact[]; monthFiles: MonthFile[]; warnings: string[] }> {
  const artifacts: RenderArtifact[] = [];
  const monthFiles: MonthFile[] = [];
  const warnings: string[] = [];
  const cycles = statement.months.length > 0 ? statement.months.map((m) => m.cycleStart.slice(0, 7)) : [''];
  for (const [index, period] of cycles.entries()) {
    const one = await renderOne(
      deps, emit, client, templateId, result, statement, recommend, bankName, index, period,
    );
    if (one.artifact) artifacts.push(one.artifact);
    hit(one.monthFiles, monthFiles);
    if (one.warning) warnings.push(one.warning);
  }
  return { artifacts, monthFiles, warnings };
}

/** 月份文件是按月出的，这里只是把数组摊平（保持顺序：模板 → 月份升序）。 */
function hit(from: MonthFile[] | undefined, into: MonthFile[]): void {
  for (const file of from ?? []) into.push(file);
}

/** One template × one statement: draft → (model fill → validate)* → render. */
async function renderOne(
  deps: GraphDeps,
  emit: Emit,
  client: McpStdioClient,
  templateId: string,
  result: GenerationResult,
  statement: Statement,
  recommend?: { reasons: string[]; score: number },
  bankName = '',
  cycle = -1,
  period = '',
): Promise<{ artifact?: RenderArtifact; monthFiles?: MonthFile[]; warning?: string }> {
  const contract = await client.callTool<Contract>('template_contract', { template_id: templateId });

  const draft = await client.callTool<z.infer<typeof DraftSchema>>('draft_from_ledger', {
    template_id: templateId,
    result,
    account: statement.accountType,
    cycle,
    ...(bankName && { bank_name: bankName }),
  });
  if (!draft.ok || !draft.data) {
    return { warning: `${templateId}: ledger draft failed (${(draft.errors ?? []).join('; ') || 'no detail'})` };
  }

  // The adapter's draft stays the base of every prompt: a broken first answer must not become the
  // starting point of the next round, or the model has nothing valid to fall back to.
  const base = draft.data;
  let data = draft.data;
  let attempts = 0;
  let modelFilled = false;
  let lastErrors: string[] = [];

  const policy = deps.billgen?.modelFill ?? true;

  /** 出片：不管走的是快路径还是模型补全，收尾只有这一处。 */
  const finish = async (): Promise<{ artifact?: RenderArtifact; monthFiles?: MonthFile[]; warning?: string }> => {
    // 默认交付「每月一份模板 JSON」：用户自己导进模板出账单，服务端不再拼 PDF。
    if (deps.billgen?.deliver !== 'pdf') {
      // 交付物就是模型填出来的这份数据：落盘、当场校验，报告与清单按月累积。
      const month = period || statement.periodStart.slice(0, 7);
      const answer = await client.callTool<MonthWriteAnswer>('month_write', {
        template_id: templateId,
        month,
        data,
      });
      if (answer.errors?.length) {
        return { warning: `${templateId} ${month}: 月度 JSON 失败 (${answer.errors.join('; ')})` };
      }
      if (!answer.path) return { warning: `${templateId} ${month}: 月度 JSON 没有落盘` };
      const file: MonthFile = {
        template: templateId,
        account: statement.accountType,
        month: answer.month ?? month,
        path: answer.path,
        rows: answer.rows ?? 0,
        bytes: answer.bytes ?? 0,
        sha256: answer.sha256 ?? '',
        ok: answer.ok ?? false,
      };
      const problems = [...(answer.structure ?? []), ...(answer.content ?? [])]
        .map((item) => String(item.rule ?? item.detail ?? ''))
        .filter(Boolean);
      const gaps = (answer.batch?.problems ?? []).map((item) => String(item.detail ?? item.rule ?? ''));
      return {
        monthFiles: [file],
        ...(!file.ok && {
          warning: `${templateId} ${file.month} 未通过校验（${problems.slice(0, 3).join('; ') || '见 validation-report.json'}）`,
        }),
        ...(gaps.length > 0 && { warning: `${templateId}: ${gaps.join('；')}` }),
      };
    }
    const rendered = await client.callTool<z.infer<typeof RenderSchema>>('render', {
      template_id: templateId,
      data,
      filename: `${templateId}-${statement.accountType}-${period || result.id.slice(0, 8)}.pdf`,
      watermark: true,
      encrypt: false,
    });
    if (!rendered.ok || !rendered.path) {
      return { warning: `${templateId}: render failed (${(rendered.errors ?? []).join('; ') || 'no detail'})` };
    }
    return {
      artifact: {
        template: templateId,
        account: statement.accountType,
        ...(period && { period }),
        path: rendered.path,
        pages: rendered.pages ?? 0,
        bytes: rendered.bytes ?? 0,
        sha256: rendered.sha256 ?? '',
        attempts,
        modelFilled,
        ...(recommend ? { reasons: recommend.reasons, score: recommend.score } : {}),
      },
    };
  };

  // 快路径：确定性草稿本来就是闭合的，先让服务端校验一次。合格就不用再花一次整对象输出的
  // 模型调用（一次 fill 动辄几千 token、几秒），只有草稿不合格才让模型补。
  if (policy !== 'always') {
    const pre = await client.callTool<z.infer<typeof ValidateSchema>>('validate', { template_id: templateId, data });
    if (pre.ok) {
      attempts = 1;
      return finish();
    }
    if (policy === false) {
      return { warning: `${templateId}: draft failed validation (${pre.errors.slice(0, 3).join('; ')})` };
    }
    emit({
      type: 'step',
      node: 'billgen',
      message: `${templateId}: 草稿未通过校验（${pre.errors.length} 项），交给模型补`,
    });
  }

  const maxAttempts = MAX_FILL_ATTEMPTS;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    attempts = attempt + 1;
    emit({
      type: 'step',
      node: 'billgen',
      message: `${templateId}: 模型补全第 ${attempts}/${maxAttempts} 次`,
    });
    const messages = fillMessages(
      contract,
      base,
      ledgerBrief(statement),
      deps.skills.forTemplates?.([templateId]) ?? '',
    );
    if (lastErrors.length > 0) {
      messages.push({
        role: 'user',
        content: [
          'Your previous answer did not validate. Its problems were:',
          ...lastErrors.map((e) => `- ${e}`),
          'Return the full object again, starting from the draft above (do not drop keys).',
        ].join('\n'),
      });
    }
    const filled = await deps.llm.invoke('render', FilledDataSchema, messages);
    data = filled as Record<string, unknown>;
    modelFilled = true;
    const checked = await client.callTool<z.infer<typeof ValidateSchema>>('validate', { template_id: templateId, data });
    if (checked.ok) return finish();
    lastErrors = checked.errors;
    emit({ type: 'warning', message: `${templateId}: fill attempt ${attempts} failed validation` });
  }

  return { warning: `${templateId}: data never validated (${lastErrors.slice(0, 3).join('; ')})` };
}

/**
 * Renders the finished statements through billgen's MCP server (configured by
 * `BILLGEN_MCP_CMD` / `BILLGEN_TEMPLATES` / `BILLGEN_OUT`). No config = the node is a no-op, which is
 * what unit tests and offline fixtures without billgen get.
 */
export const renderStep =
  (deps: GraphDeps, emit: Emit) =>
  async (s: GraphStateValue): Promise<GraphStateUpdate> => {
    const result = s.result;
    const config = deps.billgen;
    if (!result || !config) return {};

    // 这次出哪几套版式：页面里选的优先（`request.templates`），没选才用服务端 `BILLGEN_TEMPLATES`。
    // 「做哪家银行的账单」是每次生成的业务选择，不该只能靠改进程环境变量。
    const requested = (s.request?.templates ?? []).map((id) => id.trim()).filter(Boolean);
    const wanted = requested.length > 0 ? requested : config.templates;
    if (wanted.length === 0) return {};
    const bankName = (s.request?.bankName ?? '').trim();

    const wantsAuto = wanted.includes(AUTO_TEMPLATES);
    const pinned = wanted.filter((id) => id !== AUTO_TEMPLATES);
    const targets: { templateId: string; statement: Statement; recommend?: { reasons: string[]; score: number } }[] = [];
    const skipped = new Set<string>();
    const skipWarnings: string[] = [];
    for (const statement of result.statements) {
      if (!RENDERABLE_ACCOUNTS.has(statement.accountType)) {
        // 不说一声就少出一份账单，会被当成 bug；这里明确记下来（credit 信用卡账单
        // 服务端没有对应版式，只有存款账户版式）。
        if (!skipped.has(statement.accountType)) {
          skipped.add(statement.accountType);
          const message = `${statement.accountType}: 服务端没有该账户类型的版式，跳过出片（只有存款账户版式）`;
          skipWarnings.push(message);
          emit({ type: 'warning', message });
        }
        continue;
      }
      for (const templateId of pinned) {
        targets.push({ templateId, statement });
      }
    }
    if (targets.length === 0 && !wantsAuto) {
      // 一页都出不了也要把「为什么」带回去，否则调用方只看到 renders 为空。
      return skipWarnings.length ? { result: { ...result, warnings: [...result.warnings, ...skipWarnings] } } : {};
    }

    const artifacts: RenderArtifact[] = [];
    const monthFiles: MonthFile[] = [];
    const warnings: string[] = [...skipWarnings];
    try {
      await sharedMcpPool.with(
        { command: config.command, args: config.args, env: { BILLGEN_MCP_OUT: config.outDir } },
        async (client) => {
          emit({ type: 'node_start', node: 'billgen' });
          if (wantsAuto) {
            const limit = s.request?.templateLimit ?? config.templateLimit ?? AUTO_TEMPLATE_LIMIT;
            for (const statement of result.statements) {
              if (!RENDERABLE_ACCOUNTS.has(statement.accountType)) continue;
              const picked = await recommendFor(client, result, statement, limit);
              for (const candidate of picked.candidates) {
                targets.push({
                  templateId: candidate.template,
                  statement,
                  recommend: { reasons: candidate.reasons, score: candidate.score },
                });
              }
              for (const problem of picked.errors) warnings.push(`${statement.accountType}: ${problem}`);
              emit({
                type: 'progress',
                message: `推荐模板 ${statement.accountType}: ${picked.candidates.map((c) => c.template).join(', ') || 'none'}`,
              });
            }
          }
          // 模板之间互不依赖：并行出片，两个同时在跑（MCP 客户端是同一连接，别把并发开太大）。
          const slots: { artifacts: RenderArtifact[]; monthFiles: MonthFile[]; warnings: string[] }[] =
            new Array(targets.length);
          let cursor = 0;
          const worker = async (): Promise<void> => {
            for (let i = cursor++; i < targets.length; i = cursor++) {
              const target = targets[i]!;
              try {
                slots[i] = await renderMonthly(
                  deps, emit, client, target.templateId, result, target.statement, target.recommend, bankName,
                );
              } catch (err) {
                const message = err instanceof Error ? err.message : String(err);
                slots[i] = { artifacts: [], monthFiles: [], warnings: [`${target.templateId}: ${message}`] };
              }
            }
          };
          await Promise.all(Array.from({ length: Math.min(RENDER_CONCURRENCY, targets.length) }, worker));
          for (const slot of slots) {
            artifacts.push(...(slot?.artifacts ?? []));
            hit(slot?.monthFiles, monthFiles);
            warnings.push(...(slot?.warnings ?? []));
          }
        },
      );
    } catch (err) {
      warnings.push(`billgen mcp unavailable (${err instanceof Error ? err.message : String(err)}); nothing delivered`);
    } finally {
      emit({ type: 'node_end', node: 'billgen' });
    }

    for (const warning of warnings) emit({ type: 'warning', message: warning });
    return {
      result: {
        ...result,
        renders: artifacts,
        monthFiles,
        warnings: [...result.warnings, ...warnings],
      },
      warnings,
    };
  };
