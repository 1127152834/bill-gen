/**
 * Offline graph dependencies backed by canned answers in `packages/core/fixtures/e2e/`:
 * an Austin freelance designer (personal, self-employed). Used by the e2e tests and by the
 * server in `LLM_FAKE=1` mode. Real skills and a real `Budget`; every model call is charged.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import type { z } from 'zod';
import { Budget, type BudgetLimits } from '../budget.js';
import type { GraphDeps } from '../graph/deps.js';
import type { LlmMessage, LlmNode, StructuredLlm } from '../llm.js';
import { createMemoryCache, type SearchClient, type SearchHit } from '../research.js';
import type { GenerationRequest, PersonalProfile } from '../schemas/index.js';
import { loadSkills } from '../skills.js';

const FIXTURE_DIR = new URL('../../fixtures/e2e/', import.meta.url);
const SKILLS_DIR = new URL('../../../../skills/', import.meta.url);

export const FIXTURE_TODAY = '2026-10-29';
export const FIXTURE_BANK = 'Harborline Federal Savings';
/** Fixed clock: 2026-10-29T12:00:00Z. */
const FIXTURE_NOW = Date.UTC(2026, 9, 29, 12);

const readFixture = <T>(name: string): T => JSON.parse(readFileSync(new URL(name, FIXTURE_DIR), 'utf8')) as T;

export const fixturePersona = (): PersonalProfile => readFixture<PersonalProfile>('persona.json');
const businessFixture = readFixture<Record<string, unknown>>('persona-business.json');

/** The request the fixtures answer: Austin, 3 months to 2026-09, checking 8-13 and credit 20-35 rows a month. */
export function fixtureRequest(overrides: Partial<GenerationRequest> = {}): GenerationRequest {
  return {
    subjectType: 'personal',
    profile: fixturePersona(),
    accounts: { checking: { min: 8, max: 13 }, credit: { min: 20, max: 35 } },
    months: 3,
    endMonth: '2026-09',
    seed: 'austin-1',
    ...overrides,
  };
}

/** Canned answers per model node (`extractFacts` is answered by category instead, see `extract`). */
export type FixtureAnswers = Partial<Record<Exclude<LlmNode, 'extractFacts'>, unknown[]>>;

export type FixtureDepsOptions = {
  /** Replaces the answer queue of a node. Answers are used in order and the last one repeats; an `Error` entry is thrown. */
  answers?: FixtureAnswers;
  /** Replaces / adds extraction answers by query category (an `Error` value is thrown for that category). */
  extract?: Record<string, unknown>;
  budget?: Partial<BudgetLimits>;
  /** Delay of every fake search, to observe research concurrency. */
  searchDelayMs?: number;
  skillsDir?: string;
};

export type FixtureLlm = StructuredLlm & { calls: { node: LlmNode; messages: LlmMessage[] }[] };
export type FixtureSearch = SearchClient & { queries: string[]; maxInFlight: number };
export type FixtureDeps = GraphDeps & { llm: FixtureLlm; search: FixtureSearch };

/** Fresh copies of the default answers (one per node), for tests that tweak one. */
export function fixtureAnswers(): Required<FixtureAnswers> {
  return {
    // 企业请求与个人请求共用 `persona` 节点：按调用里给的 schema 挑对应 fixture，
    // 否则 `LLM_FAKE=1` 下 `subjectType: business` 会在 persona 解析处整单失败（实测线上）。
    persona: [
      ({ schema }: { schema: { safeParse: (v: unknown) => { success: boolean } } }) =>
        schema.safeParse(businessFixture).success ? businessFixture : readFixture('persona.json'),
    ],
    planResearch: [readFixture('plan.json')],
    lifeModel: [readFixture('lifeModel.json')],
    descriptors: [readFixture('descriptors.json')],
    audit: [readFixture('audit.json')],
    // The fixture deps run with `modelFill: false`, so the render node never asks for this one.
    render: [{}],
  };
}

const categoryOf = (messages: LlmMessage[]): string =>
  /^Category: (.*)$/m.exec(messages.map((m) => m.content).join('\n'))?.[1]?.trim() ?? '';

function fixtureLlm(answers: Required<FixtureAnswers>, extract: Record<string, unknown>, budget: Budget): FixtureLlm {
  const cursor = new Map<LlmNode, number>();
  const calls: FixtureLlm['calls'] = [];
  const answerFor = (node: LlmNode, schema: z.ZodType<unknown>, messages: LlmMessage[]): unknown => {
    if (node === 'extractFacts') return extract[categoryOf(messages)] ?? [];
    const queue = answers[node];
    if (queue.length === 0) throw new Error(`fixture llm: no answer for node "${node}"`);
    const i = cursor.get(node) ?? 0;
    cursor.set(node, i + 1);
    const entry = queue[Math.min(i, queue.length - 1)];
    // 函数条目：按调用上下文挑答案（企业/个人 persona 共用同一节点）。
    return typeof entry === 'function'
      ? (entry as (ctx: { schema: z.ZodType<unknown>; messages: LlmMessage[] }) => unknown)({ schema, messages })
      : entry;
  };
  return {
    calls,
    async invoke<T>(node: LlmNode, schema: z.ZodType<T>, messages: LlmMessage[]): Promise<T> {
      budget.chargeLlm();
      calls.push({ node, messages });
      const answer = answerFor(node, schema as z.ZodType<unknown>, messages);
      if (answer instanceof Error) throw answer;
      return schema.parse(structuredClone(answer));
    },
  };
}

/** Every query gets every fixture hit, so each category's extracted facts cite a real hit URL. */
function fixtureSearch(hits: SearchHit[], delayMs: number): FixtureSearch {
  let inFlight = 0;
  const search: FixtureSearch = {
    queries: [],
    maxInFlight: 0,
    async search(query) {
      search.queries.push(query);
      search.maxInFlight = Math.max(search.maxInFlight, ++inFlight);
      try {
        if (delayMs > 0) await new Promise((resolve) => setTimeout(resolve, delayMs));
        return structuredClone(hits);
      } finally {
        inFlight--;
      }
    },
  };
  return search;
}

export function createFixtureDeps(opts: FixtureDepsOptions = {}): FixtureDeps {
  const budget = new Budget(opts.budget, () => FIXTURE_NOW);
  const answers = { ...fixtureAnswers(), ...opts.answers } as Required<FixtureAnswers>;
  const extract = { ...readFixture<Record<string, unknown>>('extract.json'), ...opts.extract };
  return {
    llm: fixtureLlm(answers, extract, budget),
    search: fixtureSearch(readFixture<SearchHit[]>('search.json'), opts.searchDelayMs ?? 0),
    cache: createMemoryCache(),
    skills: loadSkills(opts.skillsDir ?? fileURLToPath(SKILLS_DIR)),
    budget,
    today: FIXTURE_TODAY,
    now: () => FIXTURE_NOW,
    // The canned research / model answers only cover Austin, but `randomPersona` picks a random metro and overrides
    // the model's city and state with it. Pinning the address makes every fake persona Austin, whatever the rng says.
    personaDefaults: { address: fixturePersona().address },
  };
}
