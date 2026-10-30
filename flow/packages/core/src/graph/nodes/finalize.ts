import { randomUUID } from 'node:crypto';
import type { GenerationResult } from '../../schemas/index.js';
import type { Emit, GraphDeps } from '../deps.js';
import { need, type GraphStateUpdate, type GraphStateValue } from '../state.js';

/** Messages of a failed rule copied into the result's warnings. */
const MESSAGES_PER_RULE = 3;

/**
 * Builds the `GenerationResult`. When the last round still breaks rules and an earlier round was
 * rule-clean, that earlier round (`best`) is the result. Rules still failing, high audit findings
 * left after the last round and `degraded` notes (a node fallback, a failed audit) become warnings
 * and make the status `completed_with_warnings`; research warnings alone (a dropped category) are
 * listed but keep the status `completed`.
 */
export const finalizeStep =
  (deps: GraphDeps, emit: Emit) =>
  async (s: GraphStateValue, config?: { configurable?: Record<string, unknown> }): Promise<GraphStateUpdate> => {
    const norm = need(s.norm, 'norm');
    // A last round that breaks rules never replaces a rule-clean earlier one.
    const failing = s.ruleResults.filter((r) => !r.passed);
    const kept = failing.length > 0 && s.best ? s.best : null;
    const view = kept ?? { statements: s.statements, ruleResults: s.ruleResults, findings: s.findings };
    const keptNote = kept
      ? [`rules failed in round ${s.rounds} (${failing.map((r) => r.ruleId).join(', ')}); kept round ${kept.round} result`]
      : [];
    const leftovers = [
      ...keptNote,
      ...view.ruleResults
        .filter((r) => !r.passed)
        .map((r) => `rule ${r.ruleId} failed: ${r.violations.slice(0, MESSAGES_PER_RULE).map((v) => v.message).join('; ')}`),
      ...view.findings.filter((f) => f.severity === 'high').map((f) => `audit (high, ${f.suggestedTarget}): ${f.problem}`),
    ];
    for (const message of leftovers) emit({ type: 'warning', message });

    const threadId = config?.configurable?.thread_id;
    const result: GenerationResult = {
      id: typeof threadId === 'string' && threadId ? threadId : randomUUID(),
      seed: norm.request.seed,
      createdAt: new Date(deps.now()).toISOString(),
      subject: need(norm.request.profile, 'request.profile'),
      statements: view.statements,
      audit: { rules: view.ruleResults, findings: view.findings },
      usage: deps.budget.usage(),
      monthFiles: [],
      warnings: [...s.warnings, ...s.degraded, ...leftovers],
      renders: [],
    status: leftovers.length > 0 || s.degraded.length > 0 ? 'completed_with_warnings' : 'completed',
    };
    return { result, warnings: leftovers };
  };
