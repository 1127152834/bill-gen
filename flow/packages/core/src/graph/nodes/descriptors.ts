import { z } from 'zod';
import { IRS_TEMPLATE, type DescriptorTemplate } from '../../descriptors.js';
import { isLongTail } from '../../longtail.js';
import type { NormalizedRequest } from '../../normalize.js';
import { compactFact } from '../../prompts/common.js';
import { buildDescriptorsMessages, type DescriptorRequestItem } from '../../prompts/descriptors.js';
import { NATIONAL_STATE, type FinancialModel, type MerchantFact, type Profile } from '../../schemas/index.js';
import { SKILLS_BY_NODE } from '../../skills.js';
import type { NodeDeps } from './types.js';

/** Keys with built-in templates (see `resolveTemplate`); income streams get theirs from the channel. */
const BUILTIN_KEYS = new Set(['card-payment', 'interest', 'fee-overdraft', 'fee-atm', 'atm']);
const PLACEHOLDER = /\{([^{}]*)\}/g;
const KNOWN_PLACEHOLDERS = new Set(['STORE', 'CITY', 'ST', 'MMDD', 'REF', 'ID', 'LAST4']);

const ReplySchema = z.array(z.object({ merchantKey: z.string(), template: z.string(), memo: z.string() }));

export type DescriptorsInput = {
  norm: NormalizedRequest;
  profile: Profile;
  facts: MerchantFact[];
  model: FinancialModel;
  /** The merchantKeys of the scheduled rows (duplicates allowed). */
  merchantKeys: string[];
  /** Audit findings about the previous templates; passed to the model on a send-back. */
  feedback?: string[];
};

function requestItem(key: string, fact: MerchantFact | undefined, model: FinancialModel): DescriptorRequestItem {
  const uses = [
    ...model.recurring
      .filter((r) => r.factId === key)
      .map((r) => ({ account: r.account, as: `recurring ${r.kind}`, txnType: r.txnType, label: r.label })),
    ...model.discretionaryPool
      .filter((d) => d.factId === key)
      .map((d) => ({ account: d.account, as: 'card purchase', txnType: 'POS', location: d.location })),
  ];
  return { merchantKey: key, ...(fact && { fact: compactFact(fact) }), uses };
}

type SanitizeCtx = {
  /** National company: no {CITY}/{ST}. */
  national: boolean;
  /** Not a chain: no store number. */
  independent: boolean;
  /** Every row of the key is an ACH bill: no per-row {REF} (except the IRS trace). */
  achOnly: boolean;
};

/**
 * Keeps only the known placeholders and drops the ones that would read as fake: {CITY}/{ST} for
 * national facts, `#{STORE}` for independents, {REF} on ACH-only bills (IRS keeps its trace).
 * Uppercase, allowed characters only.
 */
function sanitize(template: string, ctx: SanitizeCtx): string {
  const keepRef = !ctx.achOnly || IRS_TEMPLATE.test(template.toUpperCase());
  const drop = (name: string): boolean =>
    !KNOWN_PLACEHOLDERS.has(name) ||
    (ctx.national && (name === 'CITY' || name === 'ST')) ||
    (ctx.independent && name === 'STORE') ||
    (!keepRef && name === 'REF');
  return template
    .replace(/#\s*\{STORE\}/g, (whole) => (ctx.independent ? ' ' : whole))
    .replace(PLACEHOLDER, (whole, name: string) => (drop(name) ? ' ' : whole))
    .toUpperCase()
    .replace(/[^A-Z0-9 #*.,\-/&'{}]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/**
 * Writes one descriptor template per distinct merchantKey that needs one (not the built-in keys,
 * not income streams) in a single batched model call. Templates for keys that were not asked for
 * are ignored; a key the model skipped falls back to `resolveTemplate`'s default.
 */
export async function descriptorsNode(input: DescriptorsInput, deps: NodeDeps): Promise<DescriptorTemplate[]> {
  const { norm, profile, facts, model, merchantKeys, feedback } = input;
  const incomeIds = new Set(model.incomeStreams.map((s) => s.id));
  // The long tail carries its descriptor in the table; asking the model about a merchant nobody
  // researched only burns calls and invents names.
  const keys = [...new Set(merchantKeys)].filter((k) => !BUILTIN_KEYS.has(k) && !incomeIds.has(k) && !isLongTail(k));
  if (keys.length === 0) return [];

  const factById = new Map(facts.map((f) => [f.id, f]));
  const items = keys.map((k) => requestItem(k, factById.get(k), model));
  const skillsText = deps.skills.get(SKILLS_BY_NODE.descriptors);
  const reply = await deps.llm.invoke('descriptors', ReplySchema, buildDescriptorsMessages({ norm, profile, items, skillsText, ...(feedback && { feedback }) }));

  const wanted = new Set(keys);
  const out = new Map<string, DescriptorTemplate>();
  for (const t of reply) {
    if (!wanted.has(t.merchantKey) || out.has(t.merchantKey)) continue;
    const fact = factById.get(t.merchantKey);
    const uses = items.find((i) => i.merchantKey === t.merchantKey)!.uses;
    const template = sanitize(t.template, {
      national: fact?.state === NATIONAL_STATE,
      independent: fact !== undefined && fact.chain === undefined,
      achOnly: uses.length > 0 && uses.every((u) => u.txnType === 'ACH'),
    });
    if (!template) continue;
    const memo = t.memo.trim() || (fact ? `Payment to ${fact.name}` : '');
    out.set(t.merchantKey, { merchantKey: t.merchantKey, template, memo });
  }
  return [...out.values()];
}
