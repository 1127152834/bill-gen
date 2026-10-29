/**
 * Turns a `ScheduleOutput` into the public `Statement[]`: renders every description, attaches
 * merchant + evidence from the researched facts, and fills the per-cycle `MonthSummary`.
 *
 * The public `Transaction` schema carries no link back to the model, so the link the hard rules
 * need (merchantKey / source / factId / location) is returned beside the statements as `meta`,
 * keyed by transaction id. `runRules` takes it through its context.
 */
import type { Cycle } from './calendar.js';
import { renderDescriptor, resolveTemplate, type DescriptorTemplate } from './descriptors.js';
import type { NormalizedRequest } from './normalize.js';
import type { Rng } from './rng.js';
import type { ScheduleOutput, ScheduledTxn } from './schedule/index.js';
import type { FinancialModel, MerchantFact, MonthSummary, Statement, Transaction } from './schemas/index.js';

/** What a transaction was generated from; the rules use it to find the model item behind a row. */
export type TxnMeta = {
  merchantKey: string;
  source: ScheduledTxn['source'];
  factId?: string;
  location?: 'local' | 'online' | 'travel';
};

export type AssembleResult = { statements: Statement[]; meta: Record<string, TxnMeta> };

type Account = ScheduledTxn['account'];

const CARD_PAYMENT_KEY = 'card-payment';

const last4 = (rng: Rng, account: Account): string => String(rng.fork(`acct-${account}`).int(1000, 9999));

const dollars = (cents: number): string => `$${(cents / 100).toFixed(2)}`;

const PER_GALLON = /gallon|\bgal\b/i;

/**
 * The cents a row's price item should be near: a fixed bill's amount, the pre-tax ticket of a
 * purchase (fuel: the per-gallon price), else the row amount.
 */
function priceTarget(t: ScheduledTxn, model: FinancialModel, taxRate: number): { cents: number; perGallon: boolean } {
  if (t.source === 'recurring') {
    const item = model.recurring.find((r) => r.factId === t.factId);
    if (item?.amount.kind === 'fixed') return { cents: item.amount.cents, perGallon: false };
  }
  if (t.source === 'discretionary') {
    const item = model.discretionaryPool.find((d) => d.factId === t.factId);
    if (item?.pricing === 'fuel') return { cents: item.unitPriceCents ?? 0, perGallon: true };
    if (item?.taxable) return { cents: t.amountCents / (1 + taxRate), perGallon: false };
  }
  return { cents: t.amountCents, perGallon: false };
}

/** The fact's price item closest to the row (a per-gallon one for fuel), so the note quotes the right price. */
function priceFor(fact: MerchantFact, target: { cents: number; perGallon: boolean }): MerchantFact['priceItems'][number] | undefined {
  const pool = target.perGallon ? fact.priceItems.filter((p) => PER_GALLON.test(p.item)) : fact.priceItems;
  const candidates = pool.length > 0 ? pool : fact.priceItems;
  return candidates.reduce<MerchantFact['priceItems'][number] | undefined>(
    (best, p) => (best === undefined || Math.abs(p.priceCents - target.cents) < Math.abs(best.priceCents - target.cents) ? p : best),
    undefined,
  );
}

function evidenceFor(fact: MerchantFact, price: MerchantFact['priceItems'][number] | undefined): NonNullable<Transaction['evidence']> {
  const note = price
    ? `${fact.name}, ${fact.city}, ${fact.state}: ${price.item} ${dollars(price.priceCents)}`
    : `${fact.name} (${fact.category}), ${fact.city}, ${fact.state}`;
  return { factId: fact.id, sourceUrl: fact.sourceUrl, note };
}

const inCycle = (t: { postedDate: string }, c: Cycle): boolean => t.postedDate >= c.start && t.postedDate <= c.end;
const sum = (rows: readonly Transaction[], direction: Transaction['direction']): number =>
  rows.filter((t) => t.direction === direction).reduce((a, t) => a + t.amountCents, 0);

/** Checking months: each opens at the previous close (the first at `openingCents`). */
function checkingMonths(cycles: readonly Cycle[], txns: readonly Transaction[], openingCents: number): MonthSummary[] {
  let opening = openingCents;
  return cycles.map((c) => {
    const rows = txns.filter((t) => inCycle(t, c));
    const credits = sum(rows, 'credit');
    const debits = sum(rows, 'debit');
    const closing = opening + credits - debits;
    const month: MonthSummary = {
      cycleStart: c.start,
      cycleEnd: c.end,
      openingBalanceCents: opening,
      closingBalanceCents: closing,
      totalCreditsCents: credits,
      totalDebitsCents: debits,
      transactionCount: rows.length,
    };
    opening = closing;
    return month;
  });
}

