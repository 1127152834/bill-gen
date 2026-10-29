/**
 * Date helpers. Dates are `YYYY-MM-DD` strings, months are `YYYY-MM`;
 * all arithmetic is done in UTC (never local time).
 */

const MS_PER_DAY = 86_400_000;
const DATE_RE = /^(\d{4})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/;

function pad(n: number, width: number): string {
  return String(n).padStart(width, '0');
}

function toUtcMs(d: string): number {
  const m = DATE_RE.exec(d);
  if (!m) throw new Error(`invalid date: ${d}`);
  return Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
}

function fromUtcMs(ms: number): string {
  const dt = new Date(ms);
  return `${pad(dt.getUTCFullYear(), 4)}-${pad(dt.getUTCMonth() + 1, 2)}-${pad(dt.getUTCDate(), 2)}`;
}

function parseMonth(ym: string): { year: number; month: number } {
  const m = /^(\d{4})-(0[1-9]|1[0-2])$/.exec(ym);
  if (!m) throw new Error(`invalid month: ${ym}`);
  return { year: Number(m[1]), month: Number(m[2]) };
}

/** True when `d` is `YYYY-MM-DD` and a real calendar date (e.g. rejects 2026-02-31). */
export function isValidDate(d: string): boolean {
  const m = DATE_RE.exec(d);
  if (!m) return false;
  return fromUtcMs(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]))) === d;
}

export function addDays(d: string, n: number): string {
  return fromUtcMs(toUtcMs(d) + n * MS_PER_DAY);
}

/** Day of week, 0 = Sunday … 6 = Saturday. */
export function weekday(d: string): number {
  return new Date(toUtcMs(d)).getUTCDay();
}

export function monthStart(ym: string): string {
  parseMonth(ym);
  return `${ym}-01`;
}

export function monthEnd(ym: string): string {
  const { year, month } = parseMonth(ym);
  // Day 0 of the next month is the last day of this one.
  return fromUtcMs(Date.UTC(year, month, 0));
}

export function addMonths(ym: string, n: number): string {
  const { year, month } = parseMonth(ym);
  const idx = year * 12 + (month - 1) + n;
  return `${pad(Math.floor(idx / 12), 4)}-${pad((((idx % 12) + 12) % 12) + 1, 2)}`;
}

/** Whole days from `a` to `b` (b − a). */
export function daysBetween(a: string, b: string): number {
  return Math.round((toUtcMs(b) - toUtcMs(a)) / MS_PER_DAY);
}
