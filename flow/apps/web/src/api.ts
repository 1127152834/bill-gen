// Browser API client. Imports only `@bh/core/schemas` (zod schemas and types): the root `@bh/core`
// pulls in LangGraph and must never reach the bundle. No API key lives here; the dev proxy and the
// server's loopback bypass cover the local UI.
import type { GenerationRequest, GenerationResult, Profile, RuleResult, SubjectType } from '@bh/core/schemas'

export type GenerationStatus = 'queued' | 'running' | 'completed' | 'completed_with_warnings' | 'failed' | 'interrupted'

/** Mirrors the server's `ProgressEvent` (kept local: the type lives in the root `@bh/core`). */
export type ProgressEvent =
  | { type: 'node_start' | 'node_end'; node: string }
  | { type: 'research'; query: string; sources: string[] }
  | { type: 'rules'; results: RuleResult[] }
  | { type: 'retry'; target: string; reason: string }
  | { type: 'warning'; message: string }
  | { type: 'progress'; message: string }

export type GenerationView = {
  id: string
  subjectType: SubjectType
  status: GenerationStatus
  request: GenerationRequest | null
  /** A `GenerationResult`, or `{partial:true, statements}` when the job failed after statements existed. */
  result: GenerationResult | { partial: true; statements: unknown[] } | null
  /** `usage`: what a failed job used before it stopped. */
  error: { code?: string; message?: string; usage?: { llmCalls: number; tokens: number; tavilyQueries: number } } | null
  createdAt: string
  updatedAt: string
}

export type GenerationSummary = {
  id: string
  subjectType: SubjectType
  status: GenerationStatus
  displayName: string | null
  createdAt: string
}

export type ListQuery = { subjectType?: SubjectType; status?: GenerationStatus; limit?: number }

/** Any non-2xx answer (or a failed connection: `status` 0, code `NETWORK_ERROR`). */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

const JSON_HEADERS = { 'Content-Type': 'application/json' }

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, init)
  } catch {
    throw new ApiError(0, 'NETWORK_ERROR', '无法连接到服务器')
  }
  if (!res.ok) {
    let code = 'UNKNOWN'
    let message = `请求失败（${res.status}）`
    try {
      const body = (await res.json()) as { error?: { code?: string; message?: string } }
      code = body.error?.code ?? code
      message = body.error?.message ?? message
    } catch {
      // Not a JSON error envelope (a proxy page, say): keep the generic message.
    }
    throw new ApiError(res.status, code, message)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

/** Every POST declares JSON, even without a body: the server answers 415 otherwise. */
function post<T>(path: string, body?: unknown): Promise<T> {
  return call<T>(path, {
    method: 'POST',
    headers: JSON_HEADERS,
    ...(body !== undefined && { body: JSON.stringify(body) }),
  })
}

export function createGeneration(req: GenerationRequest): Promise<{ id: string }> {
  return post('/api/generations', req)
}

export function randomPersona(subjectType: SubjectType, partial?: Record<string, unknown>): Promise<Profile> {
  return post('/api/personas/random', { subjectType, ...(partial !== undefined && { partial }) })
}

export function getGeneration(id: string): Promise<GenerationView> {
  return call(`/api/generations/${encodeURIComponent(id)}`)
}

export function listGenerations(q: ListQuery = {}): Promise<{ items: GenerationSummary[] }> {
  const params = new URLSearchParams()
  if (q.subjectType) params.set('subjectType', q.subjectType)
  if (q.status) params.set('status', q.status)
  if (q.limit !== undefined) params.set('limit', String(q.limit))
  const qs = params.toString()
  return call(`/api/generations${qs ? `?${qs}` : ''}`)
}

export function deleteGeneration(id: string): Promise<void> {
  return call(`/api/generations/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export function resumeGeneration(id: string): Promise<{ id: string }> {
  return post(`/api/generations/${encodeURIComponent(id)}/resume`)
}

export function exportUrl(id: string): string {
  return `/api/generations/${encodeURIComponent(id)}/export.json`
}

export type SubscribeHandlers = {
  /** The server finished streaming; `status` is the job's final status. */
  onDone?: (status: GenerationStatus) => void
  /** The connection broke. EventSource retries on its own (resuming from Last-Event-ID). */
  onError?: () => void
}

/** Streams a job's progress. Returns an unsubscribe function. */
export function subscribeEvents(
  id: string,
  onEvent: (e: ProgressEvent) => void,
  handlers: SubscribeHandlers = {},
): () => void {
  const source = new EventSource(`/api/generations/${encodeURIComponent(id)}/events`)
  source.onmessage = (m: MessageEvent<string>) => {
    try {
      onEvent(JSON.parse(m.data) as ProgressEvent)
    } catch {
      // A malformed frame is skipped rather than killing the stream.
    }
  }
  source.addEventListener('done', (m) => {
    let status: GenerationStatus = 'failed'
    try {
      status = (JSON.parse((m as MessageEvent<string>).data) as { status: GenerationStatus }).status
    } catch {
      // Keep the conservative default.
    }
    source.close()
    handlers.onDone?.(status)
  })
  source.onerror = () => handlers.onError?.()
  return () => source.close()
}
