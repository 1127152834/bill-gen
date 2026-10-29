/**
 * Bank-style transaction descriptors. A template is written once per merchant (by the
 * descriptors node); code fills in the per-transaction details, so the same merchant always
 * reads the same way on a statement.
 */
import type { Rng } from './rng.js';
import type { FinancialModel, IncomeStream, MerchantFact } from './schemas/index.js';
import { longTailMerchant } from './longtail.js';
import type { ScheduledTxn } from './schedule/place.js';

/** Placeholders: {STORE} {CITY} {ST} {MMDD} {REF} {ID} {LAST4}. `memo` is plain English, used as-is. */
export type DescriptorTemplate = { merchantKey: string; template: string; memo: string };

export type DescriptorCtx = {
  txn: ScheduledTxn;
  fact?: MerchantFact;
  /** Last four digits of the other account of a card payment. */
  linkedLast4?: string;
};

const MAX_LENGTH = 60;
const MAX_CITY_LENGTH = 13;
const REF_LENGTH = 10;
const REF_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
const DIGITS = '0123456789';
/** IRS Direct Pay / EFTPS traces are 15 digits; Direct Pay ones start 2706. */
const IRS_TRACE_PREFIX = '2706';
const IRS_TRACE_LENGTH = 15;
const ID_LENGTH = { min: 6, max: 10 } as const;
const PLACEHOLDER = /\{(STORE|CITY|ST|MMDD|REF|ID|LAST4)\}/g;
/** A template for an IRS payment (its {REF} is the numeric trace). */
export const IRS_TEMPLATE = /\bIRS\b|USATAXPYMT/;

/**
 * Store number: 4 digits, a pure function of the rng's seed, the merchantKey and the month.
 *
 * A real person does not visit the same branch of a chain for three months straight, so the number
 * drifts per statement cycle; it stays put within a month so a double charge still looks like the
 * same till.
 */
function storeNumber(merchantKey: string, rng: Rng, month: string): string {
  return String(rng.fork(`${merchantKey}:${month}`).int(0, 9999)).padStart(4, '0');
}

/** Account / customer number: 6-10 digits (no leading 0), a pure function of the rng's seed and the merchantKey. */
function accountId(merchantKey: string, rng: Rng): string {
  const r = rng.fork(`id:${merchantKey}`);
  const length = r.int(ID_LENGTH.min, ID_LENGTH.max);
  return String(r.int(1, 9)) + draw(r, DIGITS, length - 1);
}

function draw(rng: Rng, alphabet: string, length: number): string {
  return Array.from({ length }, () => alphabet[rng.int(0, alphabet.length - 1)]).join('');
}

/**
 * Per-row reference: an IRS trace (15 digits from 2706) on IRS templates, digits on other ACH
 * rows, and 10 A-Z0-9 elsewhere (Amazon order codes, Zelle confirmations, ATM terminals).
 */
function reference(t: DescriptorTemplate, txn: ScheduledTxn, rng: Rng): string {
  if (IRS_TEMPLATE.test(t.template)) return IRS_TRACE_PREFIX + draw(rng, DIGITS, IRS_TRACE_LENGTH - IRS_TRACE_PREFIX.length);
  return draw(rng, txn.type === 'ACH' ? DIGITS : REF_ALPHABET, REF_LENGTH);
}

/**
 * Renders one transaction's description: placeholders filled, uppercased, runs of whitespace
 * collapsed, cut to 60 characters. `{STORE}` is the fact's researched store number, else fixed
 * per merchantKey (derived from `rng`'s seed, so pass the same base rng for every row), and so
 * is `{ID}`; `{REF}` consumes `rng` and differs per call.
 */
export function renderDescriptor(t: DescriptorTemplate, ctx: DescriptorCtx, rng: Rng): string {
  const { txn, fact, linkedLast4 } = ctx;
  const fill = (key: string): string => {
    switch (key) {
      case 'STORE':
        return fact?.storeNumber ?? storeNumber(txn.merchantKey, rng, txn.transactionDate.slice(0, 7));
      case 'ID':
        return accountId(txn.merchantKey, rng);
      case 'CITY':
        return (fact?.city ?? '').toUpperCase().slice(0, MAX_CITY_LENGTH);
      case 'ST':
        return fact?.state ?? '';
      case 'MMDD':
        return txn.transactionDate.slice(5, 7) + txn.transactionDate.slice(8, 10);
      case 'REF':
        return reference(t, txn, rng);
      default:
        return linkedLast4 ?? '';
    }
  };
  return t.template
    .replace(PLACEHOLDER, (_, key: string) => fill(key))
    .toUpperCase()
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, MAX_LENGTH)
    .trimEnd();
}

