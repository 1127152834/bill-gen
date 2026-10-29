import { describe, expect, it } from 'vitest';
import { openDb } from '../src/db.js';
import { loadEnv } from '../src/env.js';
import { maskSecret, resolveLlm, saveSettings } from '../src/settings.js';
import { setup } from './helpers.js';

const jsonPut = (body: unknown): RequestInit => ({
  method: 'PUT',
  body: JSON.stringify(body),
  headers: { 'content-type': 'application/json' },
});

const env = loadEnv({ LLM_FAKE: '1', API_KEY: 'k', BANK_DISPLAY_NAME: 'Bank of America' });

describe('resolveLlm', () => {
  it('uses the env defaults when nothing is stored', () => {
    const db = openDb(':memory:');
    const eff = resolveLlm(db, env);
    expect(eff.ready).toBe(false);
    expect(eff.llmFake).toBe(true);
    expect(eff.source).toEqual({ apiKey: 'none', baseUrl: 'none', tavily: 'none' });
  });

  it('is ready once baseUrl + apiKey + tavily key are stored, and stops using fixtures', () => {
    const db = openDb(':memory:');
    const eff = saveSettings(
      db,
      { baseUrl: 'https://llm.example.test/v1', apiKey: 'sk-secret-llm', tavilyApiKey: 'tvly-secret' },
      env,
    );
    expect(eff.ready).toBe(true);
    expect(eff.llmFake).toBe(false);
    expect(eff.llm).toEqual({
      baseUrl: 'https://llm.example.test/v1',
      apiKey: 'sk-secret-llm',
      model: 'gpt-4o',
      structuredMode: 'json_schema',
    });
    expect(eff.source).toEqual({ apiKey: 'db', baseUrl: 'db', tavily: 'db' });
  });

  it('lets the stored value win over env, and null falls back to env', () => {
    const withEnv = loadEnv({
      API_KEY: 'k',
      LLM_BASE_URL: 'https://env.example.test/v1',
      LLM_API_KEY: 'sk-from-env',
      TAVILY_API_KEY: 'tvly-from-env',
    });
    const db = openDb(':memory:');
    const stored = saveSettings(db, { model: 'model-from-ui' }, withEnv);
    expect(stored.llm?.model).toBe('model-from-ui');
    expect(stored.llm?.apiKey).toBe('sk-from-env');
    expect(stored.source.apiKey).toBe('env');
    const cleared = saveSettings(db, { apiKey: null }, withEnv);
    expect(cleared.llm?.apiKey).toBe('sk-from-env');
  });

  it('does not flip back to fixtures when a key is cleared but env still has one', () => {
    const withEnv = loadEnv({
      API_KEY: 'k',
      LLM_BASE_URL: 'https://env.example.test/v1',
      LLM_API_KEY: 'sk-from-env',
      TAVILY_API_KEY: 'tvly-from-env',
    });
    const db = openDb(':memory:');
    saveSettings(db, { llmFake: true }, withEnv);
    expect(resolveLlm(db, withEnv).llmFake).toBe(true);
    const back = saveSettings(db, { llmFake: false }, withEnv);
    expect(back.llmFake).toBe(false);
    expect(back.ready).toBe(true);
  });

  it('treats an empty API key as "leave it alone"', () => {
    const db = openDb(':memory:');
    saveSettings(db, { baseUrl: 'https://llm.example.test/v1', apiKey: 'sk-keep-me', tavilyApiKey: 'tvly' }, env);
    const eff = saveSettings(db, { apiKey: '' }, env);
    expect(eff.llm?.apiKey).toBe('sk-keep-me');
  });
});

describe('maskSecret', () => {
  it('keeps the prefix and the last four characters', () => {
    expect(maskSecret('sk-abcdefghijklmnop1234')).toBe('sk-ab…1234');
  });

  it('hides short secrets entirely', () => {
    expect(maskSecret('short')).toBe('••••');
    expect(maskSecret(undefined)).toBeNull();
  });
});

describe('settings routes', () => {
  it('GET returns the effective config with masked keys only', async () => {
    const h = setup();
    await h.json('/api/settings', jsonPut({ baseUrl: 'https://llm.example.test/v1', apiKey: 'sk-secret-llm', tavilyApiKey: 'tvly-secret', model: 'm1' }));
    const { status, body } = await h.json('/api/settings');
    expect(status).toBe(200);
    expect(body).toMatchObject({ ready: true, llmFake: false, model: 'm1', hasApiKey: true, hasTavilyKey: true, apiKeyMasked: 'sk-se…-llm' });
    expect(JSON.stringify(body)).not.toContain('sk-secret-llm');
  });

  it('PUT rejects a malformed URL instead of storing it', async () => {
    const h = setup();
    const { status, body } = await h.json('/api/settings', jsonPut({ baseUrl: 'not-a-url' }));
    expect(status).toBe(400);
    expect(body.error.code).toBe('VALIDATION_ERROR');
    expect(body.error.message).toContain('baseUrl');
  });

  it('POST /test refuses an incomplete config and names what is missing', async () => {
    const h = setup();
    await h.json('/api/settings', jsonPut({ baseUrl: 'https://llm.example.test/v1' }));
    const { status, body } = await h.json('/api/settings/test', { method: 'POST', body: '{}', headers: { 'content-type': 'application/json' } });
    expect(status).toBe(400);
    expect(body.error.code).toBe('SETTINGS_INCOMPLETE');
    expect(body.error.message).toContain('apiKey');
    expect(body.error.message).toContain('tavilyApiKey');
  });
});
