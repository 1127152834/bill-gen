import { writeFileSync } from 'node:fs';
import { MemorySaver } from '@langchain/langgraph';
import { describe, expect, it } from 'vitest';
import {
  BudgetExceededError,
  GRAPH_NODES,
  GenerationResultSchema,
  LifeModelError,
  LlmOutputError,
  runGeneration,
  type AuditFinding,
  type FinancialModel,
  type GenerationRequest,
  type ProgressEvent,
} from '../src/index.js';
import {
  createFixtureDeps,
  fixtureAnswers,
  fixtureGenerationResult,
  fixtureRequest,
  type FixtureDeps,
} from '../src/testing/index.js';

type Run = { deps: FixtureDeps; events: ProgressEvent[]; result: Awaited<ReturnType<typeof runGeneration>> };

async function run(deps: FixtureDeps = createFixtureDeps(), request: GenerationRequest = fixtureRequest()): Promise<Run> {
  const events: ProgressEvent[] = [];
  const result = await runGeneration(request, deps, {
    threadId: 'job-1',
    checkpointer: new MemorySaver(),
    onEvent: (e) => events.push(e),
  });
  return { deps, events, result };
}

const callsOf = (deps: FixtureDeps, node: string) => deps.llm.calls.filter((c) => c.node === node);
const userText = (call: { messages: { role: string; content: string }[] }): string =>
  call.messages.filter((m) => m.role === 'user').map((m) => m.content).join('\n');
const retries = (events: ProgressEvent[]) => events.filter((e): e is Extract<ProgressEvent, { type: 'retry' }> => e.type === 'retry');

const goodModel = (): FinancialModel => fixtureAnswers().lifeModel[0] as FinancialModel;

const high = (suggestedTarget: AuditFinding['suggestedTarget'], problem: string): AuditFinding => ({
  severity: 'high',
  transactionIds: ['CC1'],
  problem,
  suggestedTarget,
});

