import { describe, expect, it } from 'vitest';
import { RuleIdSchema, addDays, runRules, type RuleId, type RuleResult, type Statement, type Transaction, type TxnMeta } from '../src/index.js';
import { FIXTURES, type Accounts } from './schedule-helpers.js';
import { build, recompute, type Built } from './statement-helpers.js';

const ALL_RULES = RuleIdSchema.options;

const run = (b: Built, statements: Statement[] = b.statements, meta: Record<string, TxnMeta> = b.meta): RuleResult[] =>
  runRules(statements, { ...b.ctx, meta });

const failing = (results: RuleResult[]): RuleId[] => results.filter((r) => !r.passed).map((r) => r.ruleId);

/** Asserts exactly `id` fails, and that its violations name transactions (or are month/statement level). */
function expectOnlyFails(results: RuleResult[], id: RuleId): RuleResult {
  expect(failing(results)).toEqual([id]);
  const r = results.find((x) => x.ruleId === id)!;
  expect(r.violations.length).toBeGreaterThan(0);
  for (const v of r.violations) expect(v.message.length).toBeGreaterThan(0);
  return r;
}

const rowsOf = (b: Built, accountType: Statement['accountType'] | 'checking-any'): Transaction[] =>
  b.statements.filter((s) => (accountType === 'credit' ? s.accountType === 'credit' : s.accountType !== 'credit')).flatMap((s) => s.transactions);

const pick = (rows: Transaction[], b: Built, pred: (m: TxnMeta, t: Transaction) => boolean): Transaction => {
  const t = rows.find((x) => pred(b.meta[x.id]!, x));
  if (!t) throw new Error('test precondition: no matching transaction');
  return t;
};

/** Edits one transaction and rebuilds every derived number, so only the targeted rule can break. */
function edit(b: Built, id: string, f: (t: Transaction) => Transaction): Statement[] {
  const next = b.statements.map((s) => ({ ...s, transactions: s.transactions.map((t) => (t.id === id ? f(t) : t)) }));
  return recompute(next, b.norm, b.model);
}

describe('runRules', () => {
  it('returns one result per rule, in order, with the documented targets', () => {
    const results = run(build('freelancer', 'alpha'));
    expect(results.map((r) => r.ruleId)).toEqual(ALL_RULES);
    expect(results.map((r) => [r.ruleId, r.target])).toEqual([
      ['R1_count_in_range', 'schedule'],
      ['R2_balance_continuity', 'schedule'],
      ['R3_dates_in_period', 'schedule'],
      ['R4_recurring_consistency', 'schedule'],
      ['R5_card_payment_match', 'schedule'],
      ['R6_amount_within_range', 'lifeModel'],
      ['R7_geo_consistency', 'lifeModel'],
      ['R8_no_duplicates', 'schedule'],
      ['R9_evidence_present', 'lifeModel'],
      ['R10_no_payroll_income_personal', 'lifeModel'],
      ['R11_credit_within_limit', 'lifeModel'],
      ['R12_no_placeholder_names', 'lifeModel'],
      ['R13_occupation_category_fit', 'lifeModel'],
    ]);
  });

  it('recompute() is the identity on assembled statements (so the bad fixtures differ only by the edit)', () => {
    const b = build('rideshare', 'bravo', 'both', 3);
    expect(recompute(b.statements, b.norm, b.model)).toEqual(b.statements);
  });
});

describe('good statements (fixture model -> schedule -> assemble) pass every rule', () => {
  const SEEDS = ['alpha', 'bravo', 'charlie', 'delta'];
  const ACCOUNTS: Accounts[] = ['both', 'checking', 'credit'];
  for (const fixture of FIXTURES) {
    for (const accounts of ACCOUNTS) {
      it(`${fixture} / ${accounts}`, () => {
        SEEDS.forEach((seed, i) => {
          const b = build(fixture, seed, accounts, i % 2 === 0 ? 6 : 3);
          const results = run(b);
          expect(results.filter((r) => !r.passed).map((r) => [seed, r.ruleId, r.violations[0]?.message]), `${fixture} ${accounts} ${seed}`).toEqual([]);
        });
      });
    }
  }
});

