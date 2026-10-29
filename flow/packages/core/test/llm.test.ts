import { describe, expect, it } from 'vitest';
import { z } from 'zod';
import {
  Budget,
  BudgetExceededError,
  LlmCallError,
  LlmOutputError,
  createOpenAiStructuredLlm,
  FinancialModelSchema,
  parseJsonLoose,
  toProviderJsonSchema,
  type LlmEnv,
  type LlmMessage,
} from '../src/index.js';
import { createFakeLlm } from '../src/testing/index.js';

const SECRET = 'sk-live-TOPSECRET-1234567890';
const env: LlmEnv = {
  baseUrl: 'https://llm.example.test/v1',
  apiKey: SECRET,
  model: 'test-model',
  structuredMode: 'json_schema',
};
const schema = z.object({ ok: z.boolean() });
const messages: LlmMessage[] = [
  { role: 'system', content: 'sys' },
  { role: 'user', content: 'go' },
];

type Call = { method: string; messages: LlmMessage[]; signal: AbortSignal };

/** Scripted callModel: each entry is a value to return or an Error to throw. */
function scripted(steps: Array<unknown>) {
  const calls: Call[] = [];
  const callModel = async (method: string, _s: unknown, msgs: LlmMessage[], signal: AbortSignal) => {
    calls.push({ method, messages: msgs, signal });
    const step = steps[calls.length - 1];
    if (step instanceof Error) throw step;
    return step;
  };
  return { calls, callModel };
}

function instantSleep() {
  const delays: number[] = [];
  return { delays, sleep: async (ms: number) => void delays.push(ms) };
}

function httpError(status: number, message = `HTTP ${status}`): Error {
  return Object.assign(new Error(message), { status });
}

describe('parseJsonLoose', () => {
  it('strips ```json fences and surrounding prose', () => {
    expect(parseJsonLoose('Here you go:\n```json\n{"ok":true}\n```\nThanks')).toEqual({ ok: true });
  });
  it('cuts at the matching close and ignores trailing text', () => {
    expect(parseJsonLoose('{"a":{"b":[1,2]}} trailing')).toEqual({ a: { b: [1, 2] } });
  });
  it('handles arrays and leading prose', () => {
    expect(parseJsonLoose('result: [1, {"x": 2}] done')).toEqual([1, { x: 2 }]);
  });
  it('is not fooled by braces and escaped quotes inside strings', () => {
    expect(parseJsonLoose('x {"a":"}{ \\" ]","b":1} y')).toEqual({ a: '}{ " ]', b: 1 });
  });
  it('throws when no JSON value is present or it is unbalanced', () => {
    expect(() => parseJsonLoose('no json here')).toThrow();
    expect(() => parseJsonLoose('{"a": [1, 2')).toThrow();
  });
});

describe('Budget', () => {
  it('allows 60 llm calls and throws BudgetExceededError on the 61st', () => {
    const b = new Budget();
    for (let i = 0; i < 60; i++) b.chargeLlm();
    expect(() => b.chargeLlm()).toThrow(BudgetExceededError);
    try {
      b.chargeLlm();
    } catch (e) {
      expect((e as BudgetExceededError).code).toBe('BUDGET_EXCEEDED');
    }
  });
  it('allows 60 tavily queries and throws on the 61st', () => {
    const b = new Budget();
    for (let i = 0; i < 60; i++) b.chargeTavily();
    expect(() => b.chargeTavily()).toThrow(BudgetExceededError);
  });
  it('throws once the injected clock passes 900000ms', () => {
    let t = 1_000;
    const b = new Budget(undefined, () => t);
    b.chargeLlm();
    t += 900_000;
    b.chargeLlm(); // exactly at the limit is still fine
    t += 1;
    expect(() => b.chargeLlm()).toThrow(BudgetExceededError);
    expect(() => b.chargeTavily()).toThrow(BudgetExceededError);
  });
  it('accumulates usage including tokens', () => {
    const b = new Budget();
    b.chargeLlm(100);
    b.chargeLlm();
    b.addTokens(25);
    b.chargeTavily();
    expect(b.usage()).toEqual({ llmCalls: 2, tokens: 125, tavilyQueries: 1 });
  });
  it('honours custom limits', () => {
    const b = new Budget({ llmCalls: 1, tavilyQueries: 1, ms: 10 });
    b.chargeLlm();
    expect(() => b.chargeLlm()).toThrow(BudgetExceededError);
  });
});

