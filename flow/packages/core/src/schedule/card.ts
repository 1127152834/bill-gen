/**
 * Credit-card side of the schedule: statement balances, minimum due, interest, and the payment
 * that settles each statement (paired with a same-amount checking debit when checking exists).
 */
import { isBankBusinessDay, nextBankBusinessDay } from '../calendar.js';
import { addDays } from '../date.js';
import type { Rng } from '../rng.js';
import type { FinancialModel } from '../schemas/index.js';
import type { IdAllocator, ScheduledTxn } from './place.js';

export const CARD_PAYMENT_KEY = 'card-payment';
const INTEREST_KEY = 'interest';
/** Card payments and interest are never trimmed. */
const CARD_ROW_PRIORITY = 1;

const MIN_DUE_FLOOR_CENTS = 3500;
const MIN_DUE_BALANCE_SHARE = 0.01;
/** Statement due date = close + 25 days, rolled to a bank business day (same rule as `creditCycles`). */
const DUE_AFTER_CLOSE_DAYS = 25;
/** Checking pays this many days before the due date. */
const PAY_LEAD_DAYS = { min: 3, max: 10 } as const;
/** The card posts the payment this many days after checking sends it. */
const CREDIT_POSTING_LAG_DAYS = { min: 0, max: 2 } as const;
/** Share of the statement balance paid under the `partial` strategy. */
const PARTIAL_SHARE = { min: 0.3, max: 0.7 } as const;

export type CardStrategy = NonNullable<FinancialModel['cardPaymentStrategy']>;

/** max(35.00, 1% of balance + interest); the whole balance when it is below 35.00; 0 when nothing is owed. */
export function minimumDueCents(balanceCents: number, interestCents: number): number {
  if (balanceCents <= 0) return 0;
  if (balanceCents < MIN_DUE_FLOOR_CENTS) return balanceCents;
  return Math.min(
    balanceCents,
    Math.max(MIN_DUE_FLOOR_CENTS, Math.round(balanceCents * MIN_DUE_BALANCE_SHARE) + interestCents),
  );
}

/** One month of interest on the unpaid balance: unpaid × apr / 12 (0 when nothing is unpaid). */
export function interestCents(unpaidCents: number, apr: number): number {
  return unpaidCents > 0 ? Math.round((unpaidCents * apr) / 12) : 0;
}

/** `full` pays the statement balance; `partial` pays 30–70% of it, never below the minimum due. */
export function paymentCents(balanceCents: number, minDueCents: number, strategy: CardStrategy, rng: Rng): number {
  if (balanceCents <= 0) return 0;
  if (strategy === 'full') return balanceCents;
  const share = PARTIAL_SHARE.min + (PARTIAL_SHARE.max - PARTIAL_SHARE.min) * rng.float();
  return Math.min(balanceCents, Math.max(minDueCents, Math.round(balanceCents * share)));
}

/** Closing balance: previous statement + debits − credits posted in the cycle. */
export function statementBalanceCents(previousCents: number, cycleRows: readonly ScheduledTxn[]): number {
  return cycleRows.reduce((s, t) => s + (t.direction === 'debit' ? t.amountCents : -t.amountCents), previousCents);
}

export function dueDateFor(closeDate: string): string {
  return nextBankBusinessDay(addDays(closeDate, DUE_AFTER_CLOSE_DAYS));
}

function onOrBeforeBankBusinessDay(d: string): string {
  let cur = d;
  while (!isBankBusinessDay(cur)) cur = addDays(cur, -1);
  return cur;
}

export type CardPayment = { credit: ScheduledTxn; checking: ScheduledTxn };

/**
 * The payment of one statement: checking sends it `rng.int(3,10)` days before `dueDate`
 * (rolled back to a bank business day) as an ACH debit; the card posts a `PAYMENT` credit of
 * the same amount 0–2 days later, on a bank business day. Both sides are always built; the caller decides which to keep.
 */
export function cardPayment(dueDate: string, amountCents: number, rng: Rng, nextId: IdAllocator): CardPayment {
  const sent = onOrBeforeBankBusinessDay(addDays(dueDate, -rng.int(PAY_LEAD_DAYS.min, PAY_LEAD_DAYS.max)));
  // The card posts on a bank business day too; lag 0 always qualifies because `sent` is one.
  const lags: number[] = [];
  for (let k = CREDIT_POSTING_LAG_DAYS.min; k <= CREDIT_POSTING_LAG_DAYS.max; k++) {
    if (isBankBusinessDay(addDays(sent, k))) lags.push(k);
  }
  const posted = addDays(sent, rng.pick(lags));
  const common = {
    transactionDate: sent,
    amountCents,
    merchantKey: CARD_PAYMENT_KEY,
    source: 'card_payment',
    priority: CARD_ROW_PRIORITY,
  } as const;
  return {
    credit: {
      ...common,
      id: nextId('card_payment', CARD_PAYMENT_KEY, sent),
      account: 'credit',
      postedDate: posted,
      type: 'PAYMENT',
      direction: 'credit',
    },
    checking: {
      ...common,
      id: nextId('card_payment', CARD_PAYMENT_KEY, sent),
      account: 'checking',
      postedDate: sent,
      type: 'ACH',
      direction: 'debit',
    },
  };
}

/** Interest charge posted on the cycle's closing date. */
export function interestTxn(closeDate: string, amountCents: number, nextId: IdAllocator): ScheduledTxn {
  return {
    id: nextId('interest', INTEREST_KEY, closeDate),
    account: 'credit',
    transactionDate: closeDate,
    postedDate: closeDate,
    type: 'INTEREST',
    direction: 'debit',
    amountCents,
    merchantKey: INTEREST_KEY,
    source: 'interest',
    priority: CARD_ROW_PRIORITY,
  };
}