describe('R1_count_in_range', () => {
  it('fails when a cycle drops below the minimum', () => {
    const b = build('freelancer', 'alpha', 'both', 3, { checking: { min: 12, max: 12 }, credit: { min: 12, max: 12 } });
    expect(failing(run(b))).toEqual([]);
    const victim = pick(rowsOf(b, 'checking-any'), b, (m) => m.source === 'discretionary');
    const statements = recompute(
      b.statements.map((s) => ({ ...s, transactions: s.transactions.filter((t) => t.id !== victim.id) })),
      b.norm,
      b.model,
    );
    const r = expectOnlyFails(run(b, statements), 'R1_count_in_range');
    expect(r.violations[0]!.message).toMatch(/11 rows, expected 12\.\.12/);
    expect(r.violations[0]!.transactionIds.length).toBe(11);
  });

  it('fails when a requested account has no statement', () => {
    const b = build('freelancer', 'alpha', 'both', 3);
    const results = run(b, [b.statements[0]!]);
    expect(results.find((r) => r.ruleId === 'R1_count_in_range')!.violations[0]!.message).toMatch(/no credit statement/);
  });
});

describe('R2_balance_continuity', () => {
  it('fails when a running balance is off', () => {
    const b = build('freelancer', 'alpha');
    const statements = b.statements.map((s) =>
      s.accountType === 'credit'
        ? s
        : { ...s, transactions: s.transactions.map((t, i) => (i === 7 ? { ...t, runningBalanceCents: t.runningBalanceCents! + 100 } : t)) },
    );
    const r = expectOnlyFails(run(b, statements), 'R2_balance_continuity');
    expect(r.violations[0]!.transactionIds).toEqual([b.statements[0]!.transactions[7]!.id]);
  });

  it('fails when a month summary does not link to the previous close', () => {
    const b = build('rideshare', 'alpha', 'checking');
    const s = b.statements[0]!;
    const months = s.months.map((m, i) => (i === 1 ? { ...m, openingBalanceCents: m.openingBalanceCents + 1 } : m));
    expectOnlyFails(run(b, [{ ...s, months }]), 'R2_balance_continuity');
  });

  it('fails when a credit statement balance does not follow from the rows', () => {
    const b = build('rideshare', 'alpha', 'credit');
    const s = b.statements[0]!;
    const months = s.months.map((m, i) => (i === 0 ? { ...m, closingBalanceCents: m.closingBalanceCents + 500, credit: { ...m.credit!, statementBalanceCents: m.closingBalanceCents + 500 } } : m));
    expect(failing(run(b, [{ ...s, months }]))).toContain('R2_balance_continuity');
  });
});

describe('R3_dates_in_period', () => {
  it('fails when the transaction date is more than 5 days before posting', () => {
    const b = build('freelancer', 'alpha');
    const t = pick(rowsOf(b, 'checking-any'), b, (m) => m.source === 'discretionary');
    const r = expectOnlyFails(run(b, edit(b, t.id, (x) => ({ ...x, transactionDate: addDays(x.postedDate, -6) }))), 'R3_dates_in_period');
    expect(r.violations[0]!.transactionIds).toEqual([t.id]);
  });

  it('fails when the transaction date is after posting', () => {
    const b = build('freelancer', 'alpha');
    const t = pick(rowsOf(b, 'credit'), b, (m) => m.source === 'discretionary');
    expectOnlyFails(run(b, edit(b, t.id, (x) => ({ ...x, transactionDate: addDays(x.postedDate, 1) }))), 'R3_dates_in_period');
  });

  it('fails when a row posts outside every cycle of its account', () => {
    const b = build('freelancer', 'alpha', 'checking');
    const t = pick(rowsOf(b, 'checking-any'), b, (m) => m.source === 'discretionary');
    const statements = edit(b, t.id, (x) => ({ ...x, transactionDate: '2020-01-02', postedDate: '2020-01-03' }));
    const results = run(b, statements);
    expect(failing(results)).toContain('R3_dates_in_period');
    expect(results.find((r) => r.ruleId === 'R3_dates_in_period')!.violations[0]!.transactionIds).toContain(t.id);
  });
});

