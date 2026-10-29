import { z } from 'zod';
import { Budget, createOpenAiStructuredLlm, LlmCallError } from '@bh/core';
import { Hono } from 'hono';
import type { Db } from '../db.js';
import type { Env } from '../env.js';
import { fail, readJsonBody } from '../http.js';
import {
  LlmSettingsPatchSchema,
  publicSettings,
  resolveLlm,
  saveSettings,
  type EffectiveLlm,
} from '../settings.js';

export type SettingsRoutesDeps = { db: Db; env: Env };

/** 探活用的最小 schema：模型只要能回一个布尔就算连通。 */
const PingSchema = z.object({ ok: z.boolean() });

/**
 * 模型设置：页面上改完立即对下一次生成生效（`resolveLlm` 每个 job 都重新读一次）。
 *
 * - `GET  /api/settings`      当前生效配置（密钥打码）
 * - `PUT  /api/settings`      覆盖一项或多项；`null` 清除，回落到 `.env`
 * - `POST /api/settings/test` 用当前（或本次提交的）配置发一次最小调用
 */
export function settingsRoutes({ db, env }: SettingsRoutesDeps): Hono {
  const app = new Hono();

  app.get('/api/settings', (c) => c.json(publicSettings(resolveLlm(db, env))));

  app.put('/api/settings', async (c) => {
    const body = await readJsonBody(c);
    if (body === undefined) return c.json(fail('VALIDATION_ERROR', 'Request body must be valid JSON'), 400);
    const parsed = LlmSettingsPatchSchema.safeParse(body);
    if (!parsed.success) {
      const detail = parsed.error.issues.map((i) => `${i.path.join('.') || 'body'}: ${i.message}`).join('; ');
      return c.json(fail('VALIDATION_ERROR', detail), 400);
    }
    const effective = saveSettings(db, parsed.data, env);
    return c.json(publicSettings(effective));
  });

  app.post('/api/settings/test', async (c) => {
    const body = await readJsonBody(c);
    if (body === undefined) return c.json(fail('VALIDATION_ERROR', 'Request body must be valid JSON'), 400);
    // 允许「先测再存」：提交里带的字段优先于已存的设置。
    const parsed = LlmSettingsPatchSchema.safeParse(body);
    if (!parsed.success) {
      const detail = parsed.error.issues.map((i) => `${i.path.join('.') || 'body'}: ${i.message}`).join('; ');
      return c.json(fail('VALIDATION_ERROR', detail), 400);
    }

    const effective: EffectiveLlm =
      Object.keys(parsed.data).length > 0 ? saveSettings(db, parsed.data, env) : resolveLlm(db, env);

    if (!effective.llm || !effective.ready) {
      const missing = [
        !effective.llm?.baseUrl && 'baseUrl',
        !effective.llm?.apiKey && 'apiKey',
        !effective.tavilyApiKey && 'tavilyApiKey',
      ].filter(Boolean);
      return c.json(
        fail('SETTINGS_INCOMPLETE', `还缺这些项才能调用真实模型：${missing.join('、')}`),
        400,
      );
    }

    const started = Date.now();
    try {
      // 留出重试余量（客户端遇到 429/5xx 会退避重试），别让预算先于真实错误触发。
      const budget = new Budget({ llmCalls: 4, tavilyQueries: 0, ms: 90_000 });
      const llm = createOpenAiStructuredLlm(effective.llm, budget);
      const out = await llm.invoke('persona', PingSchema, [
        { role: 'system', content: 'Reply with {"ok": true} and nothing else.' },
        { role: 'user', content: 'ping' },
      ]);
      return c.json({
        ok: out.ok === true,
        ms: Date.now() - started,
        model: effective.llm.model,
        tokens: budget.usage().tokens,
      });
    } catch (err) {
      const message = err instanceof LlmCallError ? err.message : err instanceof Error ? err.message : '模型调用失败';
      // 只回类型与状态，不回密钥。
      return c.json(
        fail('LLM_TEST_FAILED', message.replaceAll(effective.llm.apiKey, '***').slice(0, 300)),
        502,
      );
    }
  });

  return app;
}
