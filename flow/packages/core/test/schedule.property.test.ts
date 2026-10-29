import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { createRng, daysBetween, isBankBusinessDay, type FinancialModel, type NormalizedRequest } from '../src/index.js';
import { ScheduleInfeasibleError, schedule, type ScheduleOutput } from '../src/schedule/index.js';
import { incomeOccurrences, recurringOccurrences } from '../src/schedule/place.js';
import {
  FIXTURES,
  loadModel,
  makeNorm,
  postedIn,
  signed,
  cardPaymentPairingViolations,
  type Accounts,
  type FixtureName,
} from './schedule-helpers.js';

const RUNS = 200;
const models = Object.fromEntries(FIXTURES.map((f) => [f, loadModel(f)]));

const countRange = fc
  .integer({ min: 3, max: 30 })
  .chain((min) => fc.integer({ min, max: min + 15 }).map((max) => ({ min, max })));

const caseArb = fc.record({
  fixture: fc.constantFrom<FixtureName>(...FIXTURES),
  seed: fc.string({ minLength: 1, maxLength: 12 }),
  accounts: fc.constantFrom<Accounts>('checking', 'credit', 'both'),
  checking: countRange,
  credit: countRange,
  months: fc.constantFrom<1 | 3 | 6>(1, 3, 6),
  endMonth: fc.constantFrom('2026-09', '2026-02', '2025-12'),
  /** Overrides the fixture's checking opening balance; `null` keeps the fixture value. */
  openingBalanceCents: fc.constantFrom<number | null>(0, 5000, null),
});
type Case = typeof caseArb extends fc.Arbitrary<infer T> ? T : never;

type Run = { norm: NormalizedRequest; out?: ScheduleOutput; infeasible?: ScheduleInfeasibleError };

function modelFor(c: Case): FinancialModel {
  const base = models[c.fixture]!;
  return c.openingBalanceCents === null ? base : { ...base, openingBalanceCents: c.openingBalanceCents };
}

function run(c: Case): Run {
  const norm = makeNorm(c);
  try {
    return { norm, out: schedule(modelFor(c), norm, createRng(c.seed)) };
  } catch (e) {
    if (e instanceof ScheduleInfeasibleError) return { norm, infeasible: e };
    throw e;
  }
}

/** Property over feasible runs only; infeasible runs are checked by their own property below. */
function forFeasible(check: (r: Required<Pick<Run, 'out'>> & Run, c: Case) => void): void {
  fc.assert(
    fc.property(caseArb, (c) => {
      const r = run(c);
      if (r.out) check(r as Required<Pick<Run, 'out'>> & Run, c);
    }),
    { numRuns: RUNS },
  );
}

const accountsOf = (norm: NormalizedRequest) =>
  (['checking', 'credit'] as const).filter((a) => norm.request.accounts[a] !== undefined);

