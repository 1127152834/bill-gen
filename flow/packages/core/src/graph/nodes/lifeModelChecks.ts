/**
 * Code-side post-processing of a model-written FinancialModel: deterministic fix-ups
 * (`normalizeModel`) and the checks that send it back to the model (`checkModel`).
 */
import type { NormalizedRequest } from '../../normalize.js';
import type { FinancialModel, MerchantFact, RecurringItem } from '../../schemas/index.js';
import { HOUSING_CATEGORY } from './research.js';

type Account = 'checking' | 'credit';

/** Built-in merchantKeys of the scheduler; an income stream id must not collide with them. */
const RESERVED_KEYS = new Set(['card-payment', 'interest', 'fee-overdraft', 'fee-atm', 'atm']);
const WAGE_WORDS = /payroll|salary|wages|direct dep/i;
const PER_GALLON = /gallon|\bgal\b/i;
/** ACH company-name field: an income payer is shown cut to this many characters. */
const MAX_PAYER_LENGTH = 16;

/** Average rows per month of a biweekly rule (for money estimates). */
const BIWEEKLY_PER_MONTH = 2.17;
/** Most rows one statement cycle can hold for each cadence (the scheduler throws when protected rows exceed max). */
const WORST_PER_CYCLE = { weekly: 5, biweekly: 3, monthly: 1 } as const;
/** Expected monthly card spending must stay at or below this share of the credit limit. */
const MAX_CARD_SPEND_SHARE = 0.6;
/** Gallons per fill-up used to estimate fuel spending. */
const AVERAGE_GALLONS = 12;

/** Allowed priority band per recurring kind (ruling: obligations 1-2, utilities 2-3, subscriptions 3-4). */
const PRIORITY_BAND: Partial<Record<RecurringItem['kind'], [number, number]>> = {
  rent: [1, 2],
  loan: [1, 2],
  payroll: [1, 2],
  tax_estimated: [1, 2],
  insurance: [1, 2],
  utility: [2, 3],
  subscription: [3, 4],
};

/** Letters and digits only, lowercase: "Amazon.com" and "AMAZON COM" compare equal. */
const squash = (text: string): string => text.toLowerCase().replace(/[^a-z0-9]/g, '');
/** Equal, or one a prefix of the other when the shorter has 5+ characters ("AMAZON" ~ "Amazon.com", not "HEB" ~ "HEBERT"). */
const sameName = (a: string, b: string): boolean => {
  const [x, y] = [squash(a), squash(b)];
  const [short, long] = x.length <= y.length ? [x, y] : [y, x];
  return short.length > 0 && (short === long || (short.length >= 5 && long.startsWith(short)));
};

/** Same first word ("UBER USA" ~ "Uber", "DOORDASH INC" ~ "DoorDash"): a payer named like its evidence. */
const sameFirstWord = (a: string, b: string): boolean => {
  const first = (t: string): string => squash(t.trim().split(/\s+/)[0] ?? '');
  return first(a).length >= 3 && first(a) === first(b);
};

const clamp = (n: number, [lo, hi]: [number, number]): number => Math.min(hi, Math.max(lo, n));

/** A ticket range narrower than this (max / min) makes repeat visits cost the identical amount. */
const MIN_TICKET_SPREAD = 1.25;
/** …so it is widened to this: a visit is a whole order, not one menu item. */
const WIDENED_TICKET_SPREAD = 1.6;

type Discretionary = FinancialModel['discretionaryPool'][number];

/**
 * Fuel: per-gallon price from the fact (a 0 / null one counts as missing). Others: no
 * unitPriceCents, and a ticket range wide enough to vary.
 */
function normalizeDiscretionary(d: Discretionary, fact: MerchantFact | undefined): Discretionary {
  const { unitPriceCents, ...rest } = d;
  if (d.pricing === 'fuel') {
    const perGallon = fact?.priceItems.find((p) => PER_GALLON.test(p.item));
    if (perGallon) return { ...rest, unitPriceCents: perGallon.priceCents };
    return unitPriceCents ? { ...rest, unitPriceCents } : rest;
  }
  const { min, max } = d.ticketCents;
  return max < min * MIN_TICKET_SPREAD ? { ...rest, ticketCents: { min, max: Math.round(min * WIDENED_TICKET_SPREAD) } } : rest;
}

