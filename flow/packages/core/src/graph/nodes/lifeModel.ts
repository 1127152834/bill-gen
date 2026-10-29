import { z } from 'zod';
import type { LlmMessage } from '../../llm.js';
import type { NormalizedRequest } from '../../normalize.js';
import {
  buildLifeModelMessages,
  buildLifeModelPatchMessage,
  buildLifeModelRetryMessage,
} from '../../prompts/lifeModel.js';
import {
  DiscretionaryItemSchema,
  FinancialModelSchema,
  type FinancialModel,
  type MerchantFact,
  type Profile,
} from '../../schemas/index.js';
import { SKILLS_BY_NODE } from '../../skills.js';
import { checkModel, normalizeModel } from './lifeModelChecks.js';
import type { NodeDeps } from './types.js';

/** Room for a full FinancialModel; a runaway string field is cut off instead of stalling the call. */
const LIFE_MODEL_MAX_TOKENS = 6000;
/** A patch only carries the arrays that failed, so it needs far less room. */
const PATCH_MAX_TOKENS = 2500;

/**
 * A repair reply: only the top-level keys that were wrong. Arrays are replaced whole (a re-emitted
 * `recurring` is a corrected list), objects are merged key by key.
 */
// Permissive on purpose: a reply that carries `patch` is merged, and a reply that carries a whole
// model is accepted as-is (some providers ignore the instruction and resend everything).
const ModelPatchSchema = z
  .object({ patch: z.record(z.string(), z.unknown()).optional() })
  .catchall(z.unknown());
type ModelPatch = z.infer<typeof ModelPatchSchema>;

/** Top-level keys a patch may touch; anything else in the reply is ignored. */
const PATCHABLE = new Set([
  'incomeStreams',
  'recurring',
  'discretionaryPool',
  'atm',
  'openingBalanceCents',
  'creditLimitCents',
  'apr',
  'creditOpeningBalanceCents',
  'cardPaymentStrategy',
  'closingDay',
]);

/** Merge a repair patch into the rejected model. Arrays replace, plain objects merge, scalars overwrite. */
function applyPatch(model: FinancialModel, patch: Record<string, unknown>): FinancialModel {
  const merged: Record<string, unknown> = { ...model };
  for (const [key, value] of Object.entries(patch)) {
    if (!PATCHABLE.has(key) || value === undefined) continue;
    const current = merged[key];
    const bothObjects =
      value !== null && typeof value === 'object' && !Array.isArray(value) && current !== null && typeof current === 'object' && !Array.isArray(current);
    merged[key] = bothObjects ? { ...(current as object), ...(value as object) } : value;
  }
  return merged as unknown as FinancialModel;
}

/** The model's FinancialModel failed the code checks twice (first answer and one retry). */
export class LifeModelError extends Error {
  readonly code = 'LIFE_MODEL_INVALID';
  readonly problems: string[];
  constructor(problems: string[]) {
    super(`Life model is invalid after one retry: ${problems.join('; ')}`);
    this.name = 'LifeModelError';
    this.problems = problems;
  }
}

/**
 * What the model must return. Fuel items may omit `unitPriceCents` here because code fills it
 * from the fact's per-gallon price; a still-missing one is reported by `checkModel`. Models often
 * write 0 or null for "no unit price" on other items: accepted here, removed by `normalizeModel`.
 */
const ModelReplySchema = FinancialModelSchema.extend({
  discretionaryPool: z.array(
    z.object({ ...DiscretionaryItemSchema.shape, unitPriceCents: z.number().int().min(0).nullish() }),
  ),
});

export type LifeModelInput = {
  norm: NormalizedRequest;
  profile: Profile;
  facts: MerchantFact[];
  /** Validation / audit problems of the previous round (Task 12 passes them on a send-back). */
  feedback?: string[];
  /** 覆盖注入的技能文本（默认按节点取全套；个人主体只取相关三个，见 `lifeModelSkills`）。 */
  skillsText?: string;
};

/**
 * Asks the model for a FinancialModel, applies deterministic fixes and checks it. On problems
 * the problems (and the rejected model) are appended and the model is asked once more; that retry
 * costs one more model call but is not a send-back round.
 * @throws LifeModelError when the second answer still has problems.
 */
export async function lifeModelNode(input: LifeModelInput, deps: NodeDeps): Promise<FinancialModel> {
  const { norm, profile, facts, feedback } = input;
  const skillsText = input.skillsText ?? deps.skills.get(SKILLS_BY_NODE.lifeModel);
  let messages: LlmMessage[] = buildLifeModelMessages({ norm, profile, facts, skillsText, ...(feedback && { feedback }) });

  const problemsOf = (model: FinancialModel): string[] => {
    const problems = checkModel(model, norm, facts);
    if (problems.length > 0) return problems;
    const parsed = FinancialModelSchema.safeParse(model);
    return parsed.success ? [] : parsed.error.issues.map((i) => `${i.path.join('.') || '(root)'}: ${i.message}`);
  };

  let problems: string[] = [];
  let model: FinancialModel | undefined;
  for (let attempt = 0; attempt < 2; attempt++) {
    const patchMode = attempt === 1 && model !== undefined;
    const reply = patchMode
      ? await deps.llm.invoke('lifeModel', ModelPatchSchema, messages, { maxTokens: PATCH_MAX_TOKENS })
      : await deps.llm.invoke('lifeModel', ModelReplySchema, messages, { maxTokens: LIFE_MODEL_MAX_TOKENS });
    const patch = patchMode ? (reply as ModelPatch).patch : undefined;
    model =
      patchMode && model && patch
        ? normalizeModel(applyPatch(model, patch), norm, facts)
        : normalizeModel(reply as FinancialModel, norm, facts);
    problems = problemsOf(model);
    if (problems.length === 0) return model;
    // The retry asks only for the pieces that failed: the rejected model is resent as context, but the
    // reply carries just the arrays that need fixing, so it decodes a fraction of a full life and
    // leaves the already-correct parts untouched (a full re-emit tends to invent new mismatches).
    if (attempt === 0) messages = [...messages, buildLifeModelPatchMessage(problems, model)];
  }
  throw new LifeModelError(problems);
}
