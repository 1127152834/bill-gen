/**
 * Per-cycle row counts: the mutable ledger, trimming required rows down to a cycle's target,
 * filling with weighted discretionary purchases and ATM withdrawal pairs, and the final count
 * fix-up. Nothing here throws on infeasibility — the orchestrator (index.ts) decides.
 */
import type { Cycle } from '../calendar.js';
import { addDays, weekday } from '../date.js';
import type { Rng } from '../rng.js';
import type { AtmConfig, CountRange, DiscretionaryItem } from '../schemas/index.js';
import { discretionaryAmount } from './amounts.js';
import { addBankBusinessDays, postingDate, type IdAllocator, type ScheduledTxn } from './place.js';

export type Account = ScheduledTxn['account'];

/** Rows at or below this priority are never deleted to make room. */
export const PROTECTED_PRIORITY = 2;
const DISCRETIONARY_PRIORITY = 5;
/** Slowest card posting, in bank business days (see `postingDate`). */
const MAX_CARD_POSTING_DAYS = 2;
/** Swipe-day draws per purchase before a day the merchant already has is accepted. */
const DAY_ATTEMPTS = 10;
/** Draws of a cheaper purchase when one would push the card past its room. */
const ROOM_ATTEMPTS = 6;

export const postedIn = (t: ScheduledTxn, c: Cycle): boolean => t.postedDate >= c.start && t.postedDate <= c.end;

export const cycleOf = (cycles: readonly Cycle[], date: string): Cycle | undefined =>
  cycles.find((c) => date >= c.start && date <= c.end);

/** Deletion order: highest priority number first, then the latest posting. */
export const deletionOrder = (a: ScheduledTxn, b: ScheduledTxn): number =>
  b.priority - a.priority || (a.postedDate === b.postedDate ? (a.id < b.id ? 1 : -1) : a.postedDate < b.postedDate ? 1 : -1);

/** Latest-posted first. */
export const latestFirst = (a: ScheduledTxn, b: ScheduledTxn): number =>
  a.postedDate === b.postedDate ? (a.id < b.id ? 1 : -1) : a.postedDate < b.postedDate ? 1 : -1;

/** Mutable txn store for one schedule() run. An ATM withdrawal and its fee are added and removed together. */
export class Ledger {
  readonly txns: ScheduledTxn[] = [];
  private readonly partner = new Map<string, string>();

  add(...ts: ScheduledTxn[]): void {
    this.txns.push(...ts);
  }

  addPair(a: ScheduledTxn, b: ScheduledTxn): void {
    this.txns.push(a, b);
    this.partner.set(a.id, b.id).set(b.id, a.id);
  }

  isPaired(t: ScheduledTxn): boolean {
    return this.partner.has(t.id);
  }

  /** Removes `t` and its pair partner, if any; returns how many rows were removed. */
  remove(t: ScheduledTxn): number {
    const ids = new Set([t.id]);
    const other = this.partner.get(t.id);
    if (other !== undefined) {
      ids.add(other);
      this.partner.delete(t.id);
      this.partner.delete(other);
    }
    const before = this.txns.length;
    for (let i = this.txns.length - 1; i >= 0; i--) if (ids.has(this.txns[i]!.id)) this.txns.splice(i, 1);
    return before - this.txns.length;
  }

  /** Swaps an unpaired row for its updated copy. */
  replace(old: ScheduledTxn, next: ScheduledTxn): void {
    this.txns[this.txns.findIndex((t) => t.id === old.id)] = next;
  }

  inCycle(account: Account, cycle: Cycle): ScheduledTxn[] {
    return this.txns.filter((t) => t.account === account && postedIn(t, cycle));
  }
}

/** What an account needs to draw extra rows into its cycles. */
export type FillCtx = {
  account: Account;
  /** Discretionary items for this account only. */
  pool: readonly DiscretionaryItem[];
  /** Checking only. */
  atm?: AtmConfig;
  taxRate: number;
  rng: Rng;
  nextId: IdAllocator;
  /**
   * Credit only: cents that may still be charged in `cycle` before the statement balance passes
   * its cap. A purchase above it is redrawn from cheaper items; the count guarantee still wins.
   */
  room?: (cycle: Cycle) => number;
};

