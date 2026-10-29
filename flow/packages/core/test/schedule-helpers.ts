/** Shared helpers for the scheduler tests (not a test file itself). */
import { readFileSync } from 'node:fs';
import {
  FinancialModelSchema,
  daysBetween,
  normalizeRequest,
  type CountRange,
  type FinancialModel,
  type GenerationRequest,
  type NormalizedRequest,
} from '../src/index.js';
import type { ScheduledTxn } from '../src/schedule/place.js';

export const TODAY = '2026-10-29';
export const FIXTURES = ['freelancer', 'rideshare', 'cafe-business'] as const;
export type FixtureName = (typeof FIXTURES)[number];

export function loadModel(name: FixtureName): FinancialModel {
  const raw: unknown = JSON.parse(readFileSync(new URL(`./fixtures/models/${name}.json`, import.meta.url), 'utf8'));
  return FinancialModelSchema.parse(raw);
}

export type Accounts = 'checking' | 'credit' | 'both';

export function makeNorm(opts: {
  fixture: FixtureName;
  seed: string;
  accounts: Accounts;
  checking?: CountRange;
  credit?: CountRange;
  months: 1 | 3 | 6;
  endMonth: string;
}): NormalizedRequest {
  const range = { min: 10, max: 20 };
  const req: GenerationRequest = {
    subjectType: opts.fixture === 'cafe-business' ? 'business' : 'personal',
    accounts: {
      ...(opts.accounts !== 'credit' && { checking: opts.checking ?? range }),
      ...(opts.accounts !== 'checking' && { credit: opts.credit ?? range }),
    },
    months: opts.months,
    endMonth: opts.endMonth,
    seed: opts.seed,
  };
  return normalizeRequest(req, TODAY);
}

export const signed = (t: ScheduledTxn): number => (t.direction === 'credit' ? t.amountCents : -t.amountCents);

export const postedIn = (t: ScheduledTxn, c: { start: string; end: string }): boolean =>
  t.postedDate >= c.start && t.postedDate <= c.end;

/** The card posts a payment at most this many days after checking sends it. */
const MAX_CARD_POSTING_LAG = 2;

/**
 * Both-accounts card-payment pairing violations (empty when consistent):
 * - every checking card payment matches a distinct credit PAYMENT of the same amount posted
 *   0–2 days later, unless its credit side may have posted after the last credit cycle;
 * - every credit PAYMENT sent (transactionDate) inside a checking cycle has its checking side.
 */
export function cardPaymentPairingViolations(txns: readonly ScheduledTxn[], norm: NormalizedRequest): string[] {
  const credits = txns.filter((t) => t.account === 'credit' && t.type === 'PAYMENT' && t.merchantKey === 'card-payment');
  const checkings = txns.filter((t) => t.account === 'checking' && t.merchantKey === 'card-payment');
  const lastCreditEnd = norm.cycles.credit!.at(-1)!.end;
  const out: string[] = [];
  const used = new Set<string>();
  for (const c of checkings) {
    const m = credits.find((p) => {
      if (used.has(p.id) || p.amountCents !== c.amountCents) return false;
      const gap = daysBetween(c.postedDate, p.postedDate);
      return gap >= 0 && gap <= MAX_CARD_POSTING_LAG;
    });
    if (m) used.add(m.id);
    else if (daysBetween(c.postedDate, lastCreditEnd) >= MAX_CARD_POSTING_LAG) out.push(`checking ${c.id} has no credit side`);
  }
  for (const p of credits) {
    if (!norm.cycles.checking!.some((cy) => p.transactionDate >= cy.start && p.transactionDate <= cy.end)) continue;
    const ok = checkings.some((c) => c.postedDate === p.transactionDate && c.amountCents === p.amountCents);
    if (!ok) out.push(`credit ${p.id} sent ${p.transactionDate} has no checking side`);
  }
  return out;
}
