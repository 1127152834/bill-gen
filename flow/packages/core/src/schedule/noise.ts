/**
 * Noise: the rows that make a statement look lived-in instead of authored.
 *
 * A schedule built purely from the model is *coherent* — every row has a reason, a regular cadence
 * and a round amount. Real statements are not: a refund lands nine days after the charge, the same
 * coffee shop charges twice on a Saturday and one of them comes back, the parking meter takes
 * $3.75, and once a quarter something big arrives that nobody budgeted for.
 *
 * This module runs *inside* each cycle, right after it has been filled, and adds:
 *
 * - **refund** — a card charge partly or fully reversed, same merchant key, 2–9 days later.
 * - **duplicate** — a same-day repeat of a card charge plus its reversal the next day.
 * - **misc** — sub-$15 debit-card noise (vending, toll, parking, laundry, a donation).
 * - **event** — at most one per period: urgent care, a brake job, a flight, a citation.
 *
 * Every row carries `priority: 4`, between protected rows (<= 2) and discretionary purchases (5),
 * so a cycle that overflows after injection drops *purchases* first and keeps the noise. Callers
 * trim back to the cycle's max afterwards; the balance pass in `schedule()` already runs after.
 */
import type { Cycle } from '../calendar.js';
import { addDays } from '../date.js';
import { longTailPool, type LongTailMerchant } from '../longtail.js';
import type { Rng } from '../rng.js';
import type { Transaction } from '../schemas/output.js';
import { postingDate, type IdAllocator } from './place.js';
import type { Account, Ledger } from './fill.js';
import type { ScheduledTxn } from './place.js';

/** Between protected rows (<= 2) and discretionary purchases (5). */
const NOISE_PRIORITY = 4;
/** Refunds per card cycle: at least one, sometimes two. */
const REFUNDS_PER_CYCLE = { min: 1, extra: 0.35 } as const;
/** Share of card cycles that carry a double charge with one reversal. */
const DUPLICATE_RATE = 0.3;
/** New debit-card noise rows per cycle, inclusive. */
const MISC_PER_CYCLE = { min: 1, max: 3 } as const;
/** A big surprise every so often, at most one per period. */
const EVENT_RATE = 0.55;

export type NoiseResult = {
  /** Rows this cycle gained, in insertion order; the caller may roll some back. */
  rows: ScheduledTxn[];
  notes: string[];
};

export type NoiseArgs = {
  ledger: Ledger;
  account: Account;
  cycle: Cycle;
  nextId: IdAllocator;
  rng: Rng;
};

/** Retail-looking cents: most amounts keep their drawn value, many snap to a price-tag tail. */
function ticketCents(m: LongTailMerchant, rng: Rng): number {
  const { min, max } = m.ticket;
  const drawn = rng.int(min, max);
  if (!rng.chance(0.55)) return drawn;
  const tail = rng.pick([0, 29, 49, 79, 99] as const);
  const snapped = Math.floor(drawn / 100) * 100 + tail;
  return snapped < min || snapped > max ? drawn : snapped;
}

function makeRow(
  args: NoiseArgs,
  m: LongTailMerchant,
  opts: {
    transactionDate: string;
    amountCents: number;
    type: Transaction['type'];
    direction: 'credit' | 'debit';
    merchantKey?: string;
  },
): ScheduledTxn | undefined {
  const { cycle, nextId, rng, account, ledger } = args;
  const posted = postingDate(opts.transactionDate, opts.type, rng);
  if (posted < cycle.start || posted > cycle.end) return undefined;
  const merchantKey = opts.merchantKey ?? m.key;
  const row: ScheduledTxn = {
    id: nextId('noise', merchantKey, opts.transactionDate),
    account,
    transactionDate: opts.transactionDate,
    postedDate: posted,
    type: opts.type,
    direction: opts.direction,
    amountCents: opts.amountCents,
    merchantKey,
    source: 'noise',
    priority: NOISE_PRIORITY,
  };
  ledger.add(row);
  return row;
}

/** A random day inside the cycle, at least `lead` days before its end so the posting lands. */
function dayIn(cycle: Cycle, rng: Rng, lead = 3): string {
  const span = Math.max(1, Math.min(26, Math.floor((Date.parse(cycle.end) - Date.parse(cycle.start)) / 86_400_000) - lead));
  return addDays(cycle.start, rng.int(0, span));
}

