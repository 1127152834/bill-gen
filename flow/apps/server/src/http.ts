import type { Context } from 'hono';
import { bodyLimit } from 'hono/body-limit';

export const MAX_BODY_BYTES = 256 * 1024;
const MAX_ISSUES_SHOWN = 8;

/** The one error body shape: `{error:{code,message}}`. Never put secrets, model output or stacks in `message`. */
export const fail = (code: string, message: string) => ({ error: { code, message } });

/** `JSON.parse` that yields `undefined` instead of throwing. */
export const parseJson = (text: string | null): unknown => {
  if (text === null) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
};

/** Caps the request body at 256 KiB (413 VALIDATION_ERROR). */
export const limitBody = () =>
  bodyLimit({
    maxSize: MAX_BODY_BYTES,
    onError: (c) => c.json(fail('VALIDATION_ERROR', 'Request body is too large'), 413),
  });

/** Reads the body as JSON; `undefined` when it is not valid JSON. */
export async function readJsonBody(c: Context): Promise<unknown> {
  return parseJson(await c.req.text());
}

/** `path: message` pairs of the first few zod issues. Messages name the problem, never echo the given value. */
export function describeIssues(issues: readonly { path: PropertyKey[]; message: string }[]): string {
  return issues
    .slice(0, MAX_ISSUES_SHOWN)
    .map((i) => `${i.path.map(String).join('.') || '(body)'}: ${i.message}`)
    .join('; ');
}