describe('runGeneration with fixture dependencies', () => {
  it('Austin freelancer, 3 months, both accounts: valid result, R1-R11 pass, events from normalize to finalize', async () => {
    const { result, events, deps } = await run();

    expect(GenerationResultSchema.safeParse(result).success).toBe(true);
    expect(result.status).toBe('completed');
    expect(result.audit.rules).toHaveLength(11);
    expect(result.audit.rules.filter((r) => !r.passed).map((r) => r.ruleId)).toEqual([]);
    expect(result.statements.map((s) => s.accountType)).toEqual(['checking', 'credit']);
    expect(result.statements.every((s) => s.months.length === 3)).toBe(true);
    expect(result.id).toBe('job-1');
    expect(result.seed).toBe('austin-1');

    expect(events[0]).toEqual({ type: 'node_start', node: 'normalize' });
    expect(events.at(-1)).toEqual({ type: 'node_end', node: 'finalize' });
    for (const node of GRAPH_NODES) {
      expect(events, node).toContainEqual({ type: 'node_start', node });
      expect(events, node).toContainEqual({ type: 'node_end', node });
    }
    const research = events.filter((e) => e.type === 'research');
    expect(research).toHaveLength(13);
    for (const e of research) expect(e.type === 'research' && e.sources.length).toBeGreaterThan(0);
    const validateEnd = events.findIndex((e) => e.type === 'node_end' && e.node === 'validate');
    expect(events[validateEnd - 1]).toMatchObject({ type: 'rules' });
    expect(retries(events)).toEqual([]);

    // planResearch 1 + extraction 13 + lifeModel 1 + descriptors 1 + audit 1
    expect(result.usage).toMatchObject({ llmCalls: 17, tavilyQueries: 13 });
    expect(deps.budget.usage().llmCalls).toBe(17);
  });

  it('a first life model that breaks a lifeModel-target rule (R7 travel share) is sent back once with the violations', async () => {
    // R6 cannot be broken by a schema-valid model (the scheduler draws amounts from the ranges R6
    // checks), so the lifeModel-target rule broken here is R7: travel above 10% of debits.
    const bad = goodModel();
    const travel = bad.discretionaryPool.find((d) => d.location === 'travel')!;
    Object.assign(travel, { weight: 40, perMonth: { min: 10, max: 14 } });
    const deps = createFixtureDeps({ answers: { lifeModel: [bad, goodModel()] } });

    const { result, events } = await run(deps);

    expect(retries(events)).toHaveLength(1);
    expect(retries(events)[0]).toMatchObject({ target: 'lifeModel' });
    expect(retries(events)[0]!.reason).toContain('R7_geo_consistency');
    const lifeModelCalls = callsOf(deps, 'lifeModel');
    expect(lifeModelCalls).toHaveLength(2);
    expect(userText(lifeModelCalls[1]!)).toContain('R7_geo_consistency');
    expect(result.status).toBe('completed');
    expect(result.audit.rules.every((r) => r.passed)).toBe(true);
  });

  it('audit reporting high three times: two send-backs at most, then completed_with_warnings', async () => {
    const finding = high('descriptors', 'Coffee shop descriptor uses a processor prefix the shop does not use');
    const deps = createFixtureDeps({ answers: { audit: [[finding]] } });

    const { result, events } = await run(deps);

    expect(callsOf(deps, 'audit')).toHaveLength(3);
    expect(retries(events).map((e) => e.target)).toEqual(['descriptors', 'descriptors']);
    // Each send-back to descriptors asks for every template again, with the finding in the prompt.
    const descriptorCalls = callsOf(deps, 'descriptors');
    expect(descriptorCalls).toHaveLength(3);
    expect(userText(descriptorCalls[1]!)).toContain(finding.problem);
    expect(result.status).toBe('completed_with_warnings');
    expect(result.warnings.some((w) => w.includes(finding.problem))).toBe(true);
    expect(result.audit.findings.some((f) => f.severity === 'high')).toBe(true);
    expect(GenerationResultSchema.safeParse(result).success).toBe(true);
  });

  it('routes high audit findings by priority lifeModel > schedule > descriptors', async () => {
    const deps = createFixtureDeps({
      answers: { audit: [[high('schedule', 'weekend clustering'), high('lifeModel', 'rent too low for Austin')], []] },
    });
    const { result, events } = await run(deps);
    expect(retries(events).map((e) => e.target)).toEqual(['lifeModel']);
    expect(callsOf(deps, 'lifeModel')).toHaveLength(2);
    expect(userText(callsOf(deps, 'lifeModel')[1]!)).toContain('rent too low for Austin');
    expect(result.status).toBe('completed');
  });

  it('a schedule-target rule failure re-runs only the scheduler with a new rng fork', async () => {
    // For this seed the first schedule (rng fork schedule-r0) has two identical rows (R8): two swipes
    // on different days that post on the same day for the same amount.
    const deps = createFixtureDeps();
    const { result, events } = await run(deps, fixtureRequest({ seed: 'fixture-seed-36' }));
    expect(retries(events)).toEqual([{ type: 'retry', target: 'schedule', reason: 'rules failed: R8_no_duplicates' }]);
    expect(events.filter((e) => e.type === 'node_start' && e.node === 'schedule')).toHaveLength(2);
    // Templates are kept, so the second descriptors pass makes no model call.
    expect(callsOf(deps, 'lifeModel')).toHaveLength(1);
    expect(callsOf(deps, 'descriptors')).toHaveLength(1);
    expect(result.status).toBe('completed');
    expect(result.audit.rules.every((r) => r.passed)).toBe(true);
  });

  it('throws BudgetExceededError when the job runs out of model calls', async () => {
    const deps = createFixtureDeps({ budget: { llmCalls: 3 } });
    await expect(run(deps)).rejects.toBeInstanceOf(BudgetExceededError);
  });

  it('same seed and fixtures give deeply equal statements; another seed does not', async () => {
    const a = await run();
    const b = await run();
    expect(b.result.statements).toEqual(a.result.statements);
    const c = await run(createFixtureDeps(), fixtureRequest({ seed: 'another-seed' }));
    expect(c.result.statements).not.toEqual(a.result.statements);
  });
});