/** Debit-card noise: the rows a checking statement has that are neither bills nor ACH. */
function addMisc(args: NoiseArgs, added: ScheduledTxn[], notes: string[]): void {
  const pool = longTailPool('misc', args.account);
  const count = args.rng.int(MISC_PER_CYCLE.min, MISC_PER_CYCLE.max);
  for (let i = 0; i < count; i++) {
    const m = args.rng.pick(pool);
    const row = makeRow(args, m, {
      transactionDate: dayIn(args.cycle, args.rng),
      amountCents: ticketCents(m, args.rng),
      type: 'POS',
      direction: 'debit',
    });
    if (row) {
      added.push(row);
      notes.push(`${args.account} cycle ${args.cycle.index}: noise ${m.key} ${row.amountCents}`);
    }
  }
}

/** A charge that comes back: same merchant, same or partial amount, 2–9 days later. */
function addRefund(args: NoiseArgs, added: ScheduledTxn[], notes: string[]): void {
  const { ledger, cycle, rng } = args;
  const candidates = ledger
    .inCycle(args.account, cycle)
    .filter((t) => t.type === 'POS' && t.direction === 'debit' && t.source === 'discretionary');
  if (candidates.length === 0) return;
  const wanted = REFUNDS_PER_CYCLE.min + (rng.chance(REFUNDS_PER_CYCLE.extra) ? 1 : 0);
  const used = new Set<string>();
  for (let i = 0; i < wanted; i++) {
    const pool = candidates.filter((c) => !used.has(c.id));
    if (pool.length === 0) return;
    const original = rng.pick(pool);
    used.add(original.id);
    const when = addDays(original.transactionDate, rng.int(2, 9));
    if (when > cycle.end) continue;
    const partial = rng.chance(0.45);
    const amount = partial ? Math.max(1, Math.round(original.amountCents * (rng.int(30, 70) / 100))) : original.amountCents;
    const row = makeRow(args, { key: original.merchantKey } as LongTailMerchant, {
      transactionDate: when,
      amountCents: amount,
      type: 'REFUND',
      direction: 'credit',
    });
    if (row) {
      added.push(row);
      notes.push(`${args.account} cycle ${cycle.index}: refunded ${amount} of ${original.id}`);
    }
  }
}

/** Charged twice on the same day, one of them reversed the next. */
function addDuplicate(args: NoiseArgs, added: ScheduledTxn[], notes: string[]): void {
  const { ledger, cycle, rng } = args;
  const candidates = ledger
    .inCycle(args.account, cycle)
    .filter((t) => t.type === 'POS' && t.direction === 'debit' && t.source === 'discretionary');
  if (candidates.length === 0 || !rng.chance(DUPLICATE_RATE)) return;
  const original = rng.pick(candidates);
  const merchant = { key: original.merchantKey } as LongTailMerchant;
  const dup = makeRow(args, merchant, {
    transactionDate: original.transactionDate,
    amountCents: original.amountCents,
    type: 'POS',
    direction: 'debit',
  });
  if (!dup) return;
  const back = makeRow(args, merchant, {
    transactionDate: addDays(original.transactionDate, 1),
    amountCents: original.amountCents,
    type: 'REFUND',
    direction: 'credit',
  });
  if (!back) {
    ledger.remove(dup);
    return;
  }
  added.push(dup, back);
  notes.push(`${args.account} cycle ${cycle.index}: duplicate ${original.id} reversed ${back.postedDate}`);
}

/** One shock per period at most: the rows that make a month look nothing like the last one. */
function addEvent(args: NoiseArgs, added: ScheduledTxn[], notes: string[]): void {
  const { cycle, rng, account } = args;
  if (cycle.index !== 0 || account !== 'checking') return;
  if (!rng.chance(EVENT_RATE)) return;
  const m = rng.pick(longTailPool('event', account));
  const row = makeRow(args, m, {
    transactionDate: dayIn(cycle, rng, 6),
    amountCents: ticketCents(m, rng),
    type: rng.chance(0.5) ? 'CHECK' : 'ACH',
    direction: 'debit',
  });
  if (row) {
    added.push(row);
    notes.push(`${account} cycle ${cycle.index}: one-off ${m.key} ${row.amountCents}`);
  }
}

/**
 * Adds this cycle's noise rows to the ledger. Returns notes for the schedule log; the caller trims
 * the cycle back to its row range afterwards.
 */
export function injectCycleNoise(args: NoiseArgs): NoiseResult {
  const added: ScheduledTxn[] = [];
  const notes: string[] = [];
  addMisc(args, added, notes);
  if (args.account === 'credit') {
    addRefund(args, added, notes);
    addDuplicate(args, added, notes);
  }
  addEvent(args, added, notes);
  return { rows: added, notes };
}
