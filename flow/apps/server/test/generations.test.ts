import { describe, expect, it } from 'vitest';
import { FIXTURE_TODAY, GenerationResultSchema, LLM_SECRET, API_KEY, createFixtureDeps, deferred, fixtureRequest, gatedDeps, parseSse, setup } from './helpers.js';

const JSON_HEADERS = { 'content-type': 'application/json' };
const validBody = () => fixtureRequest({ seed: 'api-1' });

describe('auth', () => {
  it('rejects a non-loopback request without a key with 401 UNAUTHORIZED', async () => {
    const h = setup();
    const { status, body } = await h.json('/api/generations/x', undefined, '203.0.113.9');
    expect(status).toBe(401);
    expect(body).toEqual({ error: { code: 'UNAUTHORIZED', message: expect.any(String) } });
  });

  it('rejects wrong keys of any length without throwing', async () => {
    const h = setup();
    for (const key of ['', 'x', API_KEY.slice(0, -1), API_KEY.replace(/.$/, 'X'), API_KEY + 'extra']) {
      const res = await h.call('/api/generations/x', { headers: { 'X-API-Key': key } }, '203.0.113.9');
      expect(res.status, key).toBe(401);
    }
  });

  it('accepts the right key from a remote address (reaches the handler: unknown id is 404)', async () => {
    const h = setup();
    const res = await h.call('/api/generations/x', { headers: { 'X-API-Key': API_KEY } }, '203.0.113.9');
    expect(res.status).toBe(404);
  });

  it('lets loopback addresses skip the key', async () => {
    const h = setup();
    for (const addr of ['127.0.0.1', '::1', '::ffff:127.0.0.1']) {
      expect((await h.call('/api/generations/x', undefined, addr)).status, addr).toBe(404);
    }
  });

  it('treats an unknown remote address as remote (fails closed)', async () => {
    const h = setup();
    expect((await h.call('/api/generations/x', undefined, null)).status).toBe(401);
  });
});

describe('cross-site protection', () => {
  const body = () => JSON.stringify(validBody());

  it('rejects a non-JSON content type on POST with 415 UNSUPPORTED_MEDIA_TYPE, even from loopback', async () => {
    const h = setup();
    for (const headers of [{ 'content-type': 'text/plain' }, {}, { 'content-type': 'application/x-www-form-urlencoded' }]) {
      const res = await h.json('/api/generations', { method: 'POST', headers, body: body() });
      expect(res.status).toBe(415);
      expect(res.body.error.code).toBe('UNSUPPORTED_MEDIA_TYPE');
    }
    const resume = await h.json('/api/generations/x/resume', { method: 'POST', headers: { 'content-type': 'text/plain' } });
    expect(resume.status).toBe(415);
    expect(h.db.prepare('SELECT COUNT(*) AS n FROM generations').get()).toEqual({ n: 0 });
  });

  it('rejects a foreign Origin with 403 FORBIDDEN_ORIGIN, even from loopback', async () => {
    const h = setup();
    for (const origin of ['https://evil.example', 'null', 'http://localhost.evil.example', 'http://127.0.0.1.evil.example:8797']) {
      const res = await h.json('/api/generations', {
        method: 'POST',
        headers: { 'content-type': 'application/json', Origin: origin },
        body: body(),
      });
      expect(res.status, origin).toBe(403);
      expect(res.body.error.code).toBe('FORBIDDEN_ORIGIN');
    }
    expect((await h.json('/api/generations/x', { headers: { Origin: 'https://evil.example' } })).status).toBe(403);
    expect(h.db.prepare('SELECT COUNT(*) AS n FROM generations').get()).toEqual({ n: 0 });
  });

  it('accepts JSON POSTs carrying a localhost Origin (the Vite dev proxy) and a charset parameter', async () => {
    const h = setup();
    for (const origin of ['http://localhost:5197', 'http://127.0.0.1:5197', 'http://[::1]:5197', 'http://localhost']) {
      const res = await h.json('/api/generations', {
        method: 'POST',
        headers: { 'content-type': 'application/json; charset=utf-8', Origin: origin },
        body: body(),
      });
      expect(res.status, origin).toBe(202);
    }
  });
});

