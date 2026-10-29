import { act, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fixtureGenerationResult } from '@bh/core/testing/result'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return { ...actual, getGeneration: vi.fn(), subscribeEvents: vi.fn() }
})

import { ApiError, getGeneration, subscribeEvents, type GenerationView } from '../api'
import Generation from './Generation'

const getMock = vi.mocked(getGeneration)
const subscribeMock = vi.mocked(subscribeEvents)

const base: GenerationView = {
  id: 'job-1',
  subjectType: 'personal',
  status: 'running',
  request: null,
  result: null,
  error: null,
  createdAt: '2026-10-29T12:00:00.000Z',
  updatedAt: '2026-10-29T12:00:00.000Z',
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={['/g/job-1']}>
      <Routes>
        <Route path="/g/:id" element={<Generation />} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => vi.clearAllMocks())

describe('Generation', () => {
  it('shows the progress timeline while the job runs, then the result after done', async () => {
    let done: (s: GenerationView['status']) => void = () => {}
    subscribeMock.mockImplementation((_id, _on, h = {}) => {
      done = (s) => h.onDone?.(s)
      return () => {}
    })
    getMock.mockResolvedValueOnce(base)
    renderPage()
    expect(await screen.findByRole('list', { name: '工作流' })).toBeInTheDocument()

    getMock.mockResolvedValueOnce({ ...base, status: 'completed', result: fixtureGenerationResult() })
    await act(async () => done('completed'))
    expect(await screen.findAllByRole('tab')).toHaveLength(2)
    expect(screen.queryByRole('list', { name: '工作流' })).not.toBeInTheDocument()
  })

  it('goes straight to the result for a settled job', async () => {
    getMock.mockResolvedValueOnce({ ...base, status: 'completed', result: fixtureGenerationResult() })
    renderPage()
    expect(await screen.findAllByRole('tab')).toHaveLength(2)
    expect(subscribeMock).not.toHaveBeenCalled()
  })

  it('shows a not-found message', async () => {
    getMock.mockRejectedValueOnce(new ApiError(404, 'NOT_FOUND', 'Generation not found'))
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('找不到')
  })
})
