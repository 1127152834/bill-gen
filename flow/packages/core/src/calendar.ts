import { addDays, addMonths, monthEnd, monthStart, weekday } from './date.js';

const pad2 = (n: number): string => String(n).padStart(2, '0');

/** Date of the n-th (1-based) given weekday (0=Sun) in a month. */
function nthWeekday(year: number, month: number, dow: number, n: number): string {
  const first = `${year}-${pad2(month)}-01`;
  const offset = (dow - weekday(first) + 7) % 7;
  return addDays(first, offset + (n - 1) * 7);
}

function lastWeekday(year: number, month: number, dow: number): string {
  const last = monthEnd(`${year}-${pad2(month)}`);
  return addDays(last, -((weekday(last) - dow + 7) % 7));
}

/**
 * Federal Reserve rule for fixed-date holidays: Sunday is observed Monday; Saturday is not
 * observed at all (banks stay open the preceding Friday), so it yields no holiday.
 */
function observed(date: string): string[] {
  const dow = weekday(date);
  if (dow === 6) return [];
  return [dow === 0 ? addDays(date, 1) : date];
}

/** Federal Reserve bank holidays for `year`, ascending. */
export function bankHolidays(year: number): string[] {
  const fixed = (m: number, d: number) => observed(`${year}-${pad2(m)}-${pad2(d)}`);
  return [
    ...fixed(1, 1), // New Year's Day
    nthWeekday(year, 1, 1, 3), // Martin Luther King Jr. Day
    nthWeekday(year, 2, 1, 3), // Washington's Birthday (Presidents Day)
    lastWeekday(year, 5, 1), // Memorial Day
    ...fixed(6, 19), // Juneteenth
    ...fixed(7, 4), // Independence Day
    nthWeekday(year, 9, 1, 1), // Labor Day
    nthWeekday(year, 10, 1, 2), // Columbus Day
    ...fixed(11, 11), // Veterans Day
    nthWeekday(year, 11, 4, 4), // Thanksgiving
    ...fixed(12, 25), // Christmas
  ].sort();
}

const holidayCache = new Map<number, ReadonlySet<string>>();

function holidaySet(year: number): ReadonlySet<string> {
  let s = holidayCache.get(year);
  if (!s) {
    s = new Set(bankHolidays(year));
    holidayCache.set(year, s);
  }
  return s;
}

export function isBankBusinessDay(d: string): boolean {
  const dow = weekday(d);
  if (dow === 0 || dow === 6) return false;
  return !holidaySet(Number(d.slice(0, 4))).has(d);
}

/** `d` itself when it is a bank business day, otherwise the next one. */
export function nextBankBusinessDay(d: string): string {
  let cur = d;
  while (!isBankBusinessDay(cur)) cur = addDays(cur, 1);
  return cur;
}

/** IRS estimated-tax due dates for a tax year: Apr 15, Jun 15, Sep 15, Jan 15 (next year). */
export function estimatedTaxDates(taxYear: number): string[] {
  return [`${taxYear}-04-15`, `${taxYear}-06-15`, `${taxYear}-09-15`, `${taxYear + 1}-01-15`].map(
    nextBankBusinessDay,
  );
}

export type Cycle = { index: number; start: string; end: string; dueDate?: string };

/** `months` natural-month cycles ending at `endMonth`, oldest first. No dueDate. */
export function checkingCycles(endMonth: string, months: number): Cycle[] {
  return Array.from({ length: months }, (_, index) => {
    const ym = addMonths(endMonth, index - (months - 1));
    return { index, start: monthStart(ym), end: monthEnd(ym) };
  });
}

/**
 * `months` credit-card cycles; cycle i ends on `closingDay` of its month and starts the day
 * after the previous close. dueDate = end + 25 days, rolled to the next bank business day.
 */
export function creditCycles(endMonth: string, months: number, closingDay: number): Cycle[] {
  if (!Number.isInteger(closingDay) || closingDay < 1 || closingDay > 28) {
    throw new Error(`closingDay must be an integer in 1-28, got ${closingDay}`);
  }
  const closeOf = (ym: string) => `${ym}-${pad2(closingDay)}`;
  return Array.from({ length: months }, (_, index) => {
    const ym = addMonths(endMonth, index - (months - 1));
    const end = closeOf(ym);
    return {
      index,
      start: addDays(closeOf(addMonths(ym, -1)), 1),
      end,
      dueDate: nextBankBusinessDay(addDays(end, 25)),
    };
  });
}
