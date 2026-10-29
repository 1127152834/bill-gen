import { describe, expect, it } from 'vitest';
import { StatementSchema, assembleStatements, createRng } from '../src/index.js';
import * as core from '../src/index.js';
import { schedule } from '../src/schedule/index.js';
import { FIXTURES, loadModel } from './schedule-helpers.js';
import { BANK, build, loadFacts, loadTemplates, makeNormAt } from './statement-helpers.js';

const SEEDS = ['alpha', 'bravo', 'charlie'];

describe('fixtures', () => {
  it.each(FIXTURES)('%s: every factId in the model has a fact and a template', (name) => {
    const model = loadModel(name);
    const factIds = new Set(loadFacts(name).map((f) => f.id));
    const templateKeys = new Set(loadTemplates(name).map((t) => t.merchantKey));
    const wanted = [
      ...model.recurring.map((r) => r.factId),
      ...model.discretionaryPool.map((d) => d.factId),
      ...model.incomeStreams.flatMap((s) => (s.factId ? [s.factId] : [])),
    ];
    for (const id of wanted) {
      expect(factIds.has(id), `fact ${id}`).toBe(true);
      expect(templateKeys.has(id), `template ${id}`).toBe(true);
    }
    for (const s of model.incomeStreams) expect(templateKeys.has(s.id), `template ${s.id}`).toBe(true);
  });
});

