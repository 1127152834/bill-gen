/**
 * The generation graph:
 *
 *   normalize → planResearch → research ×N (Send) → lifeModel → schedule → descriptors → validate → audit → finalize
 *
 * Send-backs (at most 2 rounds per job, shared): lifeModel → lifeModel (LifeModelError),
 * schedule → lifeModel (infeasible), validate → lifeModel | schedule, audit → lifeModel | schedule | descriptors.
 *
 * Failures: a budget error always ends the job. Any other failure of lifeModel, schedule or
 * descriptors after a result exists goes straight to finalize with the best earlier round
 * (`fallBack`); with no result yet it ends the job. An audit failure leaves no findings and a warning.
 */
import { END, START, Send, StateGraph, type BaseCheckpointSaver } from '@langchain/langgraph';
import { assembleStatements } from '../assemble.js';
import { BudgetExceededError } from '../budget.js';
import { createRng } from '../rng.js';
import type { AuditFinding, Statement } from '../schemas/index.js';
import type { Emit, GraphDeps, GraphNodeName, ProgressEvent, RetryTarget } from './deps.js';
import { auditNode } from './nodes/audit.js';
import { descriptorsNode } from './nodes/descriptors.js';
import { LifeModelError, lifeModelNode } from './nodes/lifeModel.js';
import { finalizeStep } from './nodes/finalize.js';
import { renderStep } from './nodes/render.js';
import { normalizeStep } from './nodes/normalize.js';
import { planResearchNode } from './nodes/planResearch.js';
import { researchStep, type ResearchTask } from './nodes/research.js';
import { scheduleStep } from './nodes/schedule.js';
import { validateStep } from './nodes/validate.js';
import { canRetry, errorCode, fallBack, sendBack } from './retry.js';
import { GraphState, need, uniqueFacts, type GraphStateUpdate, type GraphStateValue } from './state.js';

/** Audit send-back priority when high findings suggest different targets. */
const AUDIT_TARGET_ORDER: readonly RetryTarget[] = ['lifeModel', 'schedule', 'descriptors'];
/** Rows quoted per audit finding in the feedback. */
const ROWS_PER_FINDING = 4;
/** A research warning for a category that ended with no facts. */
const DROPPED_CATEGORY = /^category (.+) dropped: (.+)$/;

/** Spec 7: every dropped research category is also a low finding in the audit report. */
function droppedCategoryFindings(warnings: readonly string[]): AuditFinding[] {
  return [...new Set(warnings)].flatMap((w) => {
    const m = DROPPED_CATEGORY.exec(w);
    if (!m) return [];
    return [
      {
        severity: 'low' as const,
        transactionIds: [],
        problem: `Research found no usable sources for "${m[1]}" (${m[2]}), so the statements have no ${m[1]} merchant.`,
        suggestedTarget: 'lifeModel' as const,
      },
    ];
  });
}

type Step = (s: GraphStateValue, config?: { configurable?: Record<string, unknown> }) => Promise<GraphStateUpdate>;

const profileOf = (s: GraphStateValue) => need(need(s.norm, 'norm').request.profile, 'request.profile');

/** A finding plus the rows it cites, so the node it goes back to sees what was wrong. */
function describeFinding(f: AuditFinding, statements: Statement[]): string {
  const cited = new Set(f.transactionIds);
  const rows = statements
    .flatMap((st) => st.transactions)
    .filter((t) => cited.has(t.id))
    .slice(0, ROWS_PER_FINDING)
    .map((t) => `${t.postedDate} "${t.description}" ${t.direction} ${t.amountCents}c`);
  return rows.length > 0 ? `${f.problem} (rows: ${rows.join('; ')})` : f.problem;
}

