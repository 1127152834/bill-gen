/** Shared helpers for the assemble / validate tests (not a test file itself). */
import { readFileSync } from 'node:fs';
import {
  MerchantFactSchema,
  assembleStatements,
  createRng,
  normalizeRequest,
  schedule,
  type DescriptorTemplate,
  type FinancialModel,
  type GenerationRequest,
  type MerchantFact,
  type NormalizedRequest,
  type RuleContext,
  type Statement,
  type TxnMeta,
} from '../src/index.js';
import { TODAY, loadModel, type Accounts, type FixtureName } from './schedule-helpers.js';

export const BANK = 'Harborline Federal Savings';

const ADDRESS = {
  freelancer: { street: '1200 S Lamar Blvd', city: 'Austin', state: 'TX', zip: '78704' },
  rideshare: { street: '5400 Westheimer Rd', city: 'Houston', state: 'TX', zip: '77056' },
  'cafe-business': { street: '1500 Wynkoop St', city: 'Denver', state: 'CO', zip: '80202' },
} as const;

export function loadFacts(name: FixtureName): MerchantFact[] {
  const raw = JSON.parse(readFileSync(new URL(`./fixtures/facts/${name}.json`, import.meta.url), 'utf8')) as unknown[];
  return raw.map((f) => MerchantFactSchema.parse(f));
}

export function loadTemplates(name: FixtureName): DescriptorTemplate[] {
  return JSON.parse(readFileSync(new URL(`./fixtures/templates/${name}.json`, import.meta.url), 'utf8')) as DescriptorTemplate[];
}

/** Like `makeNorm`, but pins the persona to the fixture's city so `local` merchants match the persona state. */
export function makeNormAt(opts: {
  fixture: FixtureName;
  seed: string;
  accounts: Accounts;
  months: 1 | 3 | 6;
  endMonth?: string;
  checking?: { min: number; max: number };
  credit?: { min: number; max: number };
}): NormalizedRequest {
  const range = { min: 10, max: 20 };
  const address = ADDRESS[opts.fixture];
  const business = opts.fixture === 'cafe-business';
  const profile = business
    ? { legalName: 'Mile High Roasting Co LLC', entityType: 'LLC' as const, industry: 'Cafe', description: 'Neighborhood coffee shop', founded: '2019-04', employeeCount: 6, address }
    : {
        name: 'Jordan Reyes',
        age: 34,
        address,
        occupation: opts.fixture === 'freelancer' ? 'Freelance designer' : 'Rideshare driver',
        household: { maritalStatus: 'single' as const, children: 0 },
        hobbies: ['cycling'],
      };
  const req: GenerationRequest = {
    subjectType: business ? 'business' : 'personal',
    profile,
    accounts: {
      ...(opts.accounts !== 'credit' && { checking: opts.checking ?? range }),
      ...(opts.accounts !== 'checking' && { credit: opts.credit ?? range }),
    },
    months: opts.months,
    endMonth: opts.endMonth ?? '2026-09',
    seed: opts.seed,
  };
  return normalizeRequest(req, TODAY);
}

export type Built = {
  norm: NormalizedRequest;
  model: FinancialModel;
  facts: MerchantFact[];
  templates: DescriptorTemplate[];
  statements: Statement[];
  meta: Record<string, TxnMeta>;
  ctx: RuleContext;
};

/** fixture model → schedule → assemble. */
export function build(
  fixture: FixtureName,
  seed: string,
  accounts: Accounts = 'both',
  months: 1 | 3 | 6 = 3,
  ranges: { checking?: { min: number; max: number }; credit?: { min: number; max: number } } = {},
): Built {
  const model = loadModel(fixture);
  const facts = loadFacts(fixture);
  const templates = loadTemplates(fixture);
  const norm = makeNormAt({ fixture, seed, accounts, months, ...ranges });
  const out = schedule(model, norm, createRng(seed));
  const { statements, meta } = assembleStatements(out, templates, norm, model, facts, BANK, createRng(`${seed}/assemble`));
  return { norm, model, facts, templates, statements, meta, ctx: { norm, model, facts, meta } };
}

/**
 * Rebuilds every derived number (running balances, month summaries, statement openings/closings)
 * from `transactions`, so a test can edit one thing and know that only the targeted rule breaks.
 */
export function recompute(statements: Statement[], norm: NormalizedRequest, model: FinancialModel): Statement[] {
  return statements.map((s) => {
    const credit = s.accountType === 'credit';
    const cycles = (credit ? norm.cycles.credit : norm.cycles.checking)!;
    let bal = credit ? (model.creditOpeningBalanceCents ?? 0) : model.openingBalanceCents;
    const first = bal;
    const transactions = s.transactions.map((t) => {
      if (credit) return t;
      bal += t.direction === 'credit' ? t.amountCents : -t.amountCents;
      return { ...t, runningBalanceCents: bal };
    });
    let opening = first;
    const months = cycles.map((c, i) => {
      const rows = transactions.filter((t) => t.postedDate >= c.start && t.postedDate <= c.end);
      const credits = rows.filter((t) => t.direction === 'credit').reduce((a, t) => a + t.amountCents, 0);
      const debits = rows.filter((t) => t.direction === 'debit').reduce((a, t) => a + t.amountCents, 0);
      const closing = credit ? opening + debits - credits : opening + credits - debits;
      const old = s.months[i]!;
      const m = {
        ...old,
        openingBalanceCents: opening,
        closingBalanceCents: closing,
        totalCreditsCents: credits,
        totalDebitsCents: debits,
        transactionCount: rows.length,
        ...(old.credit && { credit: { ...old.credit, statementBalanceCents: closing } }),
      };
      opening = closing;
      return m;
    });
    return {
      ...s,
      transactions,
      months,
      ...(!credit && { checking: { openingBalanceCents: first, closingBalanceCents: opening } }),
    };
  });
}