describe('POST /api/generations validation', () => {
  it('returns 400 VALIDATION_ERROR for an invalid body, naming paths without echoing values', async () => {
    const h = setup();
    const { status, body } = await h.post({ subjectType: 'alien', accounts: {}, months: 2, junk: 'SECRET-PAYLOAD' });
    expect(status).toBe(400);
    expect(body.error.code).toBe('VALIDATION_ERROR');
    expect(body.error.message).toContain('subjectType');
    expect(body.error.message).toContain('months');
    expect(JSON.stringify(body)).not.toContain('SECRET-PAYLOAD');
  });

  it('returns 400 VALIDATION_ERROR for a body that is not JSON', async () => {
    const h = setup();
    const res = await h.call('/api/generations', { method: 'POST', body: '{nope', headers: { 'content-type': 'application/json' } });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { error: { code: string } }).error.code).toBe('VALIDATION_ERROR');
  });

  it('returns 400 INFEASIBLE_REQUEST when max is below the base required count', async () => {
    const h = setup();
    const { status, body } = await h.post(fixtureRequest({ accounts: { checking: { min: 1, max: 2 }, credit: { min: 1, max: 5 } } }));
    expect(status).toBe(400);
    expect(body.error.code).toBe('INFEASIBLE_REQUEST');
    expect(body.error.message).toMatch(/checking max/);
  });

  it('returns 400 INFEASIBLE_REQUEST for a profile in an unknown state', async () => {
    const h = setup();
    const req = fixtureRequest();
    const profile = { ...(req.profile as object), address: { street: '1 Main St', city: 'Nowhere', state: 'ZZ', zip: '12345' } };
    const { status, body } = await h.post({ ...req, profile });
    expect(status).toBe(400);
    expect(body.error.code).toBe('INFEASIBLE_REQUEST');
  });
});

describe('generation lifecycle', () => {
  it('accepts a valid request with 202, runs it to completed and returns a schema-valid result', async () => {
    const h = setup();
    const { status, body } = await h.post(validBody());
    expect(status).toBe(202);
    expect(body.id).toMatch(/^[0-9a-f-]{36}$/);

    const done = await h.waitForStatus(body.id, 'completed', 'completed_with_warnings', 'failed');
    expect(done.status).toBe('completed');
    expect(done.error).toBeNull();
    const result = GenerationResultSchema.parse(done.result);
    expect(result.id).toBe(body.id);
    expect(result.statements.length).toBeGreaterThan(0);
    expect(done.request.seed).toBe('api-1');
  });

  it('GET of an unknown id is 404 NOT_FOUND; unknown routes are 404 NOT_FOUND', async () => {
    const h = setup();
    expect((await h.json('/api/generations/nope')).body.error.code).toBe('NOT_FOUND');
    const unknown = await h.json('/api/whatever');
    expect(unknown.status).toBe(404);
    expect(unknown.body.error.code).toBe('NOT_FOUND');
  });

  it('keeps the third job queued until one of the first two finishes', async () => {
    const gate = deferred();
    const h = setup({ makeDeps: () => gatedDeps(gate.promise) });
    const ids = [];
    for (let i = 0; i < 3; i++) ids.push((await h.post(fixtureRequest({ seed: `c-${i}` }))).body.id as string);

    await h.waitForStatus(ids[0]!, 'running');
    await h.waitForStatus(ids[1]!, 'running');
    await new Promise((r) => setTimeout(r, 60));
    expect((await h.json(`/api/generations/${ids[2]}`)).body.status).toBe('queued');

    gate.resolve();
    for (const id of ids) expect((await h.waitForStatus(id, 'completed', 'failed')).status).toBe('completed');
  });
});