/** Credit months: each opens at the previous statement balance (the first at `openingCents`). */
function creditMonths(
  cycles: readonly Cycle[],
  txns: readonly Transaction[],
  openingCents: number,
  perCycle: NonNullable<ScheduleOutput['balances']['credit']>['perCycle'],
): MonthSummary[] {
  let opening = openingCents;
  return cycles.map((c, i) => {
    const rows = txns.filter((t) => inCycle(t, c));
    const bal = perCycle[i];
    if (!bal || !c.dueDate) throw new Error(`assembleStatements: missing credit balances or due date for cycle ${i}`);
    const month: MonthSummary = {
      cycleStart: c.start,
      cycleEnd: c.end,
      openingBalanceCents: opening,
      closingBalanceCents: bal.statementBalanceCents,
      totalCreditsCents: sum(rows, 'credit'),
      totalDebitsCents: sum(rows, 'debit'),
      transactionCount: rows.length,
      credit: {
        statementBalanceCents: bal.statementBalanceCents,
        minimumDueCents: bal.minimumDueCents,
        dueDate: c.dueDate,
        interestChargedCents: bal.interestChargedCents,
      },
    };
    opening = bal.statementBalanceCents;
    return month;
  });
}

/**
 * Builds one statement per requested account (checking first, then credit).
 * @param templates one per merchantKey; fixed keys (card-payment, interest, fees, atm) and missing
 *   ones fall back to built-ins, see `resolveTemplate`.
 */
export function assembleStatements(
  out: ScheduleOutput,
  templates: DescriptorTemplate[],
  norm: NormalizedRequest,
  model: FinancialModel,
  facts: MerchantFact[],
  rng: Rng,
): AssembleResult {
  const factById = new Map(facts.map((f) => [f.id, f]));
  const templateByKey = new Map(templates.map((t) => [t.merchantKey, t]));
  const accounts: Account[] = (['checking', 'credit'] as const).filter((a) => norm.cycles[a] !== undefined);
  const lastFour = Object.fromEntries(accounts.map((a) => [a, last4(rng, a)])) as Partial<Record<Account, string>>;
  const descriptorRng = rng.fork('descriptors');
  const balance = out.balances.checking?.byTxnId;

  const meta: Record<string, TxnMeta> = {};
  const built = new Map<string, Transaction>();
  for (const t of out.txns) {
    const fact = t.factId !== undefined ? factById.get(t.factId) : undefined;
    const template = resolveTemplate(t, templateByKey, model, fact);
    const other = t.account === 'checking' ? lastFour.credit : lastFour.checking;
    const linkedLast4 = t.merchantKey === CARD_PAYMENT_KEY ? other : undefined;
    built.set(t.id, {
      id: t.id,
      transactionDate: t.transactionDate,
      postedDate: t.postedDate,
      description: renderDescriptor(template, { txn: t, ...(fact && { fact }), ...(linkedLast4 && { linkedLast4 }) }, descriptorRng),
      memo: template.memo,
      type: t.type,
      direction: t.direction,
      amountCents: t.amountCents,
      ...(t.account === 'checking' && balance?.[t.id] !== undefined && { runningBalanceCents: balance[t.id]! }),
      ...(fact && { merchant: { name: fact.name, category: fact.category, city: fact.city, state: fact.state } }),
      ...(fact && { evidence: evidenceFor(fact, priceFor(fact, priceTarget(t, model, norm.location.combinedSalesTaxRate))) }),
    });
    meta[t.id] = {
      merchantKey: t.merchantKey,
      source: t.source,
      ...(t.factId !== undefined && { factId: t.factId }),
      ...(t.location !== undefined && { location: t.location }),
    };
  }

  const statements = accounts.map((account): Statement => {
    const cycles = norm.cycles[account]!;
    const txns = out.txns.filter((t) => t.account === account).map((t) => built.get(t.id)!);
    const common = {
      // 行名交给渲染层：用模板自带的银行，flow 不再决定印哪家。
      bankName: '',
      accountLast4: lastFour[account]!,
      periodStart: cycles[0]!.start,
      periodEnd: cycles.at(-1)!.end,
      transactions: txns,
    };
    if (account === 'checking') {
      const openingCents = out.balances.checking?.openingCents ?? model.openingBalanceCents;
      const months = checkingMonths(cycles, txns, openingCents);
      return {
        ...common,
        accountType: norm.request.subjectType === 'business' ? 'business_checking' : 'checking',
        months,
        checking: { openingBalanceCents: openingCents, closingBalanceCents: months.at(-1)!.closingBalanceCents },
      };
    }
    if (model.creditLimitCents === undefined) throw new Error('assembleStatements: credit account requires model.creditLimitCents');
    const perCycle = out.balances.credit?.perCycle ?? [];
    return {
      ...common,
      accountType: 'credit',
      months: creditMonths(cycles, txns, model.creditOpeningBalanceCents ?? 0, perCycle),
      credit: { creditLimitCents: model.creditLimitCents, apr: model.apr ?? 0 },
    };
  });
  return { statements, meta };
}
