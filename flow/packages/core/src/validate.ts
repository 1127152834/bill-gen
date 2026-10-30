/**
 * Hard rules R1-R11 over the assembled statements. One rule = one small function returning its
 * violations; `RULES` is the registry. Each violation names the offending transaction ids.
 * `target` says where a failure is sent back: the scheduler, or the life model.
 */
import type { TxnMeta } from './assemble.js';
import { nextBankBusinessDay } from './calendar.js';
import { addDays, addMonths, daysBetween } from './date.js';
import type { NormalizedRequest } from './normalize.js';
import { expectedCategories, looksLikePlaceholder } from './realism.js';
import { ROLLING_TYPES } from './schedule/place.js';
import { NATIONAL_STATE, type FinancialModel, type MerchantFact, type RecurringItem, type RuleId, type RuleResult, type Statement, type Transaction } from './schemas/index.js';

export type RuleContext = {
  norm: NormalizedRequest;
  model: FinancialModel;
  facts: MerchantFact[];
  /** From `assembleStatements`: links each transaction id back to its model item. */
  meta: Readonly<Record<string, TxnMeta>>;
};

type Violation = RuleResult['violations'][number];
type Check = (statements: Statement[], ctx: RuleContext) => Violation[];

const MAX_TRANSACTION_LAG_DAYS = 5;
const MAX_TRAVEL_SHARE = 0.1;
/** Below this many debits one trip is not a pattern: the travel share is not checked. */
const MIN_DEBITS_FOR_TRAVEL_SHARE = 20;
/** The card posts a payment this many days after checking sends it, at most. */
const MAX_CARD_POSTING_LAG = 2;
const FUEL_GALLONS = { min: 8, max: 16 } as const;
const TIP_FACTOR = 1.22;
const ROUNDING_SLACK_CENTS = 100;
const PAYROLL_WORDS = /PAYROLL|DIRECT DEP|SALARY/i;

const isCredit = (s: Statement): boolean => s.accountType === 'credit';
const cyclesOf = (s: Statement, norm: NormalizedRequest) => (isCredit(s) ? norm.cycles.credit : norm.cycles.checking) ?? [];
const within = (d: string, c: { start: string; end: string }): boolean => d >= c.start && d <= c.end;
const ids = (ts: readonly Transaction[]): string[] => ts.map((t) => t.id);
const signed = (t: Transaction): number => (t.direction === 'credit' ? t.amountCents : -t.amountCents);
const flat = (statements: Statement[]): Transaction[] => statements.flatMap((s) => s.transactions);
const named = (s: Statement): string => `${s.accountType} ****${s.accountLast4}`;

/** R1: every cycle of every requested account holds a number of rows inside its {min, max}. */
const r1Count: Check = (statements, { norm }) => {
  const missing = (['checking', 'credit'] as const)
    .filter((a) => norm.request.accounts[a] && !statements.some((s) => isCredit(s) === (a === 'credit')))
    .map((a) => ({ transactionIds: [], message: `no ${a} statement although the ${a} account was requested` }));
  const counts = statements.flatMap((s) => {
    const range = norm.request.accounts[isCredit(s) ? 'credit' : 'checking'];
    if (!range) return [{ transactionIds: ids(s.transactions), message: `${named(s)} statement but that account was not requested` }];
    return cyclesOf(s, norm).flatMap((c) => {
      const rows = s.transactions.filter((t) => within(t.postedDate, { start: c.start, end: c.end }));
      if (rows.length >= range.min && rows.length <= range.max) return [];
      return [{ transactionIds: ids(rows), message: `${named(s)} cycle ${c.start}..${c.end}: ${rows.length} rows, expected ${range.min}..${range.max}` }];
    });
  });
  return [...missing, ...counts];
};

