import { describe, expect, it } from 'vitest';
import {
  bankHolidays,
  checkingCycles,
  creditCycles,
  estimatedTaxDates,
  isBankBusinessDay,
  nextBankBusinessDay,
} from '../src/index.js';

describe('bankHolidays', () => {
  it('2026 follows Fed rules (Sat 7/4 not observed)', () => {
    expect(bankHolidays(2026)).toEqual([
      '2026-01-01', '2026-01-19', '2026-02-16', '2026-05-25', '2026-06-19',
      '2026-09-07', '2026-10-12', '2026-11-11', '2026-11-26', '2026-12-25',
    ]);
  });
  it('2025 includes 7/4 (Friday)', () => {
    expect(bankHolidays(2025)).toContain('2025-07-04');
  });
  it('Sunday holidays shift to Monday, Saturday ones are dropped', () => {
    // 2027-07-04 is Sunday -> 07-05; 2027-06-19 is Saturday -> not observed
    const h = bankHolidays(2027);
    expect(h).toContain('2027-07-05');
    expect(h).not.toContain('2027-07-04');
    expect(h).not.toContain('2027-06-18');
    expect(h).not.toContain('2027-06-19'); // Saturday: dropped entirely
    expect(h).toEqual([...h].sort());
  });
});

describe('business days', () => {
  it('isBankBusinessDay', () => {
    expect(isBankBusinessDay('2026-09-07')).toBe(false); // Labor Day
    expect(isBankBusinessDay('2026-09-06')).toBe(false); // Sunday
    expect(isBankBusinessDay('2026-07-03')).toBe(true); // Friday before Sat 7/4: open
    expect(isBankBusinessDay('2026-09-08')).toBe(true);
  });
  it('nextBankBusinessDay', () => {
    expect(nextBankBusinessDay('2026-09-05')).toBe('2026-09-08');
    expect(nextBankBusinessDay('2026-09-08')).toBe('2026-09-08');
  });
  it('rolls across a year boundary holiday', () => {
    expect(nextBankBusinessDay('2026-12-31')).toBe('2026-12-31');
    expect(nextBankBusinessDay('2027-01-01')).toBe('2027-01-04'); // Fri holiday -> Mon (Sat/Sun)
  });
});

describe('estimatedTaxDates', () => {
  it('2026', () => {
    expect(estimatedTaxDates(2026)).toEqual(['2026-04-15', '2026-06-15', '2026-09-15', '2027-01-15']);
    expect(estimatedTaxDates(2025)[3]).toBe('2026-01-15');
  });
  it('rolls weekend and holiday dates forward', () => {
    // 2027: Jan 15 2028 is Saturday -> Tue 2028-01-18 (MLK Mon 01-17)
    expect(estimatedTaxDates(2027)[3]).toBe('2028-01-18');
    // 2028-04-15 is Saturday -> Monday 04-17
    expect(estimatedTaxDates(2028)[0]).toBe('2028-04-17');
  });
});

describe('cycles', () => {
  it('checkingCycles are natural months, oldest first, without dueDate', () => {
    const c = checkingCycles('2026-02', 6);
    expect(c.map((x) => x.start)).toEqual([
      '2025-09-01', '2025-10-01', '2025-11-01', '2025-12-01', '2026-01-01', '2026-02-01',
    ]);
    expect(c.map((x) => x.index)).toEqual([0, 1, 2, 3, 4, 5]);
    expect(c[5]?.end).toBe('2026-02-28');
    expect(c.every((x) => x.dueDate === undefined)).toBe(true);
  });
  it('creditCycles are contiguous and carry dueDate on a business day', () => {
    const c = creditCycles('2026-09', 3, 12);
    expect(c.map((x) => [x.start, x.end])).toEqual([
      ['2026-06-13', '2026-07-12'],
      ['2026-07-13', '2026-08-12'],
      ['2026-08-13', '2026-09-12'],
    ]);
    expect(c[2]?.dueDate).toBe('2026-10-07');
    expect(c.map((x) => x.index)).toEqual([0, 1, 2]);
    for (const x of c) expect(isBankBusinessDay(x.dueDate!)).toBe(true);
  });
  it('creditCycles roll dueDate past weekends/holidays', () => {
    // end 2026-07-12 + 25 = 2026-08-06 (Thu); end 2026-02-12 + 25 = 2026-03-09 (Mon)
    // closingDay 20: 2026-08-20 + 25 = 2026-09-14 (Mon); 2026-07-21? use Labor Day case:
    // end 2026-08-13 + 25 = 2026-09-07 (Labor Day) -> 2026-09-08
    const c = creditCycles('2026-08', 1, 13);
    expect(c[0]?.dueDate).toBe('2026-09-08');
  });
  it('creditCycles rejects closingDay outside 1-28', () => {
    expect(() => creditCycles('2026-09', 3, 29)).toThrow();
    expect(() => creditCycles('2026-09', 3, 0)).toThrow();
  });
});
