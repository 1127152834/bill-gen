import { describe, expect, it } from 'vitest';
import {
  InfeasibleRequestError,
  checkingCycles,
  createRng,
  creditCycles,
  lookupLocation,
  normalizeRequest,
  pickWeightedMetro,
  type GenerationRequest,
} from '../src/index.js';

const TODAY = '2026-10-29';

function req(over: Partial<GenerationRequest> = {}): GenerationRequest {
  return {
    subjectType: 'personal',
    accounts: { checking: { min: 10, max: 20 } },
    months: 3,
    ...over,
  };
}

describe('lookupLocation', () => {
  it('matches known cities with Tax Foundation combined rates', () => {
    expect(lookupLocation('Austin', 'TX')).toMatchObject({
      combinedSalesTaxRate: 0.0825,
      matchedBy: 'city',
    });
    expect(lookupLocation('Chicago', 'IL').combinedSalesTaxRate).toBe(0.1025);
    expect(lookupLocation('Portland', 'OR').combinedSalesTaxRate).toBe(0);
  });

  it('is case-insensitive and trims the city', () => {
    expect(lookupLocation('  aUsTiN ', 'TX')).toMatchObject({ matchedBy: 'city', metroId: 'austin-tx' });
  });

  it('does not match a city name in the wrong state', () => {
    expect(lookupLocation('Portland', 'ME').matchedBy).toBe('state');
  });

  it('falls back to the state row for an unknown city', () => {
    expect(lookupLocation('Marfa', 'TX')).toMatchObject({
      matchedBy: 'state',
      metroId: 'state-TX',
      metroName: 'Texas',
      state: 'TX',
      combinedSalesTaxRate: 0.082,
    });
  });

  it('throws on an unknown state', () => {
    expect(() => lookupLocation('Springfield', 'ZZ')).toThrow(/unknown state/i);
  });
});

describe('pickWeightedMetro', () => {
  it('is deterministic per seed and favours populous metros', () => {
    expect(pickWeightedMetro(createRng('a'))).toEqual(pickWeightedMetro(createRng('a')));
    const rng = createRng('weights');
    const counts = new Map<string, number>();
    for (let i = 0; i < 4000; i++) {
      const id = pickWeightedMetro(rng).metroId;
      counts.set(id, (counts.get(id) ?? 0) + 1);
    }
    expect(counts.get('new-york-ny') ?? 0).toBeGreaterThan(counts.get('tampa-fl') ?? 0);
  });
});