describe('persona and failures', () => {
  it('fills in a persona (and seed) when the request has none and writes it back to request_json', async () => {
    const h = setup();
    // Fixture deps pin the fake persona to Austin (the canned answers' place), whatever metro the rng picks.
    const { profile: _profile, ...noProfile } = fixtureRequest({ seed: 'persona-62' });
    const { body } = await h.post(noProfile);
    const done = await h.waitForStatus(body.id, 'completed', 'completed_with_warnings', 'failed');
    expect(done.error).toBeNull();
    expect(done.request.profile).toBeTruthy();
    expect(done.request.seed).toBe('persona-62');
    expect(done.result.subject).toEqual(done.request.profile);
  });

  it('maps BudgetExceededError to failed + BUDGET_EXCEEDED and keeps the statements from the checkpoint', async () => {
    const probe = createFixtureDeps();
    const h0 = setup({ makeDeps: () => probe });
    const first = await h0.post(validBody());
    await h0.waitForStatus(first.body.id, 'completed');
    const total = probe.llm.calls.length;

    const h = setup({ makeDeps: () => createFixtureDeps({ budget: { llmCalls: total - 1 } }) });
    const { body } = await h.post(validBody());
    const failed = await h.waitForStatus(body.id, 'failed', 'completed');
    expect(failed.status).toBe('failed');
    expect(failed.error.code).toBe('BUDGET_EXCEEDED');
    expect(failed.result.statements.length).toBeGreaterThan(0);
  });

  it('BUDGET_EXCEEDED before any statements leaves result null', async () => {
    const h = setup({ makeDeps: () => createFixtureDeps({ budget: { llmCalls: 1 } }) });
    const { body } = await h.post(validBody());
    const failed = await h.waitForStatus(body.id, 'failed', 'completed');
    expect(failed.error.code).toBe('BUDGET_EXCEEDED');
    expect(failed.result).toBeNull();
  });

  it('records a coded error with its code and a generic one as GENERATION_FAILED, never leaking keys or stacks', async () => {
    const coded = Object.assign(new Error(`upstream said no for key ${LLM_SECRET}`), { code: 'LLM_CALL_FAILED' });
    const h = setup({ makeDeps: () => createFixtureDeps({ answers: { planResearch: [coded] } }) });
    const a = await h.waitForStatus((await h.post(validBody())).body.id, 'failed');
    expect(a.error.code).toBe('LLM_CALL_FAILED');
    expect(JSON.stringify(a)).not.toContain(LLM_SECRET);

    const plain = new Error(`TypeError at /Users/x/file.ts ${LLM_SECRET}`);
    const h2 = setup({ makeDeps: () => createFixtureDeps({ answers: { planResearch: [plain] } }) });
    const b = await h2.waitForStatus((await h2.post(validBody())).body.id, 'failed');
    expect(b.error.code).toBe('GENERATION_FAILED');
    expect(JSON.stringify(b)).not.toContain(LLM_SECRET);
    expect(JSON.stringify(b)).not.toContain('/Users/x');

    // failure inside makeDeps itself is also recorded
    const h3 = setup({ makeDeps: () => { throw new Error('no deps'); } });
    expect((await h3.waitForStatus((await h3.post(validBody())).body.id, 'failed')).error.code).toBe('GENERATION_FAILED');
  });

  it('keeps only known core error codes; LIFE_MODEL_INVALID gets a fixed message without model text', async () => {
    const lifeModel = Object.assign(new Error('Life model is invalid after one retry: payer "IGNORE PREVIOUS" ...'), { code: 'LIFE_MODEL_INVALID' });
    const h = setup({ makeDeps: () => createFixtureDeps({ answers: { planResearch: [lifeModel] } }) });
    const a = await h.waitForStatus((await h.post(validBody())).body.id, 'failed');
    expect(a.error.code).toBe('LIFE_MODEL_INVALID');
    expect(a.error.message).toBe('The life model failed the code checks after every retry');

    const foreign = Object.assign(new Error('SQLITE_BUSY: database is locked at /var/x'), { code: 'SQLITE_BUSY' });
    const h2 = setup({ makeDeps: () => createFixtureDeps({ answers: { planResearch: [foreign] } }) });
    const b = await h2.waitForStatus((await h2.post(validBody())).body.id, 'failed');
    expect(b.error).toMatchObject({ code: 'GENERATION_FAILED', message: 'Generation failed' });
  });

  it('stores the usage of a failed job next to its error', async () => {
    const h = setup({ makeDeps: () => createFixtureDeps({ budget: { llmCalls: 3 } }) });
    const failed = await h.waitForStatus((await h.post(validBody())).body.id, 'failed');
    expect(failed.error.code).toBe('BUDGET_EXCEEDED');
    expect(failed.error.usage).toMatchObject({ llmCalls: 3, tavilyQueries: expect.any(Number) });
    expect(failed.error.usage.tavilyQueries).toBeGreaterThan(0);
  });

  it('keeps the checkpointed statements for any failure after statements exist, not only budget stops', async () => {
    const h = setup({
      makeDeps: () => {
        const deps = createFixtureDeps();
        // finalize reads the clock once the audit has run: fail there, after the statements are checkpointed.
        const now = deps.now;
        deps.now = () => {
          if (deps.llm.calls.some((c) => c.node === 'audit')) throw new Error('clock went away');
          return now();
        };
        return deps;
      },
    });
    const failed = await h.waitForStatus((await h.post(validBody())).body.id, 'failed');
    expect(failed.error.code).toBe('GENERATION_FAILED');
    expect(failed.result.partial).toBe(true);
    expect(failed.result.statements.length).toBeGreaterThan(0);
    expect(failed.error.usage.llmCalls).toBeGreaterThan(0);
  });

  it('never puts secrets into error responses', async () => {
    const h = setup();
    const bodies = [
      (await h.json('/api/generations/x', undefined, '203.0.113.9')).body,
      (await h.post({ subjectType: LLM_SECRET })).body,
      (await h.json('/api/nope')).body,
      (await h.json('/api/generations/x/resume', { method: 'POST', headers: JSON_HEADERS })).body,
    ];
    for (const b of bodies) {
      expect(JSON.stringify(b)).not.toContain(LLM_SECRET);
      expect(Object.keys(b)).toEqual(['error']);
      expect(Object.keys(b.error).sort()).toEqual(['code', 'message']);
    }
  });
});