describe('R4_recurring_consistency', () => {
  const rent = (b: Built): Transaction => pick(rowsOf(b, 'checking-any'), b, (m) => m.merchantKey === 'fact-camden-lamar-heights-austin');

  it('fails when a fixed bill changes amount', () => {
    const b = build('freelancer', 'alpha');
    const t = rent(b);
    const r = expectOnlyFails(run(b, edit(b, t.id, (x) => ({ ...x, amountCents: x.amountCents + 1 }))), 'R4_recurring_consistency');
    expect(r.violations[0]!.transactionIds).toEqual([t.id]);
  });

  it('fails when a bill lands on the wrong day', () => {
    const b = build('freelancer', 'alpha');
    const t = rent(b);
    expectOnlyFails(run(b, edit(b, t.id, (x) => ({ ...x, transactionDate: addDays(x.transactionDate, -1) }))), 'R4_recurring_consistency');
  });

  it('fails when a seasonal bill leaves its range', () => {
    const b = build('freelancer', 'alpha');
    const t = pick(rowsOf(b, 'checking-any'), b, (m) => m.merchantKey === 'fact-austin-energy');
    expectOnlyFails(run(b, edit(b, t.id, (x) => ({ ...x, amountCents: 24101 }))), 'R4_recurring_consistency');
  });

  it('accepts biweekly and tax-date items on their rolled dates', () => {
    const b = build('cafe-business', 'alpha', 'checking', 6);
    const payroll = rowsOf(b, 'checking-any').filter((t) => b.meta[t.id]!.merchantKey === 'fact-gusto-payroll');
    expect(payroll.length).toBeGreaterThan(2);
    expect(failing(run(b))).toEqual([]);
    const tax = build('freelancer', 'bravo', 'checking', 6);
    expect(rowsOf(tax, 'checking-any').some((t) => tax.meta[t.id]!.merchantKey === 'fact-irs-direct-pay')).toBe(true);
    expect(failing(run(tax))).toEqual([]);
  });
});

describe('R5_card_payment_match', () => {
  it('fails when the checking debit and the card credit disagree on amount', () => {
    const b = build('freelancer', 'alpha', 'both', 6);
    const t = pick(rowsOf(b, 'checking-any'), b, (m) => m.merchantKey === 'card-payment');
    const r = expectOnlyFails(run(b, edit(b, t.id, (x) => ({ ...x, amountCents: x.amountCents + 100 }))), 'R5_card_payment_match');
    const ids = r.violations.flatMap((v) => v.transactionIds);
    expect(ids).toContain(t.id);
    expect(r.violations.length).toBeGreaterThanOrEqual(2); // the orphaned debit and the orphaned credit
  });

  it('is skipped (passes) when only one account is present', () => {
    for (const accounts of ['checking', 'credit'] as const) {
      const b = build('freelancer', 'alpha', accounts, 3);
      const r = run(b).find((x) => x.ruleId === 'R5_card_payment_match')!;
      expect(r).toMatchObject({ passed: true, violations: [] });
    }
  });

  it('exempts a checking payment whose card side would post after the last credit cycle', () => {
    // Early closing days put the last statement's payment inside the final checking month.
    for (let i = 0; i < 60; i++) {
      const b = build('freelancer', `late-${i}`, 'both', 3);
      const lastEnd = b.norm.cycles.credit!.at(-1)!.end;
      const late = rowsOf(b, 'checking-any').find((t) => b.meta[t.id]!.merchantKey === 'card-payment' && t.postedDate > addDays(lastEnd, -2));
      if (!late) continue;
      const strip = b.statements.map((s) =>
        s.accountType !== 'credit'
          ? s
          : { ...s, transactions: s.transactions.filter((t) => !(b.meta[t.id]!.merchantKey === 'card-payment' && t.amountCents === late.amountCents)) },
      );
      const r = run(b, recompute(strip, b.norm, b.model)).find((x) => x.ruleId === 'R5_card_payment_match')!;
      expect(r.violations.flatMap((v) => v.transactionIds)).not.toContain(late.id);
      return;
    }
    throw new Error('test precondition: no seed produced a late card payment');
  });
});

