/**
 * The scheduler: turns a `FinancialModel` into the dated, priced rows of every requested
 * statement, guaranteeing each account's per-cycle row count stays inside its `{min, max}`.
 *
 * Order of work:
 * 1. Recurring bills and income (Task 5), keeping only rows that post inside their own
 *    account's cycles.
 * 2. Credit cycles, oldest first: place the payment of the previous statement and any interest,
 *    settle the cycle's count (target → trim → fill), close the statement, and build the payment
 *    that settles it. Each side of a payment is kept only when its own date falls inside its own
 *    account's cycles, so a pair may be split at the period edges.
 * 3. Checking cycles: settle each cycle's count, then up to 5 rounds of overdraft handling and
 *    count fix-up until both hold.
 */
import type { Cycle } from '../calendar.js';
import { addDays } from '../date.js';
import type { NormalizedRequest } from '../normalize.js';
import type { Rng } from '../rng.js';
import type { CountRange, FinancialModel } from '../schemas/index.js';
import { OVERDRAFT_FEE_KEY, byPostingOrder, resolveOverdrafts, runningBalances } from './balance.js';
import {
  cardPayment,
  dueDateFor,
  interestCents,
  interestTxn,
  minimumDueCents,
  paymentCents,
  statementBalanceCents,
  type CardPayment,
} from './card.js';
import {
  Ledger,
  PROTECTED_PRIORITY,
  cycleOf,
  deletionOrder,
  fillCycle,
  fixCount,
  trimToTarget,
  type Account,
  type FillCtx,
} from './fill.js';
import { injectCycleNoise } from './noise.js';
import { idAllocator, incomeOccurrences, recurringOccurrences, type IdAllocator, type ScheduledTxn } from './place.js';

export type { ScheduledTxn } from './place.js';

export type CreditCycleBalance = { statementBalanceCents: number; minimumDueCents: number; interestChargedCents: number };

export type ScheduleOutput = {
  /** Every row of every requested account, in posting order (postedDate, credits first, transactionDate, id). */
  txns: ScheduledTxn[];
  balances: {
    checking?: { openingCents: number; byTxnId: Record<string, number> };
    credit?: { perCycle: CreditCycleBalance[] };
  };
  notes: string[];
};

export class ScheduleInfeasibleError extends Error {
  readonly code = 'SCHEDULE_INFEASIBLE';
  readonly target = 'lifeModel';
  readonly account?: Account;
  readonly cycleIndex?: number;
  /** Rows of priority <= 2 left in the offending cycle (overdraft fees included). */
  readonly mandatoryCount?: number;
  /** Of those, overdraft fees forced by a balance the model's flows cannot cover. */
  readonly overdraftFees?: number;

  constructor(
    message: string,
    where?: { account: Account; cycleIndex: number; mandatoryCount: number; overdraftFees: number },
  ) {
    super(message);
    this.name = 'ScheduleInfeasibleError';
    if (where) {
      this.account = where.account;
      this.cycleIndex = where.cycleIndex;
      this.mandatoryCount = where.mandatoryCount;
      this.overdraftFees = where.overdraftFees;
    }
  }
}

/** Rounds of overdraft handling + count fix-up before giving up. */
const MAX_FIX_ROUNDS = 5;
/** Purchases stop being drawn freely once a statement balance would pass this share of the limit. */
const CREDIT_CAP_SHARE = 0.9;

type Run = { model: FinancialModel; norm: NormalizedRequest; ledger: Ledger; nextId: IdAllocator; notes: string[] };

function infeasible(run: Run, account: Account, cycle: Cycle, why: string): ScheduleInfeasibleError {
  const rows = run.ledger.inCycle(account, cycle);
  return new ScheduleInfeasibleError(`${account} cycle ${cycle.index} (${cycle.start}..${cycle.end}): ${why}`, {
    account,
    cycleIndex: cycle.index,
    mandatoryCount: rows.filter((t) => t.priority <= PROTECTED_PRIORITY).length,
    overdraftFees: rows.filter((t) => t.merchantKey === OVERDRAFT_FEE_KEY).length,
  });
}

function fillCtx(run: Run, account: Account, rng: Rng): FillCtx {
  return {
    account,
    pool: run.model.discretionaryPool.filter((i) => i.account === account),
    ...(account === 'checking' && run.model.atm && { atm: run.model.atm }),
    taxRate: run.norm.location.combinedSalesTaxRate,
    rng,
    nextId: run.nextId,
  };
}

