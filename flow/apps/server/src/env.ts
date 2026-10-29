import type { LlmEnv } from '@bh/core';
import { z } from 'zod';

export type Env = {
  /** `LLM_FAKE=1`: offline fixture dependencies; no LLM / Tavily settings needed. */
  llmFake: boolean;
  llm?: LlmEnv;
  tavilyApiKey?: string;
  /** Key remote clients send as `X-API-Key`. */
  apiKey: string;
  bankName: string;
  /** How to reach billgen's MCP server; omitted = statements are generated but not rendered. */
  billgen?: {
    command: string;
    args: string[];
    templates: string[];
    outDir: string;
    modelFill: boolean;
    /** Templates kept per statement when `templates` contains `auto`. */
    templateLimit: number;
  };
  databasePath: string;
  host: string;
  port: number;
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
  TAVILY_API_KEY: text.optional(),
  API_KEY: text,
  BANK_DISPLAY_NAME: text.default('Harborline Bank'),
  DATABASE_PATH: text.default('./data/app.db'),
  HOST: text.default('127.0.0.1'),
  PORT: z.coerce.number().int().min(1).max(65535).default(8797),
  LLM_FAKE: z.enum(['0', '1']).default('0'),
  BILLGEN_MCP_CMD: text.optional(),
  BILLGEN_TEMPLATES: text.optional(),
  BILLGEN_OUT: text.default('./data/renders'),
  BILLGEN_AI_FILL: z.enum(['0', '1']).default('1'),
  /** Templates kept per statement when `BILLGEN_TEMPLATES=auto`. */
  BILLGEN_AUTO_LIMIT: z.coerce.number().int().min(1).max(11).default(2),
});

/**
 * Reads and validates the server settings (`process.env` by default; empty strings count as unset).
 * @throws EnvError listing the variable names that are missing or invalid.
 */
export function loadEnv(source: Record<string, string | undefined> = process.env): Env {
  const present = Object.fromEntries(Object.entries(source).filter(([, v]) => v !== undefined && v !== ''));
  const parsed = Raw.safeParse(present);
  const problems = new Set(parsed.success ? [] : parsed.error.issues.map((i) => String(i.path[0])));
  const llmFake = present.LLM_FAKE === '1';
  if (!llmFake) {
    for (const name of ['LLM_BASE_URL', 'LLM_API_KEY', 'TAVILY_API_KEY']) if (!present[name]) problems.add(name);
  }
  if (!parsed.success || problems.size > 0) throw new EnvError([...problems]);
  const v = parsed.data;

  return {
    llmFake,
    ...(v.LLM_BASE_URL && v.LLM_API_KEY && {
      llm: { baseUrl: v.LLM_BASE_URL, apiKey: v.LLM_API_KEY, model: v.LLM_MODEL, structuredMode: v.LLM_STRUCTURED_MODE },
    }),
    ...(v.TAVILY_API_KEY && { tavilyApiKey: v.TAVILY_API_KEY }),
    apiKey: v.API_KEY,
    bankName: v.BANK_DISPLAY_NAME,
    ...(v.BILLGEN_MCP_CMD && {
      billgen: {
        command: v.BILLGEN_MCP_CMD,
        args: ['mcp'],
        templates: (v.BILLGEN_TEMPLATES ?? '')
          .split(',')
          .map((t) => t.trim())
          .filter(Boolean),
        outDir: v.BILLGEN_OUT,
        modelFill: v.BILLGEN_AI_FILL === '1',
        templateLimit: v.BILLGEN_AUTO_LIMIT,
      },
    }),
    databasePath: v.DATABASE_PATH,
    host: v.HOST,
    port: v.PORT,
  };
}

/** Secret values to scrub from anything that leaves the process. */
export function secretsOf(env: Env): string[] {
  return [env.llm?.apiKey, env.tavilyApiKey, env.apiKey].filter((s): s is string => !!s);
}
