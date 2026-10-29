import { randomUUID } from 'node:crypto';
import { getConnInfo } from '@hono/node-server/conninfo';
import { Hono, type Context } from 'hono';
import { HTTPException } from 'hono/http-exception';
import { streamSSE } from 'hono/streaming';
import { GenerationRequestSchema, normalizeRequest } from '@bh/core';
import { requireApiKey } from './auth.js';
import type { MakeDeps } from './deps.js';
import { describeIssues, fail, limitBody, parseJson } from './http.js';
import { miscRoutes } from './routes/misc.js';
import { settingsRoutes } from './routes/settings.js';
import { mountWeb } from './static.js';
import { rejectForeignOrigin, requireJsonForMutations } from './guard.js';
import { SETTLED_STATUSES, getGeneration, insertGeneration, type Db, type GenerationRow } from './db.js';
import type { BusMessage, EventBus } from './events.js';
import type { Env } from './env.js';
import type { JobRunner } from './jobs.js';

export type AppDeps = {
  db: Db;
  /** 只在模型设置路由里用到：给设置表兜底默认值，并把改动写回。 */
  env: Env;
  runner: JobRunner;
  bus: EventBus;
  /** Builds fresh dependencies (own `Budget`) for the random-persona route. */
  makeDeps: MakeDeps;
  /** Key remote clients must send as `X-API-Key`. */
  apiKey: string;
  /** `YYYY-MM-DD`, for the feasibility pre-check. Default: today (UTC). */
  today?: () => string;
  /** Built web UI to serve at `/` (production single-port deploy). Omitted in dev and tests. */
  webDist?: string;
  /** Client address; the default reads the Node socket. `undefined` counts as remote. */
  remoteAddr?: (c: Context) => string | undefined;
  /** SSE keep-alive comment interval. Default 15 s. */
  heartbeatMs?: number;
};

function socketAddr(c: Context): string | undefined {
  try {
    return getConnInfo(c).remote.address;
  } catch {
    return undefined;
  }
}

function view(row: GenerationRow) {
  return {
    id: row.id,
    subjectType: row.subject_type,
    status: row.status,
    request: parseJson(row.request_json) ?? null,
    result: parseJson(row.result_json) ?? null,
    error: parseJson(row.error_json) ?? null,
    createdAt: new Date(row.created_at).toISOString(),
    updatedAt: new Date(row.updated_at).toISOString(),
  };
}

export function createApp(deps: AppDeps): Hono {
  const { db, runner, bus } = deps;
  const today = deps.today ?? (() => new Date().toISOString().slice(0, 10));
  const heartbeatMs = deps.heartbeatMs ?? 15_000;
  const app = new Hono();

  app.use('/api/*', rejectForeignOrigin());
  // Liveness for containers and uptime checks: no DB access, no auth (it reveals nothing).
  app.get('/api/health', (c) => c.json({ ok: true }));
  app.use('/api/*', requireJsonForMutations());
  app.use('/api/*', requireApiKey({ apiKey: deps.apiKey, remoteAddr: deps.remoteAddr ?? socketAddr }));

  app.post(
    '/api/generations',
    limitBody(),
    async (c) => {
      const raw = await c.req.text();
      let json: unknown;
      try {
        json = JSON.parse(raw);
      } catch {
        return c.json(fail('VALIDATION_ERROR', 'Request body must be valid JSON'), 400);
      }
      const parsed = GenerationRequestSchema.safeParse(json);
      if (!parsed.success) {
        return c.json(fail('VALIDATION_ERROR', describeIssues(parsed.error.issues)), 400);
      }
      try {
        normalizeRequest(parsed.data, today());
      } catch (err) {
        return c.json(fail('INFEASIBLE_REQUEST', err instanceof Error ? err.message.slice(0, 300) : 'Request cannot be satisfied'), 400);
      }
      const id = randomUUID();
      insertGeneration(db, { id, subjectType: parsed.data.subjectType, request: parsed.data, now: Date.now() });
      runner.enqueue(id);
      return c.json({ id }, 202);
    },
  );

  app.route('/', miscRoutes({ db, runner, makeDeps: deps.makeDeps, today }));
  app.route('/', settingsRoutes({ db, env: deps.env }));

  app.get('/api/generations/:id', (c) => {
    const row = getGeneration(db, c.req.param('id'));
    return row ? c.json(view(row)) : c.json(fail('NOT_FOUND', 'Generation not found'), 404);
  });

  app.post('/api/generations/:id/resume', (c) => {
    const row = getGeneration(db, c.req.param('id'));
    if (!row) return c.json(fail('NOT_FOUND', 'Generation not found'), 404);
    if (row.status !== 'interrupted') {
      return c.json(fail('INVALID_STATE', `Only interrupted generations can be resumed (status is ${row.status})`), 409);
    }
    runner.resume(row.id);
    return c.json({ id: row.id }, 202);
  });

  app.get('/api/generations/:id/events', (c) => {
    const id = c.req.param('id');
    if (!getGeneration(db, id)) return c.json(fail('NOT_FOUND', 'Generation not found'), 404);
    const lastRaw = Number.parseInt(c.req.header('Last-Event-ID') ?? '', 10);
    const lastSeq = Number.isFinite(lastRaw) && lastRaw > 0 ? lastRaw : 0;

    return streamSSE(c, async (stream) => {
      let aborted = false;
      let wake: (() => void) | undefined;
      const inbox: BusMessage[] = [];
      stream.onAbort(() => {
        aborted = true;
        wake?.();
      });

      // Subscribe, read history and check the status in one synchronous step: nothing can slip in between.
      const unsubscribe = bus.subscribe(id, (m) => {
        inbox.push(m);
        wake?.();
      });
      const backlog = bus.history(id, lastSeq);
      let status = getGeneration(db, id)?.status;
      let finished = status !== undefined && SETTLED_STATUSES.includes(status);

      const heartbeat = setInterval(() => void stream.write(': heartbeat\n\n').catch(() => {}), heartbeatMs);
      let sent = lastSeq;
      const sendEvent = async (seq: number, event: unknown) => {
        if (seq <= sent) return;
        sent = seq;
        await stream.writeSSE({ id: String(seq), data: JSON.stringify(event) });
      };

      try {
        for (const e of backlog) await sendEvent(e.seq, e.event);
        while (!finished && !aborted) {
          const message = inbox.shift();
          if (!message) {
            await new Promise<void>((resolve) => (wake = resolve));
            wake = undefined;
            continue;
          }
          if (message.kind === 'event') await sendEvent(message.seq, message.event);
          else {
            status = message.status;
            finished = true;
          }
        }
        if (finished && !aborted) await stream.writeSSE({ event: 'done', data: JSON.stringify({ status }) });
      } finally {
        clearInterval(heartbeat);
        unsubscribe();
      }
    });
  });

  app.notFound((c) => c.json(fail('NOT_FOUND', 'Route not found'), 404));
  app.onError((err, c) => {
    if (err instanceof HTTPException) return c.json(fail('BAD_REQUEST', 'Bad request'), err.status);
    console.error('[server] unhandled error:', err instanceof Error ? err.name : 'unknown');
    return c.json(fail('INTERNAL', 'Internal server error'), 500);
  });
    // Mounted last: it is a catch-all, and an API route registered after it would never run.
  if (deps.webDist) mountWeb(app, deps.webDist);

return app;
}