describe('createOpenAiStructuredLlm', () => {
  it('maps LLM_STRUCTURED_MODE to the LangChain method', async () => {
    const seen: string[] = [];
    for (const [mode, method] of [
      ['json_schema', 'jsonSchema'],
      ['function_calling', 'functionCalling'],
      ['json_mode', 'jsonMode'],
    ] as const) {
      const { calls, callModel } = scripted([{ ok: true }]);
      const llm = createOpenAiStructuredLlm({ ...env, structuredMode: mode }, new Budget(), { callModel });
      await llm.invoke('persona', schema, messages);
      seen.push(calls[0]!.method);
      expect(calls[0]!.method).toBe(method);
    }
    expect(seen).toHaveLength(3);
  });

  it('returns schema-parsed output and passes messages and an abort signal through', async () => {
    const { calls, callModel } = scripted([{ ok: true }]);
    const llm = createOpenAiStructuredLlm(env, new Budget(), { callModel });
    await expect(llm.invoke('audit', schema, messages)).resolves.toEqual({ ok: true });
    expect(calls).toHaveLength(1);
    expect(calls[0]!.messages).toEqual(messages);
    expect(calls[0]!.signal).toBeInstanceOf(AbortSignal);
  });

  it('parses text output leniently (fenced JSON), e.g. in json_mode', async () => {
    const { callModel } = scripted(['sure!\n```json\n{"ok": true}\n```']);
    const llm = createOpenAiStructuredLlm({ ...env, structuredMode: 'json_mode' }, new Budget(), { callModel });
    await expect(llm.invoke('descriptors', schema, messages)).resolves.toEqual({ ok: true });
  });

  it('retries 429 twice then succeeds: 3 requests, backoff 1s then 2s', async () => {
    const { calls, callModel } = scripted([httpError(429), httpError(429), { ok: true }]);
    const { delays, sleep } = instantSleep();
    const budget = new Budget();
    const llm = createOpenAiStructuredLlm(env, budget, { callModel, sleep });
    await expect(llm.invoke('persona', schema, messages)).resolves.toEqual({ ok: true });
    expect(calls).toHaveLength(3);
    expect(delays).toEqual([1000, 2000]);
    expect(budget.usage().llmCalls).toBe(3);
  });

  it('gives up after 3 retries (4 attempts) with backoff 1s, 2s, 4s', async () => {
    const { calls, callModel } = scripted([httpError(503), httpError(500), httpError(502), httpError(503, `boom ${SECRET}`)]);
    const { delays, sleep } = instantSleep();
    const llm = createOpenAiStructuredLlm(env, new Budget(), { callModel, sleep });
    const err = await llm.invoke('persona', schema, messages).catch((e) => e);
    expect(err).toBeInstanceOf(LlmCallError);
    expect(err.code).toBe('LLM_CALL_FAILED');
    expect(err.status).toBe(503);
    expect(String(err.message)).not.toContain(SECRET);
    expect(calls).toHaveLength(4);
    expect(delays).toEqual([1000, 2000, 4000]);
  });

  it('retries timeouts and network errors', async () => {
    const timeout = Object.assign(new Error('The operation was aborted due to timeout'), { name: 'TimeoutError' });
    const reset = Object.assign(new Error('socket hang up'), { code: 'ECONNRESET' });
    const { calls, callModel } = scripted([timeout, reset, { ok: true }]);
    const { delays, sleep } = instantSleep();
    const llm = createOpenAiStructuredLlm(env, new Budget(), { callModel, sleep });
    await expect(llm.invoke('lifeModel', schema, messages)).resolves.toEqual({ ok: true });
    expect(calls).toHaveLength(2 + 1);
    expect(delays).toEqual([1000, 2000]);
  });

  it.each([400, 401, 403, 404])('does not retry HTTP %i and never leaks the key', async (status) => {
    const { calls, callModel } = scripted([httpError(status, `bad key ${SECRET} rejected`)]);
    const { delays, sleep } = instantSleep();
    const llm = createOpenAiStructuredLlm(env, new Budget(), { callModel, sleep });
    const err = await llm.invoke('persona', schema, messages).catch((e) => e);
    expect(err).toBeInstanceOf(LlmCallError);
    expect(err.status).toBe(status);
    expect(String(err.message)).not.toContain(SECRET);
    expect(JSON.stringify({ m: err.message, s: err.status, c: err.code })).not.toContain(SECRET);
    expect(calls).toHaveLength(1);
    expect(delays).toEqual([]);
  });

  it('repairs once: second request carries the zod error as a user message', async () => {
    const { calls, callModel } = scripted([{ ok: 'yes' }, { ok: true }]);
    const budget = new Budget();
    const llm = createOpenAiStructuredLlm(env, budget, { callModel });
    await expect(llm.invoke('extractFacts', schema, messages)).resolves.toEqual({ ok: true });
    expect(calls).toHaveLength(2);
    expect(calls[0]!.messages).toEqual(messages);
    const second = calls[1]!.messages;
    expect(second.slice(0, messages.length)).toEqual(messages);
    expect(second).toHaveLength(messages.length + 1);
    const repair = second[second.length - 1]!;
    expect(repair.role).toBe('user');
    expect(repair.content).toContain('ok'); // zod issue path
    expect(repair.content).toMatch(/expected boolean/i);
    expect(budget.usage().llmCalls).toBe(2);
  });

  it('repairs unparseable text too', async () => {
    const { calls, callModel } = scripted(['I cannot do that', '{"ok":true}']);
    const llm = createOpenAiStructuredLlm({ ...env, structuredMode: 'json_mode' }, new Budget(), { callModel });
    await expect(llm.invoke('audit', schema, messages)).resolves.toEqual({ ok: true });
    expect(calls).toHaveLength(2);
    expect(calls[1]!.messages.at(-1)!.role).toBe('user');
  });

  it('throws LlmOutputError after the single repair fails, without echoing model output', async () => {
    const leaky = { ok: 'LEAKED-MODEL-OUTPUT-' + 'x'.repeat(5000) };
    const { calls, callModel } = scripted([leaky, leaky, { ok: true }]);
    const llm = createOpenAiStructuredLlm(env, new Budget(), { callModel });
    const err = await llm.invoke('descriptors', schema, messages).catch((e) => e);
    expect(err).toBeInstanceOf(LlmOutputError);
    expect(err.code).toBe('LLM_OUTPUT_INVALID');
    expect(err.node).toBe('descriptors');
    expect(String(err.message)).not.toContain('LEAKED-MODEL-OUTPUT');
    expect(String(err.message).length).toBeLessThan(1000);
    expect(calls).toHaveLength(2);
  });

  it('charges the budget per attempt and aborts with BudgetExceededError', async () => {
    const budget = new Budget({ llmCalls: 2, tavilyQueries: 25, ms: 600_000 });
    const { calls, callModel } = scripted([httpError(429), httpError(429), { ok: true }]);
    const { sleep } = instantSleep();
    const llm = createOpenAiStructuredLlm(env, budget, { callModel, sleep });
    await expect(llm.invoke('persona', schema, messages)).rejects.toBeInstanceOf(BudgetExceededError);
    expect(calls).toHaveLength(2);
  });

  it('records token usage reported by the model call', async () => {
    const budget = new Budget();
    const callModel = async (_m: string, _s: unknown, _msgs: LlmMessage[], _sig: AbortSignal, onTokens?: (n: number) => void) => {
      onTokens?.(321);
      return { ok: true };
    };
    const llm = createOpenAiStructuredLlm(env, budget, { callModel });
    await llm.invoke('persona', schema, messages);
    expect(budget.usage()).toEqual({ llmCalls: 1, tokens: 321, tavilyQueries: 0 });
  });
});

