import { createHash, timingSafeEqual } from 'node:crypto';
import type { MiddlewareHandler } from 'hono';
import { hasLocalHost } from './guard.js';

const LOOPBACK = new Set(['127.0.0.1', '::1', '::ffff:127.0.0.1']);

export const isLoopback = (address: string | undefined): boolean => address !== undefined && LOOPBACK.has(address);

/** Constant-time string comparison; differing lengths do not throw (both sides are hashed first). */
export function safeEqual(a: string, b: string): boolean {
  const digest = (s: string) => createHash('sha256').update(s).digest();
  return timingSafeEqual(digest(a), digest(b));
}

export function unauthorized() {
  return { error: { code: 'UNAUTHORIZED', message: 'Missing or invalid X-API-Key' } };
}

/**
 * A valid `X-API-Key` always passes. Loopback clients (the local web UI) pass without one, but only when they
 * addressed us by a local `Host` (403 FORBIDDEN_HOST otherwise, against DNS rebinding); everyone else gets 401.
 * An unknown remote address counts as remote. Proxy headers are deliberately not trusted.
 */
export function requireApiKey(opts: {
  apiKey: string;
  remoteAddr: (c: Parameters<MiddlewareHandler>[0]) => string | undefined;
}): MiddlewareHandler {
  return async (c, next) => {
    const given = c.req.header('X-API-Key');
    if (opts.apiKey !== '' && given !== undefined && safeEqual(given, opts.apiKey)) return next();
    if (isLoopback(opts.remoteAddr(c))) {
      if (hasLocalHost(c.req.url)) return next();
      return c.json({ error: { code: 'FORBIDDEN_HOST', message: 'Host header is not a local address' } }, 403);
    }
    return c.json(unauthorized(), 401);
  };
}