describe('GET /api/generations/:id/events (SSE)', () => {
  it('replays node_start ... done for a finished job, with seq as event id and a closed stream', async () => {
    const h = setup();
    const { body } = await h.post(validBody());
    await h.waitForStatus(body.id, 'completed');

    const res = await h.call(`/api/generations/${body.id}/events`);
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toContain('text/event-stream');
    const msgs = parseSse(await res.text());
    const progress = msgs.filter((m) => m.event === 'message');
    expect(JSON.parse(progress[0]!.data)).toEqual({ type: 'node_start', node: 'normalize' });
    expect(progress.map((m) => Number(m.id))).toEqual(progress.map((_, i) => i + 1));
    const last = msgs.at(-1)!;
    expect(last.event).toBe('done');
    expect(JSON.parse(last.data)).toEqual({ status: 'completed' });
    expect(progress.length).toBeGreaterThan(5);
  });

  it('with Last-Event-ID: 3 only replays events with seq > 3', async () => {
    const h = setup();
    const { body } = await h.post(validBody());
    await h.waitForStatus(body.id, 'completed');
    const all = parseSse(await (await h.call(`/api/generations/${body.id}/events`)).text()).filter((m) => m.event === 'message');

    const res = await h.call(`/api/generations/${body.id}/events`, { headers: { 'Last-Event-ID': '3' } });
    const msgs = parseSse(await res.text());
    const ids = msgs.filter((m) => m.event === 'message').map((m) => Number(m.id));
    expect(ids[0]).toBe(4);
    expect(ids).toEqual(all.map((m) => Number(m.id)).filter((n) => n > 3));
    expect(msgs.at(-1)!.event).toBe('done');
  });

  it('streams live events of a running job, sends heartbeats, then done and closes', async () => {
    const gate = deferred();
    const h = setup({ makeDeps: () => gatedDeps(gate.promise), heartbeatMs: 15 });
    const { body } = await h.post(validBody());
    await h.waitForStatus(body.id, 'running');

    const res = await h.call(`/api/generations/${body.id}/events`);
    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let text = '';
    const readUntil = async (needle: string) => {
      while (!text.includes(needle)) {
        const { value, done } = await reader.read();
        if (done) throw new Error(`stream ended before ${needle}; got ${text}`);
        text += decoder.decode(value);
      }
    };
    await readUntil('"node_start"');
    await readUntil(': heartbeat');
    gate.resolve();
    await readUntil('event: done');
    await readUntil('"status":"completed"');
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      text += decoder.decode(value);
    }
    const ids = parseSse(text).filter((m) => m.event === 'message').map((m) => Number(m.id));
    expect(ids).toEqual(ids.map((_, i) => i + 1));
  });

  it('404s for an unknown job', async () => {
    const h = setup();
    expect((await h.json('/api/generations/nope/events')).status).toBe(404);
  });
});

