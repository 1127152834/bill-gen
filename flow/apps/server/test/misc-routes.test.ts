import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { LlmCallError, type BudgetLimits } from '@bh/core';
import { describe, expect, it } from 'vitest';
import { finishGeneration, insertGeneration, setStatus, type GenerationStatus } from '../src/db.js';
import {
  API_KEY,
  GenerationResultSchema,
  LLM_SECRET,
  createFixtureDeps,
  deferred,
  fixtureRequest,
  gatedDeps,
  setup,
  type Harness,
} from './helpers.js';

const JSON_HEADERS = { 'content-type': 'application/json' };
const postJson = (h: Harness, path: string, body: unknown, headers: Record<string, string> = {}) =>
  h.json(path, { method: 'POST', headers: { ...JSON_HEADERS, ...headers }, body: JSON.stringify(body) });

const austin = { street: '2104 S 1st St', city: 'Austin', state: 'TX', zip: '78704' };
const batchBody = (over: Record<string, unknown> = {}) => ({
  count: 3,
  subjectType: 'personal',
  accounts: { checking: { min: 8, max: 13 }, credit: { min: 20, max: 35 } },
  months: 3,
  endMonth: '2026-09',
  ...over,
});

/** Inserts a generation row directly (no job runs) with the given status. */
function seed(
  h: Harness,
  o: { id: string; subjectType?: string; status?: GenerationStatus; profile?: unknown; now?: number; result?: unknown },
) {
  const request = { subjectType: o.subjectType ?? 'personal', accounts: { checking: { min: 8, max: 13 } }, months: 1, ...(o.profile !== undefined && { profile: o.profile }) };
  insertGeneration(h.db, { id: o.id, subjectType: o.subjectType ?? 'personal', request, now: o.now ?? Date.now() });
  if (o.status && o.status !== 'queued') {
    finishGeneration(h.db, o.id, { status: o.status, ...(o.result !== undefined && { result: o.result }) });
  }
}

describe('Host allowlist (DNS rebinding)', () => {
  it('rejects a loopback keyless request whose Host is not local with 403 FORBIDDEN_HOST', async () => {
    const h = setup();
    for (const url of ['http://evil.example/api/generations/x', 'http://localhost.evil.example:8797/api/generations/x', 'http://10.0.0.5/api/generations/x']) {
      const res = await h.json(url, undefined, '127.0.0.1');
      expect(res.status, url).toBe(403);
      expect(res.body).toEqual({ error: { code: 'FORBIDDEN_HOST', message: expect.any(String) } });
    }
  });

  it('accepts localhost, 127.0.0.1 and [::1] on any port', async () => {
    const h = setup();
    for (const host of ['localhost', 'localhost:8797', '127.0.0.1', '127.0.0.1:5197', '[::1]', '[::1]:8797']) {
      expect((await h.call(`http://${host}/api/generations/x`, undefined, '127.0.0.1')).status, host).toBe(404);
    }
  });

  it('does not apply to requests carrying the API key', async () => {
    const h = setup();
    const keyed = { headers: { 'X-API-Key': API_KEY } };
    expect((await h.call('http://bill.example.com/api/generations/x', keyed, '203.0.113.9')).status).toBe(404);
    expect((await h.call('http://bill.example.com/api/generations/x', keyed, '127.0.0.1')).status).toBe(404);
  });

  it('still answers 401 (not 403) for remote clients without a key', async () => {
    const h = setup();
    expect((await h.call('http://evil.example/api/generations/x', undefined, '203.0.113.9')).status).toBe(401);
  });
});