/** R2: running balances chain from the opening balance; month summaries add up and link up. */
const r2Balance: Check = (statements, { model }) =>
  statements.flatMap((s): Violation[] => {
    const out: Violation[] = [];
    const credit = isCredit(s);
    let expectedOpening = credit ? (model.creditOpeningBalanceCents ?? 0) : model.openingBalanceCents;
    if (!credit) {
      let bal = s.checking?.openingBalanceCents;
      if (bal !== expectedOpening) out.push({ transactionIds: [], message: `${named(s)} opens at ${bal}, model opens at ${expectedOpening}` });
      for (const t of s.transactions) {
        const expected = (bal ?? expectedOpening) + signed(t);
        if (t.runningBalanceCents !== expected) {
          out.push({ transactionIds: [t.id], message: `${named(s)} running balance after ${t.id} is ${t.runningBalanceCents}, expected ${expected}` });
        }
        bal = t.runningBalanceCents ?? expected;
      }
    }
    for (const m of s.months) {
      const rows = s.transactions.filter((t) => within(t.postedDate, { start: m.cycleStart, end: m.cycleEnd }));
      const credits = rows.filter((t) => t.direction === 'credit').reduce((a, t) => a + t.amountCents, 0);
      const debits = rows.filter((t) => t.direction === 'debit').reduce((a, t) => a + t.amountCents, 0);
      const closing = credit ? m.openingBalanceCents + debits - credits : m.openingBalanceCents + credits - debits;
      const where = `${named(s)} ${m.cycleStart}..${m.cycleEnd}`;
      const problems = [
        m.openingBalanceCents !== expectedOpening && `opens at ${m.openingBalanceCents}, previous close was ${expectedOpening}`,
        m.totalCreditsCents !== credits && `credits total ${m.totalCreditsCents}, rows add to ${credits}`,
        m.totalDebitsCents !== debits && `debits total ${m.totalDebitsCents}, rows add to ${debits}`,
        m.transactionCount !== rows.length && `count ${m.transactionCount}, rows ${rows.length}`,
        m.closingBalanceCents !== closing && `closes at ${m.closingBalanceCents}, rows give ${closing}`,
        m.credit && m.credit.statementBalanceCents !== m.closingBalanceCents && `statement balance ${m.credit.statementBalanceCents} differs from close ${m.closingBalanceCents}`,
      ].filter((p): p is string => typeof p === 'string');
      for (const p of problems) out.push({ transactionIds: ids(rows), message: `${where}: ${p}` });
      expectedOpening = m.closingBalanceCents;
    }
    const last = s.months.at(-1);
    if (!credit && last && s.checking && s.checking.closingBalanceCents !== last.closingBalanceCents) {
      out.push({ transactionIds: [], message: `${named(s)} closes at ${s.checking.closingBalanceCents}, last month closes at ${last.closingBalanceCents}` });
    }
    return out;
  });

/** R3: postedDate inside one of the account's cycles; transactionDate 0-5 days before it. */
const r3Dates: Check = (statements, { norm }) =>
  statements.flatMap((s) => {
    const cycles = cyclesOf(s, norm);
    return s.transactions.flatMap((t): Violation[] => {
      if (!cycles.some((c) => within(t.postedDate, c))) {
        return [{ transactionIds: [t.id], message: `${t.id} posts ${t.postedDate}, outside every ${s.accountType} cycle` }];
      }
      if (t.transactionDate > t.postedDate || t.transactionDate < addDays(t.postedDate, -MAX_TRANSACTION_LAG_DAYS)) {
        return [{ transactionIds: [t.id], message: `${t.id} transacted ${t.transactionDate}, must be within ${MAX_TRANSACTION_LAG_DAYS} days before posting ${t.postedDate}` }];
      }
      return [];
    });
  });

/** Could `txDate` be the (possibly rolled) date this item lands on? Mirrors `recurringOccurrences`. */
function recurringDateFits(item: RecurringItem, txDate: string, norm: NormalizedRequest): boolean {
  const land = (d: string): string => (ROLLING_TYPES.has(item.txnType) ? nextBankBusinessDay(d) : d);
  const rule = item.dayRule;
  switch (rule.kind) {
    case 'dayOfMonth': {
      const ym = txDate.slice(0, 7);
      return [ym, addMonths(ym, -1)].some((m) => land(`${m}-${String(rule.day).padStart(2, '0')}`) === txDate);
    }
    case 'taxDates':
      return norm.taxDates.some((d) => land(d) === txDate);
    case 'biweekly':
      return Array.from({ length: 7 }, (_, k) => addDays(txDate, -k)).some(
        (d) => daysBetween(rule.anchor, d) % 14 === 0 && land(d) === txDate,
      );
  }
}