describe('interrupt and resume', () => {
  it('on startup marks running jobs interrupted and runs queued ones again; resume then completes the job', async () => {
    const h = setup();
    const now = Date.now();
    for (const [id, status] of [['job-running', 'running'], ['job-queued', 'queued'], ['job-done', 'completed']] as const) {
      h.db
        .prepare(`INSERT INTO generations (id, subject_type, status, request_json, result_json, error_json, created_at, updated_at) VALUES (?, 'personal', ?, ?, NULL, NULL, ?, ?)`)
        .run(id, status, JSON.stringify(validBody()), now, now);
    }
    h.runner.recoverOnStartup();
    expect((await h.json('/api/generations/job-running')).body.status).toBe('interrupted');
    expect((await h.json('/api/generations/job-done')).body.status).toBe('completed');
    const queued = await h.waitForStatus('job-queued', 'completed', 'failed');
    expect(queued.status).toBe('completed');

    const res = await h.json('/api/generations/job-running/resume', { method: 'POST', headers: JSON_HEADERS });
    expect(res.status).toBe(202);
    expect(res.body).toEqual({ id: 'job-running' });
    const done = await h.waitForStatus('job-running', 'completed', 'failed');
    expect(done.status).toBe('completed');
    expect(GenerationResultSchema.parse(done.result).id).toBe('job-running');
  });

  it('resume is only allowed for interrupted jobs (409) and unknown ids are 404', async () => {
    const h = setup();
    const { body } = await h.post(validBody());
    await h.waitForStatus(body.id, 'completed');
    const conflict = await h.json(`/api/generations/${body.id}/resume`, { method: 'POST', headers: JSON_HEADERS });
    expect(conflict.status).toBe(409);
    expect(conflict.body.error.code).toBe('INVALID_STATE');
    expect((await h.json('/api/generations/nope/resume', { method: 'POST', headers: JSON_HEADERS })).status).toBe(404);
  });

  it('continues from the checkpoint instead of starting over', async () => {
    const probe = createFixtureDeps();
    const h0 = setup({ makeDeps: () => probe });
    await h0.waitForStatus((await h0.post(validBody())).body.id, 'completed');
    const total = probe.llm.calls.length;

    const deps = [createFixtureDeps({ budget: { llmCalls: total - 1 } }), createFixtureDeps()];
    const h = setup({ makeDeps: () => deps.shift()! });
    const { body } = await h.post(validBody());
    await h.waitForStatus(body.id, 'failed');
    h.db.prepare(`UPDATE generations SET status = 'interrupted' WHERE id = ?`).run(body.id);

    const resumeDeps = deps[0]!;
    expect((await h.json(`/api/generations/${body.id}/resume`, { method: 'POST', headers: JSON_HEADERS })).status).toBe(202);
    const done = await h.waitForStatus(body.id, 'completed', 'failed');
    expect(done.status).toBe('completed');
    expect(done.error).toBeNull();
    expect(resumeDeps.llm.calls.map((c) => c.node)).toEqual(['audit']);
  });
});

void FIXTURE_TODAY;
