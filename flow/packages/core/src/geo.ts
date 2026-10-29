import { z } from 'zod';
import metrosJson from '../data/metros.json' with { type: 'json' };
import stateTaxJson from '../data/state-tax.json' with { type: 'json' };
import type { Rng } from './rng.js';

const ColTierSchema = z.enum(['low', 'mid', 'high']);
const RateSchema = z.number().min(0).max(0.2);
const StateCodeSchema = z.string().regex(/^[A-Z]{2}$/);

const MetroRowSchema = z.object({
  metroId: z.string().min(1),
  metroName: z.string().min(1),
  city: z.string().min(1),
  state: StateCodeSchema,
  population: z.number().int().positive(),
  combinedSalesTaxRate: RateSchema,
  colTier: ColTierSchema,
});
export type MetroRow = z.infer<typeof MetroRowSchema>;

const StateRowSchema = z.object({
  code: StateCodeSchema,
  name: z.string().min(1),
  combinedSalesTaxRate: RateSchema,
  colTier: ColTierSchema,
});

const SourceSchema = z.record(z.string(), z.string().min(1));

// Validated at module load: bad data files fail loudly on import.
const metros: readonly MetroRow[] = z
  .object({ _source: SourceSchema, metros: z.array(MetroRowSchema).min(1) })
  .parse(metrosJson).metros;
const states = z
  .object({ _source: SourceSchema, states: z.array(StateRowSchema).min(1) })
  .parse(stateTaxJson).states;

const stateByCode = new Map(states.map((s) => [s.code, s]));
const metroByKey = new Map(metros.map((m) => [cityKey(m.city, m.state), m]));
if (metroByKey.size !== metros.length) throw new Error('metros.json: duplicate city/state');
for (const m of metros) {
  if (!stateByCode.has(m.state)) throw new Error(`metros.json: ${m.metroId} has unknown state ${m.state}`);
}

function cityKey(city: string, state: string): string {
  return `${city.trim().toLowerCase()}|${state}`;
}

export type ColTier = 'low' | 'mid' | 'high';

export type LocationInfo = {
  metroId: string;
  metroName: string;
  state: string;
  combinedSalesTaxRate: number;
  colTier: ColTier;
  matchedBy: 'city' | 'state';
};

/** Full state name for a 2-letter code (e.g. TX → Texas). */
export function stateNameOf(code: string): string | undefined {
  return stateByCode.get(code)?.name;
}

/** The metro's plain city name (e.g. "New York" for metro "New York City"); undefined for state-level locations. */
export function cityOfMetro(metroId: string): string | undefined {
  return metros.find((m) => m.metroId === metroId)?.city;
}

export function metroToLocation(m: MetroRow): LocationInfo {
  return {
    metroId: m.metroId,
    metroName: m.metroName,
    state: m.state,
    combinedSalesTaxRate: m.combinedSalesTaxRate,
    colTier: m.colTier,
    matchedBy: 'city',
  };
}

/**
 * City (case-insensitive, trimmed) + exact 2-letter state → metro row; unknown city → the
 * state-average row (`metroId: 'state-XX'`); unknown state → throws.
 */
export function lookupLocation(city: string, state: string): LocationInfo {
  const metro = metroByKey.get(cityKey(city, state));
  if (metro) return metroToLocation(metro);
  const row = stateByCode.get(state);
  if (!row) throw new Error(`lookupLocation: unknown state "${state}"`);
  return {
    metroId: `state-${row.code}`,
    metroName: row.name,
    state: row.code,
    combinedSalesTaxRate: row.combinedSalesTaxRate,
    colTier: row.colTier,
    matchedBy: 'state',
  };
}

/** A metro drawn with probability proportional to its population. */
export function pickWeightedMetro(rng: Rng): MetroRow {
  return rng.weighted(metros.map((m) => ({ item: m, weight: m.population })));
}
