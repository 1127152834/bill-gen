import { z } from 'zod';
import { BudgetExceededError, type Budget } from './budget.js';
import { cityOfMetro, stateNameOf, type LocationInfo } from './geo.js';
import type { StructuredLlm } from './llm.js';
import { buildExtractFactsMessages, type SearchHit } from './prompts/extractFacts.js';
import {
  MAX_FACT_TEXT,
  MerchantFactSchema,
  NATIONAL_STATE,
  PriceItemSchema,
  type MerchantFact,
  type ResearchQuery,
} from './schemas/research.js';

export type { SearchHit };

export interface SearchClient {
  search(query: string): Promise<SearchHit[]>;
}

export interface ResearchCache {
  get(key: string, now: number): { facts: MerchantFact[]; fetchedAt: number; stale: boolean } | null;
  set(key: string, facts: MerchantFact[], now: number): void;
}

export type ResearchDeps = {
  search: SearchClient;
  llm: StructuredLlm;
  cache: ResearchCache;
  budget: Budget;
  /** Epoch milliseconds. */
  now: number;
};

export const CACHE_TTL_MS = 30 * 24 * 60 * 60 * 1000;
const SEARCH_TIMEOUT_MS = 60_000;

// ---------------------------------------------------------------- Tavily client

/** Tavily via `@langchain/tavily`, loaded lazily so importing core stays light. The key is never logged or put in errors. */
export function createTavilyClient(apiKey: string): SearchClient {
  let tool: Promise<import('@langchain/tavily').TavilySearch> | undefined;
  const getTool = () =>
    (tool ??= import('@langchain/tavily').then(
      ({ TavilySearch }) => new TavilySearch({ tavilyApiKey: apiKey, maxResults: 5, searchDepth: 'advanced' }),
    ));

  return {
    async search(query) {
      let raw: unknown;
      try {
        raw = await (await getTool()).invoke({ query }, { signal: AbortSignal.timeout(SEARCH_TIMEOUT_MS) });
      } catch (err) {
        throw new Error(`Tavily search failed: ${redactKey(err instanceof Error ? err.message : String(err), apiKey)}`);
      }
      return parseTavilyResponse(raw, apiKey);
    },
  };
}

