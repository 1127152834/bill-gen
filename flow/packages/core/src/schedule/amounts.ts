/**
 * Amount generation for scheduled transactions. All money is integer cents; every random
 * draw goes through the supplied `Rng` so results are deterministic per seed.
 */
import type { Rng } from '../rng.js';
import type { DiscretionaryItem, IncomeStream, RecurringItem } from '../schemas/index.js';

/** Cents endings a menu-priced item can plausibly have. */
const MENU_ENDINGS = [0, 25, 49, 50, 75, 95, 99] as const;
/** Chance that a basket total is allowed to be a whole-dollar base. */
const WHOLE_DOLLAR_BASKET_CHANCE = 0.03;
const TIP_MIN = 0.15;
const TIP_MAX = 0.22;
/** Share of the range, from the top, that peak months draw from (and the bottom share for others). */
const PEAK_SHARE = 0.4;

/** Gasoline: round(price/gal × gallons), gallons uniform in 8.000–16.000 (3 decimals). Never taxed or tipped. */
export function fuelAmount(pricePerGallonCents: number, rng: Rng): number {
  const milliGallons = rng.int(8000, 16000);
  return Math.round((pricePerGallonCents * milliGallons) / 1000);
}

function basketBase(min: number, max: number, rng: Rng): number {
  let base = rng.int(min, max);
  if (base % 100 === 0 && !rng.chance(WHOLE_DOLLAR_BASKET_CHANCE)) {
    // Nudge off the whole dollar, staying inside the ticket range when possible.
    const delta = rng.int(1, 99);
    if (base + delta <= max) base += delta;
    else if (base - delta >= min) base -= delta;
  }
  return base;
}

function menuBase(min: number, max: number, rng: Rng): number {
  const loDollar = Math.floor(min / 100);
  const hiDollar = Math.floor(max / 100);
  for (let attempt = 0; attempt < 50; attempt++) {
    const v = rng.int(loDollar, hiDollar) * 100 + rng.pick(MENU_ENDINGS);
    if (v >= min && v <= max) return v;
  }
  // Range too narrow to hold a menu price: fall back to the closest in-range value.
  return Math.min(max, Math.max(min, rng.int(loDollar, hiDollar) * 100 + 99));
}

/**
 * One discretionary card purchase.
 * - fuel: `fuelAmount(unitPriceCents)`, no tax/tip.
 * - fixed_menu: menu-style base in `ticketCents`; basket: random-cents base in `ticketCents`.
 * - then `round(base × (1 + taxRate))` when taxable, plus a 15–22% tip on the pre-tax base when
 *   `tip` (and a 50% chance the total is rounded up to a whole dollar).
 */
export function discretionaryAmount(item: DiscretionaryItem, taxRate: number, rng: Rng): number {
  if (item.pricing === 'fuel') {
    if (item.unitPriceCents === undefined) {
      throw new Error(`discretionary item ${item.factId}: unitPriceCents is required for fuel pricing`);
    }
    return fuelAmount(item.unitPriceCents, rng);
  }

  const { min, max } = item.ticketCents;
  const base = item.pricing === 'fixed_menu' ? menuBase(min, max, rng) : basketBase(min, max, rng);

  let total = item.taxable ? Math.round(base * (1 + taxRate)) : base;
  if (item.tip) {
    total += Math.round(base * (TIP_MIN + (TIP_MAX - TIP_MIN) * rng.float()));
    if (rng.chance(0.5)) total = Math.ceil(total / 100) * 100;
  }
  return total;
}

/** Recurring bill amount for a given calendar month (1–12). `fixed` is identical every time. */
export function recurringAmount(item: RecurringItem, month: number, rng: Rng): number {
  const a = item.amount;
  if (a.kind === 'fixed') return a.cents;
  const range = a.maxCents - a.minCents;
  const split = Math.round(range * (1 - PEAK_SHARE)); // peak: top 40%, otherwise bottom 60%
  return a.peakMonths.includes(month)
    ? rng.int(a.minCents + split, a.maxCents)
    : rng.int(a.minCents, a.minCents + split);
}

/** Income amount for a calendar month (1–12): uniform in `amountCents`, times the month's seasonality. */
export function incomeAmount(stream: IncomeStream, month: number, rng: Rng): number {
  const raw = rng.int(stream.amountCents.min, stream.amountCents.max);
  return Math.round(raw * stream.seasonality[month - 1]!);
}
