import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { ScheduleInfeasibleError, type FinancialModel, type ProgressEvent, type RecurringItem } from '../src/index.js';
import { createSemaphore } from '../src/graph/deps.js';
import { scheduleStep } from '../src/graph/nodes/schedule.js';
import { researchStep } from '../src/graph/nodes/research.js';
import { Budget, createMemoryCache, type SearchHit } from '../src/index.js';
import { createFakeLlm } from '../src/testing/index.js';
import { loadSkills } from '../src/skills.js';
import type { GraphStateValue } from '../src/graph/state.js';
import { loadModel, makeNorm } from './schedule-helpers.js';
import { makeNormAt } from './statement-helpers.js';

const bill = (day: number): RecurringItem => ({
  id: `bill-${day}`,
  label: `Bill ${day}`,
  kind: 'other',
  dayRule: { kind: 'dayOfMonth', day },
  amount: { kind: 'fixed', cents: 10000 },
  account: 'checking',
  priority: 1,
  factId: `fact-bill-${day}`,
  txnType: 'ACH',
});

/** Five priority-1 bills on a checking account allowing at most 4 rows: the scheduler cannot fit them. */
function infeasibleState(rounds: number): GraphStateValue {
  const model: FinancialModel = { ...loadModel('freelancer'), recurring: [3, 8, 13, 18, 23].map(bill) };
  const norm = makeNorm({ fixture: 'freelancer', seed: 'too-many', accounts: 'checking', checking: { min: 3, max: 4 }, months: 1, endMonth: '2026-09' });
  return { norm, model, rounds, retryTarget: null, feedback: [], best: null, statements: [] } as unknown as GraphStateValue;
}

describe('schedule node', () => {
  it('treats ScheduleInfeasibleError as a lifeModel failure while rounds remain', async () => {
    const events: ProgressEvent[] = [];
    const update = await scheduleStep((e) => events.push(e))(infeasibleState(1));
    expect(update).toMatchObject({ rounds: 2, retryTarget: 'lifeModel', scheduleOut: null });
    expect((update.feedback as string[])[0]).toMatch(/could not build the statements: checking cycle 0/);
    expect(events).toEqual([{ type: 'retry', target: 'lifeModel', reason: expect.stringMatching(/^schedule infeasible: /) }]);
  });

  it('throws the ScheduleInfeasibleError once the rounds are used up and no earlier result exists', async () => {
    await expect(scheduleStep(() => {})(infeasibleState(2))).rejects.toBeInstanceOf(ScheduleInfeasibleError);
  });

  it('falls back to the best earlier round once the rounds are used up', async () => {
    const events: ProgressEvent[] = [];
    const best = { round: 0, statements: ['kept'], ruleResults: [], findings: [], templates: [], meta: {}, model: {}, scheduleOut: {} };
    const update = await scheduleStep((e) => events.push(e))({ ...infeasibleState(2), best } as unknown as GraphStateValue);
    expect(update).toMatchObject({ fallback: true, statements: ['kept'], degraded: ['schedule retry failed (SCHEDULE_INFEASIBLE); kept round 0 result'] });
    expect(events).toEqual([{ type: 'warning', message: 'schedule retry failed (SCHEDULE_INFEASIBLE); kept round 0 result' }]);
  });
});

describe('createSemaphore', () => {
  it('never runs more than the limit at once and runs everything', async () => {
    const gate = createSemaphore(2);
    let active = 0;
    let peak = 0;
    const done = await Promise.all(
      Array.from({ length: 7 }, (_, i) =>
        gate(async () => {
          peak = Math.max(peak, ++active);
          await new Promise((r) => setTimeout(r, 2));
          active--;
          return i;
        }),
      ),
    );
    expect(done).toEqual([0, 1, 2, 3, 4, 5, 6]);
    expect(peak).toBe(2);
  });

  it('frees the slot when a task throws', async () => {
    const gate = createSemaphore(1);
    await expect(gate(async () => Promise.reject(new Error('x')))).rejects.toThrow('x');
    await expect(gate(async () => 'ok')).resolves.toBe('ok');
  });
});

describe('research node: housing fallback (I11)', () => {
  const norm = makeNormAt({ fixture: 'freelancer', seed: 'rent', accounts: 'both', months: 1 });
  const rentQuery = { id: 'q-rent', category: 'rent', query: 'apartment rent prices Austin TX', scope: 'metro' as const };
  const hit: SearchHit = { title: 'Mueller apartments', url: 'https://www.example-apartments.com/austin/', content: '1 bed from $1,495' };
  const fact = { name: 'Aldrich 51', city: 'Austin', state: 'TX', priceItems: [{ item: '1 bedroom', priceCents: 149500, sourceUrl: hit.url }], sourceUrl: hit.url };

  function deps(answerFor: (q: string) => SearchHit[]) {
    const queries: string[] = [];
    const llm = createFakeLlm({ extractFacts: [[fact]] });
    return {
      queries,
      llm,
      deps: {
        llm,
        search: { search: async (q: string) => (queries.push(q), answerFor(q)) },
        cache: createMemoryCache(),
        skills: loadSkills(fileURLToPath(new URL('../../../skills/', import.meta.url))),
        budget: new Budget(),
        bankName: 'Test Bank',
        today: '2026-10-29',
        now: () => Date.UTC(2026, 9, 29),
      },
    };
  }

  it('runs fallback queries when the rent category finds nothing, and keeps what they find', async () => {
    const t = deps((q) => (/apartments for rent/i.test(q) ? [hit] : []));
    const events: ProgressEvent[] = [];
    const update = await researchStep(t.deps, (e) => events.push(e))({ norm, query: rentQuery });
    expect(t.queries[0]).toBe(rentQuery.query);
    expect(t.queries).toContain('apartments for rent 78704 Austin TX');
    expect((update.facts as { name: string; category: string }[]).map((f) => [f.name, f.category])).toEqual([['Aldrich 51', 'rent']]);
    expect(update.warnings).toEqual([]);
  });

  it('still drops the category (one warning) when every fallback finds nothing', async () => {
    const t = deps(() => []);
    const update = await researchStep(t.deps, () => {})({ norm, query: rentQuery });
    expect(update.facts).toEqual([]);
    expect(update.warnings).toEqual(['category rent dropped: no sources']);
    expect(t.queries.length).toBeGreaterThanOrEqual(4);
    expect(t.queries.some((q) => /mortgage servicer/i.test(q))).toBe(true);
  });

  it('skips the fallbacks when few Tavily queries are left for the other research tasks', async () => {
    const t = deps(() => []);
    t.deps.budget = new Budget({ tavilyQueries: 8 });
    await researchStep(t.deps, () => {})({ norm, query: rentQuery });
    expect(t.queries).toHaveLength(2);
  });

  it('does not run fallbacks for other categories', async () => {
    const t = deps(() => []);
    await researchStep(t.deps, () => {})({ norm, query: { ...rentQuery, id: 'q-gym', category: 'gym', query: 'gym Austin TX' } });
    expect(t.queries).toHaveLength(2); // the metro query and its state-wide retry
  });
});
