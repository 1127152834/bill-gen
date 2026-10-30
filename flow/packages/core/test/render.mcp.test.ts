/**
 * The MCP bridge, end to end and offline: a real `billgen mcp` child process renders real PDFs, and a
 * stub "model" proves the fill loop actually edits the template data (rather than just echoing).
 *
 * The Python side is discovered through BILLGEN_MCP_CMD; when it is absent these tests skip, so a
 * Node-only checkout still runs green.
 */
import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { afterAll, describe, expect, it } from 'vitest';
import { z } from 'zod';
import { McpStdioClient, type McpServerConfig } from '../src/mcp/client.js';
import { renderStep } from '../src/graph/nodes/render.js';
import type { GraphDeps } from '../src/graph/deps.js';
import type { GraphStateValue } from '../src/graph/state.js';
import type { GenerationResult, LlmMessage, LlmNode, StructuredLlm } from '../src/index.js';
import { createFixtureDeps, fixtureGenerationResult } from '../src/testing/index.js';
import { GenerationResultSchema } from '../src/schemas/index.js';

const BILLGEN = process.env.BILLGEN_MCP_CMD;
const hasBillgen = !!BILLGEN && existsSync(BILLGEN);
const outDir = mkdtempSync(join(tmpdir(), 'bh-render-'));
const config: McpServerConfig = { command: BILLGEN ?? 'billgen', args: ['mcp'], env: { BILLGEN_MCP_OUT: outDir } };

afterAll(() => rmSync(outDir, { recursive: true, force: true }));

/** A model that reads the draft out of its prompt, applies `edit`, and answers with the full object. */
function stubLlm(edit: (data: Record<string, unknown>) => Record<string, unknown>): StructuredLlm & { calls: number } {
  const llm = {
    calls: 0,
    async invoke<T>(node: LlmNode, schema: z.ZodType<T>, messages: LlmMessage[]): Promise<T> {
      llm.calls++;
      expect(node).toBe('render');
      const prompt = messages.map((m) => m.content).join('\n');
      const start = prompt.indexOf('Here is the draft');
      const braceFrom = prompt.indexOf('{', start);
      let depth = 0;
      let end = -1;
      for (let i = braceFrom; i < prompt.length; i++) {
        const c = prompt[i];
        if (c === '{') depth++;
        else if (c === '}') {
          depth--;
          if (depth === 0) {
            end = i + 1;
            break;
          }
        }
      }
      expect(end).toBeGreaterThan(braceFrom);
      const draft = JSON.parse(prompt.slice(braceFrom, end)) as Record<string, unknown>;
      return schema.parse(edit(draft)) as T;
    },
  };
  return llm as unknown as StructuredLlm & { calls: number };
}

function depsWith(
  llm: StructuredLlm,
  modelFill: boolean | 'always' = true,
  templates: string[] = ['boa-personal'],
  deliver: 'json' | 'pdf' = 'pdf',
): GraphDeps {
  return {
    ...createFixtureDeps(),
    llm,
    billgen: {
      command: BILLGEN ?? 'billgen',
      args: ['mcp'],
      templates,
      outDir,
      modelFill,
      templateLimit: 2,
      deliver,
    },
  };
}

/** Text layer of a PDF, via the same PyMuPDF the renderer uses. */
function pdfText(path: string): string {
  const python = join(dirname(BILLGEN ?? 'billgen'), 'python');
  const script = 'import sys, fitz; doc = fitz.open(sys.argv[1]); sys.stdout.write(chr(10).join(p.get_text() for p in doc))';
  return execFileSync(python, ['-c', script, path], { encoding: 'utf8' });
}

/** Parsed once so schema defaults (`renders: []`) are applied, as they are for a real job. */
const result = (): GenerationResult => GenerationResultSchema.parse(fixtureGenerationResult());

function stateWith(value: GenerationResult): GraphStateValue {
  return { result: value } as unknown as GraphStateValue;
}

/** The update as this node builds it: the full result plus the render warnings. */
type RenderUpdate = { result?: GenerationResult; warnings?: string[] };

