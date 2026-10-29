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

function depsWith(llm: StructuredLlm, modelFill = true, templates: string[] = ['boa-personal']): GraphDeps {
  return {
    ...createFixtureDeps(),
    llm,
    bankName: 'Harborline Bank',
    billgen: {
      command: BILLGEN ?? 'billgen',
      args: ['mcp'],
      templates,
      outDir,
      modelFill,
      templateLimit: 2,
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
        // First answer breaks a hard rule (formatted money), the retry repairs it.
        return round === 1 ? { ...draft, begin_balance: '$1,000.00' } : draft;
      }),
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
    expect(renders.length).toBeLessThanOrEqual(2); // templateLimit
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

  it('reports a warning instead of throwing when the server cannot start', async () => {
    const deps = depsWith(stubLlm((d) => d), false);
    deps.billgen = { ...deps.billgen!, command: join(outDir, 'does-not-exist') };
    const update = (await renderStep(deps, () => {})(stateWith(result()))) as RenderUpdate;
    expect(update.result?.renders ?? []).toHaveLength(0);
    expect(update.warnings?.[0]).toContain('mcp unavailable');
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