/** TavilySearch reports failures (and "no results") as `{error}` instead of throwing. */
function parseTavilyResponse(raw: unknown, apiKey: string): SearchHit[] {
  const data = (typeof raw === 'string' ? safeJson(raw) : raw) as { error?: unknown; results?: unknown } | null;
  if (typeof data?.error === 'string') {
    if (/no search results found/i.test(data.error)) return [];
    throw new Error(`Tavily search failed: ${redactKey(data.error, apiKey)}`);
  }
  if (!Array.isArray(data?.results)) {
    throw new Error('Tavily search failed: unexpected response shape');
  }
  const hits: SearchHit[] = [];
  for (const r of data.results as Array<Record<string, unknown>>) {
    if (typeof r?.url !== 'string' || !r.url) continue;
    hits.push({
      title: typeof r.title === 'string' ? r.title : '',
      url: r.url,
      content: typeof r.content === 'string' ? r.content : '',
    });
  }
  return hits;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function redactKey(text: string, apiKey: string): string {
  const flat = text.replace(/\s+/g, ' ').trim().slice(0, 300);
  return apiKey ? flat.split(apiKey).join('***') : flat;
}

// ---------------------------------------------------------------- cache

export function createMemoryCache(): ResearchCache {
  const entries = new Map<string, { facts: MerchantFact[]; fetchedAt: number }>();
  return {
    get(key, now) {
      const entry = entries.get(key);
      if (!entry) return null;
      return { facts: structuredClone(entry.facts), fetchedAt: entry.fetchedAt, stale: now - entry.fetchedAt > CACHE_TTL_MS };
    },
    set(key, facts, now) {
      entries.set(key, { facts: structuredClone(facts), fetchedAt: now });
    },
  };
}

// ---------------------------------------------------------------- research

/** What the model returns; `id` and `category` are assigned by code, `chain` may come back null. */
/** Scraped text is cut to `MAX_FACT_TEXT` after extraction (`cleanText`), so it is not length-checked here. */
const ExtractedFactSchema = MerchantFactSchema.omit({ id: true, category: true, chain: true, storeNumber: true }).extend({
  name: z.string().min(1),
  city: z.string().min(1),
  priceItems: z.array(PriceItemSchema.extend({ item: z.string().min(1) })),
  chain: z.string().min(1).nullish(),
  /** Free text from the model ("Store #5402", 5402); kept only when it is 2-6 digits. */
  storeNumber: z.union([z.string(), z.number()]).nullish(),
});

const STORE_NUMBER = /^\d{2,6}$/;

/** Scraped text as data: control characters become spaces, whitespace collapses, at most `MAX_FACT_TEXT` characters. */
export function cleanText(text: string): string {
  return text
    .replace(/[\u0000-\u001f\u007f-\u009f]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, MAX_FACT_TEXT)
    .trim();
}

/** "Store #5402" -> "5402"; anything that is not 2-6 digits -> undefined. */
function storeNumberOf(raw: string | number | null | undefined): string | undefined {
  if (raw === null || raw === undefined) return undefined;
  const digits = String(raw).replace(/^\D*#?\s*/, '').trim();
  return STORE_NUMBER.test(digits) ? digits : undefined;
}
const ExtractedFactsSchema = z.array(ExtractedFactSchema);

export async function researchOne(
  q: ResearchQuery,
  loc: LocationInfo,
  deps: ResearchDeps,
): Promise<{ facts: MerchantFact[]; warnings: string[] }> {
  const key = `${q.scope === 'national' ? NATIONAL_STATE : loc.metroId}:${q.category}`;
  const cached = deps.cache.get(key, deps.now);
  if (cached && !cached.stale) return { facts: cached.facts, warnings: [] };

  let facts: MerchantFact[];
  try {
    facts = await attempt(q, q.query, loc, deps);
    if (facts.length === 0 && q.scope === 'metro') {
      const widened = widenToState(q.query, loc);
      if (widened !== undefined && widened !== q.query) facts = await attempt(q, widened, loc, deps);
    }
  } catch (err) {
    if (!(err instanceof SearchFailedError)) throw err; // budget and model errors are not search failures
    const reason = err.message;
    if (cached) return { facts: cached.facts, warnings: [`research ${q.category}: using stale cache (${reason})`] };
    return { facts: [], warnings: [`category ${q.category} dropped: search failed`] };
  }

  if (facts.length === 0) {
    if (cached) {
      return { facts: cached.facts, warnings: [`research ${q.category}: using stale cache (no fresh sources)`] };
    }
    return { facts: [], warnings: [`category ${q.category} dropped: no sources`] };
  }
  deps.cache.set(key, facts, deps.now);
  return { facts, warnings: [] };
}

/** A search call failed (anything but a budget error); triggers the stale-cache fallback. */
class SearchFailedError extends Error {}

/** One charged search + extraction. Only a failed search is wrapped as SearchFailedError. */
async function attempt(q: ResearchQuery, query: string, loc: LocationInfo, deps: ResearchDeps): Promise<MerchantFact[]> {
  deps.budget.chargeTavily();
  let hits: SearchHit[];
  try {
    hits = await deps.search.search(query);
  } catch (err) {
    if (err instanceof BudgetExceededError) throw err;
    throw new SearchFailedError(err instanceof Error ? err.message : String(err));
  }
  if (hits.length === 0) return [];
  const extracted = await deps.llm.invoke(
    'extractFacts',
    ExtractedFactsSchema,
    buildExtractFactsMessages({ query: q, loc, hits }),
  );
  return verify(extracted, q, loc, hits);
}

/**
 * Keep only facts whose sources are real search hits and, for metro/state queries, that are in the
 * location's state; national facts get `state: 'US'`. Ids and category are assigned in code.
 */
function verify(
  extracted: z.infer<typeof ExtractedFactsSchema>,
  q: ResearchQuery,
  loc: LocationInfo,
  hits: SearchHit[],
): MerchantFact[] {
  const hitUrls = new Set(hits.map((h) => normalizeUrl(h.url)));
  const usedIds = new Set<string>();
  const facts: MerchantFact[] = [];
  const national = q.scope === 'national';
  for (const e of extracted) {
    if (!national && e.state !== loc.state) continue;
    if (!hitUrls.has(normalizeUrl(e.sourceUrl))) continue;
    const { chain: rawChain, storeNumber: rawStore, ...rest } = e;
    const storeNumber = storeNumberOf(rawStore);
    const name = cleanText(e.name);
    const city = cleanText(e.city);
    const chain = rawChain ? cleanText(rawChain) : '';
    if (!name || !city) continue;
    facts.push({
      ...rest,
      name,
      city,
      ...(chain ? { chain } : {}),
      ...(storeNumber !== undefined && { storeNumber }),
      ...(national && { state: NATIONAL_STATE }),
      id: uniqueId(`fact-${slug(name)}-${slug(city)}`, usedIds),
      category: q.category,
      priceItems: e.priceItems
        .filter((p) => hitUrls.has(normalizeUrl(p.sourceUrl)))
        .map((p) => ({ ...p, item: cleanText(p.item) }))
        .filter((p) => p.item),
    });
  }
  return facts;
}

function uniqueId(base: string, used: Set<string>): string {
  let id = base;
  for (let n = 2; used.has(id); n++) id = `${base}-${n}`;
  used.add(id);
  return id;
}

function slug(text: string): string {
  return text
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

/** Lowercase host, no #fragment, no utm_* params, no trailing slash. */
function normalizeUrl(raw: string): string {
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    return raw.trim();
  }
  url.hash = '';
  for (const name of [...url.searchParams.keys()]) {
    if (name.toLowerCase().startsWith('utm_')) url.searchParams.delete(name);
  }
  const path = url.pathname.replace(/\/+$/, '');
  return `${url.protocol}//${url.host}${path}${url.search}`;
}

/** Replace the city (and a following state code) in the query with the state's full name; undefined if impossible. */
function widenToState(query: string, loc: LocationInfo): string | undefined {
  const stateName = stateNameOf(loc.state);
  if (!stateName) return undefined;
  const cityNames = [loc.metroName, cityOfMetro(loc.metroId)]
    .filter((n): n is string => !!n && loc.matchedBy === 'city')
    .sort((a, b) => b.length - a.length);
  for (const city of cityNames) {
    const pattern = new RegExp(`\\b${escapeRegExp(city)}\\b(?:,?\\s+${loc.state}\\b)?`, 'i');
    if (pattern.test(query)) return query.replace(pattern, stateName);
  }
  return new RegExp(`\\b${escapeRegExp(stateName)}\\b`, 'i').test(query) ? query : `${query} ${stateName}`;
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
