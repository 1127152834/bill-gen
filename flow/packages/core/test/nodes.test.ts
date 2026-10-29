import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  LifeModelError,
  SKILLS_BY_NODE,
  auditNode,
  createRng,
  descriptorsNode,
  lifeModelNode,
  loadSkills,
  pickWeightedMetro,
  planResearchNode,
  randomPersona,
  type FinancialModel,
  type MerchantFact,
  type PersonalProfile,
  type Profile,
  type SkillName,
} from '../src/index.js';
import { createFakeLlm, type FakeLlm } from '../src/testing/index.js';
import { loadModel } from './schedule-helpers.js';
import { build, loadFacts, makeNormAt } from './statement-helpers.js';

const SKILLS_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '../../../skills');
const skills = loadSkills(SKILLS_DIR);
const titleOf = (name: SkillName): string => readFileSync(resolve(SKILLS_DIR, `${name}.md`), 'utf8').split('\n')[0]!;

const both = makeNormAt({ fixture: 'freelancer', seed: 'nodes', accounts: 'both', months: 1, checking: { min: 8, max: 14 }, credit: { min: 12, max: 25 } });
const profile = both.request.profile! as PersonalProfile;
const facts = loadFacts('freelancer');
const good = (): FinancialModel => structuredClone(loadModel('freelancer'));

const system = (llm: FakeLlm, i = 0): string => llm.calls[i]!.messages.find((m) => m.role === 'system')!.content;
const userText = (llm: FakeLlm, i = 0): string =>
  llm.calls[i]!.messages.filter((m) => m.role === 'user').map((m) => m.content).join('\n');

function expectSkillTitles(llm: FakeLlm, node: keyof typeof SKILLS_BY_NODE): void {
  for (const name of SKILLS_BY_NODE[node]) expect(system(llm)).toContain(titleOf(name));
}

// ---------------------------------------------------------------- planResearch

function query(i: number, scope: 'metro' | 'state' | 'national' = 'metro', text = `best grocery stores Austin TX ${i}`) {
  return { id: `q${i}`, category: `category ${i}`, query: text, scope };
}

describe('planResearchNode', () => {
  it('truncates 20 queries to 14 with a single planResearch call', async () => {
    const llm = createFakeLlm({ planResearch: [Array.from({ length: 20 }, (_, i) => query(i))] });
    const out = await planResearchNode({ norm: both, profile }, { llm, skills });
    expect(out).toHaveLength(14);
    expect(llm.calls.map((c) => c.node)).toEqual(['planResearch']);
  });

  it('makes every metro/state query name the city or state; national queries stay as written', async () => {
    const llm = createFakeLlm({
      planResearch: [
        [
          query(1, 'metro', 'cheap coffee shops'),
          query(2, 'state', 'renters insurance rates'),
          query(3, 'national', 'IRS Direct Pay estimated tax payment'),
          ...Array.from({ length: 4 }, (_, i) => query(10 + i)),
        ],
      ],
    });
    const out = await planResearchNode({ norm: both, profile }, { llm, skills });
    expect(out[0]!.query).toMatch(/Austin/);
    expect(out[1]!.query).toMatch(/Texas|TX/);
    expect(out[2]!.query).toBe('IRS Direct Pay estimated tax payment');
  });

  it('drops a second query for the same scope and category (they would share a cache entry)', async () => {
    const llm = createFakeLlm({
      planResearch: [[query(1), { ...query(2), category: 'Category 1' }, ...Array.from({ length: 6 }, (_, i) => query(20 + i))]],
    });
    const out = await planResearchNode({ norm: both, profile }, { llm, skills });
    expect(out.map((q) => q.id)).not.toContain('q2');
    expect(new Set(out.map((q) => q.id)).size).toBe(out.length);
  });

  it('system prompt carries the planResearch skills, location and hard rules', async () => {
    const llm = createFakeLlm({ planResearch: [Array.from({ length: 8 }, (_, i) => query(i))] });
    await planResearchNode({ norm: both, profile }, { llm, skills });
    expectSkillTitles(llm, 'planResearch');
    expect(system(llm)).toMatch(/national/);
    expect(system(llm)).toMatch(/self-employed/i);
    expect(system(llm)).toContain(both.location.colTier);
  });
});

