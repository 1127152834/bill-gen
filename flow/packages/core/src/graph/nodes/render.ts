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
import type { GenerationResult, Statement } from '../../schemas/index.js';
import type { Emit, GraphDeps } from '../deps.js';
import type { GraphStateUpdate, GraphStateValue } from '../state.js';
import { sharedMcpPool, type McpStdioClient } from '../../mcp/client.js';

/** Rounds of "draft → fill → validate" per template before giving up on that template. */
const MAX_FILL_ATTEMPTS = 3;

/** `BILLGEN_TEMPLATES=auto` asks the recommender instead of a hand-written list. */
const AUTO_TEMPLATES = 'auto';
/** Templates kept per statement when `auto` is on. */
const AUTO_TEMPLATE_LIMIT = 2;

/** Ask billgen which layouts fit this statement (deterministic scoring, no model call). */
async function recommendFor(
  client: McpStdioClient,
  result: GenerationResult,
  statement: Statement,
  bankName: string,
  limit: number,
): Promise<{ candidates: z.infer<typeof RecommendSchema>['candidates']; errors: string[] }> {
  try {
    const answer = await client.callTool<z.infer<typeof RecommendSchema>>('recommend_templates', {
      result,
      account: statement.accountType,
      cycle: -1,
      bank_name: bankName,
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

export type RenderArtifact = {
  template: string;
  account: string;
  path: string;
  pages: number;
  bytes: number;
  sha256: string;
  /** How many fill rounds it took (1 = the draft was already good). */
  attempts: number;
  /** True when the model edited the draft; false when the deterministic draft was rendered as-is. */
  modelFilled: boolean;
  /** Why the recommender picked this template (only set for `auto`). */
  reasons?: string[];
  /** Recommender score for `auto` picks. */
  score?: number;
};

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

function fillMessages(contract: Contract, draft: Record<string, unknown>, brief: string): {
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
        '',
        'Field notes (path: meaning):',
        notes,
        '',
        'Field types (path: type):',
        types,
        `Description limit: ${String(contract.limits.desc_chars ?? 38)} characters; rows per page: ${String(contract.limits.rows_per_page ?? 24)}.`,
      ].join('\n'),
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
        'Adjust it so a human reviewer sees a coherent statement: period labels and dates in the layout\'s format,',
        'product names and promo copy that belong to this bank, metadata that says it is a sample, memo/memo-ish',
        'fields consistent with the transactions, and no leftover placeholder text. Do not change the arithmetic.',
      ].join('\n'),
    },
  ];
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
): Promise<{ artifact?: RenderArtifact; warning?: string }> {
  const contract = await client.callTool<Contract>('template_contract', { template_id: templateId });

  const draft = await client.callTool<z.infer<typeof DraftSchema>>('draft_from_ledger', {
    template_id: templateId,
    result,
    account: statement.accountType,
    cycle: -1,
    bank_name: deps.bankName,
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

  const maxAttempts = deps.billgen?.modelFill === false ? 1 : MAX_FILL_ATTEMPTS;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    attempts = attempt + 1;
    if (deps.billgen?.modelFill !== false) {
      const messages = fillMessages(contract, base, ledgerBrief(statement));
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
    }
    const checked = await client.callTool<z.infer<typeof ValidateSchema>>('validate', {
      template_id: templateId,
      data,
    });
    if (checked.ok) break;
    lastErrors = checked.errors;
    emit({ type: 'warning', message: `${templateId}: fill attempt ${attempts} failed validation` });
  }

  if (lastErrors.length > 0 && maxAttempts > 0) {
    const checked = await client.callTool<z.infer<typeof ValidateSchema>>('validate', {
      template_id: templateId,
      data,
    });
    if (!checked.ok) {
      return { warning: `${templateId}: data never validated (${lastErrors.slice(0, 3).join('; ')})` };
    }
  }

  const rendered = await client.callTool<z.infer<typeof RenderSchema>>('render', {
    template_id: templateId,
    data,
    filename: `${templateId}-${statement.accountType}-${result.id.slice(0, 8)}.pdf`,
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
      path: rendered.path,
      pages: rendered.pages ?? 0,
      bytes: rendered.bytes ?? 0,
      sha256: rendered.sha256 ?? '',
      attempts,
      modelFilled,
      ...(recommend ? { reasons: recommend.reasons, score: recommend.score } : {}),
    },
  };
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
    if (!result || !config || config.templates.length === 0) return {};

    const wantsAuto = config.templates.includes(AUTO_TEMPLATES);
    const pinned = config.templates.filter((id) => id !== AUTO_TEMPLATES);
    const targets: { templateId: string; statement: Statement; recommend?: { reasons: string[]; score: number } }[] = [];
    for (const statement of result.statements) {
      if (!RENDERABLE_ACCOUNTS.has(statement.accountType)) continue;
      for (const templateId of pinned) {
        targets.push({ templateId, statement });
      }
    }
    if (targets.length === 0 && !wantsAuto) return {};

    const artifacts: RenderArtifact[] = [];
    const warnings: string[] = [];
    try {
      await sharedMcpPool.with(
        { command: config.command, args: config.args, env: { BILLGEN_MCP_OUT: config.outDir } },
        async (client) => {
          emit({ type: 'node_start', node: 'billgen' });
          if (wantsAuto) {
            const limit = config.templateLimit ?? AUTO_TEMPLATE_LIMIT;
            for (const statement of result.statements) {
              if (!RENDERABLE_ACCOUNTS.has(statement.accountType)) continue;
              const picked = await recommendFor(client, result, statement, deps.bankName, limit);
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
          for (const target of targets) {
            try {
              const { artifact, warning } = await renderOne(
                deps,
                emit,
                client,
                target.templateId,
                result,
                target.statement,
                target.recommend,
              );
              if (artifact) artifacts.push(artifact);
              if (warning) warnings.push(warning);
            } catch (err) {
              warnings.push(`${target.templateId}: ${err instanceof Error ? err.message : String(err)}`);
            }
          }
        },
      );
    } catch (err) {
      warnings.push(`billgen mcp unavailable (${err instanceof Error ? err.message : String(err)}); no PDFs rendered`);
    } finally {
      emit({ type: 'node_end', node: 'billgen' });
    }

    for (const warning of warnings) emit({ type: 'warning', message: warning });
    return {
      result: {
        ...result,
        renders: artifacts,
        warnings: [...result.warnings, ...warnings],
      },
      warnings,
    };
  };
