import type { z } from 'zod';
import type { LlmMessage, LlmNode, StructuredLlm } from '../llm.js';

export type FakeLlm = StructuredLlm & { calls: { node: LlmNode; messages: LlmMessage[] }[] };

/**
 * A preset answer, or a function that picks one from the call context. The persona node is the
 * reason this exists: personal and business requests hit the same node but need different
 * fixtures, and only the call itself (schema + messages) says which one is wanted.
 */
export type FakeAnswer = unknown | ((ctx: { schema: z.ZodType<unknown>; messages: LlmMessage[] }) => unknown);

/**
 * Deterministic stand-in for the model client: returns the preset responses of
 * each node in order, parsed through the caller's schema (so a bad fixture fails
 * exactly like a bad model answer would). Imports no network code.
 */
export function createFakeLlm(responses: Partial<Record<LlmNode, FakeAnswer[]>>): FakeLlm {
  const cursor = new Map<LlmNode, number>();
  const calls: FakeLlm['calls'] = [];
  return {
    calls,
    async invoke<T>(node: LlmNode, schema: z.ZodType<T>, messages: LlmMessage[]): Promise<T> {
      calls.push({ node, messages });
      const queue = responses[node] ?? [];
      const i = cursor.get(node) ?? 0;
      if (i >= queue.length) {
        throw new Error(`createFakeLlm: no response left for node "${node}" (call #${i + 1}, ${queue.length} preset)`);
      }
      cursor.set(node, i + 1);
      const entry = queue[i];
      const answer = typeof entry === 'function' ? (entry as (ctx: { schema: z.ZodType<unknown>; messages: LlmMessage[] }) => unknown)({ schema, messages }) : entry;
      return schema.parse(answer);
    },
  };
}
