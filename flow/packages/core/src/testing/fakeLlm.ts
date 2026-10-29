import type { z } from 'zod';
import type { LlmMessage, LlmNode, StructuredLlm } from '../llm.js';

export type FakeLlm = StructuredLlm & { calls: { node: LlmNode; messages: LlmMessage[] }[] };

/**
 * Deterministic stand-in for the model client: returns the preset responses of
 * each node in order, parsed through the caller's schema (so a bad fixture fails
 * exactly like a bad model answer would). Imports no network code.
 */
export function createFakeLlm(responses: Partial<Record<LlmNode, unknown[]>>): FakeLlm {
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
      return schema.parse(queue[i]);
    },
  };
}
