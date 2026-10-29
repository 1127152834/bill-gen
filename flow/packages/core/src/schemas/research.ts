import { z } from 'zod';

export const ResearchQuerySchema = z.object({
  id: z.string().min(1),
  category: z.string().min(1),
  query: z.string().min(1),
  /** `national`: companies that serve the whole country (SaaS, insurers, IRS…); not tied to the persona's state. */
  scope: z.enum(['metro', 'state', 'national']),
});
export type ResearchQuery = z.infer<typeof ResearchQuerySchema>;

/** Longest name / city / chain / price-item text kept from scraped web pages. */
export const MAX_FACT_TEXT = 80;
const factText = z.string().min(1).max(MAX_FACT_TEXT);

export const PriceItemSchema = z.object({
  item: factText,
  priceCents: z.number().int().positive(),
  sourceUrl: z.url(),
});
export type PriceItem = z.infer<typeof PriceItemSchema>;

/** `state` of a national-scope fact: a company serving the whole country, exempt from local-state checks. */
export const NATIONAL_STATE = 'US';

/**
 * A researched real-world merchant. Facts without a sourceUrl are invalid by design.
 * National companies carry `state: 'US'` and their HQ city (or `ONLINE`).
 */
export const MerchantFactSchema = z.object({
  id: z.string().min(1),
  name: factText,
  chain: factText.optional(),
  /** The store / location number when the source states it (chains only), e.g. Walmart "5402". */
  storeNumber: z.string().regex(/^\d{2,6}$/, 'expected 2-6 digits').optional(),
  city: factText,
  state: z.string().regex(/^[A-Z]{2}$/, 'expected 2-letter uppercase state'),
  category: z.string().min(1),
  priceItems: z.array(PriceItemSchema),
  sourceUrl: z.url(),
});
export type MerchantFact = z.infer<typeof MerchantFactSchema>;
