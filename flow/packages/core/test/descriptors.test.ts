import { describe, expect, it } from 'vitest';
import { createRng, renderDescriptor, resolveTemplate, type DescriptorTemplate, type FinancialModel, type IncomeStream, type MerchantFact } from '../src/index.js';
import * as core from '../src/index.js';
import type { ScheduledTxn } from '../src/schedule/place.js';

const fact = (over: Partial<MerchantFact> = {}): MerchantFact => ({
  id: 'fact-joes-pizza-austin',
  name: "Joe's Pizza",
  city: 'Austin',
  state: 'TX',
  category: 'restaurant',
  priceItems: [],
  sourceUrl: 'https://www.joespizzaaustin.com/',
  ...over,
});

const txn = (over: Partial<ScheduledTxn> = {}): ScheduledTxn => ({
  id: 'discretionary-fact-joes-pizza-austin-2026-09-03-0',
  account: 'credit',
  transactionDate: '2026-09-03',
  postedDate: '2026-09-04',
  type: 'POS',
  direction: 'debit',
  amountCents: 1850,
  merchantKey: 'fact-joes-pizza-austin',
  source: 'discretionary',
  priority: 5,
  factId: 'fact-joes-pizza-austin',
  location: 'local',
  ...over,
});

const tpl = (template: string): DescriptorTemplate => ({ merchantKey: 'fact-joes-pizza-austin', template, memo: 'Dinner' });

describe('renderDescriptor', () => {
  it('is exported from the package entry point', () => {
    expect(core.renderDescriptor).toBe(renderDescriptor);
  });

  it('fills {CITY} and {ST} from the fact, uppercase', () => {
    const out = renderDescriptor(tpl('TST* JOES PIZZA {STORE} {CITY} {ST}'), { txn: txn(), fact: fact() }, createRng('s'));
    expect(out).toMatch(/^TST\* JOES PIZZA \d{4} AUSTIN TX$/);
    expect(out.length).toBeLessThanOrEqual(60);
  });

  it('keeps {STORE} fixed per merchantKey across calls, txns and rng positions', () => {
    const rng = createRng('seed-1');
    const a = renderDescriptor(tpl('{STORE}'), { txn: txn(), fact: fact() }, rng);
    rng.float();
    rng.float();
    const b = renderDescriptor(tpl('{STORE}'), { txn: txn({ id: 'other', transactionDate: '2026-09-20' }), fact: fact() }, rng);
    const c = renderDescriptor(tpl('{STORE}'), { txn: txn(), fact: fact() }, createRng('seed-1'));
    expect(a).toMatch(/^\d{4}$/);
    expect(b).toBe(a);
    expect(c).toBe(a);
  });

  it('gives different merchants (usually) different store numbers', () => {
    const stores = new Set(
      Array.from({ length: 12 }, (_, i) =>
        renderDescriptor(tpl('{STORE}'), { txn: txn({ merchantKey: `fact-m${i}` }), fact: fact() }, createRng('seed-1')),
      ),
    );
    expect(stores.size).toBeGreaterThan(6);
  });

  it('truncates {CITY} to 13 characters', () => {
    const out = renderDescriptor(tpl('{CITY}|'), { txn: txn(), fact: fact({ city: 'Fort Lauderdale Beach' }) }, createRng('s'));
    expect(out).toBe('FORT LAUDERDA|');
  });

  it('fills {MMDD} from the transaction date and {LAST4} from the linked account', () => {
    const out = renderDescriptor(tpl('PMT {MMDD} CARD ENDING {LAST4}'), { txn: txn(), linkedLast4: '4821' }, createRng('s'));
    expect(out).toBe('PMT 0903 CARD ENDING 4821');
  });

  it('draws a fresh 10-character alphanumeric {REF} per call, deterministically per rng', () => {
    const rng = createRng('ref');
    const a = renderDescriptor(tpl('{REF}'), { txn: txn(), fact: fact() }, rng);
    const b = renderDescriptor(tpl('{REF}'), { txn: txn(), fact: fact() }, rng);
    expect(a).toMatch(/^[A-Z0-9]{10}$/);
    expect(b).toMatch(/^[A-Z0-9]{10}$/);
    expect(a).not.toBe(b);
    expect(renderDescriptor(tpl('{REF}'), { txn: txn(), fact: fact() }, createRng('ref'))).toBe(a);
  });

  it('uppercases, collapses runs of spaces and trims', () => {
    const out = renderDescriptor(tpl("  Joe's   Pizza  {CITY}   {ST}  "), { txn: txn() }, createRng('s'));
    expect(out).toBe("JOE'S PIZZA");
  });

  it('truncates the whole descriptor to 60 characters', () => {
    const out = renderDescriptor(tpl('A'.repeat(50) + ' {CITY} {ST} {REF} {REF}'), { txn: txn(), fact: fact() }, createRng('s'));
    expect(out.length).toBeLessThanOrEqual(60);
    expect(out.startsWith('A'.repeat(50))).toBe(true);
    expect(out).toBe(out.toUpperCase());
  });
});

