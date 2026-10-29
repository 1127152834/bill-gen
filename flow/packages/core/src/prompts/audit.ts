import type { LlmMessage } from '../llm.js';
import type { NormalizedRequest } from '../normalize.js';
import type { MerchantFact, Profile, Statement } from '../schemas/index.js';
import { UNTRUSTED_RULE, compactFact, hardRules, json, locationLine, subjectLine, untrusted } from './common.js';

export type AuditPromptInput = {
  norm: NormalizedRequest;
  profile: Profile;
  statements: Statement[];
  facts: MerchantFact[];
  skillsText: string;
};

export const MAX_FINDINGS = 12;

/** Row reference prefix of a statement in the audit table: CHK1, CHK2 … / CC1, CC2 …. */
export const refPrefix = (s: Statement): string => (s.accountType === 'credit' ? 'CC' : 'CHK');

const money = (cents: number): string => (cents / 100).toFixed(2);

/** One statement as a compact pipe table; ids replaced by short refs, evidence as its note (no URLs). */
export function renderStatementTable(s: Statement): string {
  const credit = s.accountType === 'credit';
  const head = credit
    ? `credit card ****${s.accountLast4}, limit ${money(s.credit?.creditLimitCents ?? 0)}, APR ${s.credit?.apr ?? 0}`
    : `${s.accountType} ****${s.accountLast4}, opening balance ${money(s.checking?.openingBalanceCents ?? 0)}`;
  const months = s.months.map((m) => {
    const card = m.credit
      ? `, statement balance ${money(m.credit.statementBalanceCents)}, minimum due ${money(m.credit.minimumDueCents)} by ${m.credit.dueDate}, interest ${money(m.credit.interestChargedCents)}`
      : `, closing balance ${money(m.closingBalanceCents)}`;
    return `cycle ${m.cycleStart}..${m.cycleEnd}: ${m.transactionCount} rows, credits ${money(m.totalCreditsCents)}, debits ${money(m.totalDebitsCents)}${card}`;
  });
  const prefix = refPrefix(s);
  const rows = s.transactions.map((t, i) => {
    const sign = t.direction === 'credit' ? '+' : '-';
    const balance = t.runningBalanceCents === undefined ? '' : money(t.runningBalanceCents);
    return `${prefix}${i + 1} | ${t.postedDate} | ${t.description} | ${sign}${money(t.amountCents)} | ${balance} | ${t.evidence?.note ?? ''}`;
  });
  return [`### ${head}`, ...months, 'ref | postedDate | description | amount | balance | evidence', ...rows].join('\n');
}

const RULES = `Task: you are the toughest player of the game. Read the statements below and report every detail that would let a careful person tell they were generated rather than real.

Look at:
- Descriptions: one checking card-prefix style per account, no prefix on card rows, processor prefixes (TST*, SQ *, AMZN MKTP US*) used where real, store numbers only on chains and matching the store number in the row's evidence when it names one, national companies not shown with a local city, no lowercase, no PAYROLL/DIRECT DEP/SALARY for a self-employed person, random-looking letter codes on ACH bills.
- Evidence: each row's evidence column is the researched merchant and price it is based on; the description, payer and amount must fit it (an income payer named differently from its evidence, a price far from the quoted one).
- Amounts: prices plausible for the merchant, the researched prices and the cost-of-living tier; tax and tip in the cents; bills fixed or seasonal as real bills are; deposits net of fees with odd cents; not too many round numbers.
- Repetition: the same merchant charging the identical amount again and again (only subscriptions and fixed bills repeat exactly), identical deposits, the same weekday every time for irregular income.
- Frequencies and mix: visits per month per merchant, income paid by a merchant the subject shops at, a trade without its typical costs (a driver without fuel), spending that does not fit the household or hobbies, income that looks like a salary.
- Calendar: ACH and card payments posting on weekends or bank holidays, estimated tax outside its dates, interest on a card paid in full.
- Balances: overdrafts and fees, a balance far too high or too low for the subject, card payment pairs.

Do NOT report: the fictional bank name, fictional client or customer names on income rows (not being in the researched facts is by design), masked account numbers, or descriptors cut at 60 characters - these are by design.

Each finding:
- severity: "high" = a careful player would call it fake at once, or a hard rule is broken; "medium" = noticeable on a closer look; "low" = a nitpick.
- transactionIds: the row refs from the tables (for example "CHK3", "CC12"); [] when it concerns a whole statement.
- problem: one or two sentences saying what is wrong and what a real statement would show.
- suggestedTarget: "lifeModel" (merchant mix, amounts, frequencies, income pattern, balance level), "schedule" (dates, posting order, counts, a specific placement) or "descriptors" (description or memo text).
Return at most ${MAX_FINDINGS} findings, most severe first, and [] when the statements look real.`;

/** Messages for the audit node. */
export function buildAuditMessages(input: AuditPromptInput): LlmMessage[] {
  const { norm, profile, statements, facts, skillsText } = input;
  const system = [
    'You audit generated US bank and credit-card statements for realism in a spot-the-fake game.',
    subjectLine(profile),
    locationLine(norm, profile),
    RULES,
    hardRules(norm.request.subjectType),
    UNTRUSTED_RULE,
    'Reply with a JSON array of {severity, transactionIds, problem, suggestedTarget}.',
    'Reference knowledge (skills):',
    skillsText,
  ].join('\n\n');
  const user = [
    `Statements (amount: + money in / payment to the card, - money out / card charge):`,
    ...statements.map(renderStatementTable),
    untrusted('Researched merchants behind the rows', json(facts.map(compactFact))),
  ].join('\n\n');
  return [
    { role: 'system', content: system },
    { role: 'user', content: user },
  ];
}
