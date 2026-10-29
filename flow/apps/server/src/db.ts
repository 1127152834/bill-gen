import Database from 'better-sqlite3';
import { CACHE_TTL_MS, type MerchantFact, type ResearchCache } from '@bh/core';

export type Db = Database.Database;

export const GENERATION_STATUSES = ['queued', 'running', 'completed', 'completed_with_warnings', 'failed', 'interrupted'] as const;
export type GenerationStatus = (typeof GENERATION_STATUSES)[number];

/** Statuses after which a job produces no more events until it is resumed. */
export const SETTLED_STATUSES: readonly GenerationStatus[] = ['completed', 'completed_with_warnings', 'failed', 'interrupted'];

export type GenerationRow = {
  id: string;
  subject_type: string;
  status: GenerationStatus;
  request_json: string;
  result_json: string | null;
  error_json: string | null;
  created_at: number;
  updated_at: number;
};

/** Stored in `error_json`: the public code and message, plus what the job used before it failed. */
export type JobError = { code: string; message: string; usage?: { llmCalls: number; tokens: number; tavilyQueries: number } | undefined };

const SCHEMA = `
CREATE TABLE IF NOT EXISTS generations (
  id TEXT PRIMARY KEY,
  subject_type TEXT NOT NULL,
  status TEXT NOT NULL,
  request_json TEXT NOT NULL,
  result_json TEXT,
  error_json TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  generation_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  event_json TEXT NOT NULL,
  PRIMARY KEY (generation_id, seq)
);
CREATE TABLE IF NOT EXISTS research_cache (
  key TEXT PRIMARY KEY,
  facts_json TEXT NOT NULL,
  fetched_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);
`;

/** Opens (creating if needed) the SQLite database and its tables. `:memory:` is allowed. */
export function openDb(path: string): Db {
  const db = new Database(path);
  db.pragma('journal_mode = WAL');
  db.pragma('busy_timeout = 5000');
  db.exec(SCHEMA);
  return db;
}

export function insertGeneration(db: Db, row: { id: string; subjectType: string; request: unknown; now: number }): void {
  db.prepare(
    `INSERT INTO generations (id, subject_type, status, request_json, result_json, error_json, created_at, updated_at)
     VALUES (?, ?, 'queued', ?, NULL, NULL, ?, ?)`,
  ).run(row.id, row.subjectType, JSON.stringify(row.request), row.now, row.now);
}

export function getGeneration(db: Db, id: string): GenerationRow | undefined {
  return db.prepare(`SELECT * FROM generations WHERE id = ?`).get(id) as GenerationRow | undefined;
}

export type GenerationSummary = Pick<GenerationRow, 'id' | 'subject_type' | 'status' | 'request_json' | 'created_at'>;

/** Newest first (insertion order breaks ties within the same millisecond). */
export function listGenerations(
  db: Db,
  filter: { subjectType?: string; status?: GenerationStatus; limit: number },
): GenerationSummary[] {
  const where: string[] = [];
  const args: (string | number)[] = [];
  if (filter.subjectType !== undefined) (where.push('subject_type = ?'), args.push(filter.subjectType));
  if (filter.status !== undefined) (where.push('status = ?'), args.push(filter.status));
  const sql = `SELECT id, subject_type, status, request_json, created_at FROM generations
    ${where.length > 0 ? `WHERE ${where.join(' AND ')}` : ''} ORDER BY created_at DESC, rowid DESC LIMIT ?`;
  return db.prepare(sql).all(...args, filter.limit) as GenerationSummary[];
}

/** The profile's display name in a stored request: `name` (personal) or `legalName` (business); null before randomization. */
export function parseStoredName(requestJson: string): string | null {
  try {
    const profile = (JSON.parse(requestJson) as { profile?: { name?: unknown; legalName?: unknown } | null }).profile;
    const name = profile?.name ?? profile?.legalName;
    return typeof name === 'string' ? name : null;
  } catch {
    return null;
  }
}

/** Removes the job row and its stored events in one transaction. Checkpoints live in the saver's own tables. */
export function deleteGeneration(db: Db, id: string): void {
  db.transaction(() => {
    db.prepare(`DELETE FROM events WHERE generation_id = ?`).run(id);
    db.prepare(`DELETE FROM generations WHERE id = ?`).run(id);
  })();
}

export function setStatus(db: Db, id: string, status: GenerationStatus): void {
  db.prepare(`UPDATE generations SET status = ?, updated_at = ? WHERE id = ?`).run(status, Date.now(), id);
}

export function saveRequest(db: Db, id: string, request: unknown): void {
  db.prepare(`UPDATE generations SET request_json = ?, updated_at = ? WHERE id = ?`).run(JSON.stringify(request), Date.now(), id);
}

export function finishGeneration(
  db: Db,
  id: string,
  outcome: { status: GenerationStatus; result?: unknown; error?: JobError },
): void {
  db.prepare(`UPDATE generations SET status = ?, result_json = ?, error_json = ?, updated_at = ? WHERE id = ?`).run(
    outcome.status,
    outcome.result === undefined ? null : JSON.stringify(outcome.result),
    outcome.error === undefined ? null : JSON.stringify(outcome.error),
    Date.now(),
    id,
  );
}

/** Running jobs lose their in-memory worker on restart. Returns how many were marked. */
export function markRunningInterrupted(db: Db): number {
  return db.prepare(`UPDATE generations SET status = 'interrupted', updated_at = ? WHERE status = 'running'`).run(Date.now()).changes;
}

/** Ids of `queued` jobs, oldest first (they are run again after a restart). */
export function listQueuedIds(db: Db): string[] {
  return (db.prepare(`SELECT id FROM generations WHERE status = 'queued' ORDER BY created_at, rowid`).all() as { id: string }[]).map((r) => r.id);
}

/** Research cache (metro + category, 30 days) kept in the same database. */
export function createSqliteCache(db: Db): ResearchCache {
  const select = db.prepare(`SELECT facts_json, fetched_at FROM research_cache WHERE key = ?`);
  const upsert = db.prepare(
    `INSERT INTO research_cache (key, facts_json, fetched_at) VALUES (?, ?, ?)
     ON CONFLICT(key) DO UPDATE SET facts_json = excluded.facts_json, fetched_at = excluded.fetched_at`,
  );
  return {
    get(key, now) {
      const row = select.get(key) as { facts_json: string; fetched_at: number } | undefined;
      if (!row) return null;
      return {
        facts: JSON.parse(row.facts_json) as MerchantFact[],
        fetchedAt: row.fetched_at,
        stale: now - row.fetched_at > CACHE_TTL_MS,
      };
    },
    set(key, facts, now) {
      upsert.run(key, JSON.stringify(facts), now);
    },
  };
}

/** One row of the `settings` key-value table (JSON values, written from the browser UI). */
export type SettingRow = { key: string; value_json: string };

/** All stored settings. Unknown keys are ignored by the readers, so old rows never break a start. */
export function listSettings(db: Db): SettingRow[] {
  return db.prepare(`SELECT key, value_json FROM settings`).all() as SettingRow[];
}

/** Upserts one setting; `null` deletes the row (back to the env/config default). */
export function setSetting(db: Db, key: string, value: unknown): void {
  if (value === null) {
    db.prepare(`DELETE FROM settings WHERE key = ?`).run(key);
    return;
  }
  db.prepare(
    `INSERT INTO settings (key, value_json, updated_at) VALUES (?, ?, ?)
     ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at`,
  ).run(key, JSON.stringify(value), Date.now());
}