/** Deletes rows (priority 5 → 3, latest first) until the cycle holds at most `target`; returns the removed rows. */
export function trimToTarget(ledger: Ledger, account: Account, cycle: Cycle, target: number): ScheduledTxn[] {
  const removed: ScheduledTxn[] = [];
  for (let rows = ledger.inCycle(account, cycle); rows.length > target; rows = ledger.inCycle(account, cycle)) {
    const victim = rows.filter((t) => t.priority > PROTECTED_PRIORITY).sort(deletionOrder)[0];
    if (!victim) break;
    ledger.remove(victim);
    removed.push(victim);
  }
  return removed;
}

const swipeDayCache = new Map<string, readonly string[]>();

/** Swipe days whose slowest card posting still lands inside the cycle. */
function swipeDays(cycle: Cycle): readonly string[] {
  const key = `${cycle.start}|${cycle.end}`;
  let days = swipeDayCache.get(key);
  if (!days) {
    const out: string[] = [];
    for (let d = cycle.start; d <= cycle.end; d = addDays(d, 1)) {
      if (addBankBusinessDays(d, MAX_CARD_POSTING_DAYS) <= cycle.end) out.push(d);
    }
    days = out;
    swipeDayCache.set(key, days);
  }
  return days;
}

/** A swipe day in the cycle; `weekendBias` is the chance of landing on a Saturday or Sunday. */
function pickSwipeDay(cycle: Cycle, weekendBias: number, rng: Rng): string {
  const days = swipeDays(cycle);
  const weekend = days.filter((d) => weekday(d) === 0 || weekday(d) === 6);
  const weekdays = days.filter((d) => weekday(d) !== 0 && weekday(d) !== 6);
  const useWeekend = weekend.length > 0 && (weekdays.length === 0 || rng.chance(weekendBias));
  return rng.pick(useWeekend ? weekend : weekdays);
}

/** `account|merchantKey|swipe day` of every purchase already in the ledger. */
const visitKey = (account: Account, merchantKey: string, day: string): string => `${account}|${merchantKey}|${day}`;

function visitDays(ledger: Ledger, account: Account): Set<string> {
  return new Set(ledger.txns.filter((t) => t.source === 'discretionary').map((t) => visitKey(account, t.merchantKey, t.transactionDate)));
}

/**
 * A swipe day the merchant has no visit on (basket shops: nor the day before or after): drawn
 * with the weekend bias up to `DAY_ATTEMPTS` times, then picked from the free days left (first
 * with the basket gap, then without it); only a merchant already visited every day repeats one.
 */
function pickVisitDay(item: DiscretionaryItem, ctx: FillCtx, cycle: Cycle, visited: ReadonlySet<string>): string {
  const clashes = (day: string, gap: number): boolean => {
    for (let k = -gap; k <= gap; k++) if (visited.has(visitKey(ctx.account, item.factId, addDays(day, k)))) return true;
    return false;
  };
  const gap = item.pricing === 'basket' ? 1 : 0;
  let day = pickSwipeDay(cycle, item.weekendBias, ctx.rng);
  for (let attempt = 1; attempt < DAY_ATTEMPTS && clashes(day, gap); attempt++) day = pickSwipeDay(cycle, item.weekendBias, ctx.rng);
  if (!clashes(day, gap)) return day;
  for (const g of gap > 0 ? [gap, 0] : [0]) {
    const free = swipeDays(cycle).filter((d) => !clashes(d, g));
    if (free.length > 0) return ctx.rng.pick(free);
  }
  return day;
}

