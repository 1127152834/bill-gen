/**
 * Minimal MCP (Model Context Protocol) stdio client.
 *
 * Why not `@modelcontextprotocol/sdk`: the graph only needs `initialize`, `tools/list` and
 * `tools/call` over newline-delimited JSON-RPC on a child process. Keeping it in-house means the
 * offline fixture path links no network dependency and the child can be any command (here:
 * `.venv/bin/billgen mcp`), so Python and Node stay in separate processes with a JSON boundary.
 */
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { createInterface } from 'node:readline';

export type McpTool = { name: string; description?: string };
export type McpServerConfig = {
  command: string;
  args?: string[];
  /** Extra environment for the child (the parent env is inherited). */
  env?: Record<string, string>;
  /** Milliseconds to wait for a single request; MCP tools here render PDFs, so this is generous. */
  timeoutMs?: number;
};

export const PROTOCOL_VERSION = '2024-11-05';
const DEFAULT_TIMEOUT_MS = 180_000;

/** A JSON-RPC error, or the tool's own `isError` result, surfaced as a throw. */
export class McpError extends Error {
  readonly code: string;
  constructor(message: string, code = 'MCP_ERROR') {
    super(message);
    this.name = 'McpError';
    this.code = code;
  }
}

type Pending = { resolve: (v: unknown) => void; reject: (e: Error) => void; timer: NodeJS.Timeout };
type RpcMessage = { id?: number; result?: unknown; error?: { code: number; message: string; data?: unknown } };

export class McpStdioClient {
  private child: ChildProcessWithoutNullStreams | null = null;
  private nextId = 1;
  private readonly pending = new Map<number, Pending>();
  private readonly timeoutMs: number;
  private closed = false;

  constructor(private readonly config: McpServerConfig) {
    this.timeoutMs = config.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  }

  get running(): boolean {
    return this.child !== null && !this.closed;
  }

  async start(): Promise<void> {
    if (this.child) return;
    const child = spawn(this.config.command, this.config.args ?? [], {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, ...this.config.env },
    });
    this.child = child;
    const lines = createInterface({ input: child.stdout });
    lines.on('line', (line) => this.onLine(line));
    child.stderr.on('data', (chunk: Buffer) => {
      // The server logs to stderr; keep it out of the protocol but visible when debugging.
      const text = chunk.toString().trim();
      if (text && process.env.MCP_DEBUG) process.stderr.write(`[mcp] ${text}\n`);
    });
    child.on('error', (err) => this.onError(err));
    child.on('exit', (code) => this.onExit(code));
    await this.request('initialize', {
      protocolVersion: PROTOCOL_VERSION,
      capabilities: {},
      clientInfo: { name: 'bill-hunter-flow', version: '0.1.0' },
    });
    this.notify('notifications/initialized', {});
  }

  async listTools(): Promise<McpTool[]> {
    const result = (await this.request('tools/list', {})) as { tools?: McpTool[] };
    return result.tools ?? [];
  }

  /** Calls a tool and returns its structured payload (structured content, else the single JSON text block). */
  async callTool<T>(name: string, args: Record<string, unknown>): Promise<T> {
    const raw = (await this.request('tools/call', { name, arguments: args })) as {
      content?: { type: string; text?: string }[];
      structuredContent?: unknown;
      isError?: boolean;
    };
    if (raw.isError) {
      const message = (raw.content ?? []).map((c) => c.text ?? '').join(' ').trim();
      throw new McpError(`tool ${name} failed: ${message || 'no detail'}`, 'TOOL_ERROR');
    }
    if (raw.structuredContent !== undefined) return raw.structuredContent as T;
    const text = (raw.content ?? []).find((c) => c.type === 'text')?.text;
    if (text === undefined) throw new McpError(`tool ${name} returned no content`, 'EMPTY_RESULT');
    return JSON.parse(text) as T;
  }

  async close(): Promise<void> {
    if (!this.child) return;
    this.closed = true;
    const child = this.child;
    this.child = null;
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(new McpError('client closed', 'CLOSED'));
    }
    this.pending.clear();
    child.stdin.end();
    await new Promise<void>((resolve) => {
      const timer = setTimeout(() => {
        child.kill('SIGKILL');
        resolve();
      }, 2_000);
      child.once('exit', () => {
        clearTimeout(timer);
        resolve();
      });
      child.kill('SIGTERM');
    });
  }

  private onLine(line: string): void {
    const trimmed = line.trim();
    if (!trimmed) return;
    let message: RpcMessage;
    try {
      message = JSON.parse(trimmed) as RpcMessage;
    } catch {
      return; // stray output (the server should not print to stdout, but tolerate it)
    }
    if (typeof message.id !== 'number') return; // notifications from the server
    const pending = this.pending.get(message.id);
    if (!pending) return;
    this.pending.delete(message.id);
    clearTimeout(pending.timer);
    if (message.error) pending.reject(new McpError(message.error.message, String(message.error.code)));
    else pending.resolve(message.result);
  }

  private onError(err: Error): void {
    this.closed = true;
    this.child = null;
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(new McpError(`mcp server failed to start: ${err.message}`, 'SPAWN_FAILED'));
    }
    this.pending.clear();
  }

  private onExit(code: number | null): void {
    this.closed = true;
    this.child = null;
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(new McpError(`mcp server exited (code ${code ?? 'null'})`, 'SERVER_EXIT'));
    }
    this.pending.clear();
  }

  private notify(method: string, params: unknown): void {
    this.send({ jsonrpc: '2.0', method, params });
  }

  private request(method: string, params: unknown): Promise<unknown> {
    const id = this.nextId++;
    return new Promise<unknown>((resolve, reject) => {
      if (this.closed || !this.child) {
        reject(new McpError('mcp server is not running', 'NOT_RUNNING'));
        return;
      }
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new McpError(`mcp request "${method}" timed out after ${this.timeoutMs}ms`, 'TIMEOUT'));
      }, this.timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      this.send({ jsonrpc: '2.0', id, method, params });
    });
  }

  private send(payload: unknown): void {
    if (!this.child) return;
    this.child.stdin.write(`${JSON.stringify(payload)}\n`);
  }
}

