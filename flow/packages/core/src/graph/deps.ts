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
  /**
   * 让模型改草稿的力度：
   * - `true`（默认）草稿先过校验，**通过就直接出片**，只在草稿不合格时才让模型补——省一次整对象输出；
   * - `'always'` 每次都让模型改（最像人写，也最慢）；
   * - `false` 从不让模型碰，纯确定性草稿。
   */
  modelFill: boolean | 'always';
  /** How many templates `auto` keeps per statement (default 2). */
  templateLimit?: number;
  /**
   * 交付物形态：
   * - `'json'`（默认）每月一份模板 JSON——用户自己导进模板出账单；
   * - `'pdf'` 直接出片（老路径，保留给需要现成 PDF 的场合）。
   */
  deliver?: 'json' | 'pdf';
};

/** Everything the generation graph needs from the outside world. One `Budget` per job. */
export type GraphDeps = {
  llm: StructuredLlm;
  search: SearchClient;
  cache: ResearchCache;
  skills: SkillStore;
  budget: Budget;
  /** Fictional bank shown on every statement. */
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
  /** `detail` 是给日志看的补充（例如 research 的「8 查询」）。 */
  | { type: 'node_start'; node: string; detail?: string }
  /** `ms` 是这个节点真正花掉的时间；前端只用 node 字段也照旧能跑。 */
  | { type: 'node_end'; node: string; ms?: number; detail?: string }
  /** 节点内部的一步（第几次尝试、第几条检索……），控制台日志靠它把黑盒拆开。 */
  | { type: 'step'; node: string; message: string }
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
