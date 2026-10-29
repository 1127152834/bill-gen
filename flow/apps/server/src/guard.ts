import type { MiddlewareHandler } from 'hono';

const LOCAL_ORIGIN = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:\d{1,5})?$/;
/** `Host` names a loopback client may use. Anything else (a rebound DNS name) cannot ride the keyless bypass. */
const LOCAL_HOSTNAMES = new Set(['localhost', '127.0.0.1', '[::1]']);
const BODY_METHODS = new Set(['POST', 'PUT', 'PATCH']);

const fail = (code: string, message: string) => ({ error: { code, message } });

/**
 * Cross-site request protection for the keyless loopback client. Browsers attach `Origin` to
 * cross-site requests; a page on another site must not be able to drive the local API.
 * Requests without `Origin` (curl, server-to-server) pass.
 */
export function rejectForeignOrigin(): MiddlewareHandler {
  return async (c, next) => {
    const origin = c.req.header('Origin');
    if (origin !== undefined && !LOCAL_ORIGIN.test(origin)) {
      return c.json(fail('FORBIDDEN_ORIGIN', 'Cross-origin requests are not allowed'), 403);
    }
    return next();
  };
}

/**
 * Requests with a body (POST, PUT, PATCH) must be `application/json`. That is not a CORS-safelisted type, so a
 * cross-site page cannot send one without a preflight, which fails because we send no CORS headers.
 * DELETE is not a CORS-safelisted method, so it already needs a (failing) preflight and carries no body.
 */
export function requireJsonForMutations(): MiddlewareHandler {
  return async (c, next) => {
    if (BODY_METHODS.has(c.req.method)) {
      const type = (c.req.header('Content-Type') ?? '').split(';')[0]!.trim().toLowerCase();
      if (type !== 'application/json') {
        return c.json(fail('UNSUPPORTED_MEDIA_TYPE', 'Content-Type must be application/json'), 415);
      }
    }
    return next();
  };
}

/**
 * DNS-rebinding guard for the keyless loopback client: a hostile page whose DNS name flips to 127.0.0.1 is same-origin
 * to the browser and sends no `Origin` on simple GETs, but its `Host` is still the attacker's name. The server builds
 * the request URL from the `Host` header, so its hostname is what the client addressed. Any port is fine.
 */
export function hasLocalHost(url: string): boolean {
  try {
    return LOCAL_HOSTNAMES.has(new URL(url).hostname);
  } catch {
    return false;
  }
}
