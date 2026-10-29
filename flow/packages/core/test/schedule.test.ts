import { describe, expect, it } from 'vitest';
import { addDays, createRng, nextBankBusinessDay, type DiscretionaryItem, type FinancialModel, type RecurringItem } from '../src/index.js';
import * as core from '../src/index.js';
import { ScheduleInfeasibleError, schedule } from '../src/schedule/index.js';
import { minimumDueCents } from '../src/schedule/card.js';
import { Ledger, addDiscretionary, type FillCtx } from '../src/schedule/fill.js';
import { idAllocator } from '../src/schedule/place.js';
import { cardPaymentPairingViolations, loadModel, makeNorm, postedIn, type FixtureName } from './schedule-helpers.js';

const SEEDS = ['alpha', 'bravo', 'charlie', 'delta', 'echo'];
const FIXTURES_ALL: FixtureName[] = ['freelancer', 'rideshare', 'cafe-business'];

describe('schedule', () => {
  it('is exported from the package entry point', () => {
    expect(core.schedule).toBe(schedule);
    expect(core.ScheduleInfeasibleError).toBe(ScheduleInfeasibleError);
  });

  it('puts exactly 8 txns in every cycle of both accounts for {min:8, max:8}', () => {
    const model = loadModel('freelancer');
    for (const seed of SEEDS) {
      const norm = makeNorm({
        fixture: 'freelancer',
        seed,
        accounts: 'both',
        checking: { min: 8, max: 8 },
        credit: { min: 8, max: 8 },
        months: 6,
        endMonth: '2026-09',
      });
      const out = schedule(model, norm, createRng(seed));
      for (const acct of ['checking', 'credit'] as const) {
        for (const cycle of norm.cycles[acct]!) {
          expect(out.txns.filter((t) => t.account === acct && postedIn(t, cycle)), `${seed} ${acct} ${cycle.index}`).toHaveLength(8);
        }
      }
    }
  });

  it('credit-only: card payments appear as credit PAYMENTs and nothing touches checking', () => {
    const model = loadModel('freelancer');
    const norm = makeNorm({ fixture: 'freelancer', seed: 'credit-only', accounts: 'credit', months: 3, endMonth: '2026-09' });
    const out = schedule(model, norm, createRng('credit-only'));
    expect(out.txns.filter((t) => t.account === 'checking')).toEqual([]);
    expect(out.balances.checking).toBeUndefined();
    const payments = out.txns.filter((t) => t.merchantKey === 'card-payment');
    expect(payments.length).toBeGreaterThan(0);
    for (const p of payments) {
      expect(p).toMatchObject({ account: 'credit', type: 'PAYMENT', direction: 'credit', source: 'card_payment' });
    }
  });

  it('full strategy: each statement is paid in full in the next cycle, from checking with a matching credit PAYMENT', () => {
    const model = loadModel('freelancer');
    const norm = makeNorm({ fixture: 'freelancer', seed: 'full-pay', accounts: 'both', months: 6, endMonth: '2026-09' });
    const out = schedule(model, norm, createRng('full-pay'));
    const cycles = norm.cycles.credit!;
    const perCycle = out.balances.credit!.perCycle;
    for (let i = 0; i < cycles.length - 1; i++) {
      const paid = out.txns.filter((t) => t.account === 'credit' && t.type === 'PAYMENT' && postedIn(t, cycles[i + 1]!));
      expect(paid.map((t) => t.amountCents)).toEqual([perCycle[i]!.statementBalanceCents]);
    }
    expect(cardPaymentPairingViolations(out.txns, norm)).toEqual([]);
    for (const t of out.txns.filter((x) => x.account === 'checking' && x.merchantKey === 'card-payment')) {
      expect(t).toMatchObject({ type: 'ACH', direction: 'debit', source: 'card_payment' });
    }
  });

  it('partial strategy: pays 30-70% (never below the minimum) and charges interest on the unpaid balance at cycle end', () => {
    const model = loadModel('rideshare');
    const norm = makeNorm({ fixture: 'rideshare', seed: 'partial', accounts: 'credit', months: 6, endMonth: '2026-09' });
    const out = schedule(model, norm, createRng('partial'));
    const cycles = norm.cycles.credit!;
    const perCycle = out.balances.credit!.perCycle;
    let prevStatement = model.creditOpeningBalanceCents!;
    cycles.forEach((cycle, i) => {
      const inCycle = out.txns.filter((t) => t.account === 'credit' && postedIn(t, cycle));
      const paid = inCycle.filter((t) => t.type === 'PAYMENT').reduce((s, t) => s + t.amountCents, 0);
      const unpaid = prevStatement - paid;
      const expectedInterest = unpaid > 0 ? Math.round((unpaid * model.apr!) / 12) : 0;
      const interest = inCycle.filter((t) => t.type === 'INTEREST');
      expect(perCycle[i]!.interestChargedCents).toBe(expectedInterest);
      if (expectedInterest > 0) {
        expect(interest).toHaveLength(1);
        expect(interest[0]).toMatchObject({ postedDate: cycle.end, amountCents: expectedInterest, merchantKey: 'interest' });
      }
      if (i > 0) {
        const prev = perCycle[i - 1]!;
        expect(paid).toBeGreaterThanOrEqual(prev.minimumDueCents);
        expect(paid).toBeLessThanOrEqual(prev.statementBalanceCents);
        if (prev.statementBalanceCents >= 3500 && prev.minimumDueCents < Math.round(prev.statementBalanceCents * 0.3)) {
          expect(paid).toBeGreaterThanOrEqual(Math.round(prev.statementBalanceCents * 0.3));
          expect(paid).toBeLessThanOrEqual(Math.round(prev.statementBalanceCents * 0.7));
        }
      }
      prevStatement = perCycle[i]!.statementBalanceCents;
    });
    expect(perCycle.some((c) => c.interestChargedCents > 0)).toBe(true);
  });

  it('defaults to full payment and a zero opening card balance', () => {
    const model = loadModel('cafe-business');
    expect(model.cardPaymentStrategy).toBeUndefined();
    expect(model.creditOpeningBalanceCents).toBeUndefined();
    const norm = makeNorm({ fixture: 'cafe-business', seed: 'defaults', accounts: 'credit', months: 3, endMonth: '2026-09' });
    const out = schedule(model, norm, createRng('defaults'));
    expect(out.txns.filter((t) => t.type === 'INTEREST')).toEqual([]);
    const first = norm.cycles.credit![0]!;
    const firstCycle = out.txns.filter((t) => t.account === 'credit' && postedIn(t, first));
    expect(firstCycle.some((t) => t.type === 'PAYMENT')).toBe(false);
    // Debits minus refunds: the noise layer may reverse part of a purchase.
    const net = firstCycle.reduce((s, t) => s + (t.direction === 'debit' ? t.amountCents : -t.amountCents), 0);
    expect(out.balances.credit!.perCycle[0]!.statementBalanceCents).toBe(net);
  });

  it('posts ATM withdrawals on the next bank business day with a same-day ATM fee', () => {
    const model = loadModel('rideshare');
    const norm = makeNorm({
      fixture: 'rideshare',
      seed: 'atm',
      accounts: 'checking',
      checking: { min: 25, max: 30 },
      months: 3,
      endMonth: '2026-09',
    });
    const out = schedule(model, norm, createRng('atm'));
    const atms = out.txns.filter((t) => t.type === 'ATM');
    const fees = out.txns.filter((t) => t.merchantKey === 'fee-atm');
    expect(atms.length).toBeGreaterThan(0);
    expect(fees).toHaveLength(atms.length);
    for (const a of atms) {
      expect(a.postedDate).toBe(nextBankBusinessDay(a.transactionDate));
      expect(model.atm!.amountChoicesCents).toContain(a.amountCents);
      expect(fees.some((f) => f.postedDate === a.postedDate && f.amountCents === model.atm!.feeCents)).toBe(true);
    }
    for (const f of fees) expect(f).toMatchObject({ type: 'FEE', direction: 'debit' });
  });

  it('charges a 35.00 overdraft fee when the balance runs out, keeping counts in range', () => {
    const base = loadModel('freelancer');
    const model: FinancialModel = {
      ...base,
      openingBalanceCents: 0,
      incomeStreams: base.incomeStreams.map((s) => ({ ...s, amountCents: { min: 20000, max: 30000 } })),
    };
    const range = { min: 10, max: 20 };
    const norm = makeNorm({ fixture: 'freelancer', seed: 'broke', accounts: 'checking', checking: range, months: 3, endMonth: '2026-09' });
    const out = schedule(model, norm, createRng('broke'));
    const fees = out.txns.filter((t) => t.merchantKey === 'fee-overdraft');
    expect(fees.length).toBeGreaterThan(0);
    for (const f of fees) expect(f).toMatchObject({ type: 'FEE', direction: 'debit', amountCents: 3500, account: 'checking' });
    for (const cycle of norm.cycles.checking!) {
      const n = out.txns.filter((t) => t.account === 'checking' && postedIn(t, cycle)).length;
      expect(n).toBeGreaterThanOrEqual(range.min);
      expect(n).toBeLessThanOrEqual(range.max);
    }
    expect(out.notes.some((n) => /overdraft/i.test(n))).toBe(true);
  });

  it('throws ScheduleInfeasibleError when priority 1 bills alone exceed max', () => {
    const bill = (day: number): RecurringItem => ({
      id: `bill-${day}`,
      label: `Bill ${day}`,
      kind: 'other',
      dayRule: { kind: 'dayOfMonth', day },
      amount: { kind: 'fixed', cents: 10000 },
      account: 'checking',
      priority: 1,
      factId: `fact-bill-${day}`,
      txnType: 'ACH',
    });
    const model: FinancialModel = { ...loadModel('freelancer'), recurring: [3, 8, 13, 18, 23].map(bill) };
    const norm = makeNorm({
      fixture: 'freelancer',
      seed: 'too-many',
      accounts: 'checking',
      checking: { min: 3, max: 4 },
      months: 1,
      endMonth: '2026-09',
    });
    let caught: unknown;
    try {
      schedule(model, norm, createRng('too-many'));
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(ScheduleInfeasibleError);
    expect(caught).toMatchObject({ code: 'SCHEDULE_INFEASIBLE', target: 'lifeModel', account: 'checking', cycleIndex: 0 });
  });
});

describe('schedule regressions', () => {
  it('keeps the checking side of the last statement payment when only its credit side falls after the period', () => {
    const model = loadModel('freelancer');
    const norm = makeNorm({ fixture: 'freelancer', seed: 'pay-7', accounts: 'both', months: 3, endMonth: '2026-09' });
    expect(norm.closingDay).toBe(3); // last statement closes 2026-09-03; its payment is sent mid/late September
    const out = schedule(model, norm, createRng('pay-7'));
    const lastClose = norm.cycles.credit!.at(-1)!.end;
    const lastPayment = out.txns.filter((t) => t.account === 'checking' && t.merchantKey === 'card-payment' && t.postedDate > lastClose);
    expect(lastPayment).toHaveLength(1);
    expect(lastPayment[0]!.amountCents).toBe(out.balances.credit!.perCycle.at(-1)!.statementBalanceCents);
    expect(out.txns.some((t) => t.account === 'credit' && t.postedDate > lastClose)).toBe(false);
    expect(cardPaymentPairingViolations(out.txns, norm)).toEqual([]);
  });

  // Reviewer repros: overdraft fees in cycles with no priority-5 rows used to push the count over max.
  it.each([
    { fixture: 'cafe-business', seed: 's3611', checking: { min: 3, max: 10 }, months: 3, endMonth: '2025-12' },
    { fixture: 'freelancer', seed: 's3841', checking: { min: 3, max: 7 }, months: 1, endMonth: '2026-09' },
  ] as const)('$fixture seed $seed with a zero opening balance stays within the count range', (c) => {
    const model: FinancialModel = { ...loadModel(c.fixture as FixtureName), openingBalanceCents: 0 };
    const norm = makeNorm({ ...c, fixture: c.fixture as FixtureName, accounts: 'checking' });
    const out = schedule(model, norm, createRng(c.seed));
    expect(out.txns.some((t) => t.merchantKey === 'fee-overdraft')).toBe(true);
    for (const cycle of norm.cycles.checking!) {
      const n = out.txns.filter((t) => postedIn(t, cycle)).length;
      expect(n).toBeGreaterThanOrEqual(c.checking.min);
      expect(n).toBeLessThanOrEqual(c.checking.max);
    }
  });
});

describe('discretionary visits (realism)', () => {
  const runs = FIXTURES_ALL.flatMap((fixture) => SEEDS.map((seed) => ({ fixture, seed })));
  const visits = (fixture: FixtureName, seed: string) => {
    const norm = makeNorm({ fixture, seed, accounts: 'both', months: 3, endMonth: '2026-09' });
    return { model: loadModel(fixture), txns: schedule(loadModel(fixture), norm, createRng(seed)).txns.filter((t) => t.source === 'discretionary') };
  };

  it('never puts two visits to one merchant on the same day', () => {
    for (const { fixture, seed } of runs) {
      const seen = new Set<string>();
      for (const t of visits(fixture, seed).txns) {
        const key = `${t.account}|${t.merchantKey}|${t.transactionDate}`;
        expect(seen.has(key), `${fixture}/${seed}: ${key}`).toBe(false);
        seen.add(key);
      }
    }
  });

  it('keeps grocery-style (basket) visits to one merchant at least a day apart', () => {
    for (const { fixture, seed } of runs) {
      const { model, txns } = visits(fixture, seed);
      const basket = new Set(model.discretionaryPool.filter((d) => d.pricing === 'basket').map((d) => `${d.account}|${d.factId}`));
      const days = new Set(txns.map((t) => `${t.account}|${t.merchantKey}|${t.transactionDate}`));
      for (const t of txns.filter((x) => basket.has(`${x.account}|${x.merchantKey}`))) {
        expect(days.has(`${t.account}|${t.merchantKey}|${addDays(t.transactionDate, 1)}`), `${fixture}/${seed}: ${t.merchantKey} ${t.transactionDate}`).toBe(false);
      }
    }
  });

  it('past every perMonth.max, draws the merchant furthest below its max (visits / max)', () => {
    const norm = makeNorm({ fixture: 'freelancer', seed: 'past-max', accounts: 'credit', months: 1, endMonth: '2026-09' });
    const item = (factId: string, max: number, weight: number): DiscretionaryItem => ({
      factId, weight, perMonth: { min: 0, max }, ticketCents: { min: 500, max: 900 }, pricing: 'fixed_menu', taxable: true, tip: false, account: 'credit', weekendBias: 0.3, location: 'local',
    });
    const ctx: FillCtx = { account: 'credit', pool: [item('a', 2, 100), item('b', 10, 1)], taxRate: 0.0825, rng: createRng('past-max'), nextId: idAllocator() };
    const ledger = new Ledger();
    addDiscretionary(ledger, ctx, norm.cycles.credit![0]!, 18);
    const count = (k: string) => ledger.txns.filter((t) => t.merchantKey === k).length;
    // 12 fill both maxes; the 6 past them keep the ratios level: a 3 (1.5), b 15 (1.5).
    expect([count('a'), count('b')]).toEqual([3, 15]);
  });
});

describe('credit limit (I9)', () => {
  it('keeps every statement balance within the credit limit, drawing cheaper purchases near it', () => {
    for (const seed of SEEDS) {
      const model: FinancialModel = { ...loadModel('freelancer'), creditLimitCents: 60_000, creditOpeningBalanceCents: 0 };
      const norm = makeNorm({ fixture: 'freelancer', seed, accounts: 'both', months: 3, endMonth: '2026-09', credit: { min: 10, max: 14 } });
      const out = schedule(model, norm, createRng(seed));
      for (const c of out.balances.credit!.perCycle) expect(c.statementBalanceCents, seed).toBeLessThanOrEqual(60_000);
      for (const cycle of norm.cycles.credit!) {
        const n = out.txns.filter((t) => t.account === 'credit' && postedIn(t, cycle)).length;
        expect(n).toBeGreaterThanOrEqual(10);
        expect(n).toBeLessThanOrEqual(14);
      }
    }
  });
});

describe('minimumDueCents', () => {
  it('is the whole balance below 35.00', () => {
    expect(minimumDueCents(3499, 0)).toBe(3499);
    expect(minimumDueCents(0, 0)).toBe(0);
  });

  it('is max(35.00, 1% of balance + interest) otherwise', () => {
    expect(minimumDueCents(100_000, 0)).toBe(3500);
    expect(minimumDueCents(800_000, 2_500)).toBe(10_500);
    expect(minimumDueCents(350_000, 0)).toBe(3500);
  });
});