const BUILTIN: Record<string, { checking?: Omit<DescriptorTemplate, 'merchantKey'>; credit?: Omit<DescriptorTemplate, 'merchantKey'>; both?: Omit<DescriptorTemplate, 'merchantKey'> }> = {
  'card-payment': {
    checking: { template: 'ONLINE PMT TO CARD ENDING {LAST4}', memo: 'Credit card payment' },
    credit: { template: 'ONLINE PAYMENT - THANK YOU', memo: 'Payment received, thank you' },
  },
  interest: { both: { template: 'PURCHASE INTEREST CHARGED', memo: 'Interest charged on purchases' } },
  'fee-overdraft': { both: { template: 'OVERDRAFT FEE', memo: 'Overdraft fee' } },
  // Terminal references keep two same-day withdrawals (and their fees) from reading as duplicates.
  'fee-atm': { both: { template: 'ATM SURCHARGE {MMDD} {REF}', memo: 'ATM withdrawal fee' } },
  atm: { both: { template: 'ATM WITHDRAWAL {MMDD} {REF}', memo: 'Cash withdrawal' } },
};

/**
 * Income line per channel, as the depositing bank shows it: the payer's ACH company name first
 * (`{payer}`) for clients and platforms; processors and P2P cash-outs name only themselves.
 */
const INCOME_TEMPLATE: Record<IncomeStream['channel'], string> = {
  ach_client: '{payer} PAYMENT',
  platform_payout: '{payer} PAYOUT',
  stripe: 'STRIPE TRANSFER ST-{ID}',
  square: 'SQUARE INC TRANSFER',
  paypal: 'PAYPAL TRANSFER',
  venmo: 'VENMO CASHOUT',
  zelle: 'ZELLE PAYMENT FROM {payer} {REF}',
  mobile_deposit: 'MOBILE DEPOSIT',
  card_sales: '{payer} DEPOSIT',
};

/** The template with `{payer}` filled; payer words the rest of the line already says are left out. */
function incomeTemplate(stream: IncomeStream): string {
  const pattern = INCOME_TEMPLATE[stream.channel];
  const others = new Set(pattern.replace('{payer}', ' ').toUpperCase().split(/\s+/).filter(Boolean));
  const payer = stream.payerName
    .toUpperCase()
    .split(/\s+/)
    .filter((w) => w && !others.has(w))
    .join(' ');
  return pattern.replace('{payer}', payer).replace(/\s+/g, ' ').trim();
}

/**
 * The template for a row: the supplied one for its merchantKey, else a built-in for the fixed
 * keys (card-payment, interest, fee-overdraft, fee-atm, atm), else a plain fallback built from
 * the income stream / fact so every row still reads like a bank line.
 */
export function resolveTemplate(
  txn: ScheduledTxn,
  templates: ReadonlyMap<string, DescriptorTemplate>,
  model: FinancialModel,
  fact: MerchantFact | undefined,
): DescriptorTemplate {
  const supplied = templates.get(txn.merchantKey);
  if (supplied) return supplied;
  // The long tail carries its own descriptor: nobody researched these, so the table is the source.
  const longTail = longTailMerchant(txn.merchantKey);
  if (longTail) return { merchantKey: txn.merchantKey, template: longTail.template, memo: longTail.memo };
  const builtin = BUILTIN[txn.merchantKey];
  const chosen = builtin?.[txn.account] ?? builtin?.both;
  if (chosen) return { merchantKey: txn.merchantKey, ...chosen };

  const stream = txn.source === 'income' ? model.incomeStreams.find((s) => s.id === txn.merchantKey) : undefined;
  if (stream) {
    return { merchantKey: txn.merchantKey, template: incomeTemplate(stream), memo: `Deposit from ${stream.payerName}` };
  }
  if (fact) {
    const suffix = txn.type === 'ACH' ? 'ACH PMT' : '{CITY} {ST}';
    return { merchantKey: txn.merchantKey, template: `${fact.name} ${suffix}`, memo: `Payment to ${fact.name}` };
  }
  return { merchantKey: txn.merchantKey, template: txn.merchantKey, memo: '' };
}