describe('POST /api/personas/random', () => {
  it('returns a schema-valid personal profile', async () => {
    const h = setup();
    const { status, body } = await postJson(h, '/api/personas/random', { subjectType: 'personal' });
    expect(status).toBe(200);
    expect(body.name).toEqual(expect.any(String));
    expect(body.address.state).toMatch(/^[A-Z]{2}$/);
  });

  it('keeps the fields of partial verbatim', async () => {
    const h = setup();
    const { status, body } = await postJson(h, '/api/personas/random', { subjectType: 'personal', partial: { name: 'Zed Quill', age: 44 } });
    expect(status).toBe(200);
    expect(body.name).toBe('Zed Quill');
    expect(body.age).toBe(44);
  });

  it('returns a business profile for subjectType business', async () => {
    const business = {
      legalName: 'Lone Star Signs LLC',
      entityType: 'LLC',
      industry: 'Signage',
      description: 'Custom storefront signs.',
      founded: '2019-04',
      employeeCount: 3,
      address: austin,
    };
    const h = setup({ makeDeps: () => createFixtureDeps({ answers: { persona: [business] } }) });
    const { status, body } = await postJson(h, '/api/personas/random', { subjectType: 'business', partial: { employeeCount: 3 } });
    expect(status).toBe(200);
    expect(body.legalName).toBe('Lone Star Signs LLC');
  });

  it('400 VALIDATION_ERROR for a bad body, subjectType or partial of the wrong shape', async () => {
    const h = setup();
    for (const body of [{}, { subjectType: 'robot' }, { subjectType: 'personal', partial: 'x' }, { subjectType: 'personal', partial: { age: 'old' } }, { subjectType: 'personal', partial: { legalName: 5 } }]) {
      const res = await postJson(h, '/api/personas/random', body);
      expect(res.status, JSON.stringify(body)).toBe(400);
      expect(res.body.error.code).toBe('VALIDATION_ERROR');
    }
    const notJson = await h.json('/api/personas/random', { method: 'POST', headers: JSON_HEADERS, body: '{nope' });
    expect(notJson.status).toBe(400);
    expect(notJson.body.error.code).toBe('VALIDATION_ERROR');
  });

  it('400 INVALID_LOCATION for an address in an unknown state, before any model call', async () => {
    let calls = 0;
    const h = setup({
      makeDeps: () => {
        const deps = createFixtureDeps();
        const invoke = deps.llm.invoke.bind(deps.llm);
        deps.llm.invoke = ((...a: Parameters<typeof invoke>) => (calls++, invoke(...a))) as typeof invoke;
        return deps;
      },
    });
    const res = await postJson(h, '/api/personas/random', { subjectType: 'personal', partial: { address: { ...austin, state: 'ZZ' } } });
    expect(res.status).toBe(400);
    expect(res.body.error.code).toBe('INVALID_LOCATION');
    expect(calls).toBe(0);
  });

  it('502 UPSTREAM_ERROR when the model fails, without leaking the provider message or secrets', async () => {
    const h = setup({ makeDeps: () => createFixtureDeps({ answers: { persona: [new LlmCallError(`401 from upstream, key ${LLM_SECRET}`, 401)] } }) });
    const res = await postJson(h, '/api/personas/random', { subjectType: 'personal' });
    expect(res.status).toBe(502);
    expect(res.body.error.code).toBe('UPSTREAM_ERROR');
    expect(JSON.stringify(res.body)).not.toContain(LLM_SECRET);
    expect(JSON.stringify(res.body)).not.toContain('401 from upstream');
  });

  it('asks for its own small model budget on every call', async () => {
    const seen: (Partial<BudgetLimits> | undefined)[] = [];
    const h = setup({
      makeDeps: (limits) => {
        seen.push(limits);
        return createFixtureDeps();
      },
    });
    await postJson(h, '/api/personas/random', { subjectType: 'personal' });
    await postJson(h, '/api/personas/random', { subjectType: 'personal' });
    expect(seen).toHaveLength(2);
    for (const limits of seen) expect(limits?.llmCalls).toBeLessThanOrEqual(4);
  });

  it('is protected like every other POST (415 for non-JSON)', async () => {
    const h = setup();
    const res = await h.json('/api/personas/random', { method: 'POST', headers: { 'content-type': 'text/plain' }, body: '{}' });
    expect(res.status).toBe(415);
  });
});