describe('R6_amount_within_range', () => {
  it('fails when a discretionary amount is far above the ticket range', () => {
    const b = build('freelancer', 'alpha');
    const t = pick(rowsOf(b, 'credit'), b, (m) => m.factId === 'fact-epoch-coffee-austin');
    const r = expectOnlyFails(run(b, edit(b, t.id, (x) => ({ ...x, amountCents: 20000 }))), 'R6_amount_within_range');
    expect(r.violations[0]!.transactionIds).toEqual([t.id]);
  });

  it('fails when a discretionary amount is below the ticket range', () => {
    const b = build('freelancer', 'alpha');
    const t = pick(rowsOf(b, 'credit'), b, (m) => m.factId === 'fact-heb-austin');
    expectOnlyFails(run(b, edit(b, t.id, (x) => ({ ...x, amountCents: 100 }))), 'R6_amount_within_range');
  });

  it('checks fuel by gallons: unit price x 8..16 gallons (+-$1), tighter than the ticket range', () => {
    const b = build('freelancer', 'alpha', 'credit', 6);
    const fuel = pick(rowsOf(b, 'credit'), b, (m) => m.factId === 'fact-shell-burnet-rd-austin');
    // 289c/gal: 8 gal - 100c = 2212, 16 gal + 100c = 4724; 5780 is inside ticketCents.max x tax x 1.22 + 100 but not a fill-up.
    expect(fuel.amountCents).toBeGreaterThanOrEqual(2212);
    expect(fuel.amountCents).toBeLessThanOrEqual(4724);
    expectOnlyFails(run(b, edit(b, fuel.id, (x) => ({ ...x, amountCents: 5780 }))), 'R6_amount_within_range');
  });
});

describe('R7_geo_consistency', () => {
  it('fails when a local merchant is outside the persona state', () => {
    const b = build('freelancer', 'alpha');
    const t = pick(rowsOf(b, 'credit'), b, (m) => m.location === 'local');
    const r = expectOnlyFails(run(b, edit(b, t.id, (x) => ({ ...x, merchant: { ...x.merchant!, state: 'CA' } }))), 'R7_geo_consistency');
    expect(r.violations[0]!.transactionIds).toEqual([t.id]);
  });

  it('exempts national facts (state "US") from the local-state check', () => {
    const b = build('freelancer', 'alpha');
    const t = pick(rowsOf(b, 'credit'), b, (m) => m.location === 'local');
    expect(failing(run(b, edit(b, t.id, (x) => ({ ...x, merchant: { ...x.merchant!, state: 'US' } }))))).toEqual([]);
  });

  it('skips the travel share below 20 debits (one trip in a tiny statement is not a pattern)', () => {
    const b = build('freelancer', 'alpha', 'checking', 1, { checking: { min: 8, max: 10 } });
    const debits = b.statements.flatMap((s) => s.transactions).filter((t) => t.direction === 'debit');
    expect(debits.length).toBeLessThan(20);
    const meta: Record<string, TxnMeta> = { ...b.meta };
    const discretionary = debits.filter((t) => b.meta[t.id]!.source === 'discretionary');
    for (const t of discretionary.slice(0, 2)) meta[t.id] = { ...meta[t.id]!, location: 'travel' };
    expect(failing(run(b, b.statements, meta))).toEqual([]);
  });

  it('fails when travel exceeds 10% of debits', () => {
    const b = build('freelancer', 'alpha');
    const debits = b.statements.flatMap((s) => s.transactions).filter((t) => t.direction === 'debit' && b.meta[t.id]!.source === 'discretionary');
    const meta: Record<string, TxnMeta> = { ...b.meta };
    for (const t of debits.slice(0, Math.ceil(debits.length * 0.5))) meta[t.id] = { ...meta[t.id]!, location: 'travel' };
    const r = expectOnlyFails(run(b, b.statements, meta), 'R7_geo_consistency');
    expect(r.violations[0]!.message).toMatch(/travel/);
  });
});

describe('R8_no_duplicates', () => {
  it('fails when a row repeats on the same day with the same description and amount', () => {
    const b = build('freelancer', 'alpha', 'both', 3, { checking: { min: 10, max: 30 }, credit: { min: 10, max: 30 } });
    const t = pick(rowsOf(b, 'credit'), b, (m) => m.source === 'discretionary');
    const clone = { ...t, id: `${t.id}-dup` };
    const next = b.statements.map((s) => (s.accountType === 'credit' ? { ...s, transactions: [...s.transactions, clone] } : s));
    const statements = recompute(next, b.norm, b.model);
    const meta = { ...b.meta, [clone.id]: b.meta[t.id]! };
    const r = expectOnlyFails(run(b, statements, meta), 'R8_no_duplicates');
    expect([...r.violations[0]!.transactionIds].sort()).toEqual([t.id, clone.id].sort());
  });
});