function recurringFits(item: RecurringItem, t: Transaction, norm: NormalizedRequest): boolean {
  const a = item.amount;
  const amountOk = a.kind === 'fixed' ? t.amountCents === a.cents : t.amountCents >= a.minCents && t.amountCents <= a.maxCents;
  return amountOk && t.type === item.txnType && t.direction === 'debit' && recurringDateFits(item, t.transactionDate, norm);
}

/** R4: every recurring row has its item's amount, type and day-of-month / tax / biweekly date. */
const r4Recurring: Check = (statements, { norm, model, meta }) =>
  flat(statements).flatMap((t): Violation[] => {
    const m = meta[t.id];
    if (m?.source !== 'recurring') return [];
    const items = model.recurring.filter((i) => (i.factId ?? i.id) === m.merchantKey);
    if (items.some((i) => recurringFits(i, t, norm))) return [];
    return [{ transactionIds: [t.id], message: `${t.id}: ${t.amountCents} cents on ${t.transactionDate} does not fit recurring item ${m.merchantKey}` }];
  });

/** R5: both accounts present - every card payment has its other side (see the pairing ruling). */
const r5CardPayment: Check = (statements, { norm, meta }) => {
  const checking = statements.find((s) => !isCredit(s));
  const credit = statements.find(isCredit);
  const creditCycles = norm.cycles.credit;
  const checkingCycles = norm.cycles.checking;
  if (!checking || !credit || !creditCycles || !checkingCycles) return [];

  const isPayment = (t: Transaction): boolean => meta[t.id]?.merchantKey === 'card-payment';
  const debits = checking.transactions.filter((t) => isPayment(t) && t.direction === 'debit');
  const payments = credit.transactions.filter((t) => isPayment(t) && t.type === 'PAYMENT' && t.direction === 'credit');
  const firstStart = creditCycles[0]!.start;
  const lastEnd = creditCycles.at(-1)!.end;
  const out: Violation[] = [];

  const used = new Set<string>();
  for (const d of debits) {
    const match = payments.find((p) => {
      const gap = daysBetween(d.postedDate, p.postedDate);
      return !used.has(p.id) && p.amountCents === d.amountCents && gap >= 0 && gap <= MAX_CARD_POSTING_LAG;
    });
    if (match) used.add(match.id);
    // The card side is certain to post inside the credit period only when even the slowest posting stays inside it.
    else if (d.postedDate >= firstStart && daysBetween(d.postedDate, lastEnd) >= MAX_CARD_POSTING_LAG) {
      out.push({ transactionIds: [d.id], message: `checking payment ${d.id} has no matching credit PAYMENT` });
    }
  }

  const usedDebits = new Set<string>();
  for (const p of payments) {
    if (!checkingCycles.some((c) => within(p.transactionDate, c))) continue;
    const match = debits.find((d) => !usedDebits.has(d.id) && d.postedDate === p.transactionDate && d.amountCents === p.amountCents);
    if (match) usedDebits.add(match.id);
    else out.push({ transactionIds: [p.id], message: `credit PAYMENT ${p.id} sent ${p.transactionDate} has no matching checking debit` });
  }
  return out;
};