function discretionaryTxn(item: DiscretionaryItem, ctx: FillCtx, cycle: Cycle, visited: ReadonlySet<string>): ScheduledTxn {
  const day = pickVisitDay(item, ctx, cycle, visited);
  return {
    id: ctx.nextId('discretionary', item.factId, day),
    account: ctx.account,
    transactionDate: day,
    postedDate: postingDate(day, 'POS', ctx.rng),
    type: 'POS',
    direction: 'debit',
    amountCents: discretionaryAmount(item, ctx.taxRate, ctx.rng),
    merchantKey: item.factId,
    source: 'discretionary',
    priority: DISCRETIONARY_PRIORITY,
    factId: item.factId,
    location: item.location,
  };
}

/** One ATM withdrawal (posts next bank business day) plus its same-day fee when `feeCents > 0`. */
function addAtmWithdrawal(ledger: Ledger, ctx: FillCtx, atm: AtmConfig, cycle: Cycle): void {
  const day = ctx.rng.pick(swipeDays(cycle));
  const posted = postingDate(day, 'ATM', ctx.rng);
  const withdrawal: ScheduledTxn = {
    id: ctx.nextId('atm', 'atm', day),
    account: ctx.account,
    transactionDate: day,
    postedDate: posted,
    type: 'ATM',
    direction: 'debit',
    amountCents: ctx.rng.pick(atm.amountChoicesCents),
    merchantKey: 'atm',
    source: 'atm',
    priority: DISCRETIONARY_PRIORITY,
  };
  if (atm.feeCents === 0) {
    ledger.add(withdrawal);
    return;
  }
  ledger.addPair(withdrawal, {
    id: ctx.nextId('fee', 'fee-atm', posted),
    account: ctx.account,
    transactionDate: posted,
    postedDate: posted,
    type: 'FEE',
    direction: 'debit',
    amountCents: atm.feeCents,
    merchantKey: 'fee-atm',
    source: 'fee',
    priority: DISCRETIONARY_PRIORITY,
  });
}

/** Visits relative to perMonth.max (an item with max 0 is used last). */
const fillRatio = (item: DiscretionaryItem, counts: ReadonlyMap<string, number>): number =>
  item.perMonth.max > 0 ? (counts.get(item.factId) ?? 0) / item.perMonth.max : Number.POSITIVE_INFINITY;

/** Items still below perMonth.max; once none is, the ones furthest below it (lowest visits / max). */
function candidates(pool: readonly DiscretionaryItem[], counts: ReadonlyMap<string, number>): { items: readonly DiscretionaryItem[]; pastMax: boolean } {
  const open = pool.filter((item) => (counts.get(item.factId) ?? 0) < item.perMonth.max);
  if (open.length > 0) return { items: open, pastMax: false };
  const lowest = Math.min(...pool.map((item) => fillRatio(item, counts)));
  return { items: pool.filter((item) => fillRatio(item, counts) === lowest), pastMax: true };
}

/** Lowest possible amount of one visit (pre-tax ticket minimum; fuel at 8 gallons). */
const cheapestCents = (item: DiscretionaryItem): number =>
  item.pricing === 'fuel' && item.unitPriceCents !== undefined ? item.unitPriceCents * 8 : item.ticketCents.min;

/**
 * Draws `n` weighted discretionary purchases into the cycle, honouring each item's
 * `perMonth.max`; once every item is at its max, the items furthest below their max are drawn.
 * A merchant gets at most one visit a day where the cycle allows (see `pickVisitDay`). With
 * `ctx.room`, a purchase that would pass the card's cap is redrawn from cheaper items.
 * Adds nothing when the pool is empty. Returns notes.
 */
