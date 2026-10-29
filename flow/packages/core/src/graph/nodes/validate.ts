import { runRules } from '../../validate.js';
import type { Emit } from '../deps.js';
import { canRetry, sendBack } from '../retry.js';
import { need, uniqueFacts, type GraphStateUpdate, type GraphStateValue } from '../state.js';

/** Violation messages per failed rule passed on as feedback. */
const MESSAGES_PER_RULE = 3;

/**
 * Hard rules R1-R11. On failures, while rounds remain: back to lifeModel when any failed rule
 * targets it, otherwise back to schedule. With no rounds left the run goes on to audit.
 * A round that passes every rule becomes the `best` snapshot.
 */
export const validateStep =
  (emit: Emit) =>
  async (s: GraphStateValue): Promise<GraphStateUpdate> => {
    const ctx = { norm: need(s.norm, 'norm'), model: need(s.model, 'model'), facts: uniqueFacts(s.facts), meta: s.meta };
    const ruleResults = runRules(s.statements, ctx);
    emit({ type: 'rules', results: ruleResults });

    const failed = ruleResults.filter((r) => !r.passed);
    if (failed.length === 0) {
      const best = {
        round: s.rounds,
        model: ctx.model,
        scheduleOut: need(s.scheduleOut, 'scheduleOut'),
        templates: s.templates,
        statements: s.statements,
        meta: s.meta,
        ruleResults,
        findings: [],
      };
      return { ruleResults, best, retryTarget: null, feedback: [] };
    }
    if (!canRetry(s)) return { ruleResults, retryTarget: null, feedback: [] };
    const target = failed.some((r) => r.target === 'lifeModel') ? 'lifeModel' : 'schedule';
    const feedback = failed.flatMap((r) => r.violations.slice(0, MESSAGES_PER_RULE).map((v) => `${r.ruleId}: ${v.message}`));
    return { ruleResults, ...sendBack(s, target, `rules failed: ${failed.map((r) => r.ruleId).join(', ')}`, feedback, emit) };
  };