/** R6: discretionary amounts stay within the item's ticket range (tax and tip allowed; fuel by gallons). */
const r6Amount: Check = (statements, { norm, model, meta }) =>
  flat(statements).flatMap((t): Violation[] => {
    const m = meta[t.id];
    const item = m?.source === 'discretionary' ? model.discretionaryPool.find((i) => i.factId === m.factId) : undefined;
    if (!item) return [];
    const unit = item.unitPriceCents;
    const [lo, hi] =
      item.pricing === 'fuel' && unit !== undefined
        ? [unit * FUEL_GALLONS.min - ROUNDING_SLACK_CENTS, unit * FUEL_GALLONS.max + ROUNDING_SLACK_CENTS]
        : [item.ticketCents.min, item.ticketCents.max * (1 + norm.location.combinedSalesTaxRate) * TIP_FACTOR + ROUNDING_SLACK_CENTS];
    if (t.amountCents >= lo && t.amountCents <= hi) return [];
    return [{ transactionIds: [t.id], message: `${t.id}: ${t.amountCents} cents outside ${Math.ceil(lo)}..${Math.floor(hi)} for ${item.factId}` }];
  });

/**
 * R7: `local` rows are in the persona's state (national companies, state 'US', are exempt);
 * `travel` rows are at most 10% of all debits (checked from 20 debits on).
 */
const r7Geo: Check = (statements, { norm, meta }) => {
  const all = flat(statements);
  const state = norm.location.state;
  const wrongState = all.flatMap((t): Violation[] => {
    if (meta[t.id]?.location !== 'local') return [];
    if (t.merchant?.state === state || t.merchant?.state === NATIONAL_STATE) return [];
    return [{ transactionIds: [t.id], message: `${t.id}: local merchant is in ${t.merchant?.state ?? 'an unknown state'}, persona lives in ${state}` }];
  });
  const debits = all.filter((t) => t.direction === 'debit');
  const travel = debits.filter((t) => meta[t.id]?.location === 'travel');
  const tooMuchTravel =
    debits.length >= MIN_DEBITS_FOR_TRAVEL_SHARE && travel.length > debits.length * MAX_TRAVEL_SHARE
      ? [{ transactionIds: ids(travel), message: `${travel.length} of ${debits.length} debits are travel, above ${MAX_TRAVEL_SHARE * 100}%` }]
      : [];
  return [...wrongState, ...tooMuchTravel];
};

/**
 * R8: within a statement, no two rows share posted date, description, amount and direction.
 *
 * Noise rows are exempt: a double charge that came back is one of the most common things a real
 * statement shows, and flagging it would send the schedule back for looking too human.
 */
const r8Duplicates: Check = (statements, { meta }) =>
  statements.flatMap((s) => {
    const groups = new Map<string, Transaction[]>();
    for (const t of s.transactions) {
      const key = `${t.postedDate}|${t.description}|${t.amountCents}|${t.direction}`;
      groups.set(key, [...(groups.get(key) ?? []), t]);
    }
    return [...groups.values()]
      .filter((g) => g.length > 1 && g.some((t) => meta[t.id]?.source !== 'noise'))
      .map((g) => ({ transactionIds: ids(g), message: `${named(s)}: ${g.length} identical rows (${g[0]!.postedDate} ${g[0]!.description} ${g[0]!.amountCents})` }));
  });

/** R9: every discretionary row and every recurring row with a factId cites a known fact and a source URL. */
const r9Evidence: Check = (statements, { facts, meta }) => {
  const known = new Set(facts.map((f) => f.id));
  return flat(statements).flatMap((t): Violation[] => {
    const m = meta[t.id];
    if (!m || (m.source !== 'discretionary' && !(m.source === 'recurring' && m.factId !== undefined))) return [];
    const e = t.evidence;
    if (!e?.sourceUrl) return [{ transactionIds: [t.id], message: `${t.id} has no evidence.sourceUrl` }];
    if (!known.has(e.factId) || e.factId !== m.factId) return [{ transactionIds: [t.id], message: `${t.id} cites fact ${e.factId}, expected a researched ${m.factId}` }];
    return [];
  });
};

/** R10: a personal persona is self-employed - no incoming row may read like payroll. */
const r10NoPayroll: Check = (statements, { norm }) =>
  norm.request.subjectType !== 'personal'
    ? []
    : flat(statements)
        .filter((t) => t.direction === 'credit' && PAYROLL_WORDS.test(t.description))
        .map((t) => ({ transactionIds: [t.id], message: `${t.id}: personal income described as "${t.description}"` }));

