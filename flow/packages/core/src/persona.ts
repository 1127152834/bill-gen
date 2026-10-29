/**
 * Random persona completion: code picks the place (and a trade / industry for variety),
 * the model fills the rest so the fields agree, and anything the user already gave is kept.
 */
import type { z } from 'zod';
import { lookupLocation, pickWeightedMetro } from './geo.js';
import type { StructuredLlm } from './llm.js';
import { BUSINESS_INDUSTRIES, PERSONAL_TRADES, buildPersonaMessages } from './prompts/persona.js';
import type { Rng } from './rng.js';
import {
  BusinessProfileSchema,
  PersonalProfileSchema,
  type BusinessProfile,
  type PersonalProfile,
  type Profile,
  type SubjectType,
} from './schemas/index.js';

export type PersonaDeps = { llm: StructuredLlm; rng: Rng };

/**
 * @param partial fields to keep verbatim; a given `address` must be in a known state.
 * @throws when `partial.address.state` is unknown (before any model call), or when the merged
 *   profile does not satisfy the profile schema.
 */
export async function randomPersona(
  subjectType: SubjectType,
  partial: Partial<Profile> | undefined,
  deps: PersonaDeps,
): Promise<PersonalProfile | BusinessProfile> {
  const keep: Record<string, unknown> = Object.fromEntries(Object.entries(partial ?? {}).filter(([, v]) => v !== undefined));
  const given = partial?.address;

  let place: { city: string; state: string; colTier: string };
  if (given) {
    place = { city: given.city, state: given.state, colTier: lookupLocation(given.city, given.state).colTier };
  } else {
    const metro = pickWeightedMetro(deps.rng);
    place = { city: metro.city, state: metro.state, colTier: metro.colTier };
  }
  const business = subjectType === 'business';
  const suggestion = deps.rng.fork('persona-trade').pick(business ? BUSINESS_INDUSTRIES : PERSONAL_TRADES);
  const messages = buildPersonaMessages({ subjectType, ...place, keep, suggestion });

  const schema: z.ZodType<PersonalProfile | BusinessProfile> = business ? BusinessProfileSchema : PersonalProfileSchema;
  const generated = await deps.llm.invoke('persona', schema, messages);
  const address = given ?? { ...generated.address, city: place.city, state: place.state };
  return schema.parse({ ...generated, ...keep, address });
}
