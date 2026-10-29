import { fileURLToPath } from 'node:url';
import { Budget, createOpenAiStructuredLlm, createTavilyClient, loadSkills, type BudgetLimits, type GraphDeps } from '@bh/core';
import { createFixtureDeps } from '@bh/core/testing';
import { createSqliteCache, type Db } from './db.js';
import type { Env } from './env.js';
import { resolveLlm } from './settings.js';

/** Repo-root `skills/` (this file lives in `apps/server/src`). */
const SKILLS_DIR = fileURLToPath(new URL('../../../skills/', import.meta.url));

const todayUtc = (): string => new Date().toISOString().slice(0, 10);

/** Builds the dependencies of one unit of work; `limits` replaces the default per-task budget caps. */
export type MakeDeps = (limits?: Partial<BudgetLimits>) => GraphDeps;

/**
 * Returns the factory that builds the dependencies of ONE job. Call it once per job:
 * every job gets its own `Budget` and a model client charged to it.
 * `LLM_FAKE=1` gives the offline fixture dependencies (canned Austin freelancer answers).
 */
export function makeDeps(env: Env, db: Db): MakeDeps {
  // 这两样跟配置无关、构造也贵，建一次复用；模型客户端则每次现建。
  const skills = loadSkills(SKILLS_DIR);
  const cache = createSqliteCache(db);

  return (limits) => {
    // 每开一个 job 都重新读一次：页面上刚改完的模型配置立刻生效。
    const eff = resolveLlm(db, env);
    if (!eff.ready || !eff.llm || !eff.tavilyApiKey) {
      // Fixtures have no `render` answers: render the deterministic ledger draft through MCP instead.
      return {
        ...createFixtureDeps(limits ? { budget: limits } : {}),
        ...(env.billgen && { billgen: { ...env.billgen, modelFill: false } }),
      };
    }
    const budget = new Budget(limits);
    return {
      llm: createOpenAiStructuredLlm(eff.llm, budget),
      search: createTavilyClient(eff.tavilyApiKey),
      cache,
      skills,
      budget,
      today: todayUtc(),
      now: Date.now,
      ...(env.billgen && { billgen: env.billgen }),
    };
  };
}