/** Deterministic fixes: priority bands, card bills as POS, discretionary pricing (see above), income payer / factId, credit settings from the request. */
export function normalizeModel(model: FinancialModel, norm: NormalizedRequest, facts: MerchantFact[]): FinancialModel {
  const factById = new Map(facts.map((f) => [f.id, f]));
  const out: FinancialModel = {
    ...model,
    recurring: model.recurring.map((r) => {
      const band = PRIORITY_BAND[r.kind];
      // A bill on the credit card is a card charge: ACH / CHECK / ZELLE never reach a card statement.
      const txnType = r.account === 'credit' ? 'POS' : r.txnType;
      return { ...r, txnType, ...(band && { priority: clamp(r.priority, band) }) };
    }),
    discretionaryPool: model.discretionaryPool.map((d) => normalizeDiscretionary(d, factById.get(d.factId))),
  };
  // An income factId is optional evidence; one that is unknown or names a merchant the subject pays is dropped.
  // A kept one is what the row cites, so the payer is renamed to it when the two disagree.
  const spending = new Set([...model.recurring.map((r) => r.factId), ...model.discretionaryPool.map((d) => d.factId)]);
  out.incomeStreams = model.incomeStreams.map(({ factId, ...s }) => {
    const fact = factId !== undefined && !spending.has(factId) ? factById.get(factId) : undefined;
    if (!fact) return s;
    const matches = [fact.name, fact.chain].some((n) => n !== undefined && (sameName(s.payerName, n) || sameFirstWord(s.payerName, n)));
    return matches ? { ...s, factId: fact.id } : { ...s, payerName: (fact.chain ?? fact.name).slice(0, MAX_PAYER_LENGTH).trim(), factId: fact.id };
  });
  if (norm.request.accounts.credit) {
    if (norm.closingDay !== undefined) out.closingDay = norm.closingDay;
    out.cardPaymentStrategy ??= 'full';
  }
  return out;
}


/** Average occurrences per month (money estimates). */
const monthlyCount = (rule: RecurringItem['dayRule'], norm: NormalizedRequest): number =>
  rule.kind === 'dayOfMonth' ? 1 : rule.kind === 'biweekly' ? BIWEEKLY_PER_MONTH : norm.taxDates.length > 0 ? 1 : 0;

/** Most occurrences in one cycle (row budget). */
const worstCycleCount = (rule: RecurringItem['dayRule'], norm: NormalizedRequest): number =>
  rule.kind === 'dayOfMonth' ? 1 : rule.kind === 'biweekly' ? WORST_PER_CYCLE.biweekly : norm.taxDates.length > 0 ? 1 : 0;

const averageCents = (a: RecurringItem['amount']): number => (a.kind === 'fixed' ? a.cents : (a.minCents + a.maxCents) / 2);

/**
 * Worst-case rows of priority <= 2 in one cycle of `account`, with a readable breakdown: weekly 5,
 * biweekly 3, monthly / dayOfMonth / taxDates 1, irregular perMonth.max, the card payment (1 on
 * checking when both accounts exist; 1 on the card) and interest on a partially paid card.
 */
export function protectedRows(model: FinancialModel, norm: NormalizedRequest, account: Account): { total: number; parts: string[] } {
  const parts: string[] = [];
  let total = 0;
  const add = (n: number, what: string) => {
    if (n <= 0) return;
    total += n;
    parts.push(`${what} ${n}`);
  };
  for (const r of model.recurring) {
    if (r.account === account && r.priority <= 2) add(worstCycleCount(r.dayRule, norm), r.id);
  }
  const bothAccounts = !!norm.request.accounts.checking && !!norm.request.accounts.credit;
  if (account === 'checking') {
    for (const s of model.incomeStreams) {
      const c = s.cadence;
      add(c.kind === 'irregular' ? c.perMonth.max : WORST_PER_CYCLE[c.kind], `income ${s.id}`);
    }
    if (bothAccounts) add(1, 'card payment');
  } else {
    add(1, 'card payment');
    if (model.cardPaymentStrategy === 'partial') add(1, 'interest');
  }
  return { total, parts };
}

/** Average cents of one visit (tax included; fuel at the average fill-up). */
function averageTicketCents(d: Discretionary, taxRate: number): number {
  if (d.pricing === 'fuel' && d.unitPriceCents !== undefined) return d.unitPriceCents * AVERAGE_GALLONS;
  return ((d.ticketCents.min + d.ticketCents.max) / 2) * (d.taxable ? 1 + taxRate : 1);
}

/**
 * Expected monthly card debits: credit recurring, plus discretionary purchases - the larger of
 * the planned visits (avg perMonth x avg ticket) and the rows the scheduler fills to reach the
 * account's middle count (target minus card bills, payment and interest) x the weight-averaged ticket.
 */