describe('assembleStatements', () => {
  it('is exported from the package entry point', () => {
    expect(core.assembleStatements).toBe(assembleStatements);
    expect(core.runRules).toBeTypeOf('function');
  });

  it.each(FIXTURES)('%s: builds schema-valid statements with the right shape per account', (name) => {
    for (const seed of SEEDS) {
      const { statements, norm } = build(name, seed, 'both', 3);
      expect(statements.map((s) => s.accountType)).toEqual([name === 'cafe-business' ? 'business_checking' : 'checking', 'credit']);
      for (const s of statements) {
        expect(() => StatementSchema.parse(s)).not.toThrow();
        expect(s.bankName).toBe(BANK);
        expect(s.accountLast4).toMatch(/^\d{4}$/);
        const credit = s.accountType === 'credit';
        expect(s.checking !== undefined).toBe(!credit);
        expect(s.credit !== undefined).toBe(credit);
        expect(s.months).toHaveLength(3);
        for (const m of s.months) expect(m.credit !== undefined).toBe(credit);
        const cycles = (credit ? norm.cycles.credit : norm.cycles.checking)!;
        expect(s.periodStart).toBe(cycles[0]!.start);
        expect(s.periodEnd).toBe(cycles.at(-1)!.end);
      }
    }
  });

  it('checking month summaries chain from the opening balance and match the running balances', () => {
    const { statements, model } = build('freelancer', 'alpha', 'checking', 6);
    const s = statements[0]!;
    expect(s.checking!.openingBalanceCents).toBe(model.openingBalanceCents);
    let prev = s.checking!.openingBalanceCents;
    for (const m of s.months) {
      expect(m.openingBalanceCents).toBe(prev);
      const rows = s.transactions.filter((t) => t.postedDate >= m.cycleStart && t.postedDate <= m.cycleEnd);
      expect(m.transactionCount).toBe(rows.length);
      expect(m.closingBalanceCents).toBe(rows.at(-1)!.runningBalanceCents);
      expect(m.closingBalanceCents).toBe(m.openingBalanceCents + m.totalCreditsCents - m.totalDebitsCents);
      prev = m.closingBalanceCents;
    }
    expect(s.checking!.closingBalanceCents).toBe(prev);
    expect(s.transactions.every((t) => t.runningBalanceCents !== undefined)).toBe(true);
  });

  it('credit month summaries carry the scheduler statement balances and the cycle due dates', () => {
    const seed = 'bravo';
    const model = loadModel('rideshare');
    const norm = makeNormAt({ fixture: 'rideshare', seed, accounts: 'credit', months: 6 });
    const out = schedule(model, norm, createRng(seed));
    const { statements } = assembleStatements(out, loadTemplates('rideshare'), norm, model, loadFacts('rideshare'), BANK, createRng('x'));
    const s = statements[0]!;
    expect(s.credit).toEqual({ creditLimitCents: model.creditLimitCents, apr: model.apr });
    expect(s.transactions.every((t) => t.runningBalanceCents === undefined)).toBe(true);
    let prev = model.creditOpeningBalanceCents ?? 0;
    s.months.forEach((m, i) => {
      const per = out.balances.credit!.perCycle[i]!;
      expect(m.openingBalanceCents).toBe(prev);
      expect(m.closingBalanceCents).toBe(per.statementBalanceCents);
      expect(m.credit).toEqual({
        statementBalanceCents: per.statementBalanceCents,
        minimumDueCents: per.minimumDueCents,
        interestChargedCents: per.interestChargedCents,
        dueDate: norm.cycles.credit![i]!.dueDate,
      });
      expect(m.closingBalanceCents).toBe(m.openingBalanceCents + m.totalDebitsCents - m.totalCreditsCents);
      prev = m.closingBalanceCents;
    });
  });

  it('is deterministic for a given seed and changes with it', () => {
    const a = build('freelancer', 'alpha', 'both', 3);
    const b = build('freelancer', 'alpha', 'both', 3);
    const c = build('freelancer', 'bravo', 'both', 3);
    expect(b.statements).toEqual(a.statements);
    expect(c.statements).not.toEqual(a.statements);
  });

  it('writes bank-style uppercase descriptions of at most 60 characters, with no bank name or full numbers', () => {
    for (const name of FIXTURES) {
      const { statements } = build(name, 'charlie', 'both', 6);
      for (const t of statements.flatMap((s) => s.transactions)) {
        expect(t.description, t.id).toBe(t.description.toUpperCase());
        expect(t.description.length, t.id).toBeLessThanOrEqual(60);
        expect(t.description, t.id).not.toMatch(/[{}]/);
        // Customer ids (6-10 digits) and the IRS trace (15) are real; a 16-digit card number never is.
        expect(t.description, t.id).not.toMatch(/\d{16,}/);
        expect(t.description.toLowerCase()).not.toContain('harborline');
        expect(t.memo.length, t.id).toBeGreaterThan(0);
      }
    }
  });

  it('keeps the same {STORE} number for a merchant on every row', () => {
    const { statements } = build('freelancer', 'alpha', 'credit', 6);
    const heb = statements[0]!.transactions.filter((t) => t.merchant?.name === 'H-E-B');
    expect(heb.length).toBeGreaterThan(1);
    expect(new Set(heb.map((t) => /#(\d{4})/.exec(t.description)![1])).size).toBe(1);
  });

  it('names the linked account on both sides of a card payment', () => {
    const { statements, meta } = build('freelancer', 'alpha', 'both', 6);
    const [checking, credit] = statements;
    const out = checking!.transactions.filter((t) => meta[t.id]!.merchantKey === 'card-payment');
    expect(out.length).toBeGreaterThan(0);
    for (const t of out) expect(t.description).toBe(`ONLINE PMT TO CARD ENDING ${credit!.accountLast4}`);
    const received = credit!.transactions.filter((t) => meta[t.id]!.merchantKey === 'card-payment');
    for (const t of received) expect(t).toMatchObject({ type: 'PAYMENT', direction: 'credit', description: 'ONLINE PAYMENT - THANK YOU' });
  });

  it('attaches merchant and evidence from the fact, and none to rows without one', () => {
    const { statements, meta, facts } = build('freelancer', 'alpha', 'both', 3);
    const byId = new Map(facts.map((f) => [f.id, f]));
    for (const t of statements.flatMap((s) => s.transactions)) {
      const m = meta[t.id]!;
      const fact = m.factId ? byId.get(m.factId) : undefined;
      if (fact) {
        expect(t.merchant).toEqual({ name: fact.name, category: fact.category, city: fact.city, state: fact.state });
        expect(t.evidence).toMatchObject({ factId: fact.id, sourceUrl: fact.sourceUrl });
        expect(t.evidence!.note.length).toBeGreaterThan(0);
      } else {
        expect(t.merchant).toBeUndefined();
        expect(t.evidence).toBeUndefined();
      }
    }
  });

  it('quotes the price item that fits the row in the evidence note, not always the first one', () => {
    const seed = 'alpha';
    const model = loadModel('freelancer');
    const norm = makeNormAt({ fixture: 'freelancer', seed, accounts: 'both', months: 1 });
    const out = schedule(model, norm, createRng(seed));
    const url = (id: string) => loadFacts('freelancer').find((f) => f.id === id)!.sourceUrl;
    const facts = loadFacts('freelancer').map((f) =>
      f.id === 'fact-camden-lamar-heights-austin'
        ? { ...f, priceItems: [{ item: '2 bedroom monthly rent', priceCents: 220000, sourceUrl: url(f.id) }, ...f.priceItems] }
        : f.id === 'fact-epoch-coffee-austin'
          ? { ...f, priceItems: [{ item: 'Catering box', priceCents: 9500, sourceUrl: url(f.id) }, ...f.priceItems] }
          : f,
    );
    const { statements, meta } = assembleStatements(out, loadTemplates('freelancer'), norm, model, facts, BANK, createRng('x'));
    const rows = statements.flatMap((s) => s.transactions);
    const rent = rows.find((t) => meta[t.id]!.factId === 'fact-camden-lamar-heights-austin')!;
    expect(rent.evidence!.note).toContain('1 bedroom monthly rent $1650.00');
    const coffee = rows.filter((t) => meta[t.id]!.factId === 'fact-epoch-coffee-austin');
    expect(coffee.length).toBeGreaterThan(0);
    for (const t of coffee) expect(t.evidence!.note).toContain('Latte');
  });

  it('carries source, merchantKey, factId and location in meta for every row', () => {
    const { statements, meta, model } = build('freelancer', 'bravo', 'both', 3);
    const rows = statements.flatMap((s) => s.transactions);
    expect(Object.keys(meta).sort()).toEqual(rows.map((t) => t.id).sort());
    const disc = rows.filter((t) => meta[t.id]!.source === 'discretionary');
    expect(disc.length).toBeGreaterThan(0);
    for (const t of disc) {
      const item = model.discretionaryPool.find((i) => i.factId === meta[t.id]!.factId)!;
      expect(meta[t.id]).toMatchObject({ merchantKey: item.factId, location: item.location });
    }
  });

  it('never prints payroll wording on personal income', () => {
    for (const name of ['freelancer', 'rideshare'] as const) {
      const { statements } = build(name, 'alpha', 'both', 6);
      for (const t of statements.flatMap((s) => s.transactions).filter((x) => x.direction === 'credit')) {
        expect(t.description).not.toMatch(/PAYROLL|DIRECT DEP|SALARY/);
      }
    }
  });

  it('falls back to a readable description when no template was supplied', () => {
    const seed = 'alpha';
    const model = loadModel('freelancer');
    const norm = makeNormAt({ fixture: 'freelancer', seed, accounts: 'checking', months: 1 });
    const out = schedule(model, norm, createRng(seed));
    const { statements, meta } = assembleStatements(out, [], norm, model, loadFacts('freelancer'), BANK, createRng('x'));
    const rows = statements[0]!.transactions;
    expect(rows.every((t) => t.description.length > 0 && t.description.length <= 60)).toBe(true);
    const income = rows.find((t) => meta[t.id]!.source === 'income')!;
    expect(income.description).toBe('LUMEN CREATIVE LLC PAYMENT');
    const rent = rows.find((t) => meta[t.id]!.merchantKey === 'fact-camden-lamar-heights-austin')!;
    expect(rent.description).toBe('CAMDEN LAMAR HEIGHTS ACH PMT');
  });

  it('rejects a credit account when the model has no credit limit', () => {
    const seed = 'alpha';
    const model = { ...loadModel('freelancer') };
    delete model.creditLimitCents;
    const norm = makeNormAt({ fixture: 'freelancer', seed, accounts: 'credit', months: 1 });
    const out = schedule(loadModel('freelancer'), norm, createRng(seed));
    expect(() => assembleStatements(out, [], norm, model, [], BANK, createRng('x'))).toThrow(/creditLimitCents/);
  });
});