describe('POST /api/batches', () => {
  it('rejects counts outside 1-10 and non-integers with 400 VALIDATION_ERROR, creating nothing', async () => {
    const h = setup();
    for (const count of [0, 11, -1, 2.5, '3', null]) {
      const res = await postJson(h, '/api/batches', batchBody({ count }));
      expect(res.status, String(count)).toBe(400);
      expect(res.body.error.code).toBe('VALIDATION_ERROR');
    }
    const { count: _c, ...noCount } = batchBody();
    expect((await postJson(h, '/api/batches', noCount)).status).toBe(400);
    expect(h.db.prepare('SELECT COUNT(*) AS n FROM generations').get()).toEqual({ n: 0 });
  });

  it('400 for an invalid request part and 400 INFEASIBLE_REQUEST when the counts cannot be met', async () => {
    const h = setup();
    const bad = await postJson(h, '/api/batches', batchBody({ months: 2 }));
    expect(bad.status).toBe(400);
    expect(bad.body.error.code).toBe('VALIDATION_ERROR');
    const infeasible = await postJson(h, '/api/batches', batchBody({ accounts: { checking: { min: 1, max: 1 } } }));
    expect(infeasible.status).toBe(400);
    expect(infeasible.body.error.code).toBe('INFEASIBLE_REQUEST');
  });

  it('stores one profile-less, seed-less queued request per job (a client profile or seed is dropped)', async () => {
    const h = setup({ concurrency: 0 });
    const { status, body } = await postJson(h, '/api/batches', batchBody({ count: 4, profile: fixtureRequest().profile, seed: 'same' }));
    expect(status).toBe(202);
    expect(body.ids).toHaveLength(4);
    expect(new Set(body.ids).size).toBe(4);
    for (const id of body.ids) {
      const row = h.db.prepare('SELECT status, request_json FROM generations WHERE id = ?').get(id) as { status: string; request_json: string };
      expect(row.status).toBe('queued');
      const request = JSON.parse(row.request_json);
      expect(request.profile).toBeUndefined();
      expect(request.seed).toBeUndefined();
      expect(request.subjectType).toBe('personal');
    }
  });

  it('runs a batch of 3 in fake mode to completion; every job got its own random profile and seed', async () => {
    const h = setup();
    const { status, body } = await postJson(h, '/api/batches', batchBody({ count: 3 }));
    expect(status).toBe(202);
    const done = await Promise.all(body.ids.map((id: string) => h.waitForStatus(id, 'completed', 'completed_with_warnings', 'failed')));
    for (const g of done) {
      expect(g.error).toBeNull();
      expect(['completed', 'completed_with_warnings']).toContain(g.status);
      expect(g.request.profile).toBeTruthy();
      expect(g.request.seed).toEqual(expect.any(String));
    }
    expect(new Set(done.map((g) => g.request.seed)).size).toBe(3);
  });

  it('accepts a batch of 10 (max concurrency stays with the runner)', async () => {
    const h = setup({ concurrency: 0 });
    const { status, body } = await postJson(h, '/api/batches', batchBody({ count: 10 }));
    expect(status).toBe(202);
    expect(body.ids).toHaveLength(10);
  });
});

