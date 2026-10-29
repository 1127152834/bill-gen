/**
 * Occurrence placement: which dates recurring bills and income land on, and when they post.
 * Pure and deterministic — each item draws from its own forked `Rng` stream, so adding or
 * removing one item never changes the dates/amounts of the others.
 */
import { isBankBusinessDay, nextBankBusinessDay } from '../calendar.js';
import { addDays, addMonths, daysBetween, monthEnd, monthStart, weekday } from '../date.js';
import type { NormalizedRequest } from '../normalize.js';
import type { Rng } from '../rng.js';
import type { FinancialModel, IncomeStream, RecurringItem } from '../schemas/index.js';
import type { Transaction } from '../schemas/output.js';
import { incomeAmount, recurringAmount } from './amounts.js';

export type ScheduledTxn = {
  id: string;
  account: 'checking' | 'credit';
  transactionDate: string;
  postedDate: string;
  type: Transaction['type'];
  direction: 'credit' | 'debit';
  amountCents: number;
  merchantKey: string;
  source: 'recurring' | 'income' | 'discretionary' | 'card_payment' | 'interest' | 'fee' | 'atm';
  /** 1 = least deletable … 5 = most deletable; discretionary is always 5. */
  priority: number;
  factId?: string;
  location?: 'local' | 'online' | 'travel';
};

/** Fixed priority of income rows (kept above all but the most essential bills). */
const INCOME_PRIORITY = 2;

/** Types that move through the ACH/wire rails: the date itself rolls to a bank business day. */
export const ROLLING_TYPES: ReadonlySet<Transaction['type']> = new Set(['ACH', 'ZELLE', 'PAYMENT', 'DEPOSIT', 'TRANSFER']);
/** Card-style types that post 1–2 bank business days after the swipe. */
const CARD_TYPES: ReadonlySet<Transaction['type']> = new Set(['POS', 'REFUND']);

/** Stable id: `${source}-${merchantKey}-${transactionDate}-${n}`; `n` disambiguates same-day repeats. */
export function txnId(source: ScheduledTxn['source'], merchantKey: string, transactionDate: string, n: number): string {
  return `${source}-${merchantKey}-${transactionDate}-${n}`;
}

export type IdAllocator = (source: ScheduledTxn['source'], merchantKey: string, date: string) => string;

/** Per-call id allocator: first use of (source, key, date) gets n=0, then 1, 2 … */
export function idAllocator(): IdAllocator {
  const counts = new Map<string, number>();
  return (source, merchantKey, date) => {
    const k = `${source}|${merchantKey}|${date}`;
    const n = counts.get(k) ?? 0;
    counts.set(k, n + 1);
    return txnId(source, merchantKey, date, n);
  };
}

/** `n` bank business days strictly after `d`. */
export function addBankBusinessDays(d: string, n: number): string {
  let cur = d;
  for (let left = n; left > 0; left--) cur = nextBankBusinessDay(addDays(cur, 1));
  return cur;
}

/**
 * Posted date for a transaction made on `txnDate`.
 * - POS / REFUND (card activity): 1–2 bank business days later (a Saturday swipe posts Monday at the earliest).
 * - ACH / ZELLE / PAYMENT / TRANSFER / DEPOSIT / CHECK / ATM: the next bank business day on or after `txnDate`.
 * - FEE / INTEREST: same day.
 */
export function postingDate(txnDate: string, type: Transaction['type'], rng: Rng): string {
  if (CARD_TYPES.has(type)) return addBankBusinessDays(txnDate, rng.int(1, 2));
  if (type === 'FEE' || type === 'INTEREST') return txnDate;
  return nextBankBusinessDay(txnDate);
}

/** Calendar months (`YYYY-MM`) touched by the period, oldest first. */
function monthsInPeriod(norm: NormalizedRequest): string[] {
  const out: string[] = [];
  const last = norm.periodEnd.slice(0, 7);
  for (let ym = norm.periodStart.slice(0, 7); ym <= last; ym = addMonths(ym, 1)) out.push(ym);
  return out;
}

const inWindow = (d: string, norm: NormalizedRequest): boolean => d >= norm.periodStart && d <= norm.periodEnd;

/** Every date at `dow` (0=Sun) from `from` through `to`, stepping by `stepDays` (7 or 14). */
function weekdayDates(from: string, to: string, dow: number, stepDays: number, phaseDays = 0): string[] {
  const out: string[] = [];
  let d = addDays(from, (dow - weekday(from) + 7) % 7 + phaseDays);
  for (; d <= to; d = addDays(d, stepDays)) out.push(d);
  return out;
}

function recurringDates(item: RecurringItem, norm: NormalizedRequest): string[] {
  const rule = item.dayRule;
  switch (rule.kind) {
    case 'dayOfMonth':
      return monthsInPeriod(norm)
        .map((ym) => `${ym}-${String(rule.day).padStart(2, '0')}`)
        .filter((d) => inWindow(d, norm));
    case 'taxDates':
      return norm.taxDates.filter((d) => inWindow(d, norm));
    case 'biweekly': {
      // Every 14 days from the anchor, in both directions, restricted to the window.
      const k = Math.ceil(daysBetween(rule.anchor, norm.periodStart) / 14);
      const out: string[] = [];
      for (let d = addDays(rule.anchor, k * 14); d <= norm.periodEnd; d = addDays(d, 14)) out.push(d);
      return out;
    }
  }
}