describe.skipIf(!hasBillgen)('render node over MCP', () => {
  it('splits a multi-month statement into one PDF per month', async () => {
    // 真实银行只寄当期对账单：3 个月的请求要出 3 个文件，每个月一份，文件名带月份。
    const base = result();
    const statement = base.statements.find((s) => s.accountType === 'checking')!;
    const months = [
      { cycleStart: '2026-07-24', cycleEnd: '2026-08-23' },
      { cycleStart: '2026-08-24', cycleEnd: '2026-09-23' },
      { cycleStart: '2026-09-24', cycleEnd: '2026-10-23' },
    ].map((m) => ({ ...m, openingBalanceCents: 100000, closingBalanceCents: 120000, totalCreditsCents: 0, totalDebitsCents: 0, transactionCount: 0 }));
    const value = GenerationResultSchema.parse({
      ...base,
      statements: [{ ...statement, months }],
    });
    const deps = depsWith(stubLlm((d) => d), false);
    const update = (await renderStep(deps, () => {})(stateWith(value))) as RenderUpdate;
    const renders = update.result?.renders ?? [];
    expect(renders.map((r: { period?: string }) => r.period)).toEqual(['2026-07', '2026-08', '2026-09']);
    expect(new Set(renders.map((r: { path: string }) => r.path)).size).toBe(3);
  }, 240_000);

  it('renders every checking statement and records the artifact', async () => {
    const events: unknown[] = [];
    const deps = depsWith(stubLlm((d) => d), false);
    const update = (await renderStep(deps, (event) => events.push(event))(stateWith(result()))) as RenderUpdate;
    const renders = update.result?.renders ?? [];
    expect(renders.length).toBeGreaterThan(0);
    expect(renders.every((r: { account: string }) => r.account === 'checking')).toBe(true);
    for (const artifact of renders) {
      expect(artifact.pages).toBeGreaterThan(0);
      expect(artifact.bytes).toBeGreaterThan(10_000);
      expect(existsSync(artifact.path)).toBe(true);
      expect(readFileSync(artifact.path).subarray(0, 5).toString()).toBe('%PDF-');
    }
  }, 240_000);

  it('lets the model edit the data and the edit reaches the PDF text layer', async () => {
    const marker = 'SCENARIO FOOTER LINE';
    const deps = depsWith(
      stubLlm((draft) => ({ ...draft, customer_lines: [marker, ...((draft.customer_lines as string[]) ?? []).slice(1)] })),
      'always',
    );
    const update = (await renderStep(deps, () => {})(stateWith(result()))) as RenderUpdate;
    const first = update.result?.renders?.[0];
    expect(first).toBeDefined();
    expect(first?.modelFilled).toBe(true);
    expect(first?.attempts).toBe(1);
    expect(pdfText(first!.path)).toContain(marker);
  }, 240_000);

  it('feeds validation errors back and still renders after the model fixes them', async () => {
    let round = 0;
    const deps = depsWith(
      stubLlm((draft) => {
        round++;
        // First answer drops a required key, the retry puts it back.
        const broken = { ...draft };
        delete (broken as Record<string, unknown>).summary;
        return round === 1 ? broken : draft;
      }),
      'always',
    );
    const events: { type: string; message?: string }[] = [];
    const update = (await renderStep(deps, (event) => events.push(event as { type: string; message?: string }))(
      stateWith(result()),
    )) as RenderUpdate;
    const first = update.result?.renders?.[0];
    expect(first?.attempts).toBe(2);
    expect(events.some((e) => e.type === 'warning' && e.message?.includes('failed validation'))).toBe(true);
  }, 240_000);

  it('lets the recommender pick the templates when templates=[auto]', async () => {
    const notes: string[] = [];
    const deps = depsWith(stubLlm((d) => d), false, ['auto']);
    const update = (await renderStep(deps, (event) => {
      if (event.type === 'progress') notes.push(event.message);
    })(stateWith(result()))) as RenderUpdate;
    const renders = update.result?.renders ?? [];
    expect(renders.length).toBeGreaterThan(0);
    // templateLimit 限的是「几套版式」，每个月各出一份文件，所以份数 = 版式数 × 月数
    const templates = new Set(renders.map((r: { template: string }) => r.template));
    expect(templates.size).toBeLessThanOrEqual(2); // templateLimit
    const months = new Set(renders.map((r: { period?: string }) => r.period));
    expect(renders.length).toBe(templates.size * months.size);
    for (const artifact of renders) {
      expect(artifact.reasons?.length ?? 0).toBeGreaterThan(0);
      expect(artifact.score ?? 0).toBeGreaterThan(0);
      expect(artifact.template).not.toBe('auto');
    }
    expect(notes.join('\n')).toContain('推荐模板');
  }, 240_000);

  it('honours a pinned template list alongside auto', async () => {
    const deps = depsWith(stubLlm((d) => d), false, ['boa-personal', 'auto']);
    const update = (await renderStep(deps, () => {})(stateWith(result()))) as RenderUpdate;
    const templates = (update.result?.renders ?? []).map((r) => r.template);
    expect(templates).toContain('boa-personal');
    expect(templates.length).toBeGreaterThan(1);
  }, 240_000);

  it('lets the request pick the layout and the bank name', async () => {
    // 页面上的「做哪家银行的账单 / 账单行名」属于这一次请求，不该只能靠进程环境变量。
    const deps = depsWith(stubLlm((d) => d), false, ['boa-personal']);
    const state = {
      result: result(),
      request: { templates: ['chase-personal'], bankName: 'Cascade Trust Bank' },
    } as unknown as GraphStateValue;
    const update = (await renderStep(deps, () => {})(state)) as RenderUpdate;
    const first = update.result?.renders?.[0];
    expect(first?.template).toBe('chase-personal');
    expect(pdfText(first!.path)).toContain('Cascade Trust Bank');
  }, 240_000);

  it('delivers one template JSON per month instead of a PDF', async () => {
    // 默认交付物是 JSON：用户自己导进模板出账单，服务端不再拼 PDF。
    const base = result();
    const statement = base.statements.find((s) => s.accountType === 'checking')!;
    const months = [
      { cycleStart: '2026-07-24', cycleEnd: '2026-08-23' },
      { cycleStart: '2026-08-24', cycleEnd: '2026-09-23' },
      { cycleStart: '2026-09-24', cycleEnd: '2026-10-23' },
    ].map((m) => ({ ...m, openingBalanceCents: 100000, closingBalanceCents: 120000, totalCreditsCents: 0, totalDebitsCents: 0, transactionCount: 0 }));
    const value = GenerationResultSchema.parse({ ...base, statements: [{ ...statement, months }] });
    const deps = depsWith(stubLlm((d) => d), false, ['boa-personal'], 'json');
    const update = (await renderStep(deps, () => {})(stateWith(value))) as RenderUpdate;

    expect(update.result?.renders ?? []).toHaveLength(0);
    const files = update.result?.monthFiles ?? [];
    expect(files.map((f) => f.month)).toEqual(['2026-07', '2026-08', '2026-09']);
    expect(new Set(files.map((f) => f.path)).size).toBe(3);
    for (const file of files) {
      expect(existsSync(file.path)).toBe(true);
      const data = JSON.parse(readFileSync(file.path, 'utf8')) as Record<string, unknown>;
      expect(Object.keys(data).length).toBeGreaterThan(4);
      expect(file.bytes).toBeGreaterThan(100);
      expect(file.sha256).toHaveLength(64);
    }
  }, 240_000);

  it('marks a failed month on the file instead of dropping it silently', async () => {
    const deps = depsWith(stubLlm((d) => d), false, ['boa-personal'], 'json');
    const update = (await renderStep(deps, () => {})(stateWith(result()))) as RenderUpdate;
    for (const file of update.result?.monthFiles ?? []) {
      // ok=false 的月份照样交付（用户能看见哪个月没过），只是 warnings 里点名
      if (!file.ok) expect(update.warnings?.some((w) => w.includes(file.month))).toBe(true);
    }
  }, 240_000);

  it('reports a warning instead of throwing when the server cannot start', async () => {
    const deps = depsWith(stubLlm((d) => d), false);
    deps.billgen = { ...deps.billgen!, command: join(outDir, 'does-not-exist') };
    const update = (await renderStep(deps, () => {})(stateWith(result()))) as RenderUpdate;
    expect(update.result?.renders ?? []).toHaveLength(0);
    // 跳过出片的提示也在这份 warnings 里，别假设它是第一行。
    expect(update.warnings?.some((w) => w.includes('mcp unavailable'))).toBe(true);
  }, 60_000);
});

describe.skipIf(!hasBillgen)('mcp client', () => {
  it('lists the billgen tools', async () => {
    const client = new McpStdioClient(config);
    await client.start();
    try {
      const tools = await client.listTools();
      expect(tools.map((t) => t.name).sort()).toEqual([
        'draft_from_ledger',
        'list_templates',
        'month_files',
        'month_json',
        'preview',
        'recommend_templates',
        'render',
        'template_contract',
        'validate',
      ]);
    } finally {
      await client.close();
    }
  }, 60_000);
});