describe('createOpenAiStructuredLlm: non-object root schemas', () => {
  const arraySchema = z.array(z.object({ n: z.number() }));

  it('sends an object-root schema to the model and unwraps the result', async () => {
    const sent: unknown[] = [];
    const callModel = async (_m: string, s: z.ZodType) => {
      sent.push(z.toJSONSchema(s));
      return { items: [{ n: 1 }, { n: 2 }] };
    };
    const llm = createOpenAiStructuredLlm(env, new Budget(), { callModel });
    await expect(llm.invoke('extractFacts', arraySchema, messages)).resolves.toEqual([{ n: 1 }, { n: 2 }]);
    expect((sent[0] as { type: string }).type).toBe('object');
  });

  it('tolerates a bare array in the reply (e.g. lenient text parsing)', async () => {
    const { callModel } = scripted(['here: [{"n": 3}]']);
    const llm = createOpenAiStructuredLlm(env, new Budget(), { callModel });
    await expect(llm.invoke('extractFacts', arraySchema, messages)).resolves.toEqual([{ n: 3 }]);
  });

  it('leaves object-root schemas untouched', async () => {
    const seen: z.ZodType[] = [];
    const callModel = async (_m: string, s: z.ZodType) => (seen.push(s), { ok: true });
    const llm = createOpenAiStructuredLlm(env, new Budget(), { callModel });
    await llm.invoke('audit', schema, messages);
    expect(seen[0]).toBe(schema);
  });
});

