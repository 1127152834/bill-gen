/**
 * 运行时模型配置：`.env` 只是**默认值**，浏览器里保存的设置覆盖它。
 *
 * 每次 job 开始前都会重新读一次这份配置（`makeDeps` 的闭包里），所以页面上改完
 * 立即对下一次生成生效——不用重启进程，也不用改文件。
 */
import { z } from 'zod';
import { listSettings, setSetting, type Db } from './db.js';
import type { Env } from './env.js';

/** 设置表里这一行存的就是它。 */
export const LLM_SETTING_KEY = 'llm';

export const StructuredModeSchema = z.enum(['json_schema', 'function_calling', 'json_mode']);
export type StructuredMode = z.infer<typeof StructuredModeSchema>;

/** 浏览器里能改的东西。所有字段可选：没填就沿用上一层（设置 > env）。 */
export const LlmSettingsPatchSchema = z.object({
  /** `true` = 离线 fixture（不调模型）；`false` = 用下面这套真实配置。 */
  llmFake: z.boolean().optional(),
  baseUrl: z.string().url().optional(),
  /** 空串视为「不修改」，显式 `null` 视为「清除」。 */
  apiKey: z.string().nullish(),
  model: z.string().min(1).optional(),
  structuredMode: StructuredModeSchema.optional(),
  tavilyApiKey: z.string().nullish(),
});
export type LlmSettingsPatch = z.infer<typeof LlmSettingsPatchSchema>;

/** 存进 settings 表的那一份（只放改过的字段）。 */
const StoredSchema = LlmSettingsPatchSchema.partial();
type Stored = z.infer<typeof StoredSchema>;

/** 解析后的生效配置。 */
export type EffectiveLlm = {
  llmFake: boolean;
  llm?: { baseUrl: string; apiKey: string; model: string; structuredMode: StructuredMode };
  tavilyApiKey?: string;
  /** 真实模型是否可用（三项齐全）；`false` 时走 fixture。 */
  ready: boolean;
  /** 每一项最终来自哪里：`db`（页面设置）或 `env`（配置文件）。 */
  source: { apiKey: 'db' | 'env' | 'none'; baseUrl: 'db' | 'env' | 'none'; tavily: 'db' | 'env' | 'none' };
};

function readStored(db: Db): Stored {
  const row = listSettings(db).find((r) => r.key === LLM_SETTING_KEY);
  if (!row) return {};
  try {
    const parsed = StoredSchema.safeParse(JSON.parse(row.value_json));
    return parsed.success ? parsed.data : {};
  } catch {
    return {};
  }
}

const pick = <T>(a: T | undefined, b: T | undefined): T | undefined => (a === undefined ? b : a);

/** 合并「设置 > env」，算出这一次要用的模型配置。 */
export function resolveLlm(db: Db | undefined, env: Env): EffectiveLlm {
  const stored = db ? readStored(db) : {};
  const baseUrl = pick(stored.baseUrl, env.llm?.baseUrl);
  const apiKey = pick(stored.apiKey ?? undefined, env.llm?.apiKey);
  const tavilyApiKey = pick(stored.tavilyApiKey ?? undefined, env.tavilyApiKey);
  const model = pick(stored.model, env.llm?.model) ?? 'gpt-4o';
  const structuredMode = pick(stored.structuredMode, env.llm?.structuredMode) ?? 'json_schema';
  const complete = Boolean(baseUrl && apiKey && tavilyApiKey);
  // 设置里显式开了 fixture 就用 fixture；否则只要三项齐全就走真实模型。
  const llmFake = stored.llmFake ?? (complete ? false : env.llmFake);

  return {
    llmFake,
    ...(baseUrl && apiKey && { llm: { baseUrl, apiKey, model, structuredMode } }),
    ...(tavilyApiKey && { tavilyApiKey }),
    ready: complete && !llmFake,
    source: {
      apiKey: stored.apiKey !== undefined ? 'db' : env.llm?.apiKey ? 'env' : 'none',
      baseUrl: stored.baseUrl !== undefined ? 'db' : env.llm?.baseUrl ? 'env' : 'none',
      tavily: stored.tavilyApiKey !== undefined ? 'db' : env.tavilyApiKey ? 'env' : 'none',
    },
  };
}

/** 打码后的 key：`sk-ab…cd12`。只给前端看，永远不回传明文。 */
export function maskSecret(secret: string | undefined): string | null {
  if (!secret) return null;
  if (secret.length <= 8) return '••••';
  return `${secret.slice(0, 5)}…${secret.slice(-4)}`;
}

/** 给前端看的形状：没有明文 key。 */
export function publicSettings(eff: EffectiveLlm) {
  return {
    llmFake: eff.llmFake,
    baseUrl: eff.llm?.baseUrl ?? null,
    model: eff.llm?.model ?? null,
    structuredMode: eff.llm?.structuredMode ?? 'json_schema',
    apiKeyMasked: maskSecret(eff.llm?.apiKey),
    tavilyKeyMasked: maskSecret(eff.tavilyApiKey),
    hasApiKey: Boolean(eff.llm?.apiKey),
    hasTavilyKey: Boolean(eff.tavilyApiKey),
    ready: eff.ready,
    source: eff.source,
  };
}

/**
 * 把 patch 合并进设置表并返回新的生效配置。
 * `undefined` 的字段不动；`null`（只对 key 有意义）清掉，回落到 env。
 */
export function saveSettings(db: Db, patch: LlmSettingsPatch, env: Env): EffectiveLlm {
  const current = readStored(db);
  const next: Stored = { ...current };
  if (patch.llmFake !== undefined) next.llmFake = patch.llmFake;
  if (patch.baseUrl !== undefined) next.baseUrl = patch.baseUrl;
  if (patch.model !== undefined) next.model = patch.model;
  if (patch.structuredMode !== undefined) next.structuredMode = patch.structuredMode;
  // 空串=不改；null=清除
  if (patch.apiKey !== undefined && patch.apiKey !== null && patch.apiKey !== '') next.apiKey = patch.apiKey;
  if (patch.apiKey === null) delete next.apiKey;
  if (patch.tavilyApiKey !== undefined && patch.tavilyApiKey !== null && patch.tavilyApiKey !== '') next.tavilyApiKey = patch.tavilyApiKey;
  if (patch.tavilyApiKey === null) delete next.tavilyApiKey;

  setSetting(db, LLM_SETTING_KEY, Object.keys(next).length > 0 ? next : null);
  return resolveLlm(db, env);
}
