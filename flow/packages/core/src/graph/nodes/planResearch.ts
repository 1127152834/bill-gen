import { z } from 'zod';
import { stateNameOf } from '../../geo.js';
import type { NormalizedRequest } from '../../normalize.js';
import { MAX_QUERIES, MIN_QUERIES, buildPlanResearchMessages } from '../../prompts/planResearch.js';
import { ResearchQuerySchema, type Profile, type ResearchQuery } from '../../schemas/index.js';
import { SKILLS_BY_NODE } from '../../skills.js';
import type { NodeDeps } from './types.js';

const PlanSchema = z.array(ResearchQuerySchema).min(MIN_QUERIES);

const escapeRegExp = (text: string): string => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const mentions = (text: string, name: string): boolean => new RegExp(`\\b${escapeRegExp(name)}\\b`, 'i').test(text);

/**
 * Plans the research queries: one model call, then code makes the list safe to run —
 * categories lowercased, one query per (local | national, category) since research caches by
 * category, every metro / state query names the city / state, ids unique, at most 14.
 */
export async function planResearchNode(
  input: { norm: NormalizedRequest; profile: Profile },
  deps: NodeDeps,
): Promise<ResearchQuery[]> {
  const { norm, profile } = input;
  const skillsText = deps.skills.get(SKILLS_BY_NODE.planResearch);
  const raw = await deps.llm.invoke('planResearch', PlanSchema, buildPlanResearchMessages({ norm, profile, skillsText }));

  const city = profile.address.city;
  const st = norm.location.state;
  const stateName = stateNameOf(st) ?? st;
  const seenCategories = new Set<string>();
  const seenIds = new Set<string>();
  const out: ResearchQuery[] = [];
  for (const q of raw) {
    const category = q.category.trim().toLowerCase();
    const group = q.scope === 'national' ? 'national' : 'local';
    if (seenCategories.has(`${group}:${category}`)) continue;
    seenCategories.add(`${group}:${category}`);

    let text = q.query.trim();
    if (q.scope === 'metro' && !mentions(text, city) && !mentions(text, norm.location.metroName)) text = `${text} ${city} ${st}`;
    if (q.scope === 'state' && ![city, stateName, st].some((n) => mentions(text, n))) text = `${text} ${stateName}`;

    let id = q.id;
    for (let n = 2; seenIds.has(id); n++) id = `${q.id}-${n}`;
    seenIds.add(id);

    out.push({ id, category, query: text, scope: q.scope });
    if (out.length === MAX_QUERIES) break;
  }
  return out;
}
