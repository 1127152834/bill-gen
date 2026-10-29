/**
 * The pool keeps one MCP child alive across jobs, and must never hand a dead child to the next
 * caller: a killed server has to be replaced transparently, on the same call.
 *
 * A stub MCP server stands in for `billgen mcp`: it speaks just enough JSON-RPC (initialize,
 * tools/list, tools/call) and appends its pid to a log so the test can prove reuse, not just success.
 */
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { McpPool } from '../src/mcp/client.js';

const STUB = `
import { appendFileSync } from 'node:fs';
const log = process.env.POOL_LOG;
appendFileSync(log, process.pid + '\\n');
const send = (o) => process.stdout.write(JSON.stringify(o) + '\\n');
let buf = '';
process.stdin.on('data', (chunk) => {
  buf += chunk.toString();
  for (let nl = buf.indexOf('\\n'); nl >= 0; nl = buf.indexOf('\\n')) {
    const line = buf.slice(0, nl);
    buf = buf.slice(nl + 1);
    if (!line.trim()) continue;
    const msg = JSON.parse(line);
    if (msg.method === 'initialize') {
      send({ jsonrpc: '2.0', id: msg.id, result: { protocolVersion: '2024-11-05', capabilities: {}, serverInfo: { name: 'stub', version: '0' } } });
    } else if (msg.method === 'tools/list') {
      send({ jsonrpc: '2.0', id: msg.id, result: { tools: [{ name: 'ping' }] } });
    } else if (msg.method === 'tools/call') {
      if (msg.params.name === 'ping') {
        send({ jsonrpc: '2.0', id: msg.id, result: { structuredContent: { pid: process.pid } } });
      } else if (msg.params.name === 'die') {
        process.exit(3);
      } else {
        send({ jsonrpc: '2.0', id: msg.id, result: { isError: true, content: [{ type: 'text', text: 'unknown tool' }] } });
      }
    }
  }
});
`;

function setup(): { dir: string; log: string; config: { command: string; args: string[]; env: Record<string, string> } } {
  const dir = mkdtempSync(join(tmpdir(), 'bh-pool-'));
  const script = join(dir, 'stub.mjs');
  writeFileSync(script, STUB);
  const log = join(dir, 'spawns.log');
  writeFileSync(log, '');
  return {
    dir,
    log,
    config: { command: process.execPath, args: [script], env: { POOL_LOG: log } },
  };
}

function spawnedPids(log: string): number[] {
  if (!existsSync(log)) return [];
  return readFileSync(log, 'utf8')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map(Number);
}

describe('McpPool', () => {
  it('reuses one child across calls', async () => {
    const { log, config } = setup();
    const pool = new McpPool();
    try {
      const first = await pool.with(config, (client) => client.callTool<{ pid: number }>('ping', {}));
      const second = await pool.with(config, (client) => client.callTool<{ pid: number }>('ping', {}));
      expect(second.pid).toBe(first.pid);
      expect(spawnedPids(log)).toHaveLength(1);
      expect(pool.size).toBe(1);
    } finally {
      await pool.dispose();
    }
  });

  it('replaces a child that died mid-call', async () => {
    const { log, config } = setup();
    const pool = new McpPool();
    try {
      const first = await pool.with(config, (client) => client.callTool<{ pid: number }>('ping', {}));
      // The tool kills the server; the pool must notice and retry on a fresh child.
      await expect(pool.with(config, (client) => client.callTool('die', {}))).rejects.toThrow();
      const third = await pool.with(config, (client) => client.callTool<{ pid: number }>('ping', {}));
      expect(third.pid).not.toBe(first.pid);
      const pids = spawnedPids(log);
      expect(pids.length).toBeGreaterThanOrEqual(2);
      expect(pids.at(-1)).toBe(third.pid);
    } finally {
      await pool.dispose();
    }
  });

  it('reaps idle children', async () => {
    const { config } = setup();
    let clock = 1_000;
    const pool = new McpPool({ idleMs: 60_000, now: () => clock });
    try {
      await pool.with(config, (client) => client.callTool('ping', {}));
      expect(pool.size).toBe(1);
      clock += 120_000;
      await pool.dispose();
      expect(pool.size).toBe(0);
    } finally {
      await pool.dispose();
    }
  });

  it('keeps separate children per configuration', async () => {
    const one = setup();
    const two = setup();
    const pool = new McpPool();
    try {
      const a = await pool.with(one.config, (client) => client.callTool<{ pid: number }>('ping', {}));
      const b = await pool.with(two.config, (client) => client.callTool<{ pid: number }>('ping', {}));
      expect(a.pid).not.toBe(b.pid);
      expect(pool.size).toBe(2);
    } finally {
      await pool.dispose();
    }
  });

  it('never leaves a stray child behind after dispose', async () => {
    const { log, config } = setup();
    const pool = new McpPool();
    const { pid } = await pool.with(config, (client) => client.callTool<{ pid: number }>('ping', {}));
    await pool.dispose();
    expect(pool.size).toBe(0);
    const alive = spawnSync(process.execPath, ['-e', `process.kill(${pid}, 0)`]);
    expect(alive.status).not.toBe(0); // ESRCH: the child is gone
    expect(spawnedPids(log)).toEqual([pid]);
  });
});
