/**
 * Serves the built web UI (`apps/web/dist`) from the API process, so a deployment is one port.
 *
 * Only mounted when `WEB_DIST` points at a real directory: `pnpm dev` keeps using Vite, and the API
 * tests never grow a catch-all route they did not ask for. Unknown paths fall back to `index.html`
 * because the UI is a single-page app with client-side routes.
 */
import { existsSync, readFileSync, statSync } from 'node:fs';
import { extname, join, normalize, resolve, sep } from 'node:path';
import type { Hono } from 'hono';

const TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
  '.map': 'application/json; charset=utf-8',
};

function inside(root: string, target: string): boolean {
  return target === root || target.startsWith(root + sep);
}

/** Mounts `GET *` for a static directory. Returns false when the directory is missing. */
export function mountWeb(app: Hono, distDir: string): boolean {
  const root = resolve(distDir);
  if (!existsSync(root) || !statSync(root).isDirectory()) return false;
  const index = join(root, 'index.html');
  const hasIndex = existsSync(index);

  app.get('*', (c) => {
    // API paths belong to the API: falling back to index.html would answer them with HTML.
    if (c.req.path.startsWith('/api/')) return c.notFound();
    const requestPath = decodeURIComponent(new URL(c.req.url).pathname);
    const relative = normalize(requestPath).replace(/^([/\\])+/, '');
    const candidate = resolve(root, relative);
    if (inside(root, candidate) && existsSync(candidate) && statSync(candidate).isFile()) {
      return new Response(readFileSync(candidate), {
        headers: { 'Content-Type': TYPES[extname(candidate).toLowerCase()] ?? 'application/octet-stream' },
      });
    }
    if (!hasIndex) return c.notFound();
    return new Response(readFileSync(index), { headers: { 'Content-Type': TYPES['.html']! } });
  });
  return true;
}
