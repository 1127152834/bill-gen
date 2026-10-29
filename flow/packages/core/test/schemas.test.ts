import { describe, expect, it } from 'vitest';
import {
  AddressSchema,
  AuditFindingSchema,
  BusinessProfileSchema,
  CountRangeSchema,
  FinancialModelSchema,
  GenerationRequestSchema,
  GenerationResultSchema,
  MerchantFactSchema,
  PersonalProfileSchema,
  RuleIdSchema,
  RuleResultSchema,
  StatementSchema,
  TransactionSchema,
} from '../src/index.js';
import * as schemasEntry from '../src/schemas/index.js';

const address = { street: '100 Congress Ave', city: 'Austin', state: 'TX', zip: '78701' };

const personal = {
  name: 'Maria Lopez',
  age: 34,
  address,
  occupation: 'Dental hygienist',
  household: { maritalStatus: 'married', children: 2 },
  hobbies: ['hiking', 'baking'],
};

const business = {
  legalName: 'Lone Star Landscaping LLC',
  entityType: 'LLC',
  industry: 'landscaping',
  description: 'Residential lawn care and landscaping',
  founded: '2019-04',
  employeeCount: 6,
  address,
};

const txn = {
  id: 't1',
  transactionDate: '2026-09-03',
  postedDate: '2026-09-04',
  description: 'POS DEBIT HEB #123 AUSTIN TX',
  memo: 'Groceries',
  type: 'POS',
  direction: 'debit',
  amountCents: 4231,
  runningBalanceCents: 120000,
};

const month = {
  cycleStart: '2026-09-01',
  cycleEnd: '2026-09-30',
  openingBalanceCents: 100000,
  closingBalanceCents: 95769,
  totalCreditsCents: 0,
  totalDebitsCents: 4231,
  transactionCount: 1,
};

const statement = {
  bankName: 'Lone Star Federal',
  accountType: 'checking',
  accountLast4: '4821',
  periodStart: '2026-09-01',
  periodEnd: '2026-09-30',
  months: [month],
  checking: { openingBalanceCents: 100000, closingBalanceCents: 95769 },
  transactions: [txn],
};

describe('CountRange', () => {
  it('rejects min > max', () =>
    expect(CountRangeSchema.safeParse({ min: 9, max: 8 }).success).toBe(false));
  it('accepts min == max', () =>
    expect(CountRangeSchema.safeParse({ min: 8, max: 8 }).success).toBe(true));
  it('rejects min < 1 and max > 200', () => {
    expect(CountRangeSchema.safeParse({ min: 0, max: 5 }).success).toBe(false);
    expect(CountRangeSchema.safeParse({ min: 5, max: 201 }).success).toBe(false);
  });
  it('rejects non-integers', () =>
    expect(CountRangeSchema.safeParse({ min: 1.5, max: 5 }).success).toBe(false));
});

describe('Address', () => {
  it('rejects lowercase state and 4-digit zip', () => {
    expect(AddressSchema.safeParse({ ...address, state: 'tx' }).success).toBe(false);
    expect(AddressSchema.safeParse({ ...address, zip: '7870' }).success).toBe(false);
  });
});

describe('profiles', () => {
  it('accepts a personal and a business profile', () => {
    expect(PersonalProfileSchema.safeParse(personal).success).toBe(true);
    expect(BusinessProfileSchema.safeParse(business).success).toBe(true);
  });
  it('rejects age < 18, children > 6, hobbies > 8', () => {
    expect(PersonalProfileSchema.safeParse({ ...personal, age: 17 }).success).toBe(false);
    expect(
      PersonalProfileSchema.safeParse({
        ...personal,
        household: { maritalStatus: 'single', children: 7 },
      }).success,
    ).toBe(false);
    expect(
      PersonalProfileSchema.safeParse({ ...personal, hobbies: Array(9).fill('x') }).success,
    ).toBe(false);
  });
  it('rejects bad founded format and employeeCount > 500', () => {
    expect(BusinessProfileSchema.safeParse({ ...business, founded: '2019-4' }).success).toBe(false);
    expect(BusinessProfileSchema.safeParse({ ...business, employeeCount: 501 }).success).toBe(
      false,
    );
  });
});