export function monthlyCardSpendCents(model: FinancialModel, norm: NormalizedRequest): number {
  const taxRate = norm.location.combinedSalesTaxRate;
  const pool = model.discretionaryPool.filter((d) => d.account === 'credit');
  const planned = pool.reduce((sum, d) => sum + ((d.perMonth.min + d.perMonth.max) / 2) * averageTicketCents(d, taxRate), 0);
  const cardBills = model.recurring.filter((r) => r.account === 'credit');
  const recurring = cardBills.reduce((sum, r) => sum + averageCents(r.amount) * monthlyCount(r.dayRule, norm), 0);
  const range = norm.request.accounts.credit;
  const totalWeight = pool.reduce((sum, d) => sum + d.weight, 0);
  let filled = 0;
  if (range && totalWeight > 0) {
    const fixedRows = cardBills.reduce((n, r) => n + monthlyCount(r.dayRule, norm), 0) + 1 + (model.cardPaymentStrategy === 'partial' ? 1 : 0);
    const rows = Math.max(0, (range.min + range.max) / 2 - fixedRows);
    const weightedTicket = pool.reduce((sum, d) => sum + d.weight * averageTicketCents(d, taxRate), 0) / totalWeight;
    filled = rows * weightedTicket;
  }
  return Math.round(Math.max(planned, filled) + recurring);
}

/** Rows the pool (and ATM pairs on checking) can supply in one cycle before any perMonth.max is passed. */
function poolCapacity(model: FinancialModel, account: Account): number {
  const pool = model.discretionaryPool.filter((d) => d.account === account).reduce((n, d) => n + d.perMonth.max, 0);
  const atm = account === 'checking' && model.atm ? model.atm.perMonth.max * (model.atm.feeCents > 0 ? 2 : 1) : 0;
  return pool + atm;
}

/** One month of fixed checking outflows (recurring items except estimated tax). */
export function monthlyFixedCheckingCents(model: FinancialModel, norm: NormalizedRequest): number {
  return Math.round(
    model.recurring
      .filter((r) => r.account === 'checking' && r.kind !== 'tax_estimated')
      .reduce((sum, r) => sum + averageCents(r.amount) * monthlyCount(r.dayRule, norm), 0),
  );
}

