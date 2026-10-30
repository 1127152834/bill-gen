import { randomUUID } from 'node:crypto';
import { createReadStream, statSync } from 'node:fs';
import { basename, join } from 'node:path';
import { Readable } from 'node:stream';
import { Hono } from 'hono';
import { z } from 'zod';
import {
  BudgetExceededError,
  BusinessProfileSchema,
  GenerationRequestSchema,
  LlmCallError,
  LlmOutputError,
  PersonalProfileSchema,
  SubjectTypeSchema,
  createRng,
  lookupLocation,
  normalizeRequest,
  randomPersona,
  type GenerationRequest,
  type Profile,
} from '@bh/core';
import {
  GENERATION_STATUSES,
  deleteGeneration,
  getGeneration,
  insertGeneration,
  listGenerations,
  parseStoredName,
  type Db,
} from '../db.js';
import type { MakeDeps } from '../deps.js';
import { describeIssues, fail, limitBody, parseJson, readJsonBody } from '../http.js';
import type { JobRunner } from '../jobs.js';

export type MiscRoutesDeps = {
  db: Db;
  runner: JobRunner;
  makeDeps: MakeDeps;
  /** `YYYY-MM-DD` for the feasibility pre-check. */
  today: () => string;
  /** billgen 写文件的目录（PDF / 每月 JSON / 校验报告都在这一层）。 */
  outDir?: string;
};

/** 只有这几种才发：出片目录里还可能躺着 ledger 中间件，别把目录整个暴露出去。 */
const SERVABLE = new Map<string, string>([
  ['.pdf', 'application/pdf'],
  ['.json', 'application/json; charset=utf-8'],
  ['.png', 'image/png'],
]);

/** A persona is one model call; this leaves room for a retry and a repair, and no more. */
const PERSONA_MAX_LLM_CALLS = 4;
const MAX_BATCH = 10;
const DEFAULT_LIST_LIMIT = 50;
const MAX_LIST_LIMIT = 200;
const EXPORTABLE = new Set(['completed', 'completed_with_warnings']);

const PersonaEnvelope = z.object({ subjectType: SubjectTypeSchema, partial: z.unknown().optional() });
const PartialPersonal = PersonalProfileSchema.partial().strict();
const PartialBusiness = BusinessProfileSchema.partial().strict();

const BatchCount = z.number().int().min(1).max(MAX_BATCH);

const ListQuery = z.object({
  subjectType: SubjectTypeSchema.optional(),
  status: z.enum(GENERATION_STATUSES).optional(),
  limit: z
    .string()
    .regex(/^\d+$/, 'must be an integer')
    .transform(Number)
    .pipe(z.number().int().min(1).max(MAX_LIST_LIMIT))
    .optional(),
});

/** A model-side failure of the persona route, as a fixed message: provider text and keys never reach the client. */
function upstreamMessage(err: unknown): string | undefined {
  if (err instanceof LlmCallError) return 'The language model request failed';
  if (err instanceof LlmOutputError) return 'The language model returned an unusable answer';
  if (err instanceof BudgetExceededError) return 'The language model budget for this request was used up';
  return undefined;
}