describe('renderDescriptor reference numbers (bank-style, not random letters on bills)', () => {
  it('{ID} is a 6-10 digit number fixed per merchantKey (account / customer number)', () => {
    const rng = createRng('ids');
    const a = renderDescriptor(tpl('COMED PAYMENT ID {ID}'), { txn: txn({ type: 'ACH' }) }, rng);
    rng.float();
    const b = renderDescriptor(tpl('COMED PAYMENT ID {ID}'), { txn: txn({ type: 'ACH', transactionDate: '2026-10-03' }) }, rng);
    expect(a).toMatch(/^COMED PAYMENT ID [1-9]\d{5,9}$/);
    expect(b).toBe(a);
    const others = new Set(
      Array.from({ length: 10 }, (_, i) => renderDescriptor(tpl('{ID}'), { txn: txn({ merchantKey: `k${i}` }) }, createRng('ids'))),
    );
    expect(others.size).toBeGreaterThan(5);
  });

  it('{REF} on an ACH row is digits only', () => {
    const out = renderDescriptor(tpl('ACME LLC VENDOR PMT {REF}'), { txn: txn({ type: 'ACH' }) }, createRng('ach'));
    expect(out).toMatch(/^ACME LLC VENDOR PMT \d{10}$/);
  });

  it('IRS estimated tax gets a 15-digit numeric trace starting 2706', () => {
    const out = renderDescriptor(tpl('IRS USATAXPYMT {REF}'), { txn: txn({ type: 'ACH' }) }, createRng('irs'));
    expect(out).toMatch(/^IRS USATAXPYMT 2706\d{11}$/);
  });

  it('{REF} stays alphanumeric on card, Zelle and ATM rows (order codes, confirmations, terminals)', () => {
    for (const type of ['POS', 'ZELLE', 'ATM'] as const) {
      expect(renderDescriptor(tpl('{REF}'), { txn: txn({ type }) }, createRng(`r-${type}`))).toMatch(/^[A-Z0-9]{10}$/);
    }
  });

  it('{STORE} is the researched store number when the fact has one', () => {
    const out = renderDescriptor(tpl('WAL-MART #{STORE} {CITY} {ST}'), { txn: txn(), fact: fact({ chain: 'Walmart', storeNumber: '5402' }) }, createRng('s'));
    expect(out).toBe('WAL-MART #5402 AUSTIN TX');
  });
});

describe('resolveTemplate for income: payer first, one word never repeated', () => {
  const stream = (channel: IncomeStream['channel'], payerName: string): IncomeStream => ({
    id: 'inc',
    label: 'Income',
    payerName,
    channel,
    cadence: { kind: 'monthly', day: 5 },
    amountCents: { min: 10000, max: 20000 },
    seasonality: Array(12).fill(1),
  });
  const render = (channel: IncomeStream['channel'], payerName: string): string => {
    const model = { incomeStreams: [stream(channel, payerName)] } as unknown as FinancialModel;
    const t = txn({ merchantKey: 'inc', source: 'income', direction: 'credit', type: channel === 'zelle' ? 'ZELLE' : 'ACH', account: 'checking' });
    return renderDescriptor(resolveTemplate(t, new Map(), model, undefined), { txn: t }, createRng('inc'));
  };

  it.each([
    ['ach_client', 'LUMEN CREATIVE', /^LUMEN CREATIVE PAYMENT$/],
    ['platform_payout', 'UBER', /^UBER PAYOUT$/],
    ['platform_payout', 'DOORDASH PAYOUT', /^DOORDASH PAYOUT$/],
    ['paypal', 'PAYPAL', /^PAYPAL TRANSFER$/],
    ['paypal', 'ETSY SHOP', /^PAYPAL TRANSFER$/],
    ['stripe', 'STRIPE', /^STRIPE TRANSFER ST-\d{6,10}$/],
    ['venmo', 'JANE DOE', /^VENMO CASHOUT$/],
    ['zelle', 'JANE DOE', /^ZELLE PAYMENT FROM JANE DOE [A-Z0-9]{10}$/],
    ['mobile_deposit', 'JANE DOE', /^MOBILE DEPOSIT$/],
  ] as const)('%s from %s', (channel, payer, expected) => {
    expect(render(channel, payer)).toMatch(expected);
  });
});