describe('normalizeRequest', () => {
  it('defaults endMonth to the month before today', () => {
    expect(normalizeRequest(req({ months: 3 }), TODAY).request.endMonth).toBe('2026-09');
    expect(normalizeRequest(req(), '2026-01-05').request.endMonth).toBe('2025-12');
  });

  it('keeps an explicit endMonth and seed, and generates a seed otherwise', () => {
    const out = normalizeRequest(req({ endMonth: '2026-03', seed: 'abc' }), TODAY);
    expect(out.request).toMatchObject({ endMonth: '2026-03', seed: 'abc' });
    const a = normalizeRequest(req(), TODAY).request.seed;
    const b = normalizeRequest(req(), TODAY).request.seed;
    expect(a).toMatch(/^[0-9a-f-]{36}$/);
    expect(a).not.toBe(b);
  });

  it('closingDay is deterministic per seed, in 1-28, and only set with credit', () => {
    const r = () => req({ seed: 's1', accounts: { credit: { min: 5, max: 9 } } });
    const day = normalizeRequest(r(), TODAY).closingDay;
    expect(day).toBe(normalizeRequest(r(), TODAY).closingDay);
    expect(day).toBe(createRng('s1').fork('closing').int(1, 28));
    expect(day).toBeGreaterThanOrEqual(1);
    expect(day).toBeLessThanOrEqual(28);
    expect(normalizeRequest(req({ seed: 's1' }), TODAY).closingDay).toBeUndefined();
  });

  it('builds only the requested account cycles', () => {
    const chk = normalizeRequest(req({ seed: 's' }), TODAY);
    expect(chk.cycles.checking).toEqual(checkingCycles('2026-09', 3));
    expect(chk.cycles.credit).toBeUndefined();
    const crd = normalizeRequest(req({ seed: 's', accounts: { credit: { min: 5, max: 9 } } }), TODAY);
    expect(crd.cycles.credit).toEqual(creditCycles('2026-09', 3, crd.closingDay!));
    expect(crd.cycles.checking).toBeUndefined();
  });

  it('periodStart/periodEnd span all accounts for a both-request', () => {
    const out = normalizeRequest(
      req({
        seed: 's1',
        accounts: { checking: { min: 10, max: 20 }, credit: { min: 5, max: 9 } },
      }),
      TODAY,
    );
    const all = [...out.cycles.checking!, ...out.cycles.credit!];
    expect(out.periodStart).toBe(all.map((c) => c.start).sort()[0]);
    expect(out.periodEnd).toBe(all.map((c) => c.end).sort().at(-1));
    // credit's first cycle starts in the prior month, before checking's first day
    expect(out.periodStart < out.cycles.checking![0]!.start).toBe(true);
    // checking ends on the last calendar day, after any closingDay <= 28
    expect(out.periodEnd).toBe('2026-09-30');
  });

  it('periodStart/periodEnd of a checking-only request are the calendar bounds', () => {
    const out = normalizeRequest(req({ endMonth: '2026-02', months: 6 }), TODAY);
    expect(out.periodStart).toBe('2025-09-01');
    expect(out.periodEnd).toBe('2026-02-28');
  });

  it('taxDates cover prior-year Jan 15 and in-window quarterly dates', () => {
    const out = normalizeRequest(req({ endMonth: '2026-02', months: 6 }), TODAY);
    expect(out.taxDates).toContain('2026-01-15');
    expect(out.taxDates).toContain('2025-09-15');
    expect(out.taxDates).toEqual(['2025-09-15', '2026-01-15']);
    for (const d of out.taxDates) {
      expect(d >= out.periodStart && d <= out.periodEnd).toBe(true);
    }
  });

  it('holidays are the sorted union over every year the window touches', () => {
    const out = normalizeRequest(req({ endMonth: '2026-02', months: 6 }), TODAY);
    expect(out.holidays).toContain('2025-12-25');
    expect(out.holidays).toContain('2026-01-01');
    expect(out.holidays).toContain('2025-11-27');
    expect(out.holidays).toEqual([...out.holidays].sort());
    expect(new Set(out.holidays).size).toBe(out.holidays.length);
  });

  it('uses the profile address for location, else a seeded metro pick', () => {
    const profile = {
      name: 'Ann Lee',
      age: 40,
      address: { street: '1 Main St', city: 'Austin', state: 'TX', zip: '78701' },
      occupation: 'Engineer',
      household: { maritalStatus: 'single' as const, children: 0 },
      hobbies: [],
    };
    expect(normalizeRequest(req({ profile }), TODAY).location).toMatchObject({
      metroId: 'austin-tx',
      matchedBy: 'city',
    });
    const a = normalizeRequest(req({ seed: 'loc' }), TODAY).location;
    expect(a).toEqual(normalizeRequest(req({ seed: 'loc' }), TODAY).location);
    expect(a.matchedBy).toBe('city');
  });

  it('throws InfeasibleRequestError when a max is below the base required count', () => {
    expect(() =>
      normalizeRequest(req({ accounts: { checking: { min: 1, max: 2 }, credit: { min: 5, max: 9 } } }), TODAY),
    ).toThrow(InfeasibleRequestError);
    expect(() =>
      normalizeRequest(req({ accounts: { checking: { min: 1, max: 1 } } }), TODAY),
    ).toThrow(/at least 2/);
    expect(() =>
      normalizeRequest(req({ accounts: { checking: { min: 1, max: 3 }, credit: { min: 1, max: 1 } } }), TODAY),
    ).not.toThrow();
    const err = (() => {
      try {
        normalizeRequest(req({ accounts: { checking: { min: 1, max: 2 }, credit: { min: 1, max: 3 } } }), TODAY);
      } catch (e) {
        return e as InfeasibleRequestError;
      }
    })();
    expect(err?.code).toBe('INFEASIBLE_REQUEST');
    expect(err?.message).toMatch(/at least 3/);
  });
});
