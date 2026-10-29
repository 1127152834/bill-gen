import { z } from 'zod';
import type { LlmMessage } from '../../llm.js';
import type { NormalizedRequest } from '../../normalize.js';
import { buildLifeModelMessages, buildLifeModelRetryMessage } from '../../prompts/lifeModel.js';
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
};

/**
 * Asks the model for a FinancialModel, applies deterministic fixes and checks it. On problems
 * the problems (and the rejected model) are appended and the model is asked once more; that retry
 * costs one more model call but is not a send-back round.
 * @throws LifeModelError when the second answer still has problems.
 */
export async function lifeModelNode(input: LifeModelInput, deps: NodeDeps): Promise<FinancialModel> {
  const { norm, profile, facts, feedback } = input;
  const skillsText = deps.skills.get(SKILLS_BY_NODE.lifeModel);
  let messages: LlmMessage[] = buildLifeModelMessages({ norm, profile, facts, skillsText, ...(feedback && { feedback }) });

  let problems: string[] = [];
  for (let attempt = 0; attempt < 2; attempt++) {
    const reply = await deps.llm.invoke('lifeModel', ModelReplySchema, messages);
    const model = normalizeModel(reply as FinancialModel, norm, facts);
    problems = checkModel(model, norm, facts);
    if (problems.length === 0) {
      const parsed = FinancialModelSchema.safeParse(model);
      if (parsed.success) return parsed.data;
      problems = parsed.error.issues.map((i) => `${i.path.join('.') || '(root)'}: ${i.message}`);
    }
    if (attempt === 0) messages = [...messages, buildLifeModelRetryMessage(problems, model)];
  }
  throw new LifeModelError(problems);
}
