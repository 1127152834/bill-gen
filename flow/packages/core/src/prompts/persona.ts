import type { LlmMessage } from '../llm.js';
import type { SubjectType } from '../schemas/index.js';
import { json } from './common.js';

/** Trades of self-employed-patterns.md: the code draws one so personas vary with the seed. */
export const PERSONAL_TRADES = [
  'Freelance graphic designer',
  'Freelance writer',
  'Freelance web developer',
  'Bookkeeper working for small local clients',
  'Handyman',
  'House cleaner',
  'Landscaper / lawn care',
  'Hair stylist renting a booth',
  'Wedding photographer',
  'Real estate agent (independent contractor)',
  'Personal trainer',
  'Private tutor',
  'Owner-operator trucker',
  'Independent IT consultant',
] as const;

/** Industries of industry-cost-structures.md. */
export const BUSINESS_INDUSTRIES = [
  'Full-service restaurant',
  'Cafe / coffee shop',
  'Retail boutique',
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

const PERSONAL_RULES = `- The person is self-employed: a 1099 sole proprietor or single-member LLC owner (one-person business) with no employer and no salary. "occupation" names the trade, and the trade is one where local clients or customers pay the person directly. It is a one-person business: they may hire a helper now and then, but the business is just them.
- No online-payment income anywhere: they do NOT accept cards online and have no payment gateway / processor account yet — that is exactly why they are applying for one. So no Stripe, Square, PayPal, Shopify, Etsy, Amazon, Upwork, Fiverr, Patreon, Substack payouts, and no app-platform deposits (Uber, Lyft, DoorDash, Instacart, Airbnb, YouTube, Twitch). Clients pay by bank transfer (ACH or wire), check, Zelle or cash.
- Make the fields agree: age fits the trade and career stage, household (maritalStatus, children) fits the age, hobbies (2-5) fit age, household, budget and city.`;

const BUSINESS_RULES = `- A small independent US business with 1-2 people: the owner plus at most one employee (entityType Sole Prop or single-member LLC is the norm; a two-person LLC is the ceiling). Never describe a team, a department or a staff of many.
- No online-payment income: no online store, no website checkout, no marketplace or app-platform payouts, no processor settlements (Stripe, Square, PayPal, Shopify, Amazon, Etsy, Upwork). Money comes from customers paying by bank transfer (ACH or wire), check, Zelle, cash, or from a card terminal in a physical shop — the online gateway is what they are applying for, so it cannot already be in the business.
- "description" is one plain sentence about what it sells and to whom, and it must not mention an online shop, a webshop, e-commerce or a marketplace.
- "legalName" is fictional and ends with the entity suffix when one applies (e.g. "Copper Kettle Cafe LLC"); never the name of a real chain or known company, and never a placeholder word (ACME, EXAMPLE, SAMPLE, FICTIONAL, TEST, DEMO, "Your Company", XYZ, FOO).`;

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