describe('GET /api/generations/:id/export.json', () => {
  it('downloads the stored GenerationResult pretty-printed, as an attachment', async () => {
    const h = setup();
    const { body } = await h.post(fixtureRequest({ seed: 'export-1' }));
    const done = await h.waitForStatus(body.id, 'completed', 'completed_with_warnings');
    const res = await h.call(`/api/generations/${body.id}/export.json`);
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toBe('application/json; charset=utf-8');
    expect(res.headers.get('content-disposition')).toBe(`attachment; filename="statement-${body.id}.json"`);
    const text = await res.text();
    expect(text).toBe(JSON.stringify(done.result, null, 2));
    expect(GenerationResultSchema.safeParse(JSON.parse(text)).success).toBe(true);
  });

  it('exports completed_with_warnings too', async () => {
    const h = setup();
    seed(h, { id: 'w1', status: 'completed_with_warnings', result: { hello: 'world' } });
    const res = await h.call('/api/generations/w1/export.json');
    expect(res.status).toBe(200);
    expect(await res.text()).toBe('{\n  "hello": "world"\n}');
  });

  it('409 INVALID_STATE for queued, running, failed (even with a partial result) and interrupted jobs', async () => {
    const h = setup();
    seed(h, { id: 'q' });
    seed(h, { id: 'r', status: 'running' });
    seed(h, { id: 'f', status: 'failed', result: { partial: true, statements: [] } });
    seed(h, { id: 'i', status: 'interrupted' });
    for (const id of ['q', 'r', 'f', 'i']) {
      const res = await h.json(`/api/generations/${id}/export.json`);
      expect(res.status, id).toBe(409);
      expect(res.body.error.code).toBe('INVALID_STATE');
    }
  });

  it('404 NOT_FOUND for an unknown id', async () => {
    const h = setup();
    const res = await h.json('/api/generations/nope/export.json');
    expect(res.status).toBe(404);
    expect(res.body.error.code).toBe('NOT_FOUND');
  });
});

describe('GET /api/generations (list)', () => {
  const person = { name: 'Ada Lovelace' };
  const company = { legalName: 'Acme Holdings LLC' };

  function populate(h: Harness) {
    seed(h, { id: 'a', subjectType: 'personal', status: 'completed', profile: person, now: 1_000 });
    seed(h, { id: 'b', subjectType: 'business', status: 'failed', profile: company, now: 2_000 });
    seed(h, { id: 'c', subjectType: 'personal', status: 'queued', now: 3_000 });
    seed(h, { id: 'd', subjectType: 'business', status: 'completed', profile: company, now: 4_000 });
  }

  it('lists newest first with id, subjectType, status, displayName and createdAt', async () => {
    const h = setup();
    populate(h);
    const { status, body } = await h.json('/api/generations');
    expect(status).toBe(200);
    expect(body.items.map((i: any) => i.id)).toEqual(['d', 'c', 'b', 'a']);
    expect(body.items[0]).toEqual({ id: 'd', subjectType: 'business', status: 'completed', displayName: 'Acme Holdings LLC', createdAt: new Date(4_000).toISOString() });
    const byId = Object.fromEntries(body.items.map((i: any) => [i.id, i.displayName]));
    expect(byId).toEqual({ a: 'Ada Lovelace', b: 'Acme Holdings LLC', c: null, d: 'Acme Holdings LLC' });
  });

  it('breaks ties on createdAt by insertion order (newest insert first)', async () => {
    const h = setup();
    for (const id of ['t1', 't2', 't3']) seed(h, { id, now: 5_000 });
    const { body } = await h.json('/api/generations');
    expect(body.items.map((i: any) => i.id)).toEqual(['t3', 't2', 't1']);
  });

  it('filters by subjectType, by status and by both', async () => {
    const h = setup();
    populate(h);
    const ids = async (q: string) => (await h.json(`/api/generations?${q}`)).body.items.map((i: any) => i.id);
    expect(await ids('subjectType=business')).toEqual(['d', 'b']);
    expect(await ids('status=completed')).toEqual(['d', 'a']);
    expect(await ids('subjectType=business&status=completed')).toEqual(['d']);
    expect(await ids('status=interrupted')).toEqual([]);
  });

  it('applies limit (default 50, 1-200)', async () => {
    const h = setup();
    populate(h);
    expect((await h.json('/api/generations?limit=2')).body.items.map((i: any) => i.id)).toEqual(['d', 'c']);
    for (let i = 0; i < 55; i++) seed(h, { id: `x${i}`, now: 10_000 + i });
    expect((await h.json('/api/generations')).body.items).toHaveLength(50);
    expect((await h.json('/api/generations?limit=200')).body.items).toHaveLength(59);
  });

  it('400 VALIDATION_ERROR for bad subjectType, status or limit', async () => {
    const h = setup();
    for (const q of ['subjectType=robot', 'status=done', 'limit=0', 'limit=201', 'limit=abc', 'limit=1.5', 'limit=']) {
      const res = await h.json(`/api/generations?${q}`);
      expect(res.status, q).toBe(400);
      expect(res.body.error.code).toBe('VALIDATION_ERROR');
    }
  });

  it('does not shadow GET /api/generations/:id', async () => {
    const h = setup();
    populate(h);
    expect((await h.json('/api/generations/a')).body.id).toBe('a');
  });
});

