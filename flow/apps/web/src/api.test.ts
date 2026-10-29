import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  createGeneration,
  deleteGeneration,
  exportUrl,
  listGenerations,
  randomPersona,
  resumeGeneration,
  subscribeEvents,
} from './api'

const fetchMock = vi.fn()

function json(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
}

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => vi.unstubAllGlobals())

describe('api client', () => {
  it('POSTs JSON with a JSON content type (the server answers 415 otherwise)', async () => {
    fetchMock.mockReturnValue(json(202, { id: 'g1' }))
    const req = { subjectType: 'personal', accounts: { checking: { min: 5, max: 9 } }, months: 1 } as const
    await expect(createGeneration(req)).resolves.toEqual({ id: 'g1' })
    const [url, init] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/generations')
    expect(init.method).toBe('POST')
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json')
    expect(JSON.parse(init.body)).toEqual(req)
  })

  it('sends the content type even for bodyless POSTs', async () => {
    fetchMock.mockReturnValue(json(202, { id: 'g1' }))
    await resumeGeneration('g1')
    const [url, init] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/generations/g1/resume')
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json')
  })

  it('posts the subject type and partial to the persona route', async () => {
    fetchMock.mockReturnValue(json(200, { name: 'A' }))
    await randomPersona('business', { industry: 'Bakery' })
    const [url, init] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/personas/random')
    expect(JSON.parse(init.body)).toEqual({ subjectType: 'business', partial: { industry: 'Bakery' } })
  })

  it('turns an error envelope into an ApiError', async () => {
    fetchMock.mockReturnValue(json(400, { error: { code: 'INFEASIBLE_REQUEST', message: 'nope' } }))
    const err = await createGeneration({ subjectType: 'personal', accounts: {}, months: 1 }).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 400, code: 'INFEASIBLE_REQUEST', message: 'nope' })
  })

  it('survives a non-JSON error body and a network failure', async () => {
    fetchMock.mockReturnValueOnce(Promise.resolve(new Response('<html>bad gateway</html>', { status: 502 })))
    await expect(randomPersona('personal')).rejects.toMatchObject({ status: 502, code: 'UNKNOWN' })
    fetchMock.mockRejectedValueOnce(new TypeError('fetch failed'))
    await expect(randomPersona('personal')).rejects.toMatchObject({ status: 0, code: 'NETWORK_ERROR' })
  })

  it('lists with a query string and handles 204 on delete', async () => {
    fetchMock.mockReturnValueOnce(json(200, { items: [] }))
    await listGenerations({ subjectType: 'personal', limit: 5 })
    expect(fetchMock.mock.calls[0]![0]).toBe('/api/generations?subjectType=personal&limit=5')
    fetchMock.mockReturnValueOnce(Promise.resolve(new Response(null, { status: 204 })))
    await expect(deleteGeneration('g1')).resolves.toBeUndefined()
    expect(fetchMock.mock.calls[1]![1].method).toBe('DELETE')
  })

  it('builds the export url', () => {
    expect(exportUrl('g 1')).toBe('/api/generations/g%201/export.json')
  })
})

describe('subscribeEvents', () => {
  class FakeEventSource {
    static last: FakeEventSource
    listeners = new Map<string, (e: MessageEvent) => void>()
    closed = false
    onmessage: ((e: MessageEvent) => void) | null = null
    onerror: (() => void) | null = null
    url: string
    constructor(url: string) {
      this.url = url
      FakeEventSource.last = this
    }
    addEventListener(type: string, cb: (e: MessageEvent) => void) {
      this.listeners.set(type, cb)
    }
    close() {
      this.closed = true
    }
  }

  beforeEach(() => vi.stubGlobal('EventSource', FakeEventSource))

  it('parses progress events, reports done and closes', () => {
    const onEvent = vi.fn()
    const onDone = vi.fn()
    const stop = subscribeEvents('g1', onEvent, { onDone })
    const es = FakeEventSource.last
    expect(es.url).toBe('/api/generations/g1/events')
    es.onmessage!({ data: JSON.stringify({ type: 'node_start', node: 'plan' }) } as MessageEvent)
    es.listeners.get('done')!({ data: JSON.stringify({ status: 'completed' }) } as MessageEvent)
    expect(onEvent).toHaveBeenCalledWith({ type: 'node_start', node: 'plan' })
    expect(onDone).toHaveBeenCalledWith('completed')
    expect(es.closed).toBe(true)
    stop()
  })

  it('ignores malformed frames and unsubscribes', () => {
    const onEvent = vi.fn()
    const stop = subscribeEvents('g1', onEvent)
    FakeEventSource.last.onmessage!({ data: 'not json' } as MessageEvent)
    expect(onEvent).not.toHaveBeenCalled()
    stop()
    expect(FakeEventSource.last.closed).toBe(true)
  })
})