describe('R9_evidence_present', () => {
  it('fails when a discretionary row has no evidence', () => {
    const b = build('freelancer', 'alpha');
    const t = pick(rowsOf(b, 'credit'), b, (m) => m.source === 'discretionary');
    const r = expectOnlyFails(run(b, edit(b, t.id, ({ evidence: _drop, ...x }) => x)), 'R9_evidence_present');
    expect(r.violations[0]!.transactionIds).toEqual([t.id]);
  });

  it('fails when a recurring row with a factId has no evidence', () => {
    const b = build('freelancer', 'alpha');
    const t = pick(rowsOf(b, 'checking-any'), b, (m) => m.source === 'recurring');
    expectOnlyFails(run(b, edit(b, t.id, ({ evidence: _drop, ...x }) => x)), 'R9_evidence_present');
  });

  it('fails when the cited fact was never researched', () => {
    const b = build('freelancer', 'alpha');
    const t = pick(rowsOf(b, 'credit'), b, (m) => m.source === 'discretionary');
    expectOnlyFails(run(b, edit(b, t.id, (x) => ({ ...x, evidence: { ...x.evidence!, factId: 'fact-made-up' } }))), 'R9_evidence_present');
  });
});

describe('R11_credit_within_limit', () => {
  it('fails when a credit statement balance is above the card limit', () => {
    const b = build('freelancer', 'alpha');
    const tight = b.statements.map((s) => (s.credit ? { ...s, credit: { ...s.credit, creditLimitCents: 100 } } : s));
    const r = expectOnlyFails(run(b, tight), 'R11_credit_within_limit');
    expect(r.target).toBe('lifeModel');
    expect(r.violations[0]!.message).toMatch(/above the credit limit/);
  });
});

describe('R10_no_payroll_income_personal', () => {
  it('fails when personal income is described as payroll', () => {
    // 用真公司风格的写法，把 R10 和 R12（占位名）分开测
    for (const wording of ['KESTREL DESIGN CO PAYROLL', 'DIRECT DEP KESTREL DESIGN', 'KESTREL DESIGN SALARY']) {
      const b = build('freelancer', 'alpha');
      const t = pick(rowsOf(b, 'checking-any'), b, (m) => m.source === 'income');
      const r = expectOnlyFails(run(b, edit(b, t.id, (x) => ({ ...x, description: wording }))), 'R10_no_payroll_income_personal');
      expect(r.violations[0]!.transactionIds).toEqual([t.id]);
    }
  });

  it('R12 rejects placeholder company names in income and memos', () => {
    const b = build('freelancer', 'alpha');
    const t = pick(rowsOf(b, 'checking-any'), b, (m) => m.source === 'income');
    const r = expectOnlyFails(
      run(b, edit(b, t.id, (x) => ({ ...x, description: 'ACME CORP PAYMENT', memo: 'Deposit from Acme' }))),
      'R12_no_placeholder_names',
    );
    expect(r.violations[0]!.transactionIds).toEqual([t.id]);
  });

  it('R13 stays quiet when the statement has no card spending to judge', () => {
    const b = build('freelancer', 'alpha', 'checking');
    expect(failing(run(b))).not.toContain('R13_occupation_category_fit');
  });

  it('does not apply to a business persona or to debits', () => {
    const b = build('cafe-business', 'alpha', 'checking');
    const t = pick(rowsOf(b, 'checking-any'), b, (m) => m.source === 'income');
    expect(failing(run(b, edit(b, t.id, (x) => ({ ...x, description: 'SQUARE PAYROLL DEPOSIT' }))))).toEqual([]);
    const payroll = pick(rowsOf(b, 'checking-any'), b, (m) => m.merchantKey === 'fact-gusto-payroll');
    expect(payroll.description).toContain('PAYROLL');
    const p = build('freelancer', 'alpha');
    const debit = pick(rowsOf(p, 'checking-any'), p, (m) => m.source === 'recurring');
    expect(failing(run(p, edit(p, debit.id, (x) => ({ ...x, description: 'PAYROLL SERVICE FEE' }))))).toEqual([]);
  });
});
