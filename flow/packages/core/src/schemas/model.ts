import { z } from 'zod';
import { CountRangeSchema, DateStringSchema } from './request.js';

const weekday = z.number().int().min(0).max(6);
const dayOfMonth = z.number().int().min(1).max(28);
const positiveCents = z.number().int().positive();

const minLeMax = { message: 'min must be <= max', path: ['min'] };

/** Integer cents range, min <= max. */
const centsRange = z
  .object({ min: positiveCents, max: positiveCents })
  .refine((r) => r.min <= r.max, minLeMax);

/** Integer occurrence-count range (may be 0), min <= max. */
const perMonthRange = z
  .object({ min: z.number().int().min(0), max: z.number().int().min(0) })
  .refine((r) => r.min <= r.max, minLeMax);

export const AccountKindSchema = z.enum(['checking', 'credit']);
export type AccountKind = z.infer<typeof AccountKindSchema>;

export const IncomeStreamSchema = z.object({
  id: z.string().min(1),
  label: z.string().min(1),
  payerName: z.string().min(1),
  channel: z.enum([
    'ach_client',
    'stripe',
    'square',
    'paypal',
    'zelle',
    'venmo',
    'platform_payout',
    'mobile_deposit',
    'card_sales',
  ]),
  cadence: z.discriminatedUnion('kind', [
    z.object({ kind: z.literal('weekly'), weekday }),
    z.object({ kind: z.literal('biweekly'), weekday }),
    z.object({ kind: z.literal('monthly'), day: dayOfMonth }),
    z.object({ kind: z.literal('irregular'), perMonth: CountRangeSchema }),
  ]),
  amountCents: centsRange,
  /** 12 monthly multipliers, index 0 = January. */
  seasonality: z.array(z.number().min(0)).length(12),
  factId: z.string().min(1).optional(),
});
export type IncomeStream = z.infer<typeof IncomeStreamSchema>;

export const RecurringItemSchema = z.object({
  id: z.string().min(1),
  label: z.string().min(1),
  kind: z.enum([
    'rent',
    'utility',
    'subscription',
    'insurance',
    'loan',
    'tax_estimated',
    'payroll',
    'vendor',
    'other',
  ]),
  dayRule: z.discriminatedUnion('kind', [
    z.object({ kind: z.literal('dayOfMonth'), day: dayOfMonth }),
    z.object({ kind: z.literal('taxDates') }),
    z.object({ kind: z.literal('biweekly'), weekday, anchor: DateStringSchema }),
  ]),
  amount: z.discriminatedUnion('kind', [
    z.object({ kind: z.literal('fixed'), cents: positiveCents }),
    z
      .object({
        kind: z.literal('seasonal'),
        minCents: positiveCents,
        maxCents: positiveCents,
        /** Calendar months 1-12 where the amount peaks. */
        peakMonths: z.array(z.number().int().min(1).max(12)),
      })
      .refine((a) => a.minCents <= a.maxCents, {
        message: 'minCents must be <= maxCents',
        path: ['minCents'],
      }),
  ]),
  account: AccountKindSchema,
  priority: z.number().int().min(1).max(5),
  factId: z.string().min(1),
  /** How the bill is paid: ACH (bank draft), POS (charged to a card), CHECK or ZELLE. Card-account items are always POS. */
  txnType: z.enum(['ACH', 'POS', 'CHECK', 'ZELLE']),
});
export type RecurringItem = z.infer<typeof RecurringItemSchema>;

export const DiscretionaryItemSchema = z
  .object({
    factId: z.string().min(1),
    weight: z.number().positive(),
    perMonth: perMonthRange,
    ticketCents: centsRange,
    pricing: z.enum(['basket', 'fuel', 'fixed_menu']),
    /** Per-gallon pump price in cents; required when pricing === 'fuel'. */
    unitPriceCents: positiveCents.optional(),
    taxable: z.boolean(),
    tip: z.boolean(),
    account: AccountKindSchema,
    weekendBias: z.number().min(0).max(1),
    location: z.enum(['local', 'online', 'travel']),
  })
  .superRefine((item, ctx) => {
    if (item.pricing === 'fuel' && item.unitPriceCents === undefined) {
      ctx.addIssue({
        code: 'custom',
        path: ['unitPriceCents'],
        message: 'unitPriceCents is required when pricing is "fuel"',
      });
    }
  });
export type DiscretionaryItem = z.infer<typeof DiscretionaryItemSchema>;

export const AtmConfigSchema = z.object({
  perMonth: perMonthRange,
  amountChoicesCents: z.array(positiveCents).min(1),
  /** Per-choice weights, same order as `amountChoicesCents`; omitted means uniform. */
  amountWeights: z.array(z.number().min(0)).min(1).optional(),
  feeCents: z.number().int().min(0),
  /** Spread for the fee actually charged; overrides `feeCents` when present. */
  feeRange: z
    .object({ min: z.number().int().min(0), max: z.number().int().min(0) })
    .refine((r) => r.min <= r.max, minLeMax)
    .optional(),
  /** Share of withdrawals that carry no fee (in-network ATM, or the fee gets refunded). */
  waivedProbability: z.number().min(0).max(1).optional(),
});
export type AtmConfig = z.infer<typeof AtmConfigSchema>;

export const FinancialModelSchema = z.object({
  incomeStreams: z.array(IncomeStreamSchema),
  recurring: z.array(RecurringItemSchema),
  discretionaryPool: z.array(DiscretionaryItemSchema),
  atm: AtmConfigSchema.optional(),
  openingBalanceCents: z.number().int(),
  creditLimitCents: positiveCents.optional(),
  apr: z.number().min(0).max(1).optional(),
  creditOpeningBalanceCents: z.number().int().optional(),
  cardPaymentStrategy: z.enum(['full', 'partial']).optional(),
  closingDay: dayOfMonth.optional(),
});
export type FinancialModel = z.infer<typeof FinancialModelSchema>;
