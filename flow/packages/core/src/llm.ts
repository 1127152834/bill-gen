import { z } from 'zod';
import type { Budget } from './budget.js';
import { BudgetExceededError } from './budget.js';

export type LlmNode = 'persona' | 'planResearch' | 'extractFacts' | 'lifeModel' | 'descriptors' | 'audit' | 'render';
export type LlmMessage = { role: 'system' | 'user'; content: string };

export interface StructuredLlm {
  invoke<T>(node: LlmNode, schema: z.ZodType<T>, messages: LlmMessage[]): Promise<T>;
}

export type LlmEnv = {
  baseUrl: string;
  apiKey: string;
  model: string;
  structuredMode: 'json_schema' | 'function_calling' | 'json_mode';
};

/** `withStructuredOutput` method names of LangChain. */
export type ModelMethod = 'jsonSchema' | 'functionCalling' | 'jsonMode';

export type CallModel = (
  method: ModelMethod,
  schema: z.ZodType,
  messages: LlmMessage[],
  signal: AbortSignal,
  /** Report token usage when the provider returns it. */
  onTokens?: (tokens: number) => void,
) => Promise<unknown>;

export type LlmDeps = {
  /** Replaces the LangChain call. Returns parsed data or raw model text. */
  callModel?: CallModel;
  sleep?: (ms: number) => Promise<void>;
};

const METHOD_BY_MODE: Record<LlmEnv['structuredMode'], ModelMethod> = {
  json_schema: 'jsonSchema',
  function_calling: 'functionCalling',
  json_mode: 'jsonMode',
};

const CALL_TIMEOUT_MS = 120_000;
const BACKOFF_MS = [1000, 2000, 4000] as const;

/** The model call failed (HTTP error, timeout, network) and will not be retried further. */
export class LlmCallError extends Error {
  readonly code = 'LLM_CALL_FAILED';
  readonly status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = 'LlmCallError';
    if (status !== undefined) this.status = status;
  }
}

/** The model answered, but not in the requested shape, even after one repair attempt. */
export class LlmOutputError extends Error {
  readonly code = 'LLM_OUTPUT_INVALID';
  readonly node: LlmNode;
  constructor(node: LlmNode, reason: string) {
    super(`Model output for "${node}" is invalid after one repair attempt: ${reason}`);
    this.name = 'LlmOutputError';
    this.node = node;
  }
}

/**
 * Extract the first JSON object/array from model text: tolerates ```json fences,
 * prose before/after, and trailing text after the matching close bracket.
 */
export function parseJsonLoose(text: string): unknown {
  const fenced = /```(?:json)?\s*([\s\S]*?)```/i.exec(text)?.[1];
  for (const source of fenced === undefined ? [text] : [fenced, text]) {
    for (let start = nextOpen(source, 0); start !== -1; start = nextOpen(source, start + 1)) {
      const end = matchingClose(source, start);
      if (end === -1) continue;
      try {
        return JSON.parse(source.slice(start, end + 1));
      } catch {
        // not JSON (e.g. prose in braces) — try the next opening bracket
      }
    }
  }
  throw new SyntaxError('no JSON object or array found in model output');
}

function nextOpen(s: string, from: number): number {
  for (let i = from; i < s.length; i++) if (s[i] === '{' || s[i] === '[') return i;
  return -1;
}

/** Index of the bracket closing the one at `start`, string- and escape-aware; -1 if unbalanced. */
function matchingClose(s: string, start: number): number {
  const stack: string[] = [];
  let inString = false;
  for (let i = start; i < s.length; i++) {
    const c = s[i]!;
    if (inString) {
      if (c === '\\') i++;
      else if (c === '"') inString = false;
    } else if (c === '"') inString = true;
    else if (c === '{') stack.push('}');
    else if (c === '[') stack.push(']');
    else if (c === '}' || c === ']') {
      if (stack.pop() !== c) return -1;
      if (stack.length === 0) return i;
    }
  }
  return -1;
}