describe('DELETE /api/generations/:id', () => {
  const rows = (h: Harness, sql: string, id: string) => (h.db.prepare(sql).get(id) as { n: number }).n;

  it('deletes a finished job together with its events and checkpoints', async () => {
    const h = setup();
    const { body } = await h.post(fixtureRequest({ seed: 'del-1' }));
    await h.waitForStatus(body.id, 'completed', 'completed_with_warnings');
    expect(rows(h, 'SELECT COUNT(*) AS n FROM events WHERE generation_id = ?', body.id)).toBeGreaterThan(0);
    expect(rows(h, 'SELECT COUNT(*) AS n FROM checkpoints WHERE thread_id = ?', body.id)).toBeGreaterThan(0);

    const res = await h.call(`/api/generations/${body.id}`, { method: 'DELETE' });
    expect(res.status).toBe(204);
    expect(await res.text()).toBe('');
    expect((await h.json(`/api/generations/${body.id}`)).status).toBe(404);
    expect(rows(h, 'SELECT COUNT(*) AS n FROM events WHERE generation_id = ?', body.id)).toBe(0);
    expect(rows(h, 'SELECT COUNT(*) AS n FROM checkpoints WHERE thread_id = ?', body.id)).toBe(0);
    expect(rows(h, 'SELECT COUNT(*) AS n FROM writes WHERE thread_id = ?', body.id)).toBe(0);
    expect(rows(h, 'SELECT COUNT(*) AS n FROM generations WHERE id = ?', body.id)).toBe(0);
  });

  it('only removes the targeted job', async () => {
    const h = setup();
    const a = (await h.post(fixtureRequest({ seed: 'keep-a' }))).body.id;
    const b = (await h.post(fixtureRequest({ seed: 'keep-b' }))).body.id;
    await Promise.all([h.waitForStatus(a, 'completed', 'completed_with_warnings'), h.waitForStatus(b, 'completed', 'completed_with_warnings')]);
    await h.call(`/api/generations/${a}`, { method: 'DELETE' });
    expect((await h.json(`/api/generations/${b}`)).status).toBe(200);
    expect(rows(h, 'SELECT COUNT(*) AS n FROM events WHERE generation_id = ?', b)).toBeGreaterThan(0);
    expect(rows(h, 'SELECT COUNT(*) AS n FROM checkpoints WHERE thread_id = ?', b)).toBeGreaterThan(0);
  });

  it('works when the checkpoint tables do not exist yet (job interrupted before it ever ran)', async () => {
    const h = setup();
    seed(h, { id: 'never', status: 'interrupted' });
    expect((await h.call('/api/generations/never', { method: 'DELETE' })).status).toBe(204);
    expect((await h.json('/api/generations/never')).status).toBe(404);
  });

  it('is 204 for an unknown id', async () => {
    const h = setup();
    expect((await h.call('/api/generations/nope', { method: 'DELETE' })).status).toBe(204);
  });

  it('409 INVALID_STATE for a running or queued job, which stays intact', async () => {
    const gate = deferred();
    const h = setup({ makeDeps: () => gatedDeps(gate.promise), concurrency: 1 });
    const running = (await h.post(fixtureRequest({ seed: 'busy-1' }))).body.id;
    const queued = (await h.post(fixtureRequest({ seed: 'busy-2' }))).body.id;
    await h.waitForStatus(running, 'running');

    for (const id of [running, queued]) {
      const res = await h.json(`/api/generations/${id}`, { method: 'DELETE' });
      expect(res.status, id).toBe(409);
      expect(res.body.error.code).toBe('INVALID_STATE');
      expect((await h.json(`/api/generations/${id}`)).status).toBe(200);
    }
    gate.resolve();
    await h.waitForStatus(running, 'completed', 'completed_with_warnings');
    await h.waitForStatus(queued, 'completed', 'completed_with_warnings');
    expect((await h.call(`/api/generations/${running}`, { method: 'DELETE' })).status).toBe(204);
  });

  it('requires the API key from remote clients', async () => {
    const h = setup();
    seed(h, { id: 'k', status: 'completed' });
    expect((await h.call('/api/generations/k', { method: 'DELETE' }, '203.0.113.9')).status).toBe(401);
    expect((await h.call('/api/generations/k', { method: 'DELETE', headers: { 'X-API-Key': API_KEY } }, '203.0.113.9')).status).toBe(204);
  });
});

