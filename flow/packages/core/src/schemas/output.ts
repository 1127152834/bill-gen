import { z } from 'zod';
import {
  BusinessProfileSchema,
  DateStringSchema,
  PersonalProfileSchema,
} from './request.js';

const cents = z.number().int();
const nonNegativeCents = z.number().int().min(0);
const count = z.number().int().min(0);

export const TransactionTypeSchema = z.enum([
  'POS',
  'ACH',
  'ZELLE',
  'CHECK',
  'ATM',
  'FEE',
  'INTEREST',
  'TRANSFER',
  'PAYMENT',
  'REFUND',
  'DEPOSIT',
]);
export type TransactionType = z.infer<typeof TransactionTypeSchema>;

export const TransactionSchema = z.object({
  id: z.string().min(1),
  transactionDate: DateStringSchema,
  postedDate: DateStringSchema,
  /** Bank-format description. */
  description: z.string().min(1),
  /** English memo. */
  memo: z.string(),
  type: TransactionTypeSchema,
  direction: z.enum(['credit', 'debit']),
  /** Always positive; sign comes from `direction`. */
  amountCents: z.number().int().positive(),
  /** Checking accounts only. */
  runningBalanceCents: cents.optional(),
  merchant: z
    .object({
      name: z.string().min(1),
      category: z.string().min(1),
      city: z.string().min(1),
      state: z.string().regex(/^[A-Z]{2}$/),
    })
    .optional(),
  evidence: z
    .object({
      factId: z.string().min(1),
      sourceUrl: z.url(),
      note: z.string(),
    })
    .optional(),
});
export type Transaction = z.infer<typeof TransactionSchema>;

export const MonthSummarySchema = z.object({
  cycleStart: DateStringSchema,
  cycleEnd: DateStringSchema,
  openingBalanceCents: cents,
  closingBalanceCents: cents,
  totalCreditsCents: nonNegativeCents,
  totalDebitsCents: nonNegativeCents,
  transactionCount: count,
  credit: z
    .object({
      statementBalanceCents: cents,
      minimumDueCents: nonNegativeCents,
      dueDate: DateStringSchema,
      interestChargedCents: nonNegativeCents,
    })
    .optional(),
});
export type MonthSummary = z.infer<typeof MonthSummarySchema>;

export const StatementSchema = z.object({
  /** 空串 = 用渲染模板自带的那家银行（flow 不决定印哪家）。 */
  bankName: z.string(),
  accountType: z.enum(['checking', 'business_checking', 'credit']),
  accountLast4: z.string().regex(/^\d{4}$/, 'expected 4 digits'),
  periodStart: DateStringSchema,
  periodEnd: DateStringSchema,
  months: z.array(MonthSummarySchema),
  checking: z
    .object({ openingBalanceCents: cents, closingBalanceCents: cents })
    .optional(),
  credit: z
    .object({ creditLimitCents: z.number().int().positive(), apr: z.number().min(0).max(1) })
    .optional(),
  transactions: z.array(TransactionSchema),
});
export type Statement = z.infer<typeof StatementSchema>;

export const RuleIdSchema = z.enum([
  'R1_count_in_range',
  'R2_balance_continuity',
  'R3_dates_in_period',
  'R4_recurring_consistency',
  'R5_card_payment_match',
  'R6_amount_within_range',
  'R7_geo_consistency',
  'R8_no_duplicates',
  'R9_evidence_present',
  'R10_no_payroll_income_personal',
  'R11_credit_within_limit',
  'R12_no_placeholder_names',
  'R13_occupation_category_fit',
  'R14_variety_floor',
  'R15_no_gateway_income',
]);
export type RuleId = z.infer<typeof RuleIdSchema>;

export const RuleResultSchema = z.object({
  ruleId: RuleIdSchema,
  passed: z.boolean(),
  violations: z.array(
    z.object({ transactionIds: z.array(z.string()), message: z.string() }),
  ),
  /** Which upstream node a failure should be sent back to. */
  target: z.enum(['schedule', 'lifeModel']),
});
export type RuleResult = z.infer<typeof RuleResultSchema>;

export const AuditFindingSchema = z.object({
  severity: z.enum(['low', 'medium', 'high']),
  transactionIds: z.array(z.string()),
  problem: z.string(),
  suggestedTarget: z.enum(['lifeModel', 'schedule', 'descriptors']),
});
export type AuditFinding = z.infer<typeof AuditFindingSchema>;

/** A PDF produced by billgen's MCP server for one statement. */
export const RenderArtifactSchema = z.object({
  template: z.string().min(1),
  account: z.string().min(1),
  /** 账单所属月份（`YYYY-MM`）：一个月一份 PDF，不是把几个月合进一个文件。 */
  period: z.string().optional(),
  path: z.string().min(1),
  pages: z.number().int().min(0),
  bytes: z.number().int().min(0),
  sha256: z.string(),
  /** Fill rounds it took (1 = the ledger draft validated as-is). */
  attempts: z.number().int().min(0),
  /** True when the model edited the draft before rendering. */
  modelFilled: z.boolean(),
  /** Set when `BILLGEN_TEMPLATES=auto`: why the recommender picked this layout. */
  reasons: z.array(z.string()).optional(),
  /** Recommender score behind that pick. */
  score: z.number().optional(),
});

/** 按自然月拆出来的一份模板 JSON（交付物；用户自己导进模板出账单）。 */
export const MonthFileSchema = z.object({
  template: z.string().min(1),
  account: z.string().min(1),
  /** `YYYY-MM`：一个月一份。 */
  month: z.string().regex(/^\d{4}-\d{2}$/),
  path: z.string().min(1),
  rows: z.number().int().min(0),
  bytes: z.number().int().min(0),
  sha256: z.string(),
  /** 该份过了结构 + 内容 + 跨月三层校验。 */
  ok: z.boolean(),
});
export type MonthFile = z.infer<typeof MonthFileSchema>;

export const GenerationResultSchema = z.object({
  id: z.string().min(1),
  seed: z.string(),
  createdAt: z.iso.datetime(),
  subject: z.union([PersonalProfileSchema, BusinessProfileSchema]),
  statements: z.array(StatementSchema),
  audit: z.object({
    rules: z.array(RuleResultSchema),
    findings: z.array(AuditFindingSchema),
  }),
  usage: z.object({ llmCalls: count, tokens: count, tavilyQueries: count }),
  warnings: z.array(z.string()),
  status: z.enum(['completed', 'completed_with_warnings']),
  /** PDFs rendered by the billgen MCP server; empty when rendering was not configured. */
  renders: z.array(RenderArtifactSchema).default([]),
  /** 每月一份模板 JSON（默认交付物）；`deliver: 'pdf'` 时为空。 */
  monthFiles: z.array(MonthFileSchema).default([]),
});
export type RenderArtifact = z.infer<typeof RenderArtifactSchema>;
export type GenerationResult = z.infer<typeof GenerationResultSchema>;