// ---------------------------------------------------------------- lifeModel

const withRecurring = (m: FinancialModel, id: string, patch: Partial<FinancialModel['recurring'][number]>): FinancialModel => ({
  ...m,
  recurring: m.recurring.map((r) => (r.id === id ? { ...r, ...patch } : r)),
});

describe('lifeModelNode', () => {
  const input = { norm: both, profile, facts };

  it('returns a valid model after one call; closingDay comes from the request', async () => {
    const llm = createFakeLlm({ lifeModel: [good()] });
    const model = await lifeModelNode(input, { llm, skills });
    expect(llm.calls).toHaveLength(1);
    expect(model.closingDay).toBe(both.closingDay);
    expect(model.discretionaryPool).toHaveLength(good().discretionaryPool.length);
  });

  it('re-asks once with the unknown factId in the messages and adopts the second answer', async () => {
    const bad = good();
    bad.discretionaryPool[0]!.factId = 'fact-made-up-diner';
    const second = good();
    second.openingBalanceCents = 777_777;
    const llm = createFakeLlm({ lifeModel: [bad, second] });
    const model = await lifeModelNode(input, { llm, skills });
    expect(llm.calls).toHaveLength(2);
    expect(userText(llm, 1)).toContain('fact-made-up-diner');
    expect(model.openingBalanceCents).toBe(777_777);
  });

  it('rejects payroll for a personal persona (LifeModelError after the retry)', async () => {
    const payroll = withRecurring(good(), 'adobe-cc', { kind: 'payroll' });
    const llm = createFakeLlm({ lifeModel: [payroll, payroll] });
    const err = await lifeModelNode(input, { llm, skills }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(LifeModelError);
    expect((err as LifeModelError).code).toBe('LIFE_MODEL_INVALID');
    expect((err as LifeModelError).problems.join('\n')).toMatch(/payroll/i);
    expect(llm.calls).toHaveLength(2);
  });

  it('rejects income whose payer or label reads like wages for a personal persona', async () => {
    const m = good();
    m.incomeStreams[0]!.payerName = 'ACME CORP DIRECT DEP';
    const llm = createFakeLlm({ lifeModel: [m, m] });
    await expect(lifeModelNode(input, { llm, skills })).rejects.toThrow(/wage|salary|payroll/i);
  });

  it('rejects credit items when only checking is requested', async () => {
    const norm = makeNormAt({ fixture: 'freelancer', seed: 'nodes', accounts: 'checking', months: 1 });
    const llm = createFakeLlm({ lifeModel: [good(), good()] });
    const err = (await lifeModelNode({ ...input, norm }, { llm, skills }).catch((e: unknown) => e)) as LifeModelError;
    expect(err).toBeInstanceOf(LifeModelError);
    expect(err.problems.join('\n')).toMatch(/credit/);
  });

  it('rejects checking items and income when only credit is requested', async () => {
    const norm = makeNormAt({ fixture: 'freelancer', seed: 'nodes', accounts: 'credit', months: 1 });
    const llm = createFakeLlm({ lifeModel: [good(), good()] });
    const err = (await lifeModelNode({ ...input, norm }, { llm, skills }).catch((e: unknown) => e)) as LifeModelError;
    expect(err.problems.join('\n')).toMatch(/incomeStreams/);
    expect(err.problems.join('\n')).toMatch(/checking/);
  });

  it('rejects a model whose priority <= 2 rows cannot fit the checking max', async () => {
    // Worst case: rent 1 + insurance 1 + estimated tax 1 + income irregular {1,2} -> 2 + card payment 1 = 6.
    const norm = makeNormAt({ fixture: 'freelancer', seed: 'nodes', accounts: 'both', months: 1, checking: { min: 3, max: 5 } });
    const llm = createFakeLlm({ lifeModel: [good(), good()] });
    const err = (await lifeModelNode({ ...input, norm }, { llm, skills }).catch((e: unknown) => e)) as LifeModelError;
    expect(err.problems.join('\n')).toMatch(/checking.*max 5/);
  });

  describe('row budget uses worst-case counts per cycle and allows up to max', () => {
    const withIncome = (cadence: FinancialModel['incomeStreams'][number]['cadence']): FinancialModel => {
      const m = good();
      m.incomeStreams[0] = { ...m.incomeStreams[0]!, cadence };
      return m;
    };
    const runAt = (max: number, m: FinancialModel) => {
      const norm = makeNormAt({ fixture: 'freelancer', seed: 'nodes', accounts: 'both', months: 1, checking: { min: 3, max } });
      return lifeModelNode({ ...input, norm }, { llm: createFakeLlm({ lifeModel: [m, m] }), skills });
    };
    // Fixed protected rows on checking: rent, insurance, estimated tax, card payment = 4.

    it('weekly income counts 5 per cycle', async () => {
      const m = withIncome({ kind: 'weekly', weekday: 3 });
      const err = (await runAt(8, m).catch((e: unknown) => e)) as LifeModelError;
      expect(err.problems.join('\n')).toMatch(/income client-ach 5\b/);
      await expect(runAt(9, m)).resolves.toBeDefined();
    });

    it('biweekly income counts 3 per cycle', async () => {
      const m = withIncome({ kind: 'biweekly', weekday: 5 });
      const err = (await runAt(6, m).catch((e: unknown) => e)) as LifeModelError;
      expect(err.problems.join('\n')).toMatch(/income client-ach 3\b/);
      await expect(runAt(7, m)).resolves.toBeDefined();
    });

    it('irregular income counts perMonth.max, not the average', async () => {
      const m = withIncome({ kind: 'irregular', perMonth: { min: 2, max: 12 } });
      const err = (await runAt(15, m).catch((e: unknown) => e)) as LifeModelError;
      expect(err.problems.join('\n')).toMatch(/income client-ach 12\b/);
      await expect(runAt(16, m)).resolves.toBeDefined();
    });

    it('credit cycles count the payment and, for partial payers, interest', async () => {
      const m = { ...good(), cardPaymentStrategy: 'partial' as const };
      m.recurring = m.recurring.map((r) => (r.account === 'credit' ? { ...r, kind: 'loan' as const, priority: 1 } : r));
      // adobe + spotify (priority 1) + payment + interest = 4 > max 3
      const norm = makeNormAt({ fixture: 'freelancer', seed: 'nodes', accounts: 'both', months: 1, credit: { min: 1, max: 3 } });
      const err = (await lifeModelNode({ ...input, norm }, { llm: createFakeLlm({ lifeModel: [m, m] }), skills }).catch((e: unknown) => e)) as LifeModelError;
      expect(err.problems.join('\n')).toMatch(/credit: .*interest 1.*max 3/);
    });
  });

  it('rejects a credit opening balance above the credit limit', async () => {
    const m = { ...good(), creditLimitCents: 300_000, creditOpeningBalanceCents: 320_000 };
    const llm = createFakeLlm({ lifeModel: [m, m] });
    await expect(lifeModelNode(input, { llm, skills })).rejects.toThrow(/creditOpeningBalanceCents 320000 .*limit 300000/);
  });

  it('rejects a credit limit that a month of card spending would mostly use up', async () => {
    // The fixture's credit items spend roughly $800 a month; 60% of a $1,000 limit is $600.
    const m = { ...good(), creditLimitCents: 100_000, creditOpeningBalanceCents: 20_000 };
    const llm = createFakeLlm({ lifeModel: [m, m] });
    await expect(lifeModelNode(input, { llm, skills })).rejects.toThrow(/60% of the credit limit/);
  });

  it('rejects an opening balance below one month of rent and bills', async () => {
    const m = { ...good(), openingBalanceCents: 50_000 };
    const llm = createFakeLlm({ lifeModel: [m, m] });
    await expect(lifeModelNode(input, { llm, skills })).rejects.toThrow(/openingBalanceCents/);
  });

  it('requires creditLimitCents and apr when credit is requested', async () => {
    const { creditLimitCents: _l, apr: _a, ...m } = good();
    const llm = createFakeLlm({ lifeModel: [m, m] });
    await expect(lifeModelNode(input, { llm, skills })).rejects.toThrow(/creditLimitCents/);
  });

  it('rejects a fact used by two model items', async () => {
    const m = withRecurring(good(), 'spotify', { factId: 'fact-adobe-creative-cloud' });
    const llm = createFakeLlm({ lifeModel: [m, m] });
    await expect(lifeModelNode(input, { llm, skills })).rejects.toThrow(/fact-adobe-creative-cloud/);
  });

  it('fills a fuel item unitPriceCents from the fact per-gallon price', async () => {
    const m = good();
    const fuel = m.discretionaryPool.find((d) => d.pricing === 'fuel')!;
    delete fuel.unitPriceCents;
    const llm = createFakeLlm({ lifeModel: [m] });
    const model = await lifeModelNode(input, { llm, skills });
    expect(model.discretionaryPool.find((d) => d.pricing === 'fuel')!.unitPriceCents).toBe(289);
  });

  it('widens a single-price ticket range (identical amounts would repeat) and drops unitPriceCents off non-fuel items', async () => {
    const m = good();
    const coffee = m.discretionaryPool.find((d) => d.factId === 'fact-epoch-coffee-austin')!;
    coffee.ticketCents = { min: 575, max: 575 };
    coffee.unitPriceCents = 1;
    const llm = createFakeLlm({ lifeModel: [m] });
    const model = await lifeModelNode(input, { llm, skills });
    const got = model.discretionaryPool.find((d) => d.factId === 'fact-epoch-coffee-austin')!;
    expect(got.ticketCents).toEqual({ min: 575, max: 920 });
    expect(got.unitPriceCents).toBeUndefined();
  });

  it('accepts unitPriceCents 0 or null on non-fuel items (the model means "none") without a retry', async () => {
    const m = good() as unknown as { discretionaryPool: Record<string, unknown>[] };
    const nonFuel = m.discretionaryPool.filter((d) => d.pricing !== 'fuel');
    nonFuel[0]!.unitPriceCents = 0;
    nonFuel[1]!.unitPriceCents = null;
    const llm = createFakeLlm({ lifeModel: [m] });
    const model = await lifeModelNode(input, { llm, skills });
    expect(llm.calls).toHaveLength(1);
    expect(model.discretionaryPool.filter((d) => d.pricing !== 'fuel').every((d) => d.unitPriceCents === undefined)).toBe(true);
  });

  it('renames an income payer to its evidence fact when they disagree (the row must match its evidence)', async () => {
    const m = good();
    const extra: MerchantFact = { id: 'fact-ch-robinson', name: 'C.H. Robinson Worldwide', city: 'Eden Prairie', state: 'MN', category: 'freight', priceItems: [], sourceUrl: 'https://www.chrobinson.com/' };
    m.incomeStreams[0] = { ...m.incomeStreams[0]!, payerName: 'MAPLE RIDGE LOADS', factId: 'fact-ch-robinson' };
    const llm = createFakeLlm({ lifeModel: [m] });
    const model = await lifeModelNode({ ...input, facts: [...facts, extra] }, { llm, skills });
    expect(model.incomeStreams[0]).toMatchObject({ payerName: 'C.H. Robinson Wo', factId: 'fact-ch-robinson' });
  });

  it('keeps an income payer that already matches its evidence fact (name or chain)', async () => {
    const m = good();
    const extra: MerchantFact = { id: 'fact-uber', name: 'Uber Technologies', chain: 'Uber', city: 'San Francisco', state: 'US', category: 'platform', priceItems: [], sourceUrl: 'https://www.uber.com/' };
    m.incomeStreams[0] = { ...m.incomeStreams[0]!, payerName: 'UBER USA', factId: 'fact-uber' };
    const llm = createFakeLlm({ lifeModel: [m] });
    const model = await lifeModelNode({ ...input, facts: [...facts, extra] }, { llm, skills });
    expect(model.incomeStreams[0]).toMatchObject({ payerName: 'UBER USA', factId: 'fact-uber' });
  });

  it('bills card-charged recurring items as POS (ACH never reaches a credit card)', async () => {
    const m = good();
    const onCard = m.recurring.find((r) => r.account === 'credit')!;
    onCard.txnType = 'ACH';
    const llm = createFakeLlm({ lifeModel: [m] });
    const model = await lifeModelNode(input, { llm, skills });
    expect(model.recurring.filter((r) => r.account === 'credit').every((r) => r.txnType === 'POS')).toBe(true);
  });

  it('rejects a discretionary pool whose perMonth.max values cannot fill the account up to its max', async () => {
    const m = good();
    for (const d of m.discretionaryPool.filter((x) => x.account === 'credit')) d.perMonth = { min: 0, max: 1 };
    const llm = createFakeLlm({ lifeModel: [m, m] });
    await expect(lifeModelNode(input, { llm, skills })).rejects.toThrow(/credit: the discretionary pool's perMonth\.max values add up to \d+/);
  });

  it('estimates card spending from the rows the scheduler will fill, not only perMonth', async () => {
    // Planned visits (avg 2 per item) cost about $775 a month, under 60% of $1,500; but the credit range
    // makes the scheduler fill ~20 purchase rows of the weight-averaged ticket, about $1,160.
    const m = { ...good(), creditLimitCents: 150_000, creditOpeningBalanceCents: 20_000 };
    for (const d of m.discretionaryPool.filter((x) => x.account === 'credit')) d.perMonth = { min: 0, max: 4 };
    const norm = makeNormAt({ fixture: 'freelancer', seed: 'nodes', accounts: 'both', months: 1, checking: { min: 8, max: 14 }, credit: { min: 20, max: 25 } });
    const llm = createFakeLlm({ lifeModel: [m, m] });
    await expect(lifeModelNode({ ...input, norm }, { llm, skills })).rejects.toThrow(/60% of the credit limit/);
  });

  it('rejects income whose payer is a merchant the subject spends at', async () => {
    const m = good();
    m.incomeStreams[0] = { ...m.incomeStreams[0]!, payerName: 'Amazon.com' };
    const llm = createFakeLlm({ lifeModel: [m, m] });
    await expect(lifeModelNode(input, { llm, skills })).rejects.toThrow(/Amazon/);
  });

  it('does not mistake a client whose name merely starts like a short merchant name', async () => {
    const m = good();
    m.incomeStreams[0] = { ...m.incomeStreams[0]!, payerName: 'HEBERT CONSULTING' };
    const llm = createFakeLlm({ lifeModel: [m] });
    expect((await lifeModelNode(input, { llm, skills })).incomeStreams[0]!.payerName).toBe('HEBERT CONSULTING');
  });

  it('drops an income factId that is unknown or names a merchant the subject spends at', async () => {
    const m = good();
    m.incomeStreams[0] = { ...m.incomeStreams[0]!, factId: 'fact-adobe-creative-cloud' };
    m.incomeStreams.push({ ...m.incomeStreams[0]!, id: 'second-client', factId: 'fact-nowhere' });
    const llm = createFakeLlm({ lifeModel: [m] });
    const model = await lifeModelNode(input, { llm, skills });
    expect(llm.calls).toHaveLength(1);
    expect(model.incomeStreams.map((s) => s.factId)).toEqual([undefined, undefined]);
  });

  it('clamps priorities: fixed obligations to <= 2, subscriptions to 3-4', async () => {
    const llm = createFakeLlm({ lifeModel: [good()] });
    const model = await lifeModelNode(input, { llm, skills });
    const p = (id: string) => model.recurring.find((r) => r.id === id)!.priority;
    expect(p('aca-health')).toBe(2);
    expect(p('irs-estimated')).toBe(2);
    expect(p('spotify')).toBe(4);
  });

  it('requires rent or a mortgage for a personal persona when a housing fact exists', async () => {
    const m = good();
    m.recurring = m.recurring.filter((r) => r.kind !== 'rent');
    const llm = createFakeLlm({ lifeModel: [m, m] });
    await expect(lifeModelNode(input, { llm, skills })).rejects.toThrow(/no rent or mortgage.*fact-camden-lamar-heights-austin/);
  });

  it('puts the researched facts in a block marked as untrusted data', async () => {
    const llm = createFakeLlm({ lifeModel: [good()] });
    await lifeModelNode(input, { llm, skills });
    const user = userText(llm);
    expect(user).toMatch(/untrusted[\s\S]*BEGIN UNTRUSTED[\s\S]*fact-shell-burnet-rd-austin[\s\S]*END UNTRUSTED/i);
  });

  it('prompt: skills, hard rules, facts, calendar and count ranges; feedback on a retry round', async () => {
    const llm = createFakeLlm({ lifeModel: [good()] });
    await lifeModelNode({ ...input, feedback: ['R6_amount_within_range: fuel row too large'] }, { llm, skills });
    expectSkillTitles(llm, 'lifeModel');
    const sys = system(llm);
    expect(sys).toMatch(/only.*provided facts/i);
    expect(sys).toMatch(/never.*(salary|payroll)/i);
    expect(sys).toMatch(/English/);
    expect(sys).toContain(String(both.location.combinedSalesTaxRate));
    const user = userText(llm);
    expect(user).toContain('fact-shell-burnet-rd-austin');
    expect(user).toContain(both.cycles.checking![0]!.start);
    expect(user).toMatch(/"max":\s*14/);
    expect(user).toContain('R6_amount_within_range: fuel row too large');
  });
});

// ---------------------------------------------------------------- descriptors

describe('descriptorsNode', () => {
  const nationalFacts: MerchantFact[] = facts.map((f) =>
    f.id === 'fact-spotify-premium' ? { ...f, state: 'US', city: 'New York' } : f,
  );
  const keys = [
    'fact-heb-austin',
    'fact-heb-austin',
    'fact-irs-direct-pay',
    'card-payment',
    'atm',
    'fee-atm',
    'fee-overdraft',
    'interest',
    'client-ach',
    'fact-spotify-premium',
  ];
  const reply = [
    { merchantKey: 'fact-heb-austin', template: 'CHECKCARD {MMDD} H-E-B #{STORE} {CITY} {ST}', memo: 'Groceries at H-E-B' },
    { merchantKey: 'fact-heb-austin', template: 'HEB DUPLICATE', memo: 'dup' },
    { merchantKey: 'fact-irs-direct-pay', template: 'IRS USATAXPYMT {REF} {BOGUS}', memo: 'Estimated tax' },
    { merchantKey: 'fact-spotify-premium', template: 'SPOTIFY USA {CITY} {ST}', memo: 'Music streaming' },
    { merchantKey: 'fact-not-asked', template: 'X', memo: 'x' },
  ];

  it('one batched call; one template per distinct non-builtin, non-income key', async () => {
    const llm = createFakeLlm({ descriptors: [reply] });
    const out = await descriptorsNode(
      { norm: both, profile, facts: nationalFacts, model: good(), merchantKeys: keys },
      { llm, skills },
    );
    expect(llm.calls).toHaveLength(1);
    expect(out.map((t) => t.merchantKey).sort()).toEqual(['fact-heb-austin', 'fact-irs-direct-pay', 'fact-spotify-premium']);
    expect(out.find((t) => t.merchantKey === 'fact-heb-austin')!.template).toBe('CHECKCARD {MMDD} H-E-B #{STORE} {CITY} {ST}');
    const user = userText(llm);
    expect(user).toContain('fact-irs-direct-pay');
    expect(user).not.toMatch(/"card-payment"|"client-ach"|"fee-atm"/);
  });

  it('strips unknown placeholders, and {CITY}/{ST} for national facts', async () => {
    const llm = createFakeLlm({ descriptors: [reply] });
    const out = await descriptorsNode(
      { norm: both, profile, facts: nationalFacts, model: good(), merchantKeys: keys },
      { llm, skills },
    );
    expect(out.find((t) => t.merchantKey === 'fact-irs-direct-pay')!.template).toBe('IRS USATAXPYMT {REF}');
    expect(out.find((t) => t.merchantKey === 'fact-spotify-premium')!.template).toBe('SPOTIFY USA');
  });

  it('drops {REF} from templates used only by ACH bills (IRS keeps its trace) and #{STORE} from non-chains', async () => {
    const llm = createFakeLlm({
      descriptors: [
        [
          { merchantKey: 'fact-austin-energy', template: 'AUSTIN ENERGY WEB PMTS {REF}', memo: 'Electric bill' },
          { merchantKey: 'fact-irs-direct-pay', template: 'IRS USATAXPYMT {REF}', memo: 'Estimated tax' },
          { merchantKey: 'fact-camden-lamar-heights-austin', template: 'CAMDEN LAMAR RENT {ID}', memo: 'Rent' },
          { merchantKey: 'fact-epoch-coffee-austin', template: 'TST* EPOCH COFFEE #{STORE} {CITY} {ST}', memo: 'Coffee' },
          { merchantKey: 'fact-heb-austin', template: 'H-E-B #{STORE} {CITY} {ST}', memo: 'Groceries' },
        ],
      ],
    });
    const keys = ['fact-austin-energy', 'fact-irs-direct-pay', 'fact-camden-lamar-heights-austin', 'fact-epoch-coffee-austin', 'fact-heb-austin'];
    const out = await descriptorsNode({ norm: both, profile, facts, model: good(), merchantKeys: keys }, { llm, skills });
    const tplOf = (k: string) => out.find((t) => t.merchantKey === k)!.template;
    expect(tplOf('fact-austin-energy')).toBe('AUSTIN ENERGY WEB PMTS');
    expect(tplOf('fact-irs-direct-pay')).toBe('IRS USATAXPYMT {REF}');
    expect(tplOf('fact-camden-lamar-heights-austin')).toBe('CAMDEN LAMAR RENT {ID}');
    expect(tplOf('fact-epoch-coffee-austin')).toBe('TST* EPOCH COFFEE {CITY} {ST}');
    expect(tplOf('fact-heb-austin')).toBe('H-E-B #{STORE} {CITY} {ST}');
    expect(system(llm)).toMatch(/\{ID\}/);
    expect(system(llm)).toMatch(/never \{REF\} on ACH bills/i);
  });

  it('skips the call when no key needs a template', async () => {
    const llm = createFakeLlm({});
    const out = await descriptorsNode({ norm: both, profile, facts, model: good(), merchantKeys: ['atm', 'client-ach'] }, { llm, skills });
    expect(out).toEqual([]);
    expect(llm.calls).toHaveLength(0);
  });

  it('system prompt carries the bank-descriptors skill', async () => {
    const llm = createFakeLlm({ descriptors: [reply] });
    await descriptorsNode({ norm: both, profile, facts, model: good(), merchantKeys: keys }, { llm, skills });
    expectSkillTitles(llm, 'descriptors');
    expect(system(llm)).toMatch(/English/);
  });
});

// ---------------------------------------------------------------- audit

describe('auditNode', () => {
  const b = build('freelancer', 'alpha', 'both', 1);
  const checking = b.statements.find((s) => s.accountType !== 'credit')!;
  const credit = b.statements.find((s) => s.accountType === 'credit')!;
  const run = (llm: FakeLlm) =>
    auditNode({ norm: b.norm, profile, statements: b.statements, facts: b.facts }, { llm, skills });

  it('renders a compact table (evidence note, no URLs) and maps row refs back to transaction ids', async () => {
    const llm = createFakeLlm({
      audit: [
        [
          { severity: 'high', transactionIds: ['CHK1', 'CC2', 'ZZZ9'], problem: 'Odd pair', suggestedTarget: 'descriptors' },
          { severity: 'low', transactionIds: [checking.transactions[2]!.id], problem: 'Real id cited', suggestedTarget: 'schedule' },
        ],
      ],
    });
    const out = await run(llm);
    expect(out[0]!.transactionIds).toEqual([checking.transactions[0]!.id, credit.transactions[1]!.id]);
    expect(out[1]!.transactionIds).toEqual([checking.transactions[2]!.id]);
    const user = userText(llm);
    expect(user).toContain('CHK1');
    expect(user).toContain(checking.transactions[0]!.description);
    expect(user).not.toContain('https://');
    expect(user).not.toContain(checking.transactions[0]!.id);
    const withEvidence = credit.transactions.find((t) => t.evidence)!;
    expect(user).toContain(withEvidence.evidence!.note);
  });

  it('system prompt carries every skill and asks for severity and suggestedTarget', async () => {
    const llm = createFakeLlm({ audit: [[]] });
    expect(await run(llm)).toEqual([]);
    expectSkillTitles(llm, 'audit');
    expect(system(llm)).toMatch(/suggestedTarget/);
    expect(system(llm)).toMatch(/severity/);
    // Store numbers are checked against the evidence now, not exempt.
    expect(system(llm)).not.toMatch(/fixed store number per merchant/);
    expect(system(llm)).toMatch(/store number.*evidence/i);
  });
});

// ---------------------------------------------------------------- randomPersona

const llmPersonal: PersonalProfile = {
  name: 'Model Chosen Name',
  age: 41,
  address: { street: '418 Elm Ridge Dr', city: 'Somewhere', state: 'NV', zip: '89101' },
  occupation: 'Freelance photographer',
  household: { maritalStatus: 'married', children: 2 },
  hobbies: ['hiking', 'board games'],
};

describe('randomPersona', () => {
  it('keeps given fields verbatim and places the persona in the weighted-random metro', async () => {
    const llm = createFakeLlm({ persona: [llmPersonal] });
    const p = (await randomPersona('personal', { name: 'Dana Ruiz' }, { llm, rng: createRng('p1') })) as PersonalProfile;
    const metro = pickWeightedMetro(createRng('p1'));
    expect(p.name).toBe('Dana Ruiz');
    expect(p.address).toEqual({ street: '418 Elm Ridge Dr', city: metro.city, state: metro.state, zip: '89101' });
    expect(p.occupation).toBe('Freelance photographer');
    expect(userText(llm)).toContain('Dana Ruiz');
    expect(userText(llm)).toContain(metro.city);
    expect(system(llm)).toMatch(/self-employed/i);
    expect(system(llm)).toMatch(/fictional/i);
  });

  it('uses a given address as is', async () => {
    const address = { street: '1 Main St', city: 'Austin', state: 'TX', zip: '78701' };
    const llm = createFakeLlm({ persona: [llmPersonal] });
    const p = await randomPersona('personal', { address }, { llm, rng: createRng('p2') });
    expect(p.address).toEqual(address);
  });

  it('rejects an address in an unknown state without calling the model', async () => {
    const llm = createFakeLlm({});
    const address = { street: '1 Main St', city: 'Nowhere', state: 'ZZ', zip: '00001' };
    await expect(randomPersona('personal', { address }, { llm, rng: createRng('p3') })).rejects.toThrow(/ZZ/);
    expect(llm.calls).toHaveLength(0);
  });

  it('fills a business profile and keeps the given industry', async () => {
    const llm = createFakeLlm({
      persona: [
        {
          legalName: 'Copper Kettle Cafe LLC',
          entityType: 'LLC',
          industry: 'Restaurant',
          description: 'Neighborhood cafe with breakfast and coffee',
          founded: '2018-05',
          employeeCount: 7,
          address: { street: '22 Pine St', city: 'X', state: 'CA', zip: '94105' },
        },
      ],
    });
    const partial: Partial<Profile> = { industry: 'Cafe / coffee shop' };
    const p = await randomPersona('business', partial, { llm, rng: createRng('b1') });
    expect(p).toMatchObject({ legalName: 'Copper Kettle Cafe LLC', industry: 'Cafe / coffee shop' });
    expect(llm.calls[0]!.node).toBe('persona');
  });
});