export function miscRoutes({ db, runner, makeDeps, today, outDir }: MiscRoutesDeps): Hono {
  const app = new Hono();

  app.post('/api/personas/random', limitBody(), async (c) => {
    const envelope = PersonaEnvelope.safeParse(await readJsonBody(c));
    if (!envelope.success) return c.json(fail('VALIDATION_ERROR', describeIssues(envelope.error.issues)), 400);
    const { subjectType } = envelope.data;
    const partialSchema = subjectType === 'personal' ? PartialPersonal : PartialBusiness;
    const partial = partialSchema.safeParse(envelope.data.partial ?? {});
    if (!partial.success) return c.json(fail('VALIDATION_ERROR', describeIssues(partial.error.issues)), 400);

    const given = partial.data as Partial<Profile>;
    if (given.address) {
      try {
        lookupLocation(given.address.city, given.address.state);
      } catch {
        return c.json(fail('INVALID_LOCATION', `Unknown state "${given.address.state}"`), 400);
      }
    }

    const deps = makeDeps({ llmCalls: PERSONA_MAX_LLM_CALLS });
    try {
      const profile = await randomPersona(subjectType, { ...deps.personaDefaults, ...given }, { llm: deps.llm, rng: createRng(randomUUID()) });
      return c.json(profile);
    } catch (err) {
      const message = upstreamMessage(err);
      if (message === undefined) throw err;
      return c.json(fail('UPSTREAM_ERROR', message), 502);
    }
  });

  app.post('/api/batches', limitBody(), async (c) => {
    const json = await readJsonBody(c);
    if (typeof json !== 'object' || json === null || Array.isArray(json)) {
      return c.json(fail('VALIDATION_ERROR', 'Request body must be a JSON object'), 400);
    }
    const { count, ...rest } = json as Record<string, unknown>;
    const countResult = BatchCount.safeParse(count);
    if (!countResult.success) return c.json(fail('VALIDATION_ERROR', `count: must be an integer from 1 to ${MAX_BATCH}`), 400);
    const parsed = GenerationRequestSchema.safeParse(rest);
    if (!parsed.success) return c.json(fail('VALIDATION_ERROR', describeIssues(parsed.error.issues)), 400);
    try {
      normalizeRequest(parsed.data, today());
    } catch (err) {
      return c.json(fail('INFEASIBLE_REQUEST', err instanceof Error ? err.message.slice(0, 300) : 'Request cannot be satisfied'), 400);
    }

    // Every job gets a random persona and its own seed from the job runner: a profile or seed sent by the client is dropped.
    const { subjectType, accounts, months, endMonth } = parsed.data;
    const template: GenerationRequest = { subjectType, accounts, months, ...(endMonth !== undefined && { endMonth }) };
    const now = Date.now();
    const ids = Array.from({ length: countResult.data }, () => randomUUID());
    db.transaction(() => {
      for (const id of ids) insertGeneration(db, { id, subjectType, request: template, now });
    })();
    for (const id of ids) runner.enqueue(id);
    return c.json({ ids }, 202);
  });

  app.get('/api/generations', (c) => {
    const query = ListQuery.safeParse(c.req.query());
    if (!query.success) return c.json(fail('VALIDATION_ERROR', describeIssues(query.error.issues)), 400);
    const { subjectType, status, limit } = query.data;
    const rows = listGenerations(db, {
      ...(subjectType !== undefined && { subjectType }),
      ...(status !== undefined && { status }),
      limit: limit ?? DEFAULT_LIST_LIMIT,
    });
    return c.json({
      items: rows.map((r) => ({
        id: r.id,
        subjectType: r.subject_type,
        status: r.status,
        displayName: parseStoredName(r.request_json),
        createdAt: new Date(r.created_at).toISOString(),
      })),
    });
  });

  app.get('/api/generations/:id/export.json', (c) => {
    const row = getGeneration(db, c.req.param('id'));
    if (!row) return c.json(fail('NOT_FOUND', 'Generation not found'), 404);
    // A job stopped by its budget stores `{partial:true, statements}`, which is not a GenerationResult.
    const result = EXPORTABLE.has(row.status) ? parseJson(row.result_json) : undefined;
    if (result === undefined) {
      return c.json(fail('INVALID_STATE', `Only completed generations can be exported (status is ${row.status})`), 409);
    }
    return c.body(JSON.stringify(result, null, 2), 200, {
      'Content-Type': 'application/json; charset=utf-8',
      'Content-Disposition': `attachment; filename="statement-${row.id}.json"`,
    });
  });

  /**
   * 交付文件下载。billgen 把每月 JSON、`validation-report.json`、`manifest.json` 写在出片目录，
   * 主服务按文件名发出来；只认一层文件名 + 白名单后缀，`..` 一律当找不到。
   */
  app.get('/api/v1/renders/:name', (c) => {
    const name = basename(c.req.param('name'));
    if (!name || name !== c.req.param('name') || name.startsWith('.')) {
      return c.json(fail('NOT_FOUND', 'File not found'), 404);
    }
    const type = SERVABLE.get(name.slice(name.lastIndexOf('.')).toLowerCase());
    if (!type) return c.json(fail('NOT_FOUND', 'File not found'), 404);
    if (!outDir) return c.json(fail('NOT_FOUND', 'File not found'), 404);
    const path = join(outDir, name);
    let size: number;
    try {
      const info = statSync(path);
      if (!info.isFile()) return c.json(fail('NOT_FOUND', 'File not found'), 404);
      size = info.size;
    } catch {
      return c.json(fail('NOT_FOUND', 'File not found'), 404);
    }
    const body = Readable.toWeb(createReadStream(path)) as ReadableStream<Uint8Array>;
    return c.body(body, 200, {
      'Content-Type': type,
      'Content-Length': String(size),
      'Content-Disposition': `attachment; filename="${name}"`,
    });
  });

  app.delete('/api/generations/:id', async (c) => {
    const id = c.req.param('id');
    const row = getGeneration(db, id);
    if (row && (row.status === 'running' || row.status === 'queued')) {
      return c.json(fail('INVALID_STATE', `A ${row.status} generation cannot be deleted`), 409);
    }
    await runner.deleteCheckpoints(id);
    deleteGeneration(db, id);
    return c.body(null, 204);
  });

  return app;
}