describe('web dist mounting', () => {
  it('serves the built UI at / and never shadows an API route', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'bh-web-'));
    mkdirSync(join(dir, 'assets'));
    writeFileSync(join(dir, 'index.html'), '<!doctype html><title>ui</title>');
    writeFileSync(join(dir, 'assets', 'app.js'), 'console.log(1)');
    try {
      const app = setup({ webDist: dir }).app;
      const root = await app.request('/');
      expect(root.status).toBe(200);
      expect(await root.text()).toContain('<title>ui</title>');
      const asset = await app.request('/assets/app.js');
      expect(asset.headers.get('Content-Type')).toContain('javascript');
      // A client-side route falls back to index.html…
      const spa = await app.request('/history/abc');
      expect(await spa.text()).toContain('<title>ui</title>');
      // …but /api/* never does (an HTML answer here would break every client).
      const api = await app.request('/api/nope', { headers: { 'X-API-Key': API_KEY } });
      expect(api.status).toBe(404);
      expect(api.headers.get('Content-Type') ?? '').not.toContain('html');
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it('stays unmounted without webDist (dev and tests)', async () => {
    const res = await setup().app.request('/');
    expect(res.status).toBe(404);
  });
});


describe('交付文件下载 /api/v1/renders/:name', () => {
  it('serves a month JSON and the validation report out of the billgen directory', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'bh-renders-'));
    writeFileSync(join(dir, 'chase-personal-2027-01.json'), '{"hello":"world"}');
    writeFileSync(join(dir, 'validation-report.json'), '{"ok":true}');
    const h = setup({ billgenOut: dir });
    try {
      const one = await h.call('/api/v1/renders/chase-personal-2027-01.json');
      expect(one.status).toBe(200);
      expect(one.headers.get('content-type')).toContain('application/json');
      expect(await one.text()).toBe('{"hello":"world"}');
      expect((await h.call('/api/v1/renders/validation-report.json')).status).toBe(200);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it('refuses traversal, dotfiles and suffixes that are not deliverables', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'bh-renders-'));
    writeFileSync(join(dir, 'secret.txt'), 'nope');
    writeFileSync(join(dir, 'notes.json'), '{}');
    const h = setup({ billgenOut: dir });
    try {
      expect((await h.call('/api/v1/renders/..%2Fpackage.json')).status).toBe(404);
      expect((await h.call('/api/v1/renders/secret.txt')).status).toBe(404);
      expect((await h.call('/api/v1/renders/missing.json')).status).toBe(404);
      expect((await h.call('/api/v1/renders/notes.json')).status).toBe(200);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
