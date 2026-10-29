import { describe, expect, it } from 'vitest';
import { CACHE_TTL_MS, type MerchantFact } from '@bh/core';
import { openDb, createSqliteCache } from '../src/db.js';

describe('openDb', () => {
  it('creates the generations, events and research_cache tables', () => {
    const db = openDb(':memory:');
    const names = db.prepare(`SELECT name FROM sqlite_master WHERE type = 'table'`).all().map((r) => (r as { name: string }).name);
    expect(names).toEqual(expect.arrayContaining(['generations', 'events', 'research_cache']));
    const cols = db.prepare(`PRAGMA table_info(generations)`).all().map((r) => (r as { name: string }).name);
    expect(cols).toEqual(['id', 'subject_type', 'status', 'request_json', 'result_json', 'error_json', 'created_at', 'updated_at']);
  });

  it('is idempotent when opened twice on one file', async () => {
    const { mkdtempSync } = await import('node:fs');
    const { join } = await import('node:path');
    const { tmpdir } = await import('node:os');
    const file = join(mkdtempSync(join(tmpdir(), 'bh-db-')), 'a.db');
    openDb(file).close();
    expect(() => openDb(file).close()).not.toThrow();
  });
});

describe('createSqliteCache', () => {
  const facts = [{ id: 'f1', category: 'grocery', name: 'HEB' }] as unknown as MerchantFact[];

  it('returns null for a miss, fresh facts within 30 days and stale ones after', () => {
    const cache = createSqliteCache(openDb(':memory:'));
    expect(cache.get('austin:grocery', 1000)).toBeNull();
    cache.set('austin:grocery', facts, 1000);
    expect(cache.get('austin:grocery', 1000 + CACHE_TTL_MS)).toEqual({ facts, fetchedAt: 1000, stale: false });
    expect(cache.get('austin:grocery', 1001 + CACHE_TTL_MS)?.stale).toBe(true);
  });

  it('overwrites an existing key', () => {
    const cache = createSqliteCache(openDb(':memory:'));
    cache.set('k', facts, 1);
    cache.set('k', [], 2);
    expect(cache.get('k', 2)).toEqual({ facts: [], fetchedAt: 2, stale: false });
  });
});
