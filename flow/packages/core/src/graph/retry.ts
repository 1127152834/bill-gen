import { BudgetExceededError } from '../budget.js';
import type { Emit, RetryTarget } from './deps.js';
import type { GraphStateUpdate, GraphStateValue } from './state.js';

/** Send-back rounds allowed per job (validate, audit, schedule and lifeModel failures share them). */
export const MAX_ROUNDS = 2;
/** Feedback lines handed to the node a round goes back to. */
const MAX_FEEDBACK_LINES = 12;
const MAX_LINE_CHARS = 400;

export const canRetry = (s: Pick<GraphStateValue, 'rounds'>): boolean => s.rounds < MAX_ROUNDS;

const clip = (line: string): string => (line.length > MAX_LINE_CHARS ? `${line.slice(0, MAX_LINE_CHARS)}…` : line);

/** Uses one round: emits `retry` and routes to `target` with `feedback` for it. */
export function sendBack(
  s: Pick<GraphStateValue, 'rounds'>,
  target: RetryTarget,
  reason: string,
  feedback: string[],
  emit: Emit,
): GraphStateUpdate {
  emit({ type: 'retry', target, reason });
  return { rounds: s.rounds + 1, retryTarget: target, feedback: feedback.slice(0, MAX_FEEDBACK_LINES).map(clip) };
}

/** A short code for a failure: its `code` (LLM_OUTPUT_INVALID, ...) or its class name. Never its message. */
export function errorCode(err: unknown): string {
  const code = (err as { code?: unknown } | null)?.code;
  if (typeof code === 'string' && code) return code;
  return err instanceof Error ? err.name : 'UNKNOWN';
}

/**
 * A node failed. Budget errors always end the job. Otherwise, when an earlier round produced
 * statements, the job goes straight to finalize with the best of them (`best`, else the current
 * ones) and a warning; with nothing to keep the error ends the job.
 */
export function fallBack(
  s: Pick<GraphStateValue, 'best' | 'statements'>,
  node: string,
  err: unknown,
  emit: Emit,
): GraphStateUpdate {
  if (err instanceof BudgetExceededError) throw err;
  if (!s.best && s.statements.length === 0) throw err;
  const kept = s.best ? `kept round ${s.best.round} result` : "kept the previous round's result";
  const message = `${node} retry failed (${errorCode(err)}); ${kept}`;
  emit({ type: 'warning', message });
  const restore: GraphStateUpdate = s.best
    ? {
        model: s.best.model,
        scheduleOut: s.best.scheduleOut,
        templates: s.best.templates,
        statements: s.best.statements,
        meta: s.best.meta,
        ruleResults: s.best.ruleResults,
        findings: s.best.findings,
      }
    : {};
  return { ...restore, fallback: true, retryTarget: null, feedback: [], degraded: [message] };
}