describe('runGeneration rulings', () => {
  it('requires a profile', async () => {
    const { profile: _omit, ...request } = fixtureRequest();
    await expect(runGeneration(request, createFixtureDeps(), { threadId: 't' })).rejects.toThrow(/profile is required/);
  });

  it('runs at most 4 research queries at the same time', async () => {
    const deps = createFixtureDeps({ searchDelayMs: 15 });
    await run(deps);
    expect(deps.search.queries).toHaveLength(13);
    expect(deps.search.maxInFlight).toBeGreaterThan(1);
    expect(deps.search.maxInFlight).toBeLessThanOrEqual(4);
  });

  it('drops a category whose extraction fails, with a warning, and still completes', async () => {
    const plan = [...(fixtureAnswers().planResearch[0] as unknown[]), { id: 'q-gym', category: 'gym', query: 'gym membership prices Austin TX', scope: 'metro' }];
    const deps = createFixtureDeps({
      answers: { planResearch: [plan] },
      extract: { gym: new LlmOutputError('extractFacts', 'not an array') },
    });
    const { result, events } = await run(deps);
    expect(result.warnings).toContain('category gym dropped: LLM_OUTPUT_INVALID');
    expect(events).toContainEqual({ type: 'warning', message: 'category gym dropped: LLM_OUTPUT_INVALID' });
    expect(result.status).toBe('completed');
    // Spec 7: a dropped category is also written into the audit report.
    expect(result.audit.findings).toContainEqual({
      severity: 'low',
      transactionIds: [],
      problem: 'Research found no usable sources for "gym" (LLM_OUTPUT_INVALID), so the statements have no gym merchant.',
      suggestedTarget: 'lifeModel',
    });
  });

  it('a LifeModelError uses a round and retries with its problems as feedback', async () => {
    const invalid = goodModel();
    invalid.discretionaryPool[0]!.factId = 'fact-made-up-diner';
    const deps = createFixtureDeps({ answers: { lifeModel: [invalid, invalid, goodModel()] } });
    const { result, events } = await run(deps);
    expect(retries(events)).toHaveLength(1);
    expect(retries(events)[0]).toMatchObject({ target: 'lifeModel' });
    const calls = callsOf(deps, 'lifeModel');
    expect(calls).toHaveLength(3);
    expect(userText(calls[2]!)).toContain('fact-made-up-diner');
    expect(result.status).toBe('completed');
  });

  it('throws the LifeModelError when no valid life model ever existed and the rounds are used up', async () => {
    const invalid = goodModel();
    invalid.discretionaryPool[0]!.factId = 'fact-made-up-diner';
    const deps = createFixtureDeps({ answers: { lifeModel: [invalid] } });
    const failure = run(deps);
    await expect(failure).rejects.toBeInstanceOf(LifeModelError);
    await expect(failure).rejects.toMatchObject({ code: 'LIFE_MODEL_INVALID' });
    expect(callsOf(deps, 'lifeModel')).toHaveLength(6);
  });

  it('resumes an interrupted thread from its last checkpoint', async () => {
    // A first-round descriptors failure has no earlier result to fall back on, so it ends the run.
    const deps = createFixtureDeps({ answers: { descriptors: [new Error('proxy went away'), fixtureAnswers().descriptors[0]] } });
    const checkpointer = new MemorySaver();
    const opts = { threadId: 'job-resume', checkpointer };

    await expect(runGeneration(fixtureRequest(), deps, opts)).rejects.toThrow('proxy went away');
    await expect(runGeneration(fixtureRequest(), deps, opts)).rejects.toThrow(/already has checkpoints/);
    const result = await runGeneration(fixtureRequest(), deps, { ...opts, resume: true });

    expect(result.status).toBe('completed');
    expect(callsOf(deps, 'planResearch')).toHaveLength(1);
    expect(callsOf(deps, 'lifeModel')).toHaveLength(1);
    expect(callsOf(deps, 'descriptors')).toHaveLength(2);
    expect(callsOf(deps, 'audit')).toHaveLength(1);
    const fresh = await run();
    expect(result.statements).toEqual(fresh.result.statements);
  });
});

