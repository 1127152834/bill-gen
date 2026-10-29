import { describe, expect, it } from 'vitest';
import {
  createRng,
  isBankBusinessDay,
  normalizeRequest,
  weekday,
  type DiscretionaryItem,
  type FinancialModel,
  type GenerationRequest,
  type IncomeStream,
  type RecurringItem,
} from '../src/index.js';
import {
  incomeOccurrences,
  postingDate,
  recurringOccurrences,
  txnId,
} from '../src/schedule/place.js';
import { discretionaryAmount, fuelAmount } from '../src/schedule/amounts.js';

const TODAY = '2026-10-29';

function norm(over: Partial<GenerationRequest> = {}, endMonth = '2026-09') {
  return normalizeRequest(
    {
      subjectType: 'personal',
      accounts: { checking: { min: 10, max: 20 } },
      months: 3,
      endMonth,
      seed: 'test-seed',
      ...over,
    },
    TODAY,
  );
}

function model(over: Partial<FinancialModel> = {}): FinancialModel {
  return {
    incomeStreams: [],
    recurring: [],
    discretionaryPool: [],
    openingBalanceCents: 100_000,
    ...over,
  };
}

const sub = (over: Partial<RecurringItem> = {}): RecurringItem => ({
  id: 'sub1',
  label: 'Streaming',
  kind: 'subscription',
  dayRule: { kind: 'dayOfMonth', day: 14 },
  amount: { kind: 'fixed', cents: 1599 },
  account: 'checking',
  priority: 4,
  factId: 'netflix',
  txnType: 'POS',
  ...over,
});

const stream = (over: Partial<IncomeStream> = {}): IncomeStream => ({
  id: 'inc1',
  label: 'Platform payout',
  payerName: 'Uber',
  channel: 'platform_payout',
  cadence: { kind: 'weekly', weekday: 3 },
  amountCents: { min: 30_000, max: 60_000 },
  seasonality: Array(12).fill(1),
  ...over,
});

const item = (over: Partial<DiscretionaryItem> = {}): DiscretionaryItem => ({
  factId: 'grocer',
  weight: 1,
  perMonth: { min: 1, max: 3 },
  ticketCents: { min: 1500, max: 18_000 },
  pricing: 'basket',
  taxable: true,
  tip: false,
  account: 'checking',
  weekendBias: 0.5,
  location: 'local',
  ...over,
});

describe('postingDate', () => {
  it('posts Saturday POS on Monday or later', () => {
    const rng = createRng('post');
    for (let i = 0; i < 50; i++) {
      const d = postingDate('2026-09-12', 'POS', rng);
      expect(weekday(d)).toBeGreaterThanOrEqual(1);
      expect(weekday(d)).toBeLessThanOrEqual(5);
      expect(d > '2026-09-12').toBe(true);
    }
  });

  it('posts card swipes 1-2 bank business days later, skipping holidays', () => {
    const rng = createRng('post2');
    const seen = new Set<string>();
    for (let i = 0; i < 100; i++) seen.add(postingDate('2026-09-03', 'POS', rng));
    expect([...seen].sort()).toEqual(['2026-09-04', '2026-09-08']); // Thu -> Fri | Tue (Labor Day Mon 09-07)
  });

  it('ACH/ZELLE/PAYMENT/TRANSFER/CHECK/DEPOSIT/ATM post on the next bank business day', () => {
    const rng = createRng('post3');
    for (const t of ['ACH', 'ZELLE', 'PAYMENT', 'TRANSFER', 'CHECK', 'DEPOSIT', 'ATM'] as const) {
      expect(postingDate('2026-09-12', t, rng)).toBe('2026-09-14');
      expect(postingDate('2026-09-15', t, rng)).toBe('2026-09-15');
    }
  });

  it('FEE and INTEREST post the same day', () => {
    const rng = createRng('post4');
    expect(postingDate('2026-09-12', 'FEE', rng)).toBe('2026-09-12');
    expect(postingDate('2026-09-13', 'INTEREST', rng)).toBe('2026-09-13');
  });
});

