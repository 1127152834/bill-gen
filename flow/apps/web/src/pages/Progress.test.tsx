import { act, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ProgressEvent } from '../api'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return { ...actual, subscribeEvents: vi.fn() }
})

import { subscribeEvents } from '../api'
import Progress from './Progress'

const subscribeMock = vi.mocked(subscribeEvents)
let emit: (e: ProgressEvent) => void
let handlers: NonNullable<Parameters<typeof subscribeEvents>[2]>
const unsubscribe = vi.fn()

beforeEach(() => {
  subscribeMock.mockImplementation((_id, onEvent, h = {}) => {
    emit = (e) => act(() => onEvent(e))
    handlers = h
    return unsubscribe
  })
})
afterEach(() => vi.clearAllMocks())

function nodeItem(name: string) {
  return screen.getByRole('listitem', { name: new RegExp(name) })
}

describe('Progress', () => {
  it('lists the nine nodes, all pending at first', () => {
    render(<Progress id="job-1" onSettled={() => {}} />)
    const timeline = screen.getByRole('list', { name: '工作流' })
    expect(within(timeline).getAllByRole('listitem')).toHaveLength(9)
    expect(nodeItem('normalize')).toHaveAttribute('data-state', 'pending')
    expect(subscribeMock).toHaveBeenCalledWith('job-1', expect.any(Function), expect.any(Object))
  })

  it('highlights the active node and marks finished ones done', () => {
    render(<Progress id="job-1" onSettled={() => {}} />)
    emit({ type: 'node_start', node: 'normalize' })
    emit({ type: 'node_end', node: 'normalize' })
    emit({ type: 'node_start', node: 'planResearch' })
    expect(nodeItem('normalize')).toHaveAttribute('data-state', 'done')
    expect(nodeItem('planResearch')).toHaveAttribute('data-state', 'active')
    expect(nodeItem('planResearch')).toHaveAttribute('aria-current', 'step')
    expect(nodeItem('schedule')).toHaveAttribute('data-state', 'pending')
  })

  it('shows research queries with source domains linking out in a new tab', () => {
    render(<Progress id="job-1" onSettled={() => {}} />)
    emit({ type: 'research', query: 'Austin rent 1 bedroom', sources: ['https://www.camdenliving.com/a', 'https://example.org/b'] })
    expect(screen.getByText('Austin rent 1 bedroom')).toBeInTheDocument()
    const link = screen.getByRole('link', { name: 'camdenliving.com' })
    expect(link).toHaveAttribute('href', 'https://www.camdenliving.com/a')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.getByRole('link', { name: 'example.org' })).toBeInTheDocument()
  })

  it('shows a retry banner with target and reason, and re-opens the later nodes', () => {
    render(<Progress id="job-1" onSettled={() => {}} />)
    for (const n of ['lifeModel', 'schedule', 'descriptors', 'validate']) {
      emit({ type: 'node_start', node: n })
      emit({ type: 'node_end', node: n })
    }
    emit({ type: 'retry', target: 'schedule', reason: 'R2 balance continuity failed' })
    const banner = screen.getByRole('status', { name: '打回' })
    expect(banner).toHaveTextContent('schedule')
    expect(banner).toHaveTextContent('R2 balance continuity failed')
    expect(nodeItem('lifeModel')).toHaveAttribute('data-state', 'done')
    expect(nodeItem('schedule')).toHaveAttribute('data-state', 'pending')
    expect(nodeItem('validate')).toHaveAttribute('data-state', 'pending')
  })

  it('lists warnings as text', () => {
    render(<Progress id="job-1" onSettled={() => {}} />)
    emit({ type: 'warning', message: 'Tavily cache <b>stale</b>' })
    expect(screen.getByText('Tavily cache <b>stale</b>')).toBeInTheDocument()
  })

  it('reports the final status on done and closes the stream on unmount', () => {
    const onSettled = vi.fn()
    const { unmount } = render(<Progress id="job-1" onSettled={onSettled} />)
    act(() => handlers.onDone?.('completed'))
    expect(onSettled).toHaveBeenCalledWith('completed')
    unmount()
    expect(unsubscribe).toHaveBeenCalled()
  })

  it('shows a reconnecting hint while the connection is broken', () => {
    render(<Progress id="job-1" onSettled={() => {}} />)
    act(() => handlers.onError?.())
    expect(screen.getByText(/重新连接/)).toBeInTheDocument()
    emit({ type: 'node_start', node: 'normalize' })
    expect(screen.queryByText(/重新连接/)).not.toBeInTheDocument()
  })
})