export function createOpenAiStructuredLlm(env: LlmEnv, budget: Budget, deps: LlmDeps = {}): StructuredLlm {
  const method = METHOD_BY_MODE[env.structuredMode];
  const callModel = deps.callModel ?? createLangChainCall(env);
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));

  /** One logical request: every real attempt is charged; transient failures back off and retry. */
  async function request(schema: z.ZodType, messages: LlmMessage[]): Promise<unknown> {
    for (let attempt = 0; ; attempt++) {
      budget.chargeLlm();
      try {
        return await callModel(method, schema, messages, AbortSignal.timeout(CALL_TIMEOUT_MS), (n) =>
          budget.addTokens(n),
        );
      } catch (err) {
        if (err instanceof BudgetExceededError) throw err;
        const delay = BACKOFF_MS[attempt];
        if (delay === undefined || !isRetryable(err)) throw toCallError(err, env.apiKey);
        await sleep(delay);
      }
    }
  }

  return {
    async invoke<T>(node: LlmNode, schema: z.ZodType<T>, messages: LlmMessage[]): Promise<T> {
      const root = toObjectRoot(schema);
      const first = validate(root.schema, await request(root.schema, messages), root.coerce);
      if (first.ok) return root.unwrap(first.value);
      const repair: LlmMessage = {
        role: 'user',
        content:
          `Your previous reply did not match the required JSON format: ${truncate(first.reason, 1500)}\n` +
          'Reply again with only the corrected JSON, matching the schema exactly.',
      };
      const second = validate(root.schema, await request(root.schema, [...messages, repair]), root.coerce);
      if (second.ok) return root.unwrap(second.value);
      throw new LlmOutputError(node, redact(truncate(second.reason, 300), env.apiKey));
    },
  };
}

/**
 * Providers (OpenAI json_schema in particular) only accept an object at the root of the response
 * schema, so a non-object schema (e.g. an array) travels as `{ items: <schema> }` and is unwrapped
 * again; a bare value in the reply is tolerated.
 */
function toObjectRoot<T>(schema: z.ZodType<T>): {
  schema: z.ZodType;
  coerce: (data: unknown) => unknown;
  unwrap: (value: unknown) => T;
} {
  let isObject = true;
  try {
    isObject = (z.toJSONSchema(schema) as { type?: unknown }).type === 'object';
  } catch {
    // not representable as JSON Schema — leave as is
  }
  if (isObject) return { schema, coerce: (data) => data, unwrap: (value) => value as T };
  return {
    schema: z.object({ items: schema }),
    coerce: (data) => (data !== null && typeof data === 'object' && !Array.isArray(data) ? data : { items: data }),
    unwrap: (value) => (value as { items: T }).items,
  };
}

type Validated<T> = { ok: true; value: T } | { ok: false; reason: string };

/** Zod-parse model output; text is first run through `parseJsonLoose`. */
function validate<T>(schema: z.ZodType<T>, output: unknown, coerce: (data: unknown) => unknown): Validated<T> {
  let data = output;
  if (typeof output === 'string') {
    try {
      data = parseJsonLoose(output);
    } catch (err) {
      return { ok: false, reason: `reply is not valid JSON (${(err as Error).message})` };
    }
  }
  const parsed = schema.safeParse(coerce(data));
  if (parsed.success) return { ok: true, value: parsed.data };
  const reason = parsed.error.issues
    .map((issue) => `${issue.path.length ? issue.path.join('.') : '(root)'}: ${issue.message}`)
    .join('; ');
  return { ok: false, reason };
}

const NETWORK_CODES = new Set(['ECONNRESET', 'ECONNREFUSED', 'ETIMEDOUT', 'ENOTFOUND', 'EAI_AGAIN', 'EPIPE']);
const TRANSIENT_NAMES = new Set([
  'TimeoutError',
  'AbortError',
  'APIConnectionError',
  'APIConnectionTimeoutError',
  'FetchError',
]);

function statusOf(err: unknown): number | undefined {
  const e = err as { status?: unknown; statusCode?: unknown; response?: { status?: unknown }; cause?: unknown } | null;
  for (const candidate of [e?.status, e?.statusCode, e?.response?.status]) {
    if (typeof candidate === 'number') return candidate;
  }
  return e?.cause && e.cause !== err ? statusOf(e.cause) : undefined;
}

function isTransientNetworkError(err: unknown, depth = 0): boolean {
  if (!(err instanceof Error) || depth > 3) return false;
  const code = (err as { code?: unknown }).code;
  if (typeof code === 'string' && (NETWORK_CODES.has(code) || code.startsWith('UND_ERR'))) return true;
  if (TRANSIENT_NAMES.has(err.name) || TRANSIENT_NAMES.has(err.constructor.name)) return true;
  if (/timed? ?out|fetch failed|socket hang up|network/i.test(err.message)) return true;
  return isTransientNetworkError(err.cause, depth + 1);
}