/**
 * Draws the cycle's target, trims required rows down to it, fills up to it, then lets the noise
 * layer take some of those slots back. Noise rows outrank discretionary purchases (priority 4 vs 5),
 * so a cycle that overflows drops a purchase, never the refund that came with it.
 */
function settleCycle(run: Run, ctx: FillCtx, cycle: Cycle, range: CountRange): void {
  const target = ctx.rng.int(range.min, range.max);
  for (const r of trimToTarget(run.ledger, ctx.account, cycle, target)) {
    run.notes.push(`${ctx.account} cycle ${cycle.index}: dropped ${r.merchantKey} posted ${r.postedDate} (priority ${r.priority}) to fit ${target} rows`);
  }
  if (run.ledger.inCycle(ctx.account, cycle).length > range.max) {
    throw infeasible(run, ctx.account, cycle, `priority <= ${PROTECTED_PRIORITY} rows alone exceed max ${range.max}`);
  }
  run.notes.push(...fillCycle(run.ledger, ctx, cycle, target));

  // One noise stream per (account, cycle): `fork` reseeds from the label, so cycle 2 must not
  // replay cycle 1's draws.
  // Noise competes for slots with discretionary purchases, never with a paired ATM + fee row:
  // dropping one of those would take two rows and break the cycle's count. Purchases give way
  // first; whatever still does not fit is handed back.
  const noise = injectCycleNoise({
    ledger: run.ledger,
    account: ctx.account,
    cycle,
    nextId: run.nextId,
    rng: ctx.rng.fork(`noise:${ctx.account}:${cycle.index}`),
  });
  run.notes.push(...noise.notes);
  for (let guard = 40; guard > 0; guard--) {
    const rows = run.ledger.inCycle(ctx.account, cycle);
    if (rows.length <= range.max) break;
    const victim = rows
      .filter((t) => t.priority > PROTECTED_PRIORITY && !run.ledger.isPaired(t))
      .sort(deletionOrder)[0];
    if (!victim) break;
    run.ledger.remove(victim);
    run.notes.push(`${ctx.account} cycle ${cycle.index}: dropped ${victim.merchantKey} (priority ${victim.priority}) to make room for noise`);
  }
  while (run.ledger.inCycle(ctx.account, cycle).length > range.max && noise.rows.length > 0) {
    const spare = noise.rows.pop()!;
    run.ledger.remove(spare);
    run.notes.push(`${ctx.account} cycle ${cycle.index}: rolled back noise ${spare.merchantKey} to stay within ${range.max} rows`);
  }
  if (run.ledger.inCycle(ctx.account, cycle).length < range.min) {
    throw infeasible(run, ctx.account, cycle, `no ${ctx.account} discretionary items to reach min ${range.min}`);
  }
}

/** Credit cycles oldest first; also adds the checking side of each card payment it can. */
function scheduleCredit(run: Run, rng: Rng): CreditCycleBalance[] {
  const { model, norm, ledger } = run;
  const cycles = norm.cycles.credit!;
  const checkingCycles = norm.cycles.checking;
  const strategy = model.cardPaymentStrategy ?? 'full';
  const apr = model.apr ?? 0;
  let previous = model.creditOpeningBalanceCents ?? 0;
  const cap = model.creditLimitCents === undefined ? undefined : Math.floor(model.creditLimitCents * CREDIT_CAP_SHARE);
  const ctx: FillCtx = {
    ...fillCtx(run, 'credit', rng),
    // Room left under the cap: the statement balance so far (previous + this cycle's rows) below it.
    ...(cap !== undefined && { room: (cycle: Cycle) => cap - statementBalanceCents(previous, ledger.inCycle('credit', cycle)) }),
  };

  const pay = (balance: number, minDue: number, dueDate: string): CardPayment | undefined => {
    const amount = paymentCents(balance, minDue, strategy, rng);
    return amount > 0 ? cardPayment(dueDate, amount, rng, run.nextId) : undefined;
  };

  /** The checking debit stays whenever its own date is inside a checking cycle (ruling: pairs may be split at period edges). */
  const keepCheckingSide = (p: CardPayment): void => {
    if (checkingCycles && cycleOf(checkingCycles, p.checking.postedDate)) ledger.add(p.checking);
  };

  let pending = pay(previous, minimumDueCents(previous, 0), dueDateFor(addDays(cycles[0]!.start, -1)));
  const perCycle: CreditCycleBalance[] = [];

  for (const cycle of cycles) {
    let paid = 0;
    if (pending) {
      keepCheckingSide(pending);
      if (cycleOf([cycle], pending.credit.postedDate)) {
        ledger.add(pending.credit);
        paid = pending.credit.amountCents;
      } else {
        run.notes.push(`credit: payment sent ${pending.credit.transactionDate} posts outside cycle ${cycle.index}; credit side omitted`);
      }
    }
    const interest = strategy === 'partial' ? interestCents(previous - paid, apr) : 0;
    if (interest > 0) ledger.add(interestTxn(cycle.end, interest, run.nextId));

    settleCycle(run, ctx, cycle, norm.request.accounts.credit!);

    const balance = statementBalanceCents(previous, ledger.inCycle('credit', cycle));
    const minDue = minimumDueCents(balance, interest);
    perCycle.push({ statementBalanceCents: balance, minimumDueCents: minDue, interestChargedCents: interest });
    pending = pay(balance, minDue, cycle.dueDate!);
    previous = balance;
  }
  if (pending) {
    keepCheckingSide(pending);
    run.notes.push(`credit: payment of the last statement posts on the card after the period; credit side omitted`);
  }
  return perCycle;
}