function llmSteps(deps: GraphDeps, emit: Emit) {
  const planResearch: Step = async (s) => ({
    queries: await planResearchNode({ norm: need(s.norm, 'norm'), profile: profileOf(s) }, deps),
  });

  /**
   * A LifeModelError (two failed answers) uses a round and asks again with its problems, if rounds remain.
   * Any other failure, or a LifeModelError with no rounds left, falls back to an earlier result if there is one.
   */
  const lifeModel: Step = async (s) => {
    const feedback = s.retryTarget === 'lifeModel' && s.feedback.length > 0 ? s.feedback : undefined;
    try {
      const model = await lifeModelNode(
        { norm: need(s.norm, 'norm'), profile: profileOf(s), facts: uniqueFacts(s.facts), ...(feedback && { feedback }) },
        deps,
      );
      return { model, templates: [], retryTarget: null, feedback: [] };
    } catch (err) {
      if (err instanceof LifeModelError && canRetry(s)) {
        return { model: null, ...sendBack(s, 'lifeModel', `life model invalid: ${err.problems.length} problem(s)`, err.problems, emit) };
      }
      return fallBack(s, 'lifeModel', err, emit);
    }
  };

  /**
   * Templates are kept across rounds: only keys without one are asked for, except on a send-back
   * to descriptors, which asks for every key again with the audit findings. Rendering uses an rng
   * forked per round (`assemble-r<rounds>`).
   */
  const descriptors: Step = async (s) => {
    const norm = need(s.norm, 'norm');
    const model = need(s.model, 'model');
    const out = need(s.scheduleOut, 'scheduleOut');
    const facts = uniqueFacts(s.facts);
    const redo = s.retryTarget === 'descriptors';
    const byKey = new Map(s.templates.map((t) => [t.merchantKey, t]));
    const keys = out.txns.map((t) => t.merchantKey);
    let fresh: Awaited<ReturnType<typeof descriptorsNode>>;
    try {
      fresh = await descriptorsNode(
        {
          norm,
          profile: profileOf(s),
          facts,
          model,
          merchantKeys: redo ? keys : keys.filter((k) => !byKey.has(k)),
          ...(redo && s.feedback.length > 0 && { feedback: s.feedback }),
        },
        deps,
      );
    } catch (err) {
      return fallBack(s, 'descriptors', err, emit);
    }
    for (const t of fresh) byKey.set(t.merchantKey, t);
    const templates = [...byKey.values()];
    const rng = createRng(norm.request.seed).fork(`assemble-r${s.rounds}`);
    const { statements, meta } = assembleStatements(out, templates, norm, model, facts, deps.bankName, rng);
    return { templates, statements, meta, retryTarget: null, feedback: [] };
  };

  /**
   * High findings use a round, routed to the highest-priority target any of them suggests. The
   * findings are also stored on this round's `best` snapshot. A failed audit (other than the
   * budget) leaves no findings and a warning, and the job goes on to finalize.
   */
  const audit: Step = async (s) => {
    let findings: AuditFinding[];
    const degraded: string[] = [];
    try {
      findings = await auditNode(
        { norm: need(s.norm, 'norm'), profile: profileOf(s), statements: s.statements, facts: uniqueFacts(s.facts) },
        deps,
      );
    } catch (err) {
      if (err instanceof BudgetExceededError) throw err;
      const message = `audit failed (${errorCode(err)}); statements were not reviewed`;
      emit({ type: 'warning', message });
      findings = [];
      degraded.push(message);
    }
    findings = [...findings, ...droppedCategoryFindings(s.warnings)];
    const best = s.best && s.best.round === s.rounds ? { best: { ...s.best, findings } } : {};
    const high = findings.filter((f) => f.severity === 'high');
    if (high.length === 0 || !canRetry(s)) return { findings, ...best, degraded, retryTarget: null, feedback: [] };
    const target = AUDIT_TARGET_ORDER.find((t) => high.some((f) => f.suggestedTarget === t))!;
    const feedback = high.map((f) => describeFinding(f, s.statements));
    return { findings, ...best, ...sendBack(s, target, `audit: ${high.length} high finding(s)`, feedback, emit) };
  };

  return { planResearch, lifeModel, descriptors, audit };
}

