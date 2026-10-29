import { BudgetExceededError } from '../../budget.js';
import type { NormalizedRequest } from '../../normalize.js';
import { researchOne } from '../../research.js';
import type { MerchantFact, ResearchQuery } from '../../schemas/index.js';
import { createSemaphore, type Emit, type GraphDeps } from '../deps.js';
import type { GraphStateUpdate } from '../state.js';

/**
 * Simultaneous `researchOne` calls per job. 每条检索是「一次 Tavily + 一次抽取模型调用」，
 * 主要耗时在等外部 IO，所以并发开大一点几乎线性加速；`RESEARCH_CONCURRENCY` 可覆盖。
 */
export const RESEARCH_CONCURRENCY = (() => {
  const raw = Number.parseInt(process.env.RESEARCH_CONCURRENCY ?? '', 10);
  return Number.isFinite(raw) && raw > 0 ? Math.min(raw, 16) : 8;
})();
/** A housing fallback runs only while more Tavily queries than this are left (the other tasks still need theirs). */
const FALLBACK_TAVILY_RESERVE = 6;

/** What `Send` hands each research task. */
export type ResearchTask = { norm: NormalizedRequest; query: ResearchQuery };

/** Research categories about the subject's home: a household with none of them has no rent or mortgage. */
export const HOUSING_CATEGORY = /\b(rent|rental|rentals|housing|apartments?|landlord|mortgage|property management)\b/i;

/**
 * Queries tried, in order, when a housing category finds nothing: listings by zip, property
 * managers in the city (both without a state-wide retry), then the national mortgage servicers.
 */
function housingFallbacks(query: ResearchQuery, norm: NormalizedRequest): ResearchQuery[] {
  const address = norm.request.profile?.address;
  const city = address?.city ?? norm.location.metroName;
  const st = norm.location.state;
  return [
    { id: `${query.id}-listings`, category: query.category, query: `apartments for rent ${address?.zip ?? ''} ${city} ${st}`.replace(/\s+/g, ' '), scope: 'state' },
    { id: `${query.id}-managers`, category: query.category, query: `property management company ${city} ${st} rent`, scope: 'state' },
    { id: `${query.id}-mortgage`, category: 'mortgage', query: 'largest residential mortgage servicers monthly payment', scope: 'national' },
  ];
}

const errorCode = (err: unknown): string => {
  const code = (err as { code?: unknown } | null)?.code;
  if (typeof code === 'string') return code;
  return err instanceof Error ? err.name : 'UNKNOWN';
};

/**
 * One research query (run once per `Send`). Budget errors end the job; any other failure
 * (extraction output / model call) drops the category with a warning instead. A housing category
 * that finds nothing tries `housingFallbacks` before it is dropped, while the budget has room.
 */
export function researchStep(deps: GraphDeps, emit: Emit) {
  const gate = createSemaphore(RESEARCH_CONCURRENCY);
  const one = async (norm: NormalizedRequest, query: ResearchQuery): Promise<{ facts: MerchantFact[]; warnings: string[] }> => {
    let found: { facts: MerchantFact[]; warnings: string[] };
    try {
      found = await gate(() =>
        researchOne(query, norm.location, { search: deps.search, llm: deps.llm, cache: deps.cache, budget: deps.budget, now: deps.now() }),
      );
    } catch (err) {
      if (err instanceof BudgetExceededError) throw err;
      found = { facts: [], warnings: [`category ${query.category} dropped: ${errorCode(err)}`] };
    }
    emit({ type: 'research', query: query.query, sources: [...new Set(found.facts.map((f) => f.sourceUrl))] });
    return found;
  };

  return async ({ norm, query }: ResearchTask): Promise<GraphStateUpdate> => {
    let { facts, warnings } = await one(norm, query);
    if (facts.length === 0 && HOUSING_CATEGORY.test(query.category)) {
      for (const fallback of housingFallbacks(query, norm)) {
        const left = deps.budget.remaining();
        if (left.tavilyQueries <= FALLBACK_TAVILY_RESERVE || left.llmCalls <= FALLBACK_TAVILY_RESERVE) break;
        const found = await one(norm, fallback);
        if (found.facts.length > 0) {
          ({ facts, warnings } = found);
          break;
        }
      }
    }
    for (const message of warnings) emit({ type: 'warning', message });
    return { facts, warnings };
  };
}