/** R11: no credit statement balance is above the card's limit. */
const r11CreditLimit: Check = (statements) =>
  statements.filter(isCredit).flatMap((s) => {
    const limit = s.credit?.creditLimitCents;
    if (limit === undefined) return [];
    return s.months
      .filter((m) => (m.credit?.statementBalanceCents ?? m.closingBalanceCents) > limit)
      .map((m) => ({
        transactionIds: ids(s.transactions.filter((t) => within(t.postedDate, { start: m.cycleStart, end: m.cycleEnd }))),
        message: `${named(s)} ${m.cycleStart}..${m.cycleEnd}: statement balance ${m.credit?.statementBalanceCents ?? m.closingBalanceCents} is above the credit limit ${limit}`,
      }));
  });

/** R12: 名字里不能出现 ACME/EXAMPLE/SAMPLE 这类占位词。 */
const r12NoPlaceholders: Check = (statements) => {
  const offenders = flat(statements).filter(
    (t) => looksLikePlaceholder(t.description) || looksLikePlaceholder(t.merchant?.name) || looksLikePlaceholder(t.memo),
  );
  return offenders.map((t) => ({
    transactionIds: [t.id],
    message: `${t.id}: "${t.description}" reads like a placeholder name`,
  }));
};

/** R13: 职业决定了账单里该出现哪几类消费（barista 从不买咖啡才假）。 */
const r13OccupationFit: Check = (statements, { norm, meta, facts }) => {
  const occupation = (norm.request.profile as { occupation?: string } | undefined)?.occupation;
  if (norm.request.subjectType !== 'personal') return [];
  // 只有支票账户时，日常消费根本不出现在账单上（那都在卡里），无从判断。
  if (!statements.some(isCredit)) return [];
  const wanted = expectedCategories(occupation);
  if (wanted.length === 0) return [];
  const byId = new Map(facts.map((f) => [f.id, f]));
  const seen = new Set<string>();
  for (const t of flat(statements)) {
    if (t.direction !== 'debit') continue;
    const factId = meta[t.id]?.factId;
    const category = (factId ? byId.get(factId)?.category : undefined) ?? t.merchant?.category;
    if (category) seen.add(category.toLowerCase());
  }
  // 不必每个类目都出现：3 类里命中 2 类就算这个人的生活方式对得上。
  // 一类都沾不上（或只沾一类）才是「画像和账单对不上」。
  const missing = wanted.filter((c) => !seen.has(c));
  const hit = wanted.length - missing.length;
  if (hit >= Math.max(1, wanted.length - 1)) return [];
  return [
    {
      transactionIds: [],
      message: `occupation "${occupation}" implies ${wanted.join(' / ')} spending, but the statements never show ${missing.join(' / ')}`,
    },
  ];
};

/**
 * R14: 每个账期要有足够多的不同抬头。
 *
 * 一个只有八九个商户、每月都一模一样的账单不像人的生活，像一张表格。这里只设下限，不设上限：
 * 多样性越高越真实，不能因为「太杂」把一份好账单打回。
 */
const MIN_MERCHANTS_PER_CYCLE = 4;
/** 整份账单（≥2 个账期）至少这么多不同抬头，防止「每月就那几家」。 */
const MIN_MERCHANTS_PER_STATEMENT = 10;

