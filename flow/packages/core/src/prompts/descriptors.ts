import type { LlmMessage } from '../llm.js';
import type { NormalizedRequest } from '../normalize.js';
import type { Profile } from '../schemas/index.js';
import { UNTRUSTED_RULE, hardRules, json, locationLine, subjectLine, untrusted } from './common.js';

/** One merchantKey to write a template for, with what the statement rows are. */
export type DescriptorRequestItem = {
  merchantKey: string;
  fact?: Record<string, unknown>;
  /** How the rows of this key arise: account, recurring kind / discretionary, txnType, location. */
  uses: Record<string, unknown>[];
};

export type DescriptorsPromptInput = {
  norm: NormalizedRequest;
  profile: Profile;
  items: DescriptorRequestItem[];
  skillsText: string;
  /** Audit findings about the previous templates (a send-back to descriptors). */
  feedback?: string[];
};

const RULES = `Task: write ONE bank-statement descriptor template (and a plain-English memo) for EACH merchantKey listed in the data - nothing else. The engine fills the placeholders per row, uppercases, collapses spaces and cuts to 60 characters.

Rules:
- Placeholders: {STORE} (the store number: the researched one when the fact has "storeNumber", else a fixed 4-digit one), {CITY} (the fact's city, max 13 chars), {ST} (the fact's state), {MMDD} (swipe date), {ID} (a 6-10 digit account / customer number, the same on every row of the merchant), {REF} (a per-row reference: 10 A-Z0-9 on card rows - Amazon order codes; digits on ACH rows), {LAST4}. No other braces.
- Characters: A-Z 0-9 space # * . , - / & ' only. Uppercase.
- Checking card rows (account "checking", txnType POS): choose ONE house prefix style from bank-descriptors section 3 (CHECKCARD {MMDD} / POS PURCHASE / DEBIT CARD PURCHASE) and use it on EVERY checking POS template. Credit card rows never have a prefix. A merchant used on both accounts follows the checking style.
- Card rows look like the processor sends them: ONLY chains (facts with a "chain") get a store number (#{STORE}) and {CITY} {ST}; independents via TST* / SQ * when that is how they take cards, never a store number; online merchants with a domain or phone fragment and their HQ state, never a store number.
- Facts marked "national" (state "US") are nationwide companies: never use {CITY} or {ST} for them; write their real descriptor literally (for example "NETFLIX.COM LOS GATOS CA", "SPOTIFY USA", "ADOBE *ADOBE 800-833-6687 CA").
- ACH bills (rent, utilities, phone, insurance, loans, taxes, payroll): biller name (cut at 16 chars) + entry description, never a city or state, and never {REF} on ACH bills. Utilities, phone, insurance and loans: "NAME WEB PMTS", "NAME AUTOPAY", "NAME PAYMENT ID {ID}" or "NAME INS PREM" (an account number via {ID}, or none). Rent: "APPFOLIO RENT {ID}" / "YARDI PAYMENT {ID}" style or the property manager's name + "RENT". IRS estimated tax: exactly "IRS USATAXPYMT {REF}" (the engine writes the 15-digit trace).
- Budget the 60 characters: prefix + name + store + city (13) + state must fit; shorten the name, never the state.
- memo: short plain English a person would say ("Groceries at H-E-B", "Monthly rent"), no codes.`;

/** Messages for the descriptors node: all templates in one batched call. */
export function buildDescriptorsMessages(input: DescriptorsPromptInput): LlmMessage[] {
  const { norm, profile, items, skillsText, feedback } = input;
  const system = [
    'You write the transaction description lines of realistic US bank and credit-card statements for a spot-the-fake game. Realism comes from the ugly details: processor prefixes, truncation, store numbers.',
    subjectLine(profile),
    locationLine(norm, profile),
    RULES,
    hardRules(norm.request.subjectType),
    UNTRUSTED_RULE,
    'Reply with a JSON array of {merchantKey, template, memo}, one per listed merchantKey.',
    'Reference knowledge (skills):',
    skillsText,
  ].join('\n\n');
  const accounts = (['checking', 'credit'] as const).filter((a) => norm.request.accounts[a]);
  const problems =
    feedback && feedback.length > 0
      ? `\n\nProblems a reviewer found in the previous descriptors - fix every one:\n${feedback.map((f) => `- ${f}`).join('\n')}`
      : '';
  return [
    { role: 'system', content: system },
    { role: 'user', content: `Accounts on this statement: ${accounts.join(', ')}.\n${untrusted('Merchant keys with their researched facts', json(items))}${problems}` },
  ];
}
