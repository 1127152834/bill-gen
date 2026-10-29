import type { BaseCheckpointSaver } from '@langchain/langgraph';
import { BudgetExceededError } from '../budget.js';
import type { GenerationRequest, GenerationResult } from '../schemas/index.js';
import { buildGraph } from './build.js';
import type { GraphDeps, ProgressEvent } from './deps.js';
import { need, type GraphStateUpdate } from './state.js';

export type RunOptions = {
  /** Checkpoint thread; also the result's `id`. */
  threadId: string;
  checkpointer?: BaseCheckpointSaver;
  onEvent?: (e: ProgressEvent) => void;
  /** Continue the thread from its last checkpoint (the stored request is used, not `request`). */
  resume?: boolean;
};

/** Supersteps allowed per run: 9 nodes plus 2 send-back rounds stay far below this. */
const RECURSION_LIMIT = 60;

/**
 * Runs one generation job through the graph.
 * @throws Error when `request.profile` is missing (randomize a persona first), or when the thread
 *   already has checkpoints and `resume` is not set.
 * @throws BudgetExceededError, LifeModelError, ScheduleInfeasibleError, InfeasibleRequestError and
 *   model/search errors that end the job, unchanged.
 */
export async function runGeneration(request: GenerationRequest, deps: GraphDeps, opts: RunOptions): Promise<GenerationResult> {
  if (!request.profile) {
    throw new Error('runGeneration: request.profile is required; randomize a persona before starting the job');
  }
  const graph = buildGraph(deps, opts.checkpointer, opts.onEvent);
  const config = { configurable: { thread_id: opts.threadId }, recursionLimit: RECURSION_LIMIT };

  let input: GraphStateUpdate | null = { request };
  if (opts.checkpointer) {
    const saved = await graph.getState(config);
    if (Object.keys(saved.values ?? {}).length > 0) {
      if (!opts.resume) {
        throw new Error(`runGeneration: thread "${opts.threadId}" already has checkpoints; pass resume: true or use a new threadId`);
      }
      const done = (saved.values as { result?: GenerationResult | null }).result;
      if (done && saved.next.length === 0) return done;
      input = null;
    }
  }
  try {
    const final = await graph.invoke(input, config);
    return need(final.result, 'result');
  } catch (err) {
    throw primaryError(err);
  }
}

/** Parallel research tasks failing together reach us as one AggregateError; surface the budget error, else the first. */
function primaryError(err: unknown): unknown {
  if (!(err instanceof AggregateError)) return err;
  const errors: unknown[] = err.errors;
  return errors.find((e) => e instanceof BudgetExceededError) ?? errors[0] ?? err;
}