/** Every problem that makes the model unusable; [] when it is fine. */
export function checkModel(model: FinancialModel, norm: NormalizedRequest, facts: MerchantFact[]): string[] {
  const problems: string[] = [];
  const known = new Set(facts.map((f) => f.id));
  const requested = (a: Account): boolean => norm.request.accounts[a] !== undefined;
  const personal = norm.request.subjectType === 'personal';

  // Facts: known, and one model item per fact (the fact id is the row's merchantKey).
  const users = new Map<string, string[]>();
  const useFact = (factId: string, who: string) => {
    if (!known.has(factId)) problems.push(`${who} references factId "${factId}", which is not in the facts list; use only listed fact ids`);
    users.set(factId, [...(users.get(factId) ?? []), who]);
  };
  for (const r of model.recurring) useFact(r.factId, `recurring item "${r.id}"`);
  for (const d of model.discretionaryPool) useFact(d.factId, `discretionary item "${d.factId}"`);
  for (const [factId, who] of users) {
    if (who.length > 1) problems.push(`fact "${factId}" is used by ${who.length} model items (${who.join(', ')}); use each fact at most once`);
  }

  const spendingFacts = facts.filter((f) => users.has(f.id));
  const incomeIds = new Set<string>();
  for (const s of model.incomeStreams) {
    const merchant = spendingFacts.find((f) => sameName(f.name, s.payerName) || (f.chain !== undefined && sameName(f.chain, s.payerName)));
    if (merchant) {
      problems.push(`income stream "${s.id}" is paid by "${s.payerName}", a merchant the subject spends at (${merchant.id}); income comes from clients, platforms or processors`);
    }
    if (incomeIds.has(s.id) || known.has(s.id) || RESERVED_KEYS.has(s.id)) {
      problems.push(`income stream id "${s.id}" must be unique and must not equal a fact id or a reserved key (${[...RESERVED_KEYS].join(', ')})`);
    }
    incomeIds.add(s.id);
    if (personal && (WAGE_WORDS.test(s.payerName) || WAGE_WORDS.test(s.label))) {
      problems.push(`income stream "${s.id}" ("${s.label}" from "${s.payerName}") reads like wages / salary / payroll; a self-employed person has no employer`);
    }
  }

  for (const d of model.discretionaryPool) {
    if (d.pricing === 'fuel' && d.unitPriceCents === undefined) {
      problems.push(`discretionary item "${d.factId}" has pricing "fuel" but no unitPriceCents (price per gallon in cents)`);
    }
  }
  if (personal) {
    // A household pays for its home: rent, or a mortgage (kind "loan") to a researched servicer.
    const housing = facts.filter((f) => HOUSING_CATEGORY.test(f.category));
    const housingIds = new Set(housing.map((f) => f.id));
    if (housing.length > 0 && requested('checking') && !model.recurring.some((r) => r.kind === 'rent' || housingIds.has(r.factId))) {
      problems.push(
        `no rent or mortgage payment: a household pays for its home every month; add a recurring item (kind "rent", or "loan" for a mortgage) on one of the housing facts (${housing.map((f) => f.id).join(', ')})`,
      );
    }
    for (const r of model.recurring.filter((x) => x.kind === 'payroll')) {
      problems.push(`recurring item "${r.id}" has kind "payroll"; a self-employed personal persona has no payroll`);
    }
  }

  // Accounts.
  for (const [what, account] of [
    ...model.recurring.map((r) => [`recurring item "${r.id}"`, r.account] as const),
    ...model.discretionaryPool.map((d) => [`discretionary item "${d.factId}"`, d.account] as const),
  ]) {
    if (!requested(account)) problems.push(`${what} uses account "${account}", which was not requested`);
  }
  if (!requested('checking') && model.incomeStreams.length > 0) {
    problems.push('incomeStreams must be [] because no checking account was requested (income always lands in checking)');
  }
  if (!requested('checking') && model.atm) problems.push('atm must be omitted because no checking account was requested');
  for (const account of (['checking', 'credit'] as const).filter(requested)) {
    if (!model.discretionaryPool.some((d) => d.account === account)) {
      problems.push(`the discretionary pool has no item on the ${account} account, so its cycles cannot be filled`);
    }
  }

  // Row budget: worst-case protected rows must fit the account's max in every cycle.
  for (const account of (['checking', 'credit'] as const).filter(requested)) {
    const range = norm.request.accounts[account]!;
    const { total, parts } = protectedRows(model, norm, account);
    if (total > range.max) {
      problems.push(
        `${account}: up to ${total} rows of priority <= 2 in one cycle (${parts.join(', ')}), but the account allows max ${range.max} rows; ` +
          'use fewer or less frequent income deposits or fixed bills, a lower irregular perMonth.max, or lower some bills to priority 3-4',
      );
    }
    // Without room in the pool the scheduler draws past perMonth.max: repeat visits nobody makes.
    const capacity = poolCapacity(model, account);
    const optionalBills = model.recurring
      .filter((r) => r.account === account && r.priority > 2)
      .reduce((n, r) => n + worstCycleCount(r.dayRule, norm), 0);
    const fixedRows = total + optionalBills;
    const needed = range.max - fixedRows;
    if (model.discretionaryPool.some((d) => d.account === account) && capacity < needed) {
      problems.push(
        `${account}: the discretionary pool's perMonth.max values add up to ${capacity}${account === 'checking' && model.atm ? ' (ATM pairs included)' : ''}, ` +
          `but up to ${needed} purchase rows (max ${range.max} minus ${fixedRows} fixed rows) must be filled each cycle; raise perMonth.max values or add pool items`,
      );
    }
  }

  if (requested('checking')) {
    const fixed = monthlyFixedCheckingCents(model, norm);
    if (model.openingBalanceCents < fixed) {
      problems.push(`openingBalanceCents ${model.openingBalanceCents} is below one month of fixed checking outflows (about ${fixed}); raise it`);
    }
  }
  if (requested('credit') && (model.creditLimitCents === undefined || model.apr === undefined)) {
    problems.push('creditLimitCents and apr are required because a credit account was requested');
  }
  if (requested('credit') && model.creditLimitCents !== undefined) {
    const limit = model.creditLimitCents;
    const opening = model.creditOpeningBalanceCents ?? 0;
    if (opening > limit) {
      problems.push(`creditOpeningBalanceCents ${opening} is above the credit limit ${limit}; a card statement never opens over its limit`);
    }
    const spend = monthlyCardSpendCents(model, norm);
    if (spend > limit * MAX_CARD_SPEND_SHARE) {
      problems.push(
        `expected card spending of about ${spend} cents a month is above ${MAX_CARD_SPEND_SHARE * 100}% of the credit limit ${limit}; ` +
          'raise creditLimitCents or move spending off the card so balances stay well below the limit',
      );
    }
  }
  return problems;
}