describe('GenerationRequest', () => {
  it('requires at least one account', () =>
    expect(
      GenerationRequestSchema.safeParse({ subjectType: 'personal', accounts: {}, months: 3 })
        .success,
    ).toBe(false));
  it('rejects months=2', () =>
    expect(
      GenerationRequestSchema.safeParse({
        subjectType: 'personal',
        accounts: { checking: { min: 8, max: 13 } },
        months: 2,
      }).success,
    ).toBe(false));
  it('rejects business profile under personal', () =>
    expect(
      GenerationRequestSchema.safeParse({
        subjectType: 'personal',
        profile: business,
        accounts: { checking: { min: 8, max: 13 } },
        months: 3,
      }).success,
    ).toBe(false));
  it('rejects personal profile under business', () =>
    expect(
      GenerationRequestSchema.safeParse({
        subjectType: 'business',
        profile: personal,
        accounts: { checking: { min: 8, max: 13 } },
        months: 3,
      }).success,
    ).toBe(false));
  it('accepts a full personal request', () =>
    expect(
      GenerationRequestSchema.safeParse({
        subjectType: 'personal',
        profile: personal,
        accounts: { checking: { min: 8, max: 13 }, credit: { min: 5, max: 9 } },
        months: 3,
        endMonth: '2026-09',
        seed: 'abc',
      }).success,
    ).toBe(true));
  it('accepts a business request and a request without profile', () => {
    expect(
      GenerationRequestSchema.safeParse({
        subjectType: 'business',
        profile: business,
        accounts: { credit: { min: 5, max: 9 } },
        months: 1,
      }).success,
    ).toBe(true);
    expect(
      GenerationRequestSchema.safeParse({
        subjectType: 'personal',
        accounts: { checking: { min: 1, max: 2 } },
        months: 6,
      }).success,
    ).toBe(true);
  });
  it('rejects malformed endMonth', () =>
    expect(
      GenerationRequestSchema.safeParse({
        subjectType: 'personal',
        accounts: { checking: { min: 1, max: 2 } },
        months: 1,
        endMonth: '2026-9',
      }).success,
    ).toBe(false));
});

describe('MerchantFact', () => {
  const fact = {
    id: 'f1',
    name: 'H-E-B',
    city: 'Austin',
    state: 'TX',
    category: 'grocery',
    priceItems: [{ item: 'milk 1gal', priceCents: 359, sourceUrl: 'https://heb.com/milk' }],
    sourceUrl: 'https://heb.com',
  };
  it('accepts a valid fact', () => expect(MerchantFactSchema.safeParse(fact).success).toBe(true));
  it('caps fact text at 80 characters', () => expect(MerchantFactSchema.safeParse({ ...fact, name: 'x'.repeat(81) }).success).toBe(false));
  it('rejects MerchantFact without sourceUrl', () => {
    const { sourceUrl: _omit, ...rest } = fact;
    expect(MerchantFactSchema.safeParse(rest).success).toBe(false);
  });
  it('rejects non-url sourceUrl and float price', () => {
    expect(MerchantFactSchema.safeParse({ ...fact, sourceUrl: 'not a url' }).success).toBe(false);
    expect(
      MerchantFactSchema.safeParse({
        ...fact,
        priceItems: [{ item: 'x', priceCents: 3.5, sourceUrl: 'https://a.com' }],
      }).success,
    ).toBe(false);
  });
});

describe('FinancialModel', () => {
  const model = {
    incomeStreams: [
      {
        id: 'i1',
        label: 'Salary',
        payerName: 'Acme Dental',
        channel: 'ach_client',
        cadence: { kind: 'biweekly', weekday: 5 },
        amountCents: { min: 190000, max: 210000 },
        seasonality: Array(12).fill(1),
      },
    ],
    recurring: [
      {
        id: 'r1',
        label: 'Rent',
        kind: 'rent',
        dayRule: { kind: 'dayOfMonth', day: 1 },
        amount: { kind: 'fixed', cents: 145000 },
        account: 'checking',
        priority: 1,
        factId: 'f1',
        txnType: 'ACH',
      },
    ],
    discretionaryPool: [
      {
        factId: 'f1',
        weight: 3,
        perMonth: { min: 2, max: 5 },
        ticketCents: { min: 3000, max: 9000 },
        pricing: 'basket',
        taxable: true,
        tip: false,
        account: 'credit',
        weekendBias: 0.4,
        location: 'local',
      },
    ],
    openingBalanceCents: 350000,
  };

  it('accepts a minimal model', () =>
    expect(FinancialModelSchema.safeParse(model).success).toBe(true));
  it('accepts the optional fields', () =>
    expect(
      FinancialModelSchema.safeParse({
        ...model,
        atm: { perMonth: { min: 0, max: 2 }, amountChoicesCents: [4000, 6000], feeCents: 300 },
        creditLimitCents: 500000,
        apr: 0.2399,
        creditOpeningBalanceCents: 0,
        cardPaymentStrategy: 'partial',
        closingDay: 12,
      }).success,
    ).toBe(true));
  it('rejects a fuel item without unitPriceCents', () => {
    const fuel = { ...model.discretionaryPool[0], pricing: 'fuel' };
    expect(FinancialModelSchema.safeParse({ ...model, discretionaryPool: [fuel] }).success).toBe(
      false,
    );
    expect(
      FinancialModelSchema.safeParse({
        ...model,
        discretionaryPool: [{ ...fuel, unitPriceCents: 299 }],
      }).success,
    ).toBe(true);
  });
  it('rejects non-positive weight, bad seasonality length, day 29', () => {
    expect(
      FinancialModelSchema.safeParse({
        ...model,
        discretionaryPool: [{ ...model.discretionaryPool[0], weight: 0 }],
      }).success,
    ).toBe(false);
    expect(
      FinancialModelSchema.safeParse({
        ...model,
        incomeStreams: [{ ...model.incomeStreams[0], seasonality: [1, 1] }],
      }).success,
    ).toBe(false);
    expect(
      FinancialModelSchema.safeParse({
        ...model,
        recurring: [{ ...model.recurring[0], dayRule: { kind: 'dayOfMonth', day: 29 } }],
      }).success,
    ).toBe(false);
  });
  it('rejects a recurring txnType outside the 11 transaction types', () =>
    expect(
      FinancialModelSchema.safeParse({
        ...model,
        recurring: [{ ...model.recurring[0], txnType: 'WIRE' }],
      }).success,
    ).toBe(false));
  it('rejects float money', () =>
    expect(FinancialModelSchema.safeParse({ ...model, openingBalanceCents: 10.5 }).success).toBe(
      false,
    ));
});