/**
 * Compiles the graph. `onEvent` receives progress events (node_start / node_end per node,
 * research, rules, retry, warning); research reports one node_start / node_end around all its tasks.
 */
export function buildGraph(deps: GraphDeps, checkpointer?: BaseCheckpointSaver, onEvent?: (e: ProgressEvent) => void) {
  const emit: Emit = (e) => onEvent?.(e);
  const tracked =
    (node: GraphNodeName, step: Step): Step =>
    async (s, config) => {
      emit({ type: 'node_start', node });
      const update = await step(s, config);
      emit({ type: 'node_end', node });
      return update;
    };

  const research = researchStep(deps, emit);
  let researchStarted = 0;
  let researchDone = 0;
  const trackedResearch = async (task: ResearchTask & { total: number }): Promise<GraphStateUpdate> => {
    if (researchStarted++ === 0) emit({ type: 'node_start', node: 'research' });
    const update = await research(task);
    if (++researchDone === task.total) {
      emit({ type: 'node_end', node: 'research' });
      researchStarted = researchDone = 0;
    }
    return update;
  };

  const fanOut = (s: GraphStateValue): Send[] | 'lifeModel' =>
    s.queries.length === 0
      ? 'lifeModel'
      : s.queries.map((query) => new Send('research', { norm: need(s.norm, 'norm'), query, total: s.queries.length }));

  const llm = llmSteps(deps, emit);
  const graph = new StateGraph(GraphState)
    .addNode('normalize', tracked('normalize', normalizeStep(deps)))
    .addNode('planResearch', tracked('planResearch', llm.planResearch))
    .addNode('research', trackedResearch as unknown as Step)
    .addNode('lifeModel', tracked('lifeModel', llm.lifeModel))
    .addNode('schedule', tracked('schedule', scheduleStep(emit)))
    .addNode('descriptors', tracked('descriptors', llm.descriptors))
    .addNode('validate', tracked('validate', validateStep(emit)))
    .addNode('audit', tracked('audit', llm.audit))
    .addNode('finalize', tracked('finalize', finalizeStep(deps, emit)))
    .addEdge(START, 'normalize')
    .addEdge('normalize', 'planResearch')
    .addConditionalEdges('planResearch', fanOut, ['research', 'lifeModel'])
    .addEdge('research', 'lifeModel')
    .addConditionalEdges('lifeModel', (s) => (s.retryTarget === 'lifeModel' ? 'lifeModel' : s.fallback ? 'finalize' : 'schedule'), [
      'lifeModel',
      'schedule',
      'finalize',
    ])
    .addConditionalEdges('schedule', (s) => (s.retryTarget === 'lifeModel' ? 'lifeModel' : s.fallback ? 'finalize' : 'descriptors'), [
      'lifeModel',
      'descriptors',
      'finalize',
    ])
    .addConditionalEdges('descriptors', (s) => (s.fallback ? 'finalize' : 'validate'), ['validate', 'finalize'])
    .addConditionalEdges('validate', (s) => s.retryTarget ?? 'audit', ['lifeModel', 'schedule', 'audit'])
    .addConditionalEdges('audit', (s) => s.retryTarget ?? 'finalize', ['lifeModel', 'schedule', 'descriptors', 'finalize']);

  // Rendering is a pure add-on: without a billgen config the graph is exactly the old one, so the
  // offline fixture path and its event stream stay byte-identical.
  const wired = deps.billgen
    ? graph
        .addNode('billgen', tracked('billgen', renderStep(deps, emit)))
        .addEdge('finalize', 'billgen')
        .addEdge('billgen', END)
    : graph.addEdge('finalize', END);
  return wired.compile(checkpointer ? { checkpointer } : {});
}

export type GenerationGraph = ReturnType<typeof buildGraph>;