const byDateThenId = (a: ScheduledTxn, b: ScheduledTxn): number =>
  a.transactionDate < b.transactionDate ? -1 : a.transactionDate > b.transactionDate ? 1 : a.id < b.id ? -1 : 1;

/**
 * Bills for every recurring item whose scheduled date falls in `[periodStart, periodEnd]`.
 * ACH-style types roll the date itself to the next bank business day (posted = that day);
 * card types keep the scheduled date and post via `postingDate`. Always debits.
 */
export function recurringOccurrences(model: FinancialModel, norm: NormalizedRequest, rng: Rng): ScheduledTxn[] {
  const nextId = idAllocator();
  const out: ScheduledTxn[] = [];

  for (const item of model.recurring) {
    const itemRng = rng.fork(`recurring/${item.id}`);
    const merchantKey = item.factId ?? item.id;
    for (const scheduled of recurringDates(item, norm)) {
      const transactionDate = ROLLING_TYPES.has(item.txnType) ? nextBankBusinessDay(scheduled) : scheduled;
      const month = Number(scheduled.slice(5, 7));
      out.push({
        id: nextId('recurring', merchantKey, transactionDate),
        account: item.account,
        transactionDate,
        postedDate: ROLLING_TYPES.has(item.txnType) ? transactionDate : postingDate(transactionDate, item.txnType, itemRng),
        type: item.txnType,
        direction: 'debit',
        amountCents: recurringAmount(item, month, itemRng),
        merchantKey,
        source: 'recurring',
        priority: item.priority,
        ...(item.factId !== undefined && { factId: item.factId }),
      });
    }
  }
  return out.sort(byDateThenId);
}

function incomeType(channel: IncomeStream['channel']): Transaction['type'] {
  if (channel === 'mobile_deposit') return 'DEPOSIT';
  if (channel === 'zelle') return 'ZELLE';
  return 'ACH';
}

function incomeDates(stream: IncomeStream, norm: NormalizedRequest, rng: Rng): string[] {
  const c = stream.cadence;
  switch (c.kind) {
    case 'weekly':
      return weekdayDates(norm.periodStart, norm.periodEnd, c.weekday, 7);
    case 'biweekly':
      // Anchors on the first matching weekday on/after periodStart, shifted by 0 or 7 days
      // (drawn from rng) so different streams do not all land on the same fortnight.
      return weekdayDates(norm.periodStart, norm.periodEnd, c.weekday, 14, rng.int(0, 1) * 7);
    case 'monthly':
      return monthsInPeriod(norm)
        .map((ym) => `${ym}-${String(c.day).padStart(2, '0')}`)
        .filter((d) => inWindow(d, norm));
    case 'irregular': {
      const out: string[] = [];
      for (const ym of monthsInPeriod(norm)) {
        const lo = monthStart(ym) < norm.periodStart ? norm.periodStart : monthStart(ym);
        const hi = monthEnd(ym) > norm.periodEnd ? norm.periodEnd : monthEnd(ym);
        const days: string[] = [];
        for (let d = lo; d <= hi; d = addDays(d, 1)) if (isBankBusinessDay(d)) days.push(d);
        // Draw the count, then sample distinct business days without replacement.
        const count = Math.min(rng.int(c.perMonth.min, c.perMonth.max), days.length);
        for (let i = 0; i < count; i++) out.push(days.splice(rng.int(0, days.length - 1), 1)[0]!);
      }
      return out;
    }
  }
}

/**
 * Deposits for every income stream, always into checking (so `[]` without a checking account).
 * Channel → type: mobile_deposit→DEPOSIT, zelle→ZELLE, everything else→ACH. Dates that fall
 * on a non-business day roll forward; the month's `seasonality` factor scales the amount, and a
 * month whose factor rounds the amount to 0 produces no deposit.
 */
export function incomeOccurrences(model: FinancialModel, norm: NormalizedRequest, rng: Rng): ScheduledTxn[] {
  if (!norm.request.accounts.checking) return [];
  const nextId = idAllocator();
  const out: ScheduledTxn[] = [];

  for (const stream of model.incomeStreams) {
    const streamRng = rng.fork(`income/${stream.id}`);
    for (const scheduled of incomeDates(stream, norm, streamRng)) {
      const amountCents = incomeAmount(stream, Number(scheduled.slice(5, 7)), streamRng);
      if (amountCents <= 0) continue;
      const transactionDate = nextBankBusinessDay(scheduled);
      out.push({
        id: nextId('income', stream.id, transactionDate),
        account: 'checking',
        transactionDate,
        postedDate: transactionDate,
        type: incomeType(stream.channel),
        direction: 'credit',
        amountCents,
        merchantKey: stream.id,
        source: 'income',
        priority: INCOME_PRIORITY,
        ...(stream.factId !== undefined && { factId: stream.factId }),
      });
    }
  }
  return out.sort(byDateThenId);
}
