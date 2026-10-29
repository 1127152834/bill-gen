import type { LlmMessage } from '../llm.js';
import type { NormalizedRequest } from '../normalize.js';
import type { FinancialModel, MerchantFact, Profile } from '../schemas/index.js';
import { UNTRUSTED_RULE, compactFact, hardRules, isBusinessProfile, json, locationLine, subjectLine, untrusted } from './common.js';

export type LifeModelPromptInput = {
  norm: NormalizedRequest;
  profile: Profile;
  facts: MerchantFact[];
  skillsText: string;
  /** Validation / audit problems from the previous round, to be fixed this time. */
  feedback?: string[];
};

function modelRules(norm: NormalizedRequest, business: boolean): string {
  const { checking, credit } = norm.request.accounts;
  const accounts = [checking && 'checking', credit && 'credit'].filter(Boolean).join(' and ');
  return `Task: design the subject's FinancialModel - the money that flows through the requested ${accounts} account(s). A scheduler turns it into dated rows; your job is a believable life (or business) with believable amounts.

Structure (all money is integer cents; 1234 = $12.34):
- incomeStreams: where money comes from. Always lands in checking${checking ? '' : ' - no checking account was requested, so incomeStreams MUST be []'}. "channel" picks how it arrives (see self-employed-patterns / industry-cost-structures); "payerName" is who pays, as the ACH company name shows it (max 16 characters): the platform or processor for platform_payout / stripe / square / paypal (e.g. UBER, ETSY, UPWORK, STRIPE), or for ach_client a fictional client company typical of the trade that still reads like a real business name (e.g. "KESTREL DESIGN CO", "NORTHGATE DENTAL", "PINE & PORTER") - never a placeholder word (ACME, EXAMPLE, SAMPLE, FICTIONAL, TEST, DEMO, XYZ, "Company Name"), for zelle / venmo / mobile_deposit a fictional customer's full name. NEVER a merchant from the facts that the subject buys from (a store, Amazon, a utility). Omit the income "factId" unless that fact is the paying platform itself. Cadence: weekly / biweekly {weekday 0=Sun..6=Sat}, monthly {day 1-28}, or irregular {perMonth {min,max}}. "amountCents" {min,max} per deposit, with odd cents for processor/platform payouts. "seasonality": 12 multipliers, index 0 = January, averaging about 1.0.
- recurring: fixed obligations (rent, loan, utility, phone, insurance, subscription, tax_estimated${business ? ', payroll, vendor' : ''}). Each has a "factId" from the facts list. dayRule: dayOfMonth {day 1-28}, taxDates (only for kind "tax_estimated"; lands on the federal estimated-tax dates), or biweekly {weekday, anchor YYYY-MM-DD}. amount: fixed {cents} or seasonal {minCents, maxCents, peakMonths 1-12} for utilities. txnType (ACH, POS, CHECK or ZELLE): ACH for checking bills, rent, loans, insurance, taxes${business ? ' and payroll' : ''}; POS for anything on the credit account (a card is never debited by ACH); ZELLE or CHECK only where the skills say so.
- discretionaryPool: everyday card purchases, each a fact from the list. Keep it to 8-12 items (never more than 14): a few often-visited merchants read more real than a long tail of one-offs, and every extra item is another block of JSON. weight = relative frequency; perMonth {min,max} = realistic visits per month for this person; ticketCents {min,max} = the whole visit's PRE-tax, PRE-tip total, not the price of one item: a fact "Latte: 575c" means a cafe ticket of about 450-1400 (a drink, sometimes food or a second drink); max at least 1.5x min, never min == max, or every visit costs the identical amount; pricing "basket" (groceries, retail, odd cents), "fixed_menu" (cafes, restaurants, menu prices) or "fuel" (needs unitPriceCents = the fact's per-gallon price in cents; omit unitPriceCents for every other item); taxable; tip (true only for sit-down restaurants, bars, salons, rides); weekendBias 0-1; location "local" (fact in the subject's state), "online" (national company or web shop, state "US"), "travel" (out-of-state, at most 10% of debits).
- atm (optional, checking only): perMonth, amountChoicesCents (multiples of 2000 only), feeCents (0 or 250-500).
  Vary it like a real person would: amountWeights so 2000/4000 dominate and 20000 is rare, feeRange
  (e.g. 0-350) instead of one flat fee, and waivedProbability 0.15-0.4 when the bank refunds fees or
  the person uses in-network machines. Three identical $60 withdrawals at $3 each is a fingerprint.
- openingBalanceCents: checking balance at the start (never a round number).${checking ? (business ? ' Must cover at least one month of fixed outflows (rent, payroll, loans, insurance, utilities); 3-6 weeks is typical.' : ' Must cover at least one month of rent plus bills; 2-6 weeks of fixed costs is typical for a gig worker, more for a stable consultant.') : ''}${credit ? '\n- Credit card (requested): creditLimitCents, apr (0.20-0.2999), creditOpeningBalanceCents (the previous statement balance, usually 5-40% of the limit), cardPaymentStrategy "full" or "partial" (partial: the scheduler charges interest). creditOpeningBalanceCents must not exceed creditLimitCents, and a month of expected card spending must stay at or below 60% of the limit: the scheduler fills each cycle to about the middle of the credit row range, so count (middle of the range minus card bills and the payment) purchases at the weight-averaged ticket plus sales tax, plus card-billed bills.' : ''}

Rules the code checks (a model that breaks one is rejected):
- No payerName, merchant or memo may contain a placeholder word (acme, example, sample, fictional, dummy, test, demo, foo, bar, lorem, xyz, "your company"): R12 rejects them outright.
- Every recurring and discretionary factId is an id from the facts list, and each fact is used by at most ONE recurring or discretionary item. No income payerName is a merchant the subject spends at.
- Only the requested accounts appear in "account" fields (${accounts}).
- Priorities (1 = never skipped ... 4 = first to go): rent/mortgage, loan, ${business ? 'payroll, ' : ''}tax_estimated, insurance -> 1-2; utilities and phone -> 2-3; subscriptions -> 3-4.
- Row budget: every statement cycle must hold between the account's min and max rows. Rows that can never be dropped, counted at their WORST case for one cycle - recurring items with priority 1-2 (dayOfMonth 1, biweekly 3, taxDates 1), every income deposit (weekly 5, biweekly 3, monthly 1, irregular = perMonth.max), the card payment (1 on checking when both accounts exist, 1 on the card) and interest on a partially paid card (1) - must total at most max for each account ("protectedRowCap" in the data). Discretionary purchases fill the rest up to the target, so make the pool's perMonth.max values add up to at least the account's max.${business ? '' : '\n- No payroll, salary, wages or direct deposit anywhere: no recurring kind "payroll", and no income payerName or label that reads like an employer paycheck.\n- Housing: when the facts include a housing fact (category rent / apartments / mortgage / property management), the household pays it: one recurring item of kind "rent" (or "loan" for a mortgage servicer) on that fact, priority 1, ACH or ZELLE/CHECK as the skills say.'}
- Amounts must fit the cost-of-living tier, the trade, and any researched prices (use them: a rent fact with a monthly price IS the rent).`;
}