export function addDiscretionary(ledger: Ledger, ctx: FillCtx, cycle: Cycle, n: number): string[] {
  if (n <= 0 || ctx.pool.length === 0) return [];
  const counts = new Map<string, number>();
  for (const t of ledger.inCycle(ctx.account, cycle)) {
    if (t.source === 'discretionary') counts.set(t.merchantKey, (counts.get(t.merchantKey) ?? 0) + 1);
  }
  const visited = visitDays(ledger, ctx.account);
  const notes = new Set<string>();
  for (let i = 0; i < n; i++) {
    const { items, pastMax } = candidates(ctx.pool, counts);
    if (pastMax) notes.add(`${ctx.account} cycle ${cycle.index}: discretionary pool hit every perMonth.max; drew past it`);
    const item = ctx.rng.weighted(items.map((it) => ({ item: it, weight: it.weight })));
    let txn = discretionaryTxn(item, ctx, cycle, visited);
    const room = ctx.room?.(cycle);
    if (room !== undefined && txn.amountCents > room) {
      txn = cheaperTxn(ctx, cycle, visited, room, txn);
      if (txn.amountCents > room) notes.add(`${ctx.account} cycle ${cycle.index}: a purchase passes the credit cap; no cheaper item fits`);
    }
    ledger.add(txn);
    visited.add(visitKey(ctx.account, txn.merchantKey, txn.transactionDate));
    counts.set(txn.merchantKey, (counts.get(txn.merchantKey) ?? 0) + 1);
  }
  return [...notes];
}

/** Redraws from the cheapest items (by lowest possible amount) until a purchase fits `room`; else the cheapest drawn. */
function cheaperTxn(ctx: FillCtx, cycle: Cycle, visited: ReadonlySet<string>, room: number, first: ScheduledTxn): ScheduledTxn {
  const byPrice = [...ctx.pool].sort((a, b) => cheapestCents(a) - cheapestCents(b));
  const affordable = byPrice.filter((item) => cheapestCents(item) <= room);
  const pool = affordable.length > 0 ? affordable : byPrice.slice(0, 1);
  let best = first;
  for (let attempt = 0; attempt < ROOM_ATTEMPTS; attempt++) {
    const item = ctx.rng.weighted(pool.map((it) => ({ item: it, weight: it.weight })));
    const txn = discretionaryTxn(item, ctx, cycle, visited);
    if (txn.amountCents < best.amountCents) best = txn;
    if (best.amountCents <= room) break;
  }
  return best;
}

/** Fills the cycle up to `target`: ATM pairs first (as `atm.perMonth` allows), then discretionary. */
export function fillCycle(ledger: Ledger, ctx: FillCtx, cycle: Cycle, target: number): string[] {
  let room = target - ledger.inCycle(ctx.account, cycle).length;
  if (ctx.atm && room > 0) {
    const size = ctx.atm.feeCents > 0 ? 2 : 1;
    const wanted = ctx.rng.int(ctx.atm.perMonth.min, ctx.atm.perMonth.max);
    for (let i = 0; i < wanted && room >= size; i++, room -= size) addAtmWithdrawal(ledger, ctx, ctx.atm, cycle);
  }
  return addDiscretionary(ledger, ctx, cycle, room);
}

/**
 * Final count fix-up. Above `range.max` it deletes discretionary purchases, then ATM pairs, then
 * any other deletable row in `trimToTarget` order (priority 5 → 3, latest first; never ≤ 2).
 * Below `range.min` it draws new purchases.
 */
export function fixCount(
  ledger: Ledger,
  ctx: FillCtx,
  cycle: Cycle,
  range: CountRange,
): { changed: boolean; inRange: boolean; notes: string[] } {
  let changed = false;
  let rows = ledger.inCycle(ctx.account, cycle);
  while (rows.length > range.max) {
    const victim =
      rows.filter((t) => t.source === 'discretionary').sort(latestFirst)[0] ??
      rows.filter((t) => t.priority > PROTECTED_PRIORITY).sort(deletionOrder)[0];
    if (!victim) break;
    ledger.remove(victim);
    changed = true;
    rows = ledger.inCycle(ctx.account, cycle);
  }
  const notes: string[] = [];
  if (rows.length < range.min && ctx.pool.length > 0) {
    notes.push(...addDiscretionary(ledger, ctx, cycle, range.min - rows.length));
    changed = true;
    rows = ledger.inCycle(ctx.account, cycle);
  }
  return { changed, inRange: rows.length >= range.min && rows.length <= range.max, notes };
}
