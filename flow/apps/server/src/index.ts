import { mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { serve } from '@hono/node-server';
import { config as loadDotenv } from 'dotenv';
import { createApp } from './app.js';
import { openDb } from './db.js';
import { makeDeps } from './deps.js';
import { EnvError, loadEnv, secretsOf } from './env.js';
import { resolveLlm } from './settings.js';
import { createEventBus } from './events.js';
import { createJobRunner } from './jobs.js';

const REPO_ROOT = fileURLToPath(new URL('../../../', import.meta.url));

function main(): void {
  loadDotenv({ path: resolve(REPO_ROOT, '.env'), quiet: true });
  const env = loadEnv();

  // A relative DATABASE_PATH is relative to the repo root (where `.env` and `data/` live).
  const databasePath = resolve(REPO_ROOT, env.databasePath);
  mkdirSync(dirname(databasePath), { recursive: true });
  const db = openDb(databasePath);

  const bus = createEventBus(db);
  const deps = makeDeps(env, db);
  const runner = createJobRunner({
    db,
    bus,
    makeDeps: (limits, accounts) => deps(limits, accounts),
    concurrency: 2,
    logProgress: env.logProgress,
    logResearch: env.logResearch,
    secrets: secretsOf(env),
  });
  runner.recoverOnStartup();

  const webDist = process.env.WEB_DIST ? resolve(REPO_ROOT, process.env.WEB_DIST) : undefined;
  const app = createApp({ db, env, runner, bus, makeDeps: deps, apiKey: env.apiKey, ...(webDist ? { webDist } : {}) });
  if (webDist) console.log(`[server] serving web UI from ${webDist}`);
  const server = serve({ fetch: app.fetch, hostname: env.host, port: env.port }, (info) => {
    console.log(`[server] listening on http://${info.address}:${info.port}${resolveLlm(db, env).ready ? '' : ' (offline fixtures: no model configured)'}`);
  });

  const shutdown = (): void => {
    server.close(() => {
      db.close();
      process.exit(0);
    });
    setTimeout(() => process.exit(0), 2000).unref();
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}

try {
  main();
} catch (err) {
  // EnvError names variables only; anything else is reduced to its type.
  console.error(err instanceof EnvError ? `[server] ${err.message}` : `[server] failed to start: ${err instanceof Error ? err.name : 'unknown error'}`);
  process.exit(1);
}
