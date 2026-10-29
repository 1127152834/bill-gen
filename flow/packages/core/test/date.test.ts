import { describe, expect, it } from 'vitest';
import {
  DateStringSchema,
  addDays,
  addMonths,
  daysBetween,
  isValidDate,
  monthEnd,
  monthStart,
  weekday,
} from '../src/index.js';

describe('isValidDate', () => {
  it('rejects impossible calendar dates', () => {
    expect(isValidDate('2026-02-31')).toBe(false);
    expect(isValidDate('2026-02-29')).toBe(false);
    expect(isValidDate('2026-13-01')).toBe(false);
    expect(isValidDate('2026-2-1')).toBe(false);
  });
  it('accepts real dates including leap days', () => {
    expect(isValidDate('2028-02-29')).toBe(true);
    expect(isValidDate('2026-12-31')).toBe(true);
  });
  it('is enforced by DateStringSchema', () => {
    expect(DateStringSchema.safeParse('2026-04-31').success).toBe(false);
    expect(DateStringSchema.safeParse('2026-04-30').success).toBe(true);
  });
});

describe('date helpers', () => {
  it('addDays crosses month, year and leap boundaries', () => {
    expect(addDays('2026-01-31', 1)).toBe('2026-02-01');
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01');
    expect(addDays('2028-02-28', 1)).toBe('2028-02-29');
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28');
    expect(addDays('2026-06-13', 0)).toBe('2026-06-13');
  });
  it('weekday is 0=Sunday', () => {
    expect(weekday('2026-09-06')).toBe(0);
    expect(weekday('2026-09-07')).toBe(1);
    expect(weekday('2026-09-05')).toBe(6);
  });
  it('monthStart / monthEnd', () => {
    expect(monthStart('2026-02')).toBe('2026-02-01');
    expect(monthEnd('2026-02')).toBe('2026-02-28');
    expect(monthEnd('2028-02')).toBe('2028-02-29');
    expect(monthEnd('2026-12')).toBe('2026-12-31');
  });
  it('addMonths handles negative and year rollover', () => {
    expect(addMonths('2026-01', -1)).toBe('2025-12');
    expect(addMonths('2026-11', 3)).toBe('2027-02');
    expect(addMonths('2026-02', -5)).toBe('2025-09');
    expect(addMonths('2026-05', 0)).toBe('2026-05');
  });
  it('daysBetween is b - a', () => {
    expect(daysBetween('2026-01-01', '2026-01-31')).toBe(30);
    expect(daysBetween('2026-03-01', '2026-02-01')).toBe(-28);
    expect(daysBetween('2026-03-08', '2026-03-09')).toBe(1);
  });
});
