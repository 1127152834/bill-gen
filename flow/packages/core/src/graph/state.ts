import { Annotation } from '@langchain/langgraph';
import type { TxnMeta } from '../assemble.js';
import type { DescriptorTemplate } from '../descriptors.js';
import type { NormalizedRequest } from '../normalize.js';
import type { ScheduleOutput } from '../schedule/index.js';
import type {
  AuditFinding,
  FinancialModel,
  GenerationRequest,
  GenerationResult,
  MerchantFact,
  ResearchQuery,
  RuleResult,
  Statement,
} from '../schemas/index.js';
import type { RetryTarget } from './deps.js';

/** Everything one rule-clean round produced (validate takes it, audit adds its findings). */
export type RoundSnapshot = {
  /** `rounds` when the snapshot was taken (0 = the first pass). */
  round: number;
  model: FinancialModel;
  scheduleOut: ScheduleOutput;
  templates: DescriptorTemplate[];
  statements: Statement[];
  meta: Record<string, TxnMeta>;
  ruleResults: RuleResult[];
  findings: AuditFinding[];
};

/** A field the latest write replaces. */
const latest = <T>(initial: () => T) => Annotation<T>({ reducer: (_old, next) => next, default: initial });
/** A field every write appends to (parallel research tasks write facts / warnings side by side). */
const appended = <T>() => Annotation<T[]>({ reducer: (old, next) => old.concat(next), default: () => [] });

export const GraphState = Annotation.Root({
  request: latest<GenerationRequest | null>(() => null),
  norm: latest<NormalizedRequest | null>(() => null),
  queries: latest<ResearchQuery[]>(() => []),
  facts: appended<MerchantFact>(),
  model: latest<FinancialModel | null>(() => null),
  scheduleOut: latest<ScheduleOutput | null>(() => null),
  templates: latest<DescriptorTemplate[]>(() => []),
  statements: latest<Statement[]>(() => []),
  /** From `assembleStatements`; links each transaction to its model item for the rules. */
  meta: latest<Record<string, TxnMeta>>(() => ({})),
  ruleResults: latest<RuleResult[]>(() => []),
  findings: latest<AuditFinding[]>(() => []),
  /** Send-back rounds used so far (at most 2). */
  rounds: latest<number>(() => 0),
  /** Set when a check sends work back; the target node reads `feedback` and clears both. */
  retryTarget: latest<RetryTarget | null>(() => null),
  feedback: latest<string[]>(() => []),
  warnings: appended<string>(),
  /** The last round whose statements passed every rule; finalize falls back to it when a later round is worse. */
  best: latest<RoundSnapshot | null>(() => null),
  /** Set when a node failed after a result existed: the graph goes straight to finalize with that result. */
  fallback: latest<boolean>(() => false),
  /** Warnings that make the result `completed_with_warnings` (a fallback, an unreviewed result). */
  degraded: appended<string>(),
  result: latest<GenerationResult | null>(() => null),
});

export type GraphStateValue = typeof GraphState.State;
export type GraphStateUpdate = typeof GraphState.Update;

const compare = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

/** Facts in a stable order (category, then id) without repeated ids: research tasks finish in any order. */
export function uniqueFacts(facts: readonly MerchantFact[]): MerchantFact[] {
  const seen = new Set<string>();
  return [...facts]
    .sort((a, b) => compare(a.category, b.category) || compare(a.id, b.id))
    .filter((f) => {
      if (seen.has(f.id)) return false;
      seen.add(f.id);
      return true;
    });
}

/** Reads a field a previous node must have set. */
export function need<T>(value: T | null | undefined, what: string): T {
  if (value === null || value === undefined) throw new Error(`graph state: ${what} is missing`);
  return value;
}