describe('schedule properties', () => {
  it('posts every credit-side card PAYMENT on a bank business day', () => {
    forFeasible(({ out }) => {
      for (const t of out.txns.filter((x) => x.account === 'credit' && x.type === 'PAYMENT')) {
        expect(isBankBusinessDay(t.postedDate), `${t.id} posted ${t.postedDate}`).toBe(true);
      }
    });
  });

  it('keeps every cycle count of every account within {min, max}', () => {
    forFeasible(({ norm, out }) => {
      for (const acct of accountsOf(norm)) {
        const { min, max } = norm.request.accounts[acct]!;
        for (const cycle of norm.cycles[acct]!) {
          const n = out.txns.filter((t) => t.account === acct && postedIn(t, cycle)).length;
          expect(n, `${acct} cycle ${cycle.index}`).toBeGreaterThanOrEqual(min);
          expect(n, `${acct} cycle ${cycle.index}`).toBeLessThanOrEqual(max);
        }
      }
    });
  });

  it('keeps checking balances continuous and charges an overdraft fee whenever the balance dips below zero', () => {
    forFeasible(({ norm, out }) => {
      const checking = out.txns.filter((t) => t.account === 'checking');
      if (!norm.request.accounts.checking) {
        expect(checking).toEqual([]);
        expect(out.balances.checking).toBeUndefined();
        return;
      }
      const bal = out.balances.checking!;
      expect(Object.keys(bal.byTxnId)).toHaveLength(checking.length);
      let running = bal.openingCents;
      for (const t of checking) {
        const prev = running;
        running += signed(t);
        expect(bal.byTxnId[t.id]).toBe(running);
        if (prev >= 0 && running < 0) {
          const fee = checking.find((f) => f.merchantKey === 'fee-overdraft' && f.postedDate === t.postedDate);
          expect(fee, `overdraft on ${t.postedDate} without a fee`).toBeDefined();
        }
      }
      expect(running).toBe(bal.openingCents + checking.reduce((s, t) => s + signed(t), 0));
    });
  });

  it('keeps credit statements consistent with the posted activity', () => {
    forFeasible(({ norm, out }, c) => {
      if (!norm.request.accounts.credit) {
        expect(out.balances.credit).toBeUndefined();
        return;
      }
      const perCycle = out.balances.credit!.perCycle;
      expect(perCycle).toHaveLength(norm.cycles.credit!.length);
      let prev = models[c.fixture]!.creditOpeningBalanceCents ?? 0;
      norm.cycles.credit!.forEach((cycle, i) => {
        const inCycle = out.txns.filter((t) => t.account === 'credit' && postedIn(t, cycle));
        const stmt = prev - inCycle.reduce((s, t) => s + signed(t), 0);
        expect(perCycle[i]!.statementBalanceCents).toBe(stmt);
        const interest = inCycle.filter((t) => t.type === 'INTEREST').reduce((s, t) => s + t.amountCents, 0);
        expect(perCycle[i]!.interestChargedCents).toBe(interest);
        prev = stmt;
      });
    });
  });

  it('posts every txn inside one of its own account cycles, at most 5 days after the transaction date', () => {
    forFeasible(({ norm, out }) => {
      for (const t of out.txns) {
        const cycles = norm.cycles[t.account];
        expect(cycles, `${t.id} on a missing account`).toBeDefined();
        expect(cycles!.some((cy) => postedIn(t, cy)), `${t.id} posted ${t.postedDate}`).toBe(true);
        const lag = daysBetween(t.transactionDate, t.postedDate);
        expect(lag, t.id).toBeGreaterThanOrEqual(0);
        expect(lag, t.id).toBeLessThanOrEqual(5);
      }
      expect(new Set(out.txns.map((t) => t.id)).size).toBe(out.txns.length);
    });
  });

  it('pairs card payments across accounts wherever both sides fall inside their statements', () => {
    forFeasible(({ norm, out }) => {
      if (!norm.request.accounts.checking || !norm.request.accounts.credit) return;
      expect(cardPaymentPairingViolations(out.txns, norm)).toEqual([]);
    });
  });

  it('charges a fixed recurring item the same amount every time', () => {
    forFeasible(({ out }, c) => {
      for (const item of models[c.fixture]!.recurring) {
        if (item.amount.kind !== 'fixed') continue;
        for (const t of out.txns.filter((x) => x.source === 'recurring' && x.merchantKey === item.factId)) {
          expect(t.amountCents).toBe(item.amount.cents);
        }
      }
    });
  });

  it('is deterministic for a given seed', () => {
    fc.assert(
      fc.property(caseArb, (c) => {
        expect(run(c)).toEqual(run(c));
      }),
      { numRuns: RUNS },
    );
  });

  it('is infeasible only when a cycle holds more priority <= 2 items than max', () => {
    fc.assert(
      fc.property(caseArb, (c) => {
        const { norm, infeasible } = run(c);
        if (!infeasible) return;
        const { account, cycleIndex, mandatoryCount, overdraftFees } = infeasible;
        expect(account).toBeDefined();
        const max = norm.request.accounts[account!]!.max;
        expect(mandatoryCount).toBeGreaterThan(max);
        // Upper bound from the model: priority <= 2 recurring + income in that cycle, plus at most
        // two card-side rows (checking: two payments; credit: one payment + one interest charge),
        // plus the overdraft fees the cycle was forced to carry (property 2 checks fees only
        // appear on real overdrafts).
        const model = modelFor(c);
        const rng = createRng(c.seed);
        const cycle = norm.cycles[account!]![cycleIndex!]!;
        const fromModel = [
          ...recurringOccurrences(model, norm, rng.fork('recurring')),
          ...incomeOccurrences(model, norm, rng.fork('income')),
        ].filter((t) => t.account === account && t.priority <= 2 && postedIn(t, cycle)).length;
        const cardSlack = account === 'credit' || norm.request.accounts.credit ? 2 : 0;
        expect(fromModel + cardSlack + (overdraftFees ?? 0)).toBeGreaterThan(max);
      }),
      { numRuns: RUNS },
    );
  });
});