/** Runs `fn` against a freshly started client and always tears the child down. */
export async function withMcpClient<T>(
  config: McpServerConfig,
  fn: (client: McpStdioClient) => Promise<T>,
): Promise<T> {
  const client = new McpStdioClient(config);
  await client.start();
  try {
    return await fn(client);
  } finally {
    await client.close();
  }
}

type PoolEntry = { client: McpStdioClient; config: McpServerConfig; lastUsed: number };

function configKey(config: McpServerConfig): string {
  return JSON.stringify({ command: config.command, args: config.args ?? [], env: config.env ?? {} });
}

/**
 * Keeps MCP children alive across jobs.
 *
 * A cold `billgen mcp` costs an interpreter start plus PyMuPDF import (hundreds of ms); rendering a
 * batch pays that per PDF if every call spawns. The pool keys children by (command, args, env) and
 * reuses them, restarting only when the child died — a crashed/blocked child must never poison the
 * next job, so a failing call triggers exactly one restart-and-retry.
 */
export class McpPool {
  private readonly entries = new Map<string, PoolEntry>();
  private readonly idleMs: number;
  private readonly now: () => number;

  constructor(opts: { idleMs?: number; now?: () => number } = {}) {
    this.idleMs = opts.idleMs ?? 10 * 60_000;
    this.now = opts.now ?? (() => Date.now());
  }

  /** Live children (used by tests to prove reuse). */
  get size(): number {
    return [...this.entries.values()].filter((e) => e.client.running).length;
  }

  async with<T>(config: McpServerConfig, fn: (client: McpStdioClient) => Promise<T>): Promise<T> {
    this.reap();
    const key = configKey(config);
    let entry = this.entries.get(key);
    if (entry && !entry.client.running) {
      this.entries.delete(key);
      entry = undefined;
    }
    if (!entry) {
      const client = new McpStdioClient(config);
      await client.start();
      entry = { client, config, lastUsed: this.now() };
      this.entries.set(key, entry);
    }
    entry.lastUsed = this.now();
    const used = entry;
    try {
      return await fn(used.client);
    } catch (err) {
      const code = err instanceof McpError ? err.code : 'UNKNOWN';
      const transient = code === 'SERVER_EXIT' || code === 'CLOSED' || code === 'NOT_RUNNING' || code === 'SPAWN_FAILED';
      if (!transient) throw err;
      // The child died mid-flight: drop it and retry once on a fresh one.
      this.entries.delete(key);
      await used.client.close();
      const fresh = new McpStdioClient(config);
      await fresh.start();
      this.entries.set(key, { client: fresh, config, lastUsed: this.now() });
      return fn(fresh);
    }
  }

  /** Closes one config's child (or every child when no config is given). */
  async dispose(config?: McpServerConfig): Promise<void> {
    const keys = config ? [configKey(config)] : [...this.entries.keys()];
    for (const key of keys) {
      const entry = this.entries.get(key);
      if (!entry) continue;
      this.entries.delete(key);
      await entry.client.close();
    }
  }

  private reap(): void {
    const cutoff = this.now() - this.idleMs;
    for (const [key, entry] of [...this.entries]) {
      if (entry.lastUsed > cutoff) continue;
      this.entries.delete(key);
      void entry.client.close();
    }
  }
}

/** Process-wide pool: the graph renders through this so consecutive jobs share one child. */
export const sharedMcpPool = new McpPool();