describe('toProviderJsonSchema', () => {
  const hasKey = (node: unknown, key: string): boolean =>
    node !== null && typeof node === 'object' && Object.entries(node).some(([k, v]) => k === key || hasKey(v, key));

  it('turns discriminated unions (oneOf, rejected by the provider) into anyOf', () => {
    const s = z.object({ rule: z.discriminatedUnion('kind', [z.object({ kind: z.literal('a') }), z.object({ kind: z.literal('b'), n: z.number() })]) });
    expect(hasKey(z.toJSONSchema(s), 'oneOf')).toBe(true); // the premise
    const out = toProviderJsonSchema(s);
    expect(hasKey(out, 'oneOf')).toBe(false);
    expect((out as { properties: { rule: { anyOf: unknown[] } } }).properties.rule.anyOf).toHaveLength(2);
  });

  it('handles the full FinancialModel schema: an object root without oneOf or $schema', () => {
    const out = toProviderJsonSchema(FinancialModelSchema) as Record<string, unknown>;
    expect(out.type).toBe('object');
    expect(hasKey(out, 'oneOf')).toBe(false);
    expect('$schema' in out).toBe(false);
  });
});

describe('createFakeLlm', () => {
  it('returns scripted responses per node in order and records calls', async () => {
    const fake = createFakeLlm({ persona: [{ ok: true }, { ok: false }], audit: [{ ok: true }] });
    await expect(fake.invoke('persona', schema, messages)).resolves.toEqual({ ok: true });
    await expect(fake.invoke('audit', schema, messages)).resolves.toEqual({ ok: true });
    await expect(fake.invoke('persona', schema, messages)).resolves.toEqual({ ok: false });
    expect(fake.calls.map((c) => c.node)).toEqual(['persona', 'audit', 'persona']);
    expect(fake.calls[0]!.messages).toEqual(messages);
  });
  it('runs responses through the schema and throws when they do not match', async () => {
    const fake = createFakeLlm({ persona: [{ ok: 'nope' }] });
    await expect(fake.invoke('persona', schema, messages)).rejects.toThrow();
  });
  it('throws a clear error when a node has no responses left', async () => {
    const fake = createFakeLlm({ persona: [{ ok: true }] });
    await fake.invoke('persona', schema, messages);
    await expect(fake.invoke('persona', schema, messages)).rejects.toThrow(/persona/);
    await expect(fake.invoke('audit', schema, messages)).rejects.toThrow(/audit/);
  });
});