describe('runGeneration keeps a valid earlier result when a later round fails (spec 4.4: over rounds -> completed_with_warnings)', () => {
  const sendBackTo = (target: AuditFinding['suggestedTarget']) => [[high(target, 'needs another pass')], []];

  it('a retried lifeModel that throws LlmOutputError keeps the round 0 result', async () => {
    const deps = createFixtureDeps({
      answers: { lifeModel: [goodModel(), new LlmOutputError('lifeModel', 'discretionaryPool.1.unitPriceCents: Too small')], audit: sendBackTo('lifeModel') },
    });
    const { result, events } = await run(deps);
    const fresh = await run();

    expect(result.status).toBe('completed_with_warnings');
    expect(result.warnings).toContain('lifeModel retry failed (LLM_OUTPUT_INVALID); kept round 0 result');
    expect(events).toContainEqual({ type: 'warning', message: 'lifeModel retry failed (LLM_OUTPUT_INVALID); kept round 0 result' });
    expect(result.statements).toEqual(fresh.result.statements);
    expect(result.audit.rules.every((r) => r.passed)).toBe(true);
    expect(callsOf(deps, 'lifeModel')).toHaveLength(2);
    expect(callsOf(deps, 'audit')).toHaveLength(1);
    expect(GenerationResultSchema.safeParse(result).success).toBe(true);
  });

  it('a lifeModel that stays invalid after a good first round ends completed_with_warnings once the rounds are used up', async () => {
    const invalid = goodModel();
    invalid.discretionaryPool[0]!.factId = 'fact-made-up-diner';
    const deps = createFixtureDeps({ answers: { lifeModel: [goodModel(), invalid], audit: sendBackTo('lifeModel') } });
    const { result, events } = await run(deps);

    // round 0: 1 call; round 1: 2 calls (LifeModelError, uses round 2); round 2: 2 calls, no rounds left.
    expect(callsOf(deps, 'lifeModel')).toHaveLength(5);
    expect(retries(events).map((e) => e.target)).toEqual(['lifeModel', 'lifeModel']);
    expect(result.status).toBe('completed_with_warnings');
    expect(result.warnings).toContain('lifeModel retry failed (LIFE_MODEL_INVALID); kept round 0 result');
    expect(result.statements).toEqual((await run()).result.statements);
  });

  it('a descriptors redo that fails keeps the round 0 result', async () => {
    const deps = createFixtureDeps({
      answers: { descriptors: [fixtureAnswers().descriptors[0], new Error('socket hang up')], audit: sendBackTo('descriptors') },
    });
    const { result } = await run(deps);
    expect(result.status).toBe('completed_with_warnings');
    expect(result.warnings).toContain('descriptors retry failed (Error); kept round 0 result');
    expect(result.statements).toEqual((await run()).result.statements);
  });

  it('an audit failure gives no findings and a warning, and still finalizes', async () => {
    const deps = createFixtureDeps({ answers: { audit: [new LlmOutputError('audit', 'not an array')] } });
    const { result, events } = await run(deps);
    expect(result.status).toBe('completed_with_warnings');
    expect(result.audit.findings).toEqual([]);
    expect(result.warnings).toContain('audit failed (LLM_OUTPUT_INVALID); statements were not reviewed');
    expect(events.at(-1)).toEqual({ type: 'node_end', node: 'finalize' });
  });

  it('a later round that breaks a rule does not replace the best rule-clean round', async () => {
    const bad = goodModel();
    const travel = bad.discretionaryPool.find((d) => d.location === 'travel')!;
    Object.assign(travel, { weight: 40, perMonth: { min: 10, max: 14 } });
    // round 0 is clean but audited high -> round 1 model breaks R7 -> round 2 breaks R7 again, no rounds left.
    const deps = createFixtureDeps({ answers: { lifeModel: [goodModel(), bad], audit: sendBackTo('lifeModel') } });
    const { result, events } = await run(deps);

    expect(retries(events).map((e) => e.target)).toEqual(['lifeModel', 'lifeModel']);
    expect(result.audit.rules.every((r) => r.passed)).toBe(true);
    expect(result.statements).toEqual((await run()).result.statements);
    expect(result.status).toBe('completed_with_warnings');
    expect(result.warnings.some((w) => /rules failed in round 2.*kept round 0 result/.test(w))).toBe(true);
    // The round 0 audit finding stays with the round 0 statements it was written about.
    expect(result.audit.findings.map((f) => f.problem)).toEqual(['needs another pass']);
  });

  it('budget errors still end the job even when an earlier result exists', async () => {
    const probe = createFixtureDeps();
    await run(probe);
    const deps = createFixtureDeps({ budget: { llmCalls: probe.llm.calls.length + 1 }, answers: { audit: sendBackTo('lifeModel') } });
    await expect(run(deps)).rejects.toBeInstanceOf(BudgetExceededError);
  });
});

describe('fixtureGenerationResult', () => {
  it('is a valid result matching a fresh fixture run (UPDATE_FIXTURE_RESULT=1 rewrites it)', async () => {
    const { result } = await run();
    if (process.env.UPDATE_FIXTURE_RESULT === '1') {
      const body = JSON.stringify({ ...result, id: 'fixture-job', createdAt: '2026-10-29T12:00:00.000Z' }, null, 2);
      writeFileSync(
        new URL('../src/testing/fixtureResult.ts', import.meta.url),
        '// Generated by test/graph.e2e.test.ts with UPDATE_FIXTURE_RESULT=1 - do not edit by hand.\n' +
          "import type { GenerationResult } from '../schemas/index.js';\n\n" +
          `const RESULT: GenerationResult = ${body};\n\n` +
          '/** A finished fixture job (Austin freelancer, 3 months, both accounts); browser-safe. */\n' +
          'export function fixtureGenerationResult(): GenerationResult {\n  return structuredClone(RESULT);\n}\n',
      );
      return;
    }
    const saved = fixtureGenerationResult();
    expect(GenerationResultSchema.safeParse(saved).success).toBe(true);
    expect(saved.statements).toEqual(result.statements);
    expect(saved.audit).toEqual(result.audit);
  });
});
