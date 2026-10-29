import type { LlmMessage } from '../llm.js';
import type { SubjectType } from '../schemas/index.js';
import { json } from './common.js';

/** Trades of self-employed-patterns.md: the code draws one so personas vary with the seed. */
export const PERSONAL_TRADES = [
  'Rideshare driver (Uber / Lyft)',
  'Delivery courier (DoorDash / Instacart)',
  'Etsy seller of handmade goods',
  'Freelance graphic designer',
  'Freelance writer',
  'Freelance web developer',
  'Handyman',
  'House cleaner',
  'Landscaper / lawn care',
  'Hair stylist renting a booth',
  'Wedding photographer',
  'Real estate agent (independent contractor)',
  'Personal trainer',
  'Private tutor',
  'Owner-operator trucker',
  'Short-term rental host',
  'Content creator',
  'Independent IT consultant',
] as const;

/** Industries of industry-cost-structures.md. */
export const BUSINESS_INDUSTRIES = [
  'Full-service restaurant',
  'Cafe / coffee shop',
  'Retail boutique',
  'E-commerce store',
  'Hair salon / barbershop',
  'Auto repair shop',
  'Landscaping company',
  'General contractor / remodeler',
  'HVAC / plumbing contractor',
  'Fitness studio',
  'Cleaning service company',
  'Food truck',
  'Professional services firm (accounting / design agency)',
] as const;

export type PersonaPromptInput = {
  subjectType: SubjectType;
  city: string;
  state: string;
  colTier: string;
  /** Fields the user already chose; must come back unchanged. */
  keep: Record<string, unknown>;
  /** Occupation (personal) or industry (business) drawn by code, used unless `keep` has one. */
  suggestion: string;
};

const PERSONAL_RULES = `- The person is self-employed (1099 / gig / sole proprietor) with no employer and no salary. "occupation" names the trade.
- Make the fields agree: age fits the trade and career stage, household (maritalStatus, children) fits the age, hobbies (2-5) fit age, household, budget and city.`;

const BUSINESS_RULES = `- A small independent US business. Make the fields agree: entityType fits the size (a Sole Prop has 0-2 employees), employeeCount is typical for the industry and city, founded (YYYY-MM, before 2026) is older for an established, larger business; description is one plain sentence about what it sells and to whom.
- "legalName" is fictional and ends with the entity suffix when one applies (e.g. "Copper Kettle Cafe LLC"); never the name of a real chain or known company.`;

/** Messages for the persona node (random profile completion). */
export function buildPersonaMessages(input: PersonaPromptInput): LlmMessage[] {
  const business = input.subjectType === 'business';
  const system = `You invent fictional ${business ? 'small businesses' : 'people'} for a game that generates realistic US bank statements.

Rules:
- Everything is fictional: never a celebrity, public figure, real private individual or real company. Names should sound like ordinary Americans of varied backgrounds.
${business ? BUSINESS_RULES : PERSONAL_RULES}
- The address is in ${input.city}, ${input.state}: a plausible street address in that city and a 5-digit ZIP code that belongs to that city.
- Fields listed under "keep" are fixed: return them exactly as given and make every other field consistent with them.
- Write in English.

Reply with a single JSON object: the complete ${business ? 'business' : 'personal'} profile.`;
  const user = json({
    subjectType: input.subjectType,
    city: input.city,
    state: input.state,
    costOfLivingTier: input.colTier,
    [business ? 'industry' : 'occupation']: input.suggestion,
    keep: input.keep,
  });
  return [
    { role: 'system', content: system },
    { role: 'user', content: user },
  ];
}