/** Messages for the lifeModel node. */
export function buildLifeModelMessages(input: LifeModelPromptInput): LlmMessage[] {
  const { norm, profile, facts, skillsText, feedback } = input;
  const business = isBusinessProfile(profile);
  const system = [
    'You are a financial-realism designer for a generator of realistic US bank-statement transaction data used in a spot-the-fake game: players compare real and generated statements, so every amount, frequency and payee must look like this exact subject\'s real life.',
    // Subject and location deliberately live in the user turn (see below): keeping them out of the
    // system prompt makes it byte-identical for every job with the same account shape, which is
    // what lets the provider's prefix cache hit instead of reprocessing ~8k tokens per call.
    modelRules(norm, business),
    hardRules(norm.request.subjectType),
    UNTRUSTED_RULE,
    'Reply with the FinancialModel as a single JSON object.',
    'Reference knowledge (skills):',
    skillsText,
  ].join('\n\n');

  const range = (r: { min: number; max: number } | undefined) => r && { ...r, protectedRowCap: r.max };
  const data = {
    subject: { type: norm.request.subjectType, ...profile },
    location: {
      city: profile.address.city,
      state: norm.location.state,
      combinedSalesTaxRate: norm.location.combinedSalesTaxRate,
      colTier: norm.location.colTier,
    },
    accounts: { checking: range(norm.request.accounts.checking), credit: range(norm.request.accounts.credit) },
    calendar: {
      months: norm.request.months,
      checkingCycles: norm.cycles.checking?.map((c) => ({ start: c.start, end: c.end })),
      creditCycles: norm.cycles.credit?.map((c) => ({ start: c.start, end: c.end, dueDate: c.dueDate })),
      creditClosingDay: norm.closingDay,
      estimatedTaxDates: norm.taxDates,
      bankHolidays: norm.holidays.filter((d) => d >= norm.periodStart && d <= norm.periodEnd),
    },
  };
  const parts = [
    `${subjectLine(profile)}\n${locationLine(norm, profile)}`,
    `Subject and calendar:\n${json(data)}`,
    untrusted('Researched facts', json(facts.map(compactFact))),
  ];
  if (feedback && feedback.length > 0) {
    parts.push(`Problems found in the previous attempt - fix every one:\n${feedback.map((f) => `- ${f}`).join('\n')}`);
  }
  return [
    { role: 'system', content: system },
    { role: 'user', content: parts.join('\n\n') },
  ];
}

/** Follow-up message after the code checks rejected a model. */
export function buildLifeModelRetryMessage(problems: string[], previous: FinancialModel): LlmMessage {
  return {
    role: 'user',
    content:
      `Your model was rejected by the checks:\n${problems.map((p) => `- ${p}`).join('\n')}\n\n` +
      `Your previous model:\n${json(previous)}\n\n` +
      'Reply with the complete corrected FinancialModel (every field), fixing every problem and keeping what was fine.',
  };
}

/**
 * Retry message that asks for a *patch* instead of a whole new model.
 *
 * A rejected model usually gets 2-3 problems, all inside one or two arrays. Re-sending the full
 * life invites the model to rewrite every amount and introduces fresh mismatches, and the reply is
 * ~4k tokens of decoding on top of the resend. A patch is a fraction of that and leaves the parts
 * that already passed untouched.
 */
export function buildLifeModelPatchMessage(problems: string[], previous: FinancialModel): LlmMessage {
  return {
    role: 'user',
    content:
      `Your model was rejected by the checks:\n${problems.map((p) => `- ${p}`).join('\n')}\n\n` +
      `Your previous model:\n${json(previous)}\n\n` +
      'Reply with ONLY the parts that must change, as {"patch": {...}}. Allowed keys: ' +
      'incomeStreams, recurring, discretionaryPool, atm, openingBalanceCents, creditLimitCents, apr, ' +
      'creditOpeningBalanceCents, cardPaymentStrategy, closingDay. ' +
      'An array you send replaces that whole array (re-emit the full corrected list), so keep every ' +
      'item that was already fine. Omit everything that does not need to change — do not resend the ' +
      'whole model.',
  };
}
