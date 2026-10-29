import type { LlmMessage } from '../llm.js';
import type { NormalizedRequest } from '../normalize.js';
import type { Profile } from '../schemas/index.js';
import { hardRules, isBusinessProfile, json, locationLine, subjectLine } from './common.js';

export type PlanResearchPromptInput = { norm: NormalizedRequest; profile: Profile; skillsText: string };

export const MAX_QUERIES = 14;
export const MIN_QUERIES = 6;

function task(business: boolean, city: string, st: string): string {
  return `Task: write ${MIN_QUERIES + 2}-${MAX_QUERIES} web search queries (never more than ${MAX_QUERIES}) that find the REAL merchants this subject pays and is paid by. Only merchants found by this research may appear on the statements, so every kind of transaction the subject needs must be covered by a query.

Each query has a scope:
- "metro": local spending in ${city}. The query text MUST contain "${city} ${st}". Use for: groceries, restaurants and coffee shops, fuel stations, gyms, pharmacies, ${business ? 'the commercial lease / landlord, local suppliers and wholesalers of the trade' : 'the apartment complex or landlord (rent prices), local trade suppliers'}, and the local electric / gas / water utility.
- "state": only when the answer is statewide (for example a state-regulated insurer or a statewide utility). The query MUST contain the state name.
- "national": companies used nationwide; no city in the query. Use for: streaming and software subscriptions, the trade's SaaS tools, insurers (${business ? 'business owner\'s policy, workers\' comp, commercial auto' : 'marketplace health plan, auto, renters'}), the phone carrier and internet provider, payment processors and gig platforms that pay the subject, online retailers, and the IRS ("IRS Direct Pay estimated tax payment")${business ? ', and a payroll provider (Gusto, ADP) when the business has employees' : ''}.

Guidance:
- Choose categories this ${business ? 'industry' : 'trade'} really needs (the skills below list the typical costs per trade / industry), plus everyday living costs${business ? ' of running the shop' : ''}.
- Aim for at least 5 metro queries and at least 4 national queries.
- Prefer wording that surfaces named businesses and prices: "rent prices", "menu prices", "price per gallon", "monthly plan price".
- "category": a short lowercase label, unique within the local scopes (metro + state) and unique within national, e.g. "groceries", "fuel", "rent", "electric utility", "streaming".
- "id": "q1", "q2", ... in order.
- Never search for banks or credit unions.${business ? '' : '\n- Never search for an employer, payroll or salary: the person is self-employed.'}`;
}

/** Messages for the planResearch node: system = role + task + hard rules + skills; user = compact JSON of the request. */
export function buildPlanResearchMessages(input: PlanResearchPromptInput): LlmMessage[] {
  const { norm, profile, skillsText } = input;
  const business = isBusinessProfile(profile);
  const system = [
    'You plan web research for a generator of realistic US bank-statement transaction data used in a spot-the-fake game. Realism is the whole point.',
    subjectLine(profile),
    locationLine(norm, profile),
    task(business, profile.address.city, norm.location.state),
    hardRules(norm.request.subjectType),
    'Reply with a JSON array of {id, category, query, scope}.',
    'Reference knowledge (skills):',
    skillsText,
  ].join('\n\n');
  const user = json({
    subjectType: norm.request.subjectType,
    profile,
    location: {
      city: profile.address.city,
      state: norm.location.state,
      metro: norm.location.metroName,
      combinedSalesTaxRate: norm.location.combinedSalesTaxRate,
      colTier: norm.location.colTier,
    },
    accounts: norm.request.accounts,
    period: { start: norm.periodStart, end: norm.periodEnd, months: norm.request.months },
  });
  return [
    { role: 'system', content: system },
    { role: 'user', content: user },
  ];
}
