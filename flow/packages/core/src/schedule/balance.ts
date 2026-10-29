/**
 * Checking running balance and overdraft handling. Rows are applied in posting order, credits
 * before debits within a day. When a debit takes the balance below zero the scheduler first
 * defers the latest movable purchase to the next deposit day in the cycle; if that is not
 * possible (or not enough) it charges one OVERDRAFT FEE that day and drops one row in the same
 * cycle so the count is unchanged: a priority-5 row when there is one, otherwise the next row in
 * `trimToTarget` order (priority 4 → 3; never ≤ 2).
 */
import { isBankBusinessDay } from '../calendar.js';
import type { Cycle } from '../calendar.js';
import { addDays } from '../date.js';
import { PROTECTED_PRIORITY, cycleOf, deletionOrder, latestFirst, postedIn, type Ledger } from './fill.js';
import type { IdAllocator, ScheduledTxn } from './place.js';

export const OVERDRAFT_FEE_CENTS = 3500;
export const OVERDRAFT_FEE_KEY = 'fee-overdraft';
const DELETABLE_PRIORITY = 5;
/** Safety cap; each step moves a purchase or covers a new overdraft day, so real runs need far fewer. */
const MAX_STEPS = 2000;

/** Posting order: postedDate, credits before debits, then transactionDate, then id. */
export function byPostingOrder(a: ScheduledTxn, b: ScheduledTxn): number {
  if (a.postedDate !== b.postedDate) return a.postedDate < b.postedDate ? -1 : 1;
  if (a.direction !== b.direction) return a.direction === 'credit' ? -1 : 1;
  if (a.transactionDate !== b.transactionDate) return a.transactionDate < b.transactionDate ? -1 : 1;
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

const signed = (t: ScheduledTxn): number => (t.direction === 'credit' ? t.amountCents : -t.amountCents);

/** Balance after each row; `rows` must already be in posting order. */
export function runningBalances(rows: readonly ScheduledTxn[], openingCents: number): Record<string, number> {
  const out: Record<string, number> = {};
  let bal = openingCents;
  for (const t of rows) out[t.id] = bal += signed(t);
  return out;
}

/** First debit that takes the balance from >= 0 to < 0 on a day that has no overdraft fee yet. */
function firstUncoveredDip(rows: readonly ScheduledTxn[], openingCents: number): ScheduledTxn | undefined {
  const feeDays = new Set(rows.filter((t) => t.merchantKey === OVERDRAFT_FEE_KEY).map((t) => t.postedDate));
  let bal = openingCents;
  for (const t of rows) {
    const prev = bal;
    bal += signed(t);
    if (t.direction === 'debit' && prev >= 0 && bal < 0 && !feeDays.has(t.postedDate)) return t;
  }
  return undefined;
}

function previousBankBusinessDay(d: string): string {
  let cur = addDays(d, -1);
  while (!isBankBusinessDay(cur)) cur = addDays(cur, -1);
  return cur;
}

type Ctx = { ledger: Ledger; nextId: IdAllocator; notes: string[] };

/**
 * Moves the latest not-yet-moved discretionary purchase posted on or before `day` to the next
 * deposit day in the same cycle (swipe date = the bank business day before it, never earlier
 * than the original). Returns false when there is no such purchase or deposit.
 */
function deferPurchase(ctx: Ctx, rows: readonly ScheduledTxn[], cycle: Cycle, day: string, moved: Set<string>): boolean {
  const depositDay = rows.find((t) => t.direction === 'credit' && t.postedDate > day && t.postedDate <= cycle.end)?.postedDate;
  if (!depositDay) return false;
  const candidate = rows
    .filter((t) => t.source === 'discretionary' && postedIn(t, cycle) && t.postedDate <= day && !moved.has(t.id))
    .sort(latestFirst)[0];
  if (!candidate) return false;

  const swipe = previousBankBusinessDay(depositDay);
  const transactionDate = swipe > candidate.transactionDate ? swipe : candidate.transactionDate;
  const next: ScheduledTxn = {
    ...candidate,
    id: ctx.nextId('discretionary', candidate.merchantKey, transactionDate),
    transactionDate,
    postedDate: depositDay,
  };
  ctx.ledger.replace(candidate, next);
  moved.add(next.id);
  ctx.notes.push(`checking: deferred ${candidate.merchantKey} from ${candidate.postedDate} to ${depositDay} to avoid an overdraft`);
  return true;
}

/**
 * Adds the 35.00 fee on `day` and drops one row of the cycle: an unpaired priority-5 row (posted
 * after `day` first), else the first deletable row in `trimToTarget` order.
 */
function chargeOverdraft(ctx: Ctx, cycle: Cycle, day: string): void {
  const fee: ScheduledTxn = {
    id: ctx.nextId('fee', OVERDRAFT_FEE_KEY, day),
    account: 'checking',
    transactionDate: day,
    postedDate: day,
    type: 'FEE',
    direction: 'debit',
    amountCents: OVERDRAFT_FEE_CENTS,
    merchantKey: OVERDRAFT_FEE_KEY,
    source: 'fee',
    priority: 1,
  };
  ctx.ledger.add(fee);
  const rows = ctx.ledger.inCycle('checking', cycle);
  const single = rows.filter((t) => t.priority === DELETABLE_PRIORITY && !ctx.ledger.isPaired(t)).sort(latestFirst);
  const victim =
    single.find((t) => t.postedDate > day) ??
    single[0] ??
    rows.filter((t) => t.priority > PROTECTED_PRIORITY).sort(deletionOrder)[0];
  if (victim) ctx.ledger.remove(victim);
  ctx.notes.push(
    `checking: overdraft on ${day}; charged OVERDRAFT FEE` +
      (victim ? ` and dropped ${victim.merchantKey} posted ${victim.postedDate}` : ''),
  );
}

/**
 * Resolves every overdraft in the checking rows of `ledger` (see module doc). Returns false if
 * it did not converge within the step cap.
 */
export function resolveOverdrafts(ctx: Ctx, cycles: readonly Cycle[], openingCents: number): boolean {
  const deferTried = new Set<string>();
  const moved = new Set<string>();
  for (let step = 0; step < MAX_STEPS; step++) {
    const rows = ctx.ledger.txns.filter((t) => t.account === 'checking').sort(byPostingOrder);
    const dip = firstUncoveredDip(rows, openingCents);
    if (!dip) return true;
    const day = dip.postedDate;
    const cycle = cycleOf(cycles, day)!;
    if (!deferTried.has(day)) {
      deferTried.add(day);
      if (deferPurchase(ctx, rows, cycle, day, moved)) continue;
    }
    chargeOverdraft(ctx, cycle, day);
  }
  return false;
}