const r14VarietyFloor: Check = (statements) =>
  statements.flatMap((s) => {
    const merchantsOf = (from: string, to: string): Set<string> => {
      const keys = new Set<string>();
      for (const t of s.transactions) {
        if (t.postedDate < from || t.postedDate > to) continue;
        // 还款、利息、手续费不是「去哪里花了钱」。
        if (t.type === 'PAYMENT' || t.type === 'INTEREST' || t.type === 'FEE') continue;
        keys.add(t.merchant?.name ?? t.description);
      }
      return keys;
    };
    const out: Violation[] = s.months.flatMap((m) => {
      const size = merchantsOf(m.cycleStart, m.cycleEnd).size;
      if (size >= MIN_MERCHANTS_PER_CYCLE) return [];
      return [
        {
          transactionIds: [],
          message: `${named(s)}: ${m.cycleStart.slice(0, 7)} 只有 ${size} 个不同商户，至少要 ${MIN_MERCHANTS_PER_CYCLE} 个`,
        },
      ];
    });
    if (s.months.length >= 2) {
      const size = merchantsOf(s.periodStart, s.periodEnd).size;
      if (size < MIN_MERCHANTS_PER_STATEMENT) {
        out.push({
          transactionIds: [],
          message: `${named(s)}: 整份账单只有 ${size} 个不同商户，至少要 ${MIN_MERCHANTS_PER_STATEMENT} 个`,
        });
      }
    }
    return out;
  });

/**
 * R15: 账单上不许出现线上支付 / 收单 / 平台结算的进账。
 *
 * 这份账单是拿去申请支付网关的：申请人此刻收不了线上付款，否则就没必要申请。任何一笔贷方
 * 行文里出现收单方、平台、网上商店的名字（STRIPE、SQUARE、PAYPAL、SHOPIFY、ETSY、UBER…），
 * 这张账单自己就否掉了申请资格。只查进账方向，不碰支出（在 Amazon 买东西当然可以）。
 */
const ONLINE_PAY_INCOME = /\b(stripe|square|squareup|shopify|paypal|braintree|adyen|gumroad|etsy|upwork|fiverr|patreon|substack|uber|lyft|doordash|instacart|grubhub|airbnb|vrbo|youtube|twitch|teespring|ko-?fi)\b|marketplace payout|platform payout|merchant services/i;

const r15NoGatewayIncome: Check = (statements) =>
  flat(statements)
    .filter((t) => t.direction === 'credit' && ONLINE_PAY_INCOME.test(`${t.description} ${t.memo ?? ''}`))
    .map((t) => ({
      transactionIds: [t.id],
      message: `${t.id}: 进账 "${t.description}" 读起来像收单/平台结算；申请网关的人还没有线上收款能力`,
    }));

const RULES: readonly { id: RuleId; target: RuleResult['target']; check: Check }[] = [
  { id: 'R1_count_in_range', target: 'schedule', check: r1Count },
  { id: 'R2_balance_continuity', target: 'schedule', check: r2Balance },
  { id: 'R3_dates_in_period', target: 'schedule', check: r3Dates },
  { id: 'R4_recurring_consistency', target: 'schedule', check: r4Recurring },
  { id: 'R5_card_payment_match', target: 'schedule', check: r5CardPayment },
  { id: 'R6_amount_within_range', target: 'lifeModel', check: r6Amount },
  { id: 'R7_geo_consistency', target: 'lifeModel', check: r7Geo },
  { id: 'R8_no_duplicates', target: 'schedule', check: r8Duplicates },
  { id: 'R9_evidence_present', target: 'lifeModel', check: r9Evidence },
  { id: 'R10_no_payroll_income_personal', target: 'lifeModel', check: r10NoPayroll },
  { id: 'R11_credit_within_limit', target: 'lifeModel', check: r11CreditLimit },
  { id: 'R12_no_placeholder_names', target: 'lifeModel', check: r12NoPlaceholders },
  { id: 'R13_occupation_category_fit', target: 'lifeModel', check: r13OccupationFit },
  { id: 'R14_variety_floor', target: 'schedule', check: r14VarietyFloor },
  { id: 'R15_no_gateway_income', target: 'schedule', check: r15NoGatewayIncome },
];

/** Runs every rule in order; a rule with no violations passes. */
export function runRules(statements: Statement[], ctx: RuleContext): RuleResult[] {
  return RULES.map(({ id, target, check }) => {
    const violations = check(statements, ctx);
    return { ruleId: id, passed: violations.length === 0, violations, target };
  });
}