describe('recurringOccurrences', () => {
  it('fixed subscription identical every month', () => {
    const n = norm();
    const out = recurringOccurrences(model({ recurring: [sub()] }), n, createRng('r'));
    expect(out).toHaveLength(3);
    expect(new Set(out.map((t) => t.amountCents)).size).toBe(1);
    expect(out[0]).toMatchObject({
      account: 'checking',
      direction: 'debit',
      source: 'recurring',
      merchantKey: 'netflix',
      factId: 'netflix',
      priority: 4,
      amountCents: 1599,
    });
    expect(out.map((t) => t.transactionDate.slice(8))).toEqual(['14', '14', '14']);
  });

  it('rolls ACH dates to the next business day (date itself and posting)', () => {
    // 2026-11-15 is a Sunday
    const n = norm({}, '2026-11');
    const out = recurringOccurrences(
      model({
        recurring: [
          sub({ id: 'rent', factId: 'landlord', txnType: 'ACH', dayRule: { kind: 'dayOfMonth', day: 15 } }),
        ],
      }),
      n,
      createRng('r'),
    );
    const nov = out.find((t) => t.transactionDate.startsWith('2026-11'))!;
    expect(nov.transactionDate).toBe('2026-11-16');
    expect(nov.postedDate).toBe('2026-11-16');
    for (const t of out) {
      expect(isBankBusinessDay(t.transactionDate)).toBe(true);
      expect(t.postedDate).toBe(t.transactionDate);
    }
  });

  it('POS recurring keeps the scheduled date and posts via postingDate', () => {
    // 2026-08-15 is a Saturday
    const n = norm();
    const out = recurringOccurrences(
      model({ recurring: [sub({ dayRule: { kind: 'dayOfMonth', day: 15 } })] }),
      n,
      createRng('r'),
    );
    const aug = out.find((t) => t.transactionDate === '2026-08-15')!;
    expect(aug).toBeDefined();
    expect(weekday(aug.postedDate)).toBeGreaterThanOrEqual(1);
    expect(aug.postedDate > aug.transactionDate).toBe(true);
  });

  it('tax payment only on tax dates in window', () => {
    const n = norm({ months: 6 });
    const out = recurringOccurrences(
      model({
        recurring: [
          sub({
            id: 'irs',
            factId: 'irs-est',
            kind: 'tax_estimated',
            dayRule: { kind: 'taxDates' },
            txnType: 'ACH',
            amount: { kind: 'fixed', cents: 250_000 },
            priority: 1,
          }),
        ],
      }),
      n,
      createRng('r'),
    );
    expect(out.map((t) => t.transactionDate)).toEqual(['2026-04-15', '2026-06-15', '2026-09-15']);
    expect(out.every((t) => t.priority === 1)).toBe(true);
  });

  it('biweekly rule steps 14 days from the anchor in both directions', () => {
    const n = norm();
    const out = recurringOccurrences(
      model({
        recurring: [
          sub({ dayRule: { kind: 'biweekly', weekday: 5, anchor: '2026-09-04' }, txnType: 'ACH' }),
        ],
      }),
      n,
      createRng('r'),
    );
    const dates = out.map((t) => t.transactionDate);
    expect(dates).toContain('2026-07-24');
    expect(dates).toContain('2026-09-04');
    expect(dates).toContain('2026-09-18');
    expect(dates.every((d) => d >= '2026-07-01' && d <= '2026-09-30')).toBe(true);
    expect(new Set(dates).size).toBe(dates.length);
  });

  it('seasonal amount is in the upper 40% of the range in peak months, lower 60% otherwise', () => {
    const n = norm({ months: 6 }); // Apr..Sep
    const seasonal: RecurringItem = sub({
      id: 'power',
      factId: 'utility-power',
      kind: 'utility',
      txnType: 'ACH',
      amount: { kind: 'seasonal', minCents: 10_000, maxCents: 20_000, peakMonths: [7, 8] },
    });
    for (let s = 0; s < 20; s++) {
      const out = recurringOccurrences(model({ recurring: [seasonal] }), n, createRng(`s${s}`));
      for (const t of out) {
        const m = Number(t.transactionDate.slice(5, 7));
        if (m === 7 || m === 8) expect(t.amountCents).toBeGreaterThanOrEqual(16_000);
        else expect(t.amountCents).toBeLessThanOrEqual(16_000);
        expect(t.amountCents).toBeGreaterThanOrEqual(10_000);
        expect(t.amountCents).toBeLessThanOrEqual(20_000);
      }
    }
  });

  it('occurrences stay inside periodStart..periodEnd', () => {
    const n = norm();
    const out = recurringOccurrences(model({ recurring: [sub()] }), n, createRng('r'));
    for (const t of out) {
      expect(t.transactionDate >= n.periodStart && t.transactionDate <= n.periodEnd).toBe(true);
    }
  });

  it('ids are unique, deterministic, and follow txnId', () => {
    const n = norm();
    const m = model({ recurring: [sub(), sub({ id: 'sub2' })] }); // same factId on purpose
    const a = recurringOccurrences(m, n, createRng('r'));
    const b = recurringOccurrences(m, n, createRng('r'));
    expect(a).toEqual(b);
    expect(new Set(a.map((t) => t.id)).size).toBe(a.length);
    expect(txnId('recurring', 'netflix', '2026-09-14', 0)).toBe('recurring-netflix-2026-09-14-0');
    expect(a[0]!.id).toMatch(/^recurring-netflix-\d{4}-\d{2}-\d{2}-\d+$/);
  });

  it('uses the item id as merchantKey when factId is absent', () => {
    const n = norm();
    const noFact = { ...sub(), factId: undefined } as unknown as RecurringItem;
    const out = recurringOccurrences(model({ recurring: [noFact] }), n, createRng('r'));
    expect(out[0]!.merchantKey).toBe('sub1');
    expect(out[0]!.factId).toBeUndefined();
  });
});

