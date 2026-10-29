import type { LlmEnv } from '@bh/core';
import { z } from 'zod';

export type Env = {
  /** `LLM_FAKE=1`: offline fixture dependencies; no LLM / Tavily settings needed. */
  llmFake: boolean;
  llm?: LlmEnv;
  tavilyApiKey?: string;
  /** Key remote clients send as `X-API-Key`. */
  apiKey: string;
  /** How to reach billgen's MCP server; omitted = statements are generated but not rendered. */
  billgen?: {
    command: string;
    args: string[];
    templates: string[];
    outDir: string;
    modelFill: boolean | 'always';
    /** Templates kept per statement when `templates` contains `auto`. */
    templateLimit: number;
  };
  databasePath: string;
  host: string;
  port: number;
  /** 把生成进度按行打到 stdout（默认开）；`LOG_PROGRESS=0` 关掉。 */
  logProgress: boolean;
  /** 连每次检索的明细一起打（默认关，`LOG_RESEARCH=1` 打开）。 */
  logResearch: boolean;
};

/** Startup configuration is missing or invalid. Names variables only, never their values. */
export class EnvError extends Error {
  readonly variables: string[];
  constructor(variables: string[]) {
    super(`Invalid environment: set or fix ${variables.join(', ')}`);
    this.name = 'EnvError';
    this.variables = variables;
  }
}

const text = z.string().min(1);

const Raw = z.object({
  LLM_BASE_URL: z.url().optional(),
  LLM_API_KEY: text.optional(),
  LLM_MODEL: text.default('gpt-6.1-sol'),
  LLM_STRUCTURED_MODE: z.enum(['json_schema', 'function_calling', 'json_mode']).default('json_schema'),
  /** 分节点模型覆盖：LLM_MODEL_LIFEMODEL=更快的模型，`LLM_MODEL_AUDIT=` 留空即沿用默认。 */
  LLM_MODEL_LIFEMODEL: text.optional(),
  LLM_MODEL_DESCRIPTORS: text.optional(),
  LLM_MODEL_AUDIT: text.optional(),
  LLM_MODEL_PLANRESEARCH: text.optional(),
  TAVILY_API_KEY: text.optional(),
  API_KEY: text,
  DATABASE_PATH: text.default('./data/app.db'),
  HOST: text.default('127.0.0.1'),
  PORT: z.coerce.number().int().min(1).max(65535).default(8797),
  LLM_FAKE: z.enum(['0', '1']).default('0'),
  BILLGEN_MCP_CMD: text.optional(),
  BILLGEN_TEMPLATES: text.optional(),
  BILLGEN_OUT: text.default('./data/renders'),
  /** `1` = 草稿不合格才让模型补（默认，快）；`always` = 每次都让模型改；`0` = 不让模型碰。 */
  BILLGEN_AI_FILL: z.enum(['0', '1', 'always']).default('1'),
  /** Templates kept per statement when `BILLGEN_TEMPLATES=auto`. */
  BILLGEN_AUTO_LIMIT: z.coerce.number().int().min(1).max(11).default(2),
  LOG_PROGRESS: z.enum(['0', '1']).default('1'),
  LOG_RESEARCH: z.enum(['0', '1']).default('0'),
});

/**
 * Reads the server's bootstrap settings (`process.env` by default; empty strings count as unset).
 *
 * LLM / Tavily credentials are optional here: they can be filled in from the web UI at run time
 * (see `settings.ts`, which layers the stored settings over these defaults). Only malformed
 * values are rejected.
 * @throws EnvError listing the variable names whose values are invalid.
 */
export function loadEnv(source: Record<string, string | undefined> = process.env): Env {
  const present = Object.fromEntries(Object.entries(source).filter(([, v]) => v !== undefined && v !== ''));
  const parsed = Raw.safeParse(present);
  const problems = new Set(parsed.success ? [] : parsed.error.issues.map((i) => String(i.path[0])));
  const llmFake = present.LLM_FAKE === '1';
  // 模型/搜索的凭据不再是启动前提：浏览器里保存的设置会覆盖它们（见 settings.ts）。
  // 这里只拦语法错误（坏端口、坏 URL、未知 structured mode）。
  if (!parsed.success || problems.size > 0) throw new EnvError([...problems]);
  const v = parsed.data;

  return {
    llmFake,
    ...(v.LLM_BASE_URL && v.LLM_API_KEY && {
      llm: {
        baseUrl: v.LLM_BASE_URL,
        apiKey: v.LLM_API_KEY,
        model: v.LLM_MODEL,
        structuredMode: v.LLM_STRUCTURED_MODE,
        ...(modelByNode(v) && { modelByNode: modelByNode(v) }),
      },
    }),
    ...(v.TAVILY_API_KEY && { tavilyApiKey: v.TAVILY_API_KEY }),
    apiKey: v.API_KEY,
    ...(v.BILLGEN_MCP_CMD && {
      billgen: {
        command: v.BILLGEN_MCP_CMD,
        args: ['mcp'],
        templates: (v.BILLGEN_TEMPLATES ?? '')
          .split(',')
          .map((t) => t.trim())
          .filter(Boolean),
        outDir: v.BILLGEN_OUT,
        modelFill: v.BILLGEN_AI_FILL === 'always' ? 'always' : v.BILLGEN_AI_FILL === '1',
        templateLimit: v.BILLGEN_AUTO_LIMIT,
      },
    }),
    databasePath: v.DATABASE_PATH,
    host: v.HOST,
    port: v.PORT,
    logProgress: v.LOG_PROGRESS === '1',
    logResearch: v.LOG_RESEARCH === '1',
  };
}

/** `LLM_MODEL_<NODE>` → per-node overrides; unset nodes fall back to `LLM_MODEL`. */
function modelByNode(v: { LLM_MODEL_LIFEMODEL?: string; LLM_MODEL_DESCRIPTORS?: string; LLM_MODEL_AUDIT?: string; LLM_MODEL_PLANRESEARCH?: string }) {
  const map = {
    lifeModel: v.LLM_MODEL_LIFEMODEL,
    descriptors: v.LLM_MODEL_DESCRIPTORS,
    audit: v.LLM_MODEL_AUDIT,
    planResearch: v.LLM_MODEL_PLANRESEARCH,
  };
  const kept = Object.fromEntries(Object.entries(map).filter(([, m]) => m !== undefined));
  return Object.keys(kept).length > 0 ? (kept as { lifeModel?: string; descriptors?: string; audit?: string; planResearch?: string }) : undefined;
}

/** Secret values to scrub from anything that leaves the process. */
export function secretsOf(env: Env): string[] {
  return [env.llm?.apiKey, env.tavilyApiKey, env.apiKey].filter((s): s is string => !!s);
}