describe('output schemas', () => {
  it('accepts a transaction and rejects float cents / bad date / unknown type', () => {
    expect(TransactionSchema.safeParse(txn).success).toBe(true);
    expect(TransactionSchema.safeParse({ ...txn, amountCents: 42.31 }).success).toBe(false);
    expect(TransactionSchema.safeParse({ ...txn, postedDate: '09/04/2026' }).success).toBe(false);
    expect(TransactionSchema.safeParse({ ...txn, type: 'WIRE' }).success).toBe(false);
  });
  it('accepts a transaction with merchant and evidence', () =>
    expect(
      TransactionSchema.safeParse({
        ...txn,
        merchant: { name: 'H-E-B', category: 'grocery', city: 'Austin', state: 'TX' },
        evidence: { factId: 'f1', sourceUrl: 'https://heb.com', note: 'milk $3.59' },
      }).success,
    ).toBe(true));
  it('accepts a statement; rejects 5-digit accountLast4 and unknown accountType', () => {
    expect(StatementSchema.safeParse(statement).success).toBe(true);
    expect(StatementSchema.safeParse({ ...statement, accountLast4: '48210' }).success).toBe(false);
    expect(StatementSchema.safeParse({ ...statement, accountType: 'savings' }).success).toBe(false);
  });
  it('accepts a credit statement with credit month summary', () =>
    expect(
      StatementSchema.safeParse({
        ...statement,
        accountType: 'credit',
        checking: undefined,
        credit: { creditLimitCents: 500000, apr: 0.2399 },
        months: [
          {
            ...month,
            credit: {
              statementBalanceCents: 10000,
              minimumDueCents: 3500,
              dueDate: '2026-10-07',
              interestChargedCents: 0,
            },
          },
        ],
      }).success,
    ).toBe(true));
  it('RuleId lists exactly R1..R13', () => expect(RuleIdSchema.options).toHaveLength(13));
  it('accepts RuleResult and AuditFinding; rejects unknown targets', () => {
    expect(
      RuleResultSchema.safeParse({
        ruleId: 'R2_balance_continuity',
        passed: false,
        violations: [{ transactionIds: ['t1'], message: 'gap' }],
        target: 'schedule',
      }).success,
    ).toBe(true);
    expect(
      RuleResultSchema.safeParse({
        ruleId: 'R99',
        passed: true,
        violations: [],
        target: 'schedule',
      }).success,
    ).toBe(false);
    expect(
      AuditFindingSchema.safeParse({
        severity: 'high',
        transactionIds: ['t1'],
        problem: 'dup',
        suggestedTarget: 'descriptors',
      }).success,
    ).toBe(true);
    expect(
      AuditFindingSchema.safeParse({
        severity: 'critical',
        transactionIds: [],
        problem: 'x',
        suggestedTarget: 'descriptors',
      }).success,
    ).toBe(false);
  });
  it('accepts a GenerationResult with warnings and status', () => {
    const result = {
      id: 'g1',
      seed: 'abc',
      createdAt: '2026-10-29T10:00:00.000Z',
      subject: personal,
      statements: [statement],
      audit: { rules: [], findings: [] },
      usage: { llmCalls: 3, tokens: 1200, tavilyQueries: 4 },
      warnings: [],
      status: 'completed',
    };
    expect(GenerationResultSchema.safeParse(result).success).toBe(true);
    expect(GenerationResultSchema.safeParse({ ...result, status: 'failed' }).success).toBe(false);
  });
});

describe('package surface', () => {
  it('./schemas entry re-exports the schemas', () => {
    expect(schemasEntry.GenerationRequestSchema).toBe(GenerationRequestSchema);
    expect(schemasEntry.TransactionSchema).toBe(TransactionSchema);
  });
});
