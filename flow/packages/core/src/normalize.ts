import { randomUUID } from 'node:crypto';
import { bankHolidays, checkingCycles, creditCycles, estimatedTaxDates, type Cycle } from './calendar.js';
import { addMonths } from './date.js';
import { lookupLocation, metroToLocation, pickWeightedMetro, type LocationInfo } from './geo.js';
import { createRng } from './rng.js';
import type { GenerationRequest } from './schemas/index.js';

export class InfeasibleRequestError extends Error {
  readonly code = 'INFEASIBLE_REQUEST';
  constructor(message: string) {
    super(message);
    this.name = 'InfeasibleRequestError';
  }
}

export type NormalizedRequest = {
  request: GenerationRequest & { seed: string; endMonth: string };
  location: LocationInfo;
  closingDay?: number;
  /** 支票账户的结单日（开户日），1–28；决定每月账单的起止。 */
  anchorDay?: number;
  cycles: { checking?: Cycle[]; credit?: Cycle[] };
  /** Earliest cycle start across all requested accounts. */
  periodStart: string;
  /** Latest cycle end across all requested accounts. */
  periodEnd: string;
  holidays: string[];
  taxDates: string[];
};

/** Fill defaults, derive cycles / calendar facts / location. Pure and offline; `today` is `YYYY-MM-DD`. */
export function normalizeRequest(req: GenerationRequest, today: string): NormalizedRequest {
  assertFeasible(req);

  const endMonth = req.endMonth ?? addMonths(today.slice(0, 7), -1);
  const seed = req.seed ?? randomUUID();
  const request = { ...req, endMonth, seed };

  const closingDay = req.accounts.credit
    ? createRng(seed).fork('closing').int(1, 28)
    : undefined;

  const cycles: NormalizedRequest['cycles'] = {};
  // 结单日跟着「开户日」走：同一账户每月同一天结单，不是自然月。
  // 只有显式给了 checkingAnchorDay 才滚 —— 自然月是历史默认，改不动。
  const anchorDay = req.accounts.checking ? req.checkingAnchorDay : undefined;
  if (req.accounts.checking) cycles.checking = checkingCycles(endMonth, req.months, anchorDay);
  if (req.accounts.credit) cycles.credit = creditCycles(endMonth, req.months, closingDay!);

  const all = [...(cycles.checking ?? []), ...(cycles.credit ?? [])];
  const periodStart = all.map((c) => c.start).sort()[0]!;
  const periodEnd = all.map((c) => c.end).sort().at(-1)!;

  const firstYear = Number(periodStart.slice(0, 4));
  const lastYear = Number(periodEnd.slice(0, 4));
  const years = Array.from({ length: lastYear - firstYear + 1 }, (_, i) => firstYear + i);

  const holidays = [...new Set(years.flatMap(bankHolidays))].sort();
  const taxDates = [...new Set(years.flatMap((y) => [...estimatedTaxDates(y - 1), ...estimatedTaxDates(y)]))]
    .filter((d) => d >= periodStart && d <= periodEnd)
    .sort();

  const address = req.profile?.address;
  const location = address
    ? lookupLocation(address.city, address.state)
    : metroToLocation(pickWeightedMetro(createRng(seed).fork('location')));

  return {
    request,
    location,
    ...(closingDay !== undefined && { closingDay }),
    ...(anchorDay !== undefined && { anchorDay }),
    cycles,
    periodStart,
    periodEnd,
    holidays,
    taxDates,
  };
}

/** Base required monthly count: checking 2 (+1 when a credit account also exists); credit 1. */
function assertFeasible(req: GenerationRequest): void {
  const { checking, credit } = req.accounts;
  const checkingNeeded = credit ? 3 : 2;
  if (checking && checking.max < checkingNeeded) {
    throw new InfeasibleRequestError(
      `checking max (${checking.max}) is below the base required count: need at least ${checkingNeeded}` +
        (credit ? ' (2 plus 1 for the credit-card payment)' : ''),
    );
  }
  if (credit && credit.max < 1) {
    throw new InfeasibleRequestError(`credit max (${credit.max}) is below the base required count: need at least 1`);
  }
}