describe('incomeOccurrences', () => {
  it('weekly platform payout appears ~4-5 times per month', () => {
    const n = norm();
    const out = incomeOccurrences(model({ incomeStreams: [stream()] }), n, createRng('i'));
    const byMonth = new Map<string, number>();
    for (const t of out) byMonth.set(t.transactionDate.slice(0, 7), (byMonth.get(t.transactionDate.slice(0, 7)) ?? 0) + 1);
    expect([...byMonth.keys()].sort()).toEqual(['2026-07', '2026-08', '2026-09']);
    for (const c of byMonth.values()) {
      expect(c).toBeGreaterThanOrEqual(4);
      expect(c).toBeLessThanOrEqual(5);
    }
    expect(out.every((t) => t.account === 'checking' && t.direction === 'credit' && t.priority === 2)).toBe(true);
    expect(out.every((t) => t.type === 'ACH' && t.source === 'income' && t.merchantKey === 'inc1')).toBe(true);
    expect(out.every((t) => weekday(t.transactionDate) === 3)).toBe(true);
  });

  it('returns [] when there is no checking account', () => {
    const n = norm({ accounts: { credit: { min: 10, max: 20 } } });
    expect(incomeOccurrences(model({ incomeStreams: [stream()] }), n, createRng('i'))).toEqual([]);
  });

  it('maps channels to transaction types', () => {
    const n = norm();
    const type = (channel: IncomeStream['channel']) =>
      incomeOccurrences(
        model({ incomeStreams: [stream({ channel, cadence: { kind: 'monthly', day: 10 } })] }),
        n,
        createRng('i'),
      )[0]!.type;
    expect(type('mobile_deposit')).toBe('DEPOSIT');
    expect(type('zelle')).toBe('ZELLE');
    for (const c of ['ach_client', 'stripe', 'square', 'paypal', 'venmo', 'platform_payout', 'card_sales'] as const) {
      expect(type(c)).toBe('ACH');
    }
  });

  it('multiplies by the monthly seasonality factor (index 0 = January)', () => {
    const n = norm();
    const seasonality = Array(12).fill(1);
    seasonality[7] = 2; // August
    const out = incomeOccurrences(
      model({
        incomeStreams: [
          stream({ cadence: { kind: 'monthly', day: 10 }, amountCents: { min: 100_000, max: 100_000 }, seasonality }),
        ],
      }),
      n,
      createRng('i'),
    );
    const byMonth = Object.fromEntries(out.map((t) => [t.transactionDate.slice(0, 7), t.amountCents]));
    expect(byMonth['2026-07']).toBe(100_000);
    expect(byMonth['2026-08']).toBe(200_000);
  });

  it('biweekly lands every 14 days on the requested weekday', () => {
    const n = norm();
    const out = incomeOccurrences(
      model({ incomeStreams: [stream({ cadence: { kind: 'biweekly', weekday: 5 } })] }),
      n,
      createRng('i'),
    );
    expect(out.length).toBeGreaterThanOrEqual(6);
    for (let i = 1; i < out.length; i++) {
      const gap =
        (Date.parse(out[i]!.transactionDate) - Date.parse(out[i - 1]!.transactionDate)) / 86_400_000;
      expect(gap).toBe(14);
    }
    expect(out.every((t) => weekday(t.transactionDate) === 5)).toBe(true);
  });

  it('irregular income: perMonth count on business days', () => {
    const n = norm();
    const out = incomeOccurrences(
      model({ incomeStreams: [stream({ cadence: { kind: 'irregular', perMonth: { min: 2, max: 4 } } })] }),
      n,
      createRng('i'),
    );
    const byMonth = new Map<string, number>();
    for (const t of out) byMonth.set(t.transactionDate.slice(0, 7), (byMonth.get(t.transactionDate.slice(0, 7)) ?? 0) + 1);
    expect(byMonth.size).toBe(3);
    for (const c of byMonth.values()) {
      expect(c).toBeGreaterThanOrEqual(2);
      expect(c).toBeLessThanOrEqual(4);
    }
    expect(out.every((t) => isBankBusinessDay(t.transactionDate))).toBe(true);
    expect(new Set(out.map((t) => t.id)).size).toBe(out.length);
  });

  it('is deterministic for the same rng seed', () => {
    const n = norm();
    const m = model({ incomeStreams: [stream({ cadence: { kind: 'irregular', perMonth: { min: 2, max: 4 } } })] });
    expect(incomeOccurrences(m, n, createRng('x'))).toEqual(incomeOccurrences(m, n, createRng('x')));
  });
});

