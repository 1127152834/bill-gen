import type { Budget } from '../budget.js';
import type { StructuredLlm } from '../llm.js';
import type { ResearchCache, SearchClient } from '../research.js';
import type { Profile, RuleResult } from '../schemas/index.js';
import type { SkillStore } from '../skills.js';

/** How to reach billgen's MCP server (the Python half of the pipeline). */
export type BillgenConfig = {
  /** Executable that speaks MCP on stdio, e.g. `.venv/bin/billgen`. */
  command: string;
  /** Its arguments, e.g. `['mcp']`. */
  args: string[];
  /** Template ids to render, e.g. `['boa-personal', 'chase-personal']`. Empty = render nothing. */
  templates: string[];
  /** Where the server writes PDFs and PNGs. */
  outDir: string;
  /** Let the model edit the ledger draft before validating (false = render the deterministic draft). */
  modelFill: boolean;
  /** How many templates `auto` keeps per statement (default 2). */
  templateLimit?: number;
};

/** Everything the generation graph needs from the outside world. One `Budget` per job. */
export type GraphDeps = {
  llm: StructuredLlm;
  search: SearchClient;
  cache: ResearchCache;
  skills: SkillStore;
  budget: Budget;
  /** Fictional bank shown on every statement. */
  bankName: string;
  /** `YYYY-MM-DD`; the default end month is the month before it. */
  today: string;
  /** Epoch milliseconds (research cache ages, `createdAt`). */
  now: () => number;
  /**
   * Fields every random persona keeps verbatim (merged under what the caller gives). Real deps leave it out;
   * the offline fixture deps use it to pin the persona to the one place their canned answers cover.
   */
  personaDefaults?: Partial<Profile>;
  /** Omitted in tests and in fixtures that do not ship the Python side. */
  billgen?: BillgenConfig;
};

/** Graph nodes, in pipeline order. */
export const GRAPH_NODES = [
  'normalize',
  'planResearch',
  'research',
  'lifeModel',
  'schedule',
  'descriptors',
  'validate',
  'audit',
  'finalize',
] as const;
/** `billgen` renders the finished statements afterwards, and only when the MCP server is configured. */
export type GraphNodeName = (typeof GRAPH_NODES)[number] | 'billgen';

/** Where a failed check sends the work back to. */
export type RetryTarget = 'lifeModel' | 'schedule' | 'descriptors';

export type ProgressEvent =
  | { type: 'node_start' | 'node_end'; node: string }
  | { type: 'research'; query: string; sources: string[] }
  | { type: 'rules'; results: RuleResult[] }
  | { type: 'retry'; target: string; reason: string }
  | { type: 'warning'; message: string }
  | { type: 'progress'; message: string };

export type Emit = (e: ProgressEvent) => void;

/** At most `limit` callers run `fn` at the same time; the rest wait in FIFO order. */
export function createSemaphore(limit: number): <T>(fn: () => Promise<T>) => Promise<T> {
  let active = 0;
  const waiting: (() => void)[] = [];
  return async (fn) => {
    if (active >= limit) await new Promise<void>((resolve) => waiting.push(resolve));
    else active++;
    try {
      return await fn();
    } finally {
      const next = waiting.shift();
      if (next) next();
      else active--;
    }
  };
}