/** 429, 5xx and 408 are transient; any other HTTP status is a hard failure; without status, only network/timeout errors are. */
function isRetryable(err: unknown): boolean {
  const status = statusOf(err);
  if (status !== undefined) return status === 429 || status === 408 || status >= 500;
  return isTransientNetworkError(err);
}

function toCallError(err: unknown, apiKey: string): LlmCallError {
  const status = statusOf(err);
  const reason = redact(truncate(err instanceof Error ? err.message : String(err), 200), apiKey);
  return new LlmCallError(`LLM call failed${status === undefined ? '' : ` (HTTP ${status})`}: ${reason}`, status);
}

function truncate(text: string, max: number): string {
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > max ? `${flat.slice(0, max)}…` : flat;
}

/** Strip the configured key and anything shaped like a secret from text destined for errors. */
function redact(text: string, apiKey: string): string {
  let out = apiKey ? text.split(apiKey).join('***') : text;
  out = out.replace(/Bearer\s+[A-Za-z0-9._~+/=-]+/gi, 'Bearer ***');
  return out.replace(/\bsk-[A-Za-z0-9_-]{6,}/g, 'sk-***');
}

/** Default model call: ChatOpenAI via LangChain, loaded lazily so importing core stays light. */
function createLangChainCall(env: LlmEnv): CallModel {
  let chat: Promise<import('@langchain/openai').ChatOpenAI> | undefined;
  const getChat = () =>
    (chat ??= import('@langchain/openai').then(
      ({ ChatOpenAI }) =>
        new ChatOpenAI({
          model: env.model,
          apiKey: env.apiKey,
          configuration: { baseURL: env.baseUrl },
          timeout: CALL_TIMEOUT_MS,
          maxRetries: 0,
        }),
    ));

  return async (method, schema, messages, signal, onTokens) => {
    const llm = await getChat();
    // includeRaw: if LangChain's own parsing fails we still get the raw text for lenient parsing.
    // json_schema: send plain JSON Schema (non-strict) — see toProviderJsonSchema; our zod check still applies.
    const wire = method === 'jsonSchema' ? toProviderJsonSchema(schema) : schema;
    const structured = llm.withStructuredOutput(wire as z.ZodObject, { method, includeRaw: true, name: 'result' });
    const sent = method === 'jsonMode' ? [jsonModeHint(schema), ...messages] : messages;
    const result = (await structured.invoke(sent, { signal })) as {
      raw?: { content?: unknown; tool_calls?: { args?: unknown }[]; usage_metadata?: { total_tokens?: number } };
      parsed?: unknown;
    };
    const total = result.raw?.usage_metadata?.total_tokens;
    if (typeof total === 'number') onTokens?.(total);
    const text = contentToText(result.raw?.content);
    if (method === 'functionCalling') return result.parsed ?? result.raw?.tool_calls?.[0]?.args ?? text;
    return text.trim() ? text : result.parsed;
  };
}

/**
 * The JSON Schema sent for `json_schema` mode. LangChain sends zod v4 schemas with `strict: true`,
 * and strict mode rejects what our domain schemas need (`oneOf` from discriminated unions,
 * optional fields), so a plain JSON Schema object is sent instead (LangChain leaves it non-strict)
 * with `oneOf` rewritten to the equivalent `anyOf` and no `$schema` key. The reply is still
 * validated (and repaired once) against the zod schema.
 */
export function toProviderJsonSchema(schema: z.ZodType): Record<string, unknown> {
  const rewrite = (node: unknown): unknown => {
    if (Array.isArray(node)) return node.map(rewrite);
    if (node === null || typeof node !== 'object') return node;
    return Object.fromEntries(
      Object.entries(node)
        .filter(([k]) => k !== '$schema')
        .map(([k, v]) => [k === 'oneOf' ? 'anyOf' : k, rewrite(v)]),
    );
  };
  return rewrite(z.toJSONSchema(schema, { unrepresentable: 'any' })) as Record<string, unknown>;
}

/** JSON mode only guarantees "some JSON object", so the schema must be in the prompt (and it must say "JSON"). */
function jsonModeHint(schema: z.ZodType): LlmMessage {
  return {
    role: 'system',
    content: `Respond with a single JSON object that conforms to this JSON Schema, with no extra text:\n${JSON.stringify(z.toJSONSchema(schema))}`,
  };
}

function contentToText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .map((part) => (typeof part === 'string' ? part : ((part as { text?: unknown })?.text as string | undefined) ?? ''))
    .join('');
}
