import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'

const repoRoot = fileURLToPath(new URL('../..', import.meta.url))

// The API server guards against cross-site use: every request must look local. The proxy therefore keeps the
// browser's own Host (`localhost:5197`) by leaving `changeOrigin` off, and streams SSE through untouched.
// The proxy target follows the server's `PORT` from the repo-root `.env`; `BH_API_TARGET` overrides it.
export default defineConfig(({ mode }) => {
  const port = process.env.PORT ?? loadEnv(mode, repoRoot, '').PORT ?? '8797'
  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5197,
      strictPort: false,
      proxy: {
        '/api': {
          target: process.env.BH_API_TARGET ?? `http://127.0.0.1:${port}`,
          changeOrigin: false,
          // Server-sent events: never let anything buffer or time out the stream.
          timeout: 0,
          proxyTimeout: 0,
        },
      },
    },
    test: {
      environment: 'jsdom',
      include: ['src/**/*.test.{ts,tsx}'],
      setupFiles: ['./src/test/setup.ts'],
      css: false,
    },
  }
})
