import { GenerationResultSchema, type BudgetLimits, type GraphDeps } from '@bh/core';
import { FIXTURE_TODAY, createFixtureDeps, fixtureRequest } from '@bh/core/testing';
import { createApp } from '../src/app.js';
import { loadEnv } from '../src/env.js';
import { openDb, type Db } from '../src/db.js';
import { createEventBus, type EventBus } from '../src/events.js';
import { createJobRunner, type JobRunner } from '../src/jobs.js';

export const API_KEY = 'test-api-key-0123456789';
export const LLM_SECRET = 'sk-test-LLM-SECRET-9f3a';

export type Harness = {
  db: Db;
  /** The Hono app, for paths the helpers do not wrap (static files, unknown routes). */
  app: ReturnType<typeof createApp>;
  bus: EventBus;
  runner: JobRunner;
  /** Request as a loopback client unless `addr` says otherwise. */
  call: (path: string, init?: RequestInit, addr?: string | null) => Promise<Response>;
  json: (path: string, init?: RequestInit, addr?: string | null) => Promise<{ status: number; body: any }>;
  post: (body: unknown, headers?: Record<string, string>) => Promise<{ status: number; body: any }>;
  waitForStatus: (id: string, ...statuses: string[]) => Promise<any>;
};

export type HarnessOptions = {
  /** Deps factory; `limits` (the persona route's own budget cap) are passed to the fixture budget by default. */
  makeDeps?: (limits?: Partial<BudgetLimits>) => GraphDeps;
  secrets?: string[];
  heartbeatMs?: number;
  concurrency?: number;
  /** Built web UI to serve (production single-port deploy). Omitted in tests by default. */
  webDist?: string;
};

export function setup(opts: HarnessOptions = {}): Harness {
  const db = openDb(':memory:');
  const bus = createEventBus(db);
  const makeDeps = opts.makeDeps ?? ((limits?: Partial<BudgetLimits>) => createFixtureDeps(limits ? { budget: limits } : {}));
  const runner = createJobRunner({
    db,
    bus,
    makeDeps: () => makeDeps(),
    concurrency: opts.concurrency ?? 2,
    secrets: opts.secrets ?? [LLM_SECRET, API_KEY],
  });
  const app = createApp({
    db,
    env: loadEnv({ LLM_FAKE: '1', API_KEY }),
    runner,
    bus,
    makeDeps,
    apiKey: API_KEY,
    today: () => FIXTURE_TODAY,
    ...(opts.heartbeatMs !== undefined && { heartbeatMs: opts.heartbeatMs }),
    ...(opts.webDist !== undefined && { webDist: opts.webDist }),
  });
  const call: Harness['call'] = (path, init, addr = '127.0.0.1') =>
    Promise.resolve(app.request(path, init, addr === null ? undefined : { incoming: { socket: { remoteAddress: addr } } }));
  const json: Harness['json'] = async (path, init, addr) => {
    const res = await call(path, init, addr);
    return { status: res.status, body: await res.json() };
  };
  const post: Harness['post'] = (body, headers = {}) =>
    json('/api/generations', {
      method: 'POST',
      headers: { 'content-type': 'application/json', ...headers },
      body: JSON.stringify(body),
    });
  const waitForStatus: Harness['waitForStatus'] = async (id, ...statuses) => {
    const deadline = Date.now() + 15_000;
    for (;;) {
      const { body } = await json(`/api/generations/${id}`);
      if (statuses.includes(body.status)) return body;
      if (Date.now() > deadline) throw new Error(`timeout waiting for ${statuses.join('|')}; last status ${body.status}`);
      await new Promise((r) => setTimeout(r, 15));
    }
  };
  return { db, app, bus, runner, call, json, post, waitForStatus };
}

export { GenerationResultSchema, createFixtureDeps, fixtureRequest, FIXTURE_TODAY };

/** Deps whose research search waits for `gate`, so jobs can be held mid-run. */
export function gatedDeps(gate: Promise<void>): GraphDeps {
  const deps = createFixtureDeps();
  const search = deps.search.search.bind(deps.search);
  deps.search.search = async (q) => {
    await gate;
    return search(q);
  };
  return deps;
}

export function deferred(): { promise: Promise<void>; resolve: () => void } {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => (resolve = r));
  return { promise, resolve };
}

export type SseMessage = { id?: string; event: string; data: string };

export function parseSse(text: string): SseMessage[] {
  const out: SseMessage[] = [];
  for (const block of text.split('\n\n')) {
    const lines = block.split('\n').filter((l) => l !== '' && !l.startsWith(':'));
    if (lines.length === 0) continue;
    const msg: SseMessage = { event: 'message', data: '' };
    for (const line of lines) {
      const i = line.indexOf(':');
      const field = line.slice(0, i);
      const value = line.slice(i + 1).replace(/^ /, '');
      if (field === 'id') msg.id = value;
      else if (field === 'event') msg.event = value;
      else if (field === 'data') msg.data += (msg.data ? '\n' : '') + value;
    }
    out.push(msg);
  }
  return out;
}