describe('discretionaryAmount', () => {
  it('discretionary amount within ticket*(1+tax)*(1.22)+100', () => {
    const rng = createRng('d');
    const it_ = item({ tip: true });
    for (let i = 0; i < 1000; i++) {
      const a = discretionaryAmount(it_, 0.0825, rng);
      expect(Number.isInteger(a)).toBe(true);
      expect(a).toBeGreaterThanOrEqual(it_.ticketCents.min);
      expect(a).toBeLessThanOrEqual(Math.ceil(it_.ticketCents.max * 1.0825 * 1.22) + 100);
    }
  });

  it('basket amounts are rarely whole dollars', () => {
    const rng = createRng('d2');
    const it_ = item();
    let whole = 0;
    for (let i = 0; i < 1000; i++) if (discretionaryAmount(it_, 0.0825, rng) % 100 === 0) whole++;
    expect(whole / 1000).toBeLessThan(0.05);
  });

  it('non-taxable, non-tip basket stays within its ticket range', () => {
    const rng = createRng('d3');
    const it_ = item({ taxable: false });
    for (let i = 0; i < 500; i++) {
      const a = discretionaryAmount(it_, 0.1, rng);
      expect(a).toBeGreaterThanOrEqual(it_.ticketCents.min);
      expect(a).toBeLessThanOrEqual(it_.ticketCents.max);
    }
  });

  it('fixed_menu base uses menu-style cents endings', () => {
    const rng = createRng('d4');
    const it_ = item({ pricing: 'fixed_menu', taxable: false, ticketCents: { min: 800, max: 4000 } });
    const allowed = new Set([0, 25, 49, 50, 75, 95, 99]);
    for (let i = 0; i < 500; i++) {
      const a = discretionaryAmount(it_, 0.1, rng);
      expect(allowed.has(a % 100)).toBe(true);
      expect(a).toBeGreaterThanOrEqual(800);
      expect(a).toBeLessThanOrEqual(4000);
    }
  });

  it('tipped totals round up to whole dollars about half the time', () => {
    const rng = createRng('d5');
    const it_ = item({ pricing: 'fixed_menu', tip: true, taxable: true, ticketCents: { min: 1500, max: 5000 } });
    let whole = 0;
    for (let i = 0; i < 1000; i++) if (discretionaryAmount(it_, 0.08, rng) % 100 === 0) whole++;
    expect(whole).toBeGreaterThan(400);
    expect(whole).toBeLessThan(700);
  });

  it('fuel is never taxed or tipped and equals price x gallons', () => {
    const rng = createRng('d6');
    const it_ = item({ pricing: 'fuel', unitPriceCents: 359, taxable: true, tip: true, ticketCents: { min: 2000, max: 6000 } });
    for (let i = 0; i < 500; i++) {
      const a = discretionaryAmount(it_, 0.2, rng);
      expect(a).toBeGreaterThanOrEqual(Math.round(359 * 8));
      expect(a).toBeLessThanOrEqual(Math.round(359 * 16));
    }
  });

  it('fuel without unitPriceCents throws', () => {
    expect(() => discretionaryAmount(item({ pricing: 'fuel' }), 0.08, createRng('x'))).toThrow(/unitPriceCents/);
  });
});

describe('fuelAmount', () => {
  it('is round(price * gallons) with gallons in 8.000-16.000 at 3 decimals', () => {
    const rng = createRng('f');
    const seen = new Set<number>();
    for (let i = 0; i < 1000; i++) {
      const a = fuelAmount(389, rng);
      expect(Number.isInteger(a)).toBe(true);
      expect(a).toBeGreaterThanOrEqual(3112);
      expect(a).toBeLessThanOrEqual(6224);
      seen.add(a);
    }
    expect(seen.size).toBeGreaterThan(100);
  });
});