/** Checking cycles: settle counts, then alternate overdraft handling and count fix-up. */
function scheduleChecking(run: Run, rng: Rng): void {
  const cycles = run.norm.cycles.checking!;
  const range = run.norm.request.accounts.checking!;
  const ctx = fillCtx(run, 'checking', rng);
  for (const cycle of cycles) settleCycle(run, ctx, cycle, range);

  for (let round = 0; round < MAX_FIX_ROUNDS; round++) {
    if (!resolveOverdrafts(run, cycles, run.model.openingBalanceCents)) {
      throw new ScheduleInfeasibleError('checking: overdraft handling did not converge');
    }
    let changed = false;
    for (const cycle of cycles) {
      const fixed = fixCount(run.ledger, ctx, cycle, range);
      run.notes.push(...fixed.notes);
      if (!fixed.inRange) {
        throw infeasible(run, 'checking', cycle, `priority <= ${PROTECTED_PRIORITY} rows plus overdraft fees cannot fit ${range.min}..${range.max}`);
      }
      changed ||= fixed.changed;
    }
    if (!changed) return;
  }
  throw new ScheduleInfeasibleError(`checking: row counts did not settle within ${MAX_FIX_ROUNDS} rounds`);
}

/**
 * Builds every requested statement's rows. Deterministic for a given `rng` seed.
 * @throws ScheduleInfeasibleError when a cycle's priority <= 2 rows exceed the account's max,
 *   or the count / overdraft passes cannot converge.
 */
export function schedule(model: FinancialModel, norm: NormalizedRequest, rng: Rng): ScheduleOutput {
  const run: Run = { model, norm, ledger: new Ledger(), nextId: idAllocator(), notes: [] };

  const occurrences = [
    ...recurringOccurrences(model, norm, rng.fork('recurring')),
    ...incomeOccurrences(model, norm, rng.fork('income')),
  ];
  const kept = occurrences.filter((t) => {
    const cycles = norm.cycles[t.account];
    return cycles !== undefined && cycleOf(cycles, t.postedDate) !== undefined;
  });
  run.ledger.add(...kept);
  if (kept.length < occurrences.length) {
    run.notes.push(`dropped ${occurrences.length - kept.length} recurring/income rows posting outside their account's cycles`);
  }

  const perCycle = norm.cycles.credit ? scheduleCredit(run, rng.fork('credit')) : undefined;
  if (norm.cycles.checking) scheduleChecking(run, rng.fork('checking'));

  const txns = [...run.ledger.txns].sort(byPostingOrder);
  return {
    txns,
    balances: {
      ...(norm.cycles.checking && {
        checking: {
          openingCents: model.openingBalanceCents,
          byTxnId: runningBalances(txns.filter((t) => t.account === 'checking'), model.openingBalanceCents),
        },
      }),
      ...(perCycle && { credit: { perCycle } }),
    },
    notes: run.notes,
  };
}
