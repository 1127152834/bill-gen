import { describe, expect, it } from 'vitest';
import { EnvError, loadEnv } from '../src/env.js';

const full = {
  LLM_BASE_URL: 'https://llm.example.test/v1',
  LLM_API_KEY: 'sk-secret-llm',
  TAVILY_API_KEY: 'tvly-secret',
  API_KEY: 'server-secret',
};

describe('loadEnv', () => {
  it('applies defaults', () => {
    const env = loadEnv(full);
    expect(env).toMatchObject({
      llmFake: false,
      llm: { baseUrl: full.LLM_BASE_URL, apiKey: full.LLM_API_KEY, model: 'gpt-6.1-sol', structuredMode: 'json_schema' },
      tavilyApiKey: full.TAVILY_API_KEY,
      apiKey: full.API_KEY,
      bankName: 'Harborline Bank',
      databasePath: './data/app.db',
      host: '127.0.0.1',
      port: 8797,
    });
  });

  it('names every missing variable but never prints a value', () => {
    let error: unknown;
    try {
      loadEnv({ LLM_BASE_URL: 'https://llm.example.test/v1', API_KEY: 'server-secret', PORT: 'not-a-port-SECRETISH' });
    } catch (e) {
      error = e;
    }
    expect(error).toBeInstanceOf(EnvError);
    const message = (error as Error).message;
    expect(message).toContain('LLM_API_KEY');
    expect(message).toContain('TAVILY_API_KEY');
    expect(message).toContain('PORT');
    for (const secret of ['server-secret', 'SECRETISH', 'llm.example.test']) expect(message).not.toContain(secret);
  });

  it('treats empty strings as unset', () => {
    expect(() => loadEnv({ ...full, LLM_API_KEY: '' })).toThrow(/LLM_API_KEY/);
    expect(loadEnv({ ...full, LLM_MODEL: '' }).llm?.model).toBe('gpt-6.1-sol');
  });

  it('does not need LLM or Tavily settings when LLM_FAKE=1, but still needs API_KEY', () => {
    const env = loadEnv({ LLM_FAKE: '1', API_KEY: 'k' });
    expect(env.llmFake).toBe(true);
    expect(env.llm).toBeUndefined();
    expect(() => loadEnv({ LLM_FAKE: '1' })).toThrow(/API_KEY/);
  });

  it('rejects an unknown structured mode and a bad port', () => {
    expect(() => loadEnv({ ...full, LLM_STRUCTURED_MODE: 'weird' })).toThrow(/LLM_STRUCTURED_MODE/);
    expect(() => loadEnv({ ...full, PORT: '70000' })).toThrow(/PORT/);
  });
});
