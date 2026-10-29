import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return { ...actual, listGenerations: vi.fn(), deleteGeneration: vi.fn() }
})

import { ApiError, deleteGeneration, listGenerations, type GenerationSummary } from '../api'
import History from './History'

const listMock = vi.mocked(listGenerations)
const deleteMock = vi.mocked(deleteGeneration)

const ITEMS: GenerationSummary[] = [
  { id: 'a', subjectType: 'personal', status: 'completed', displayName: 'Maya Okafor', createdAt: '2026-10-29T12:00:00.000Z' },
  { id: 'b', subjectType: 'business', status: 'running', displayName: 'Lone Star Plumbing LLC', createdAt: '2026-10-28T09:30:00.000Z' },
  { id: 'c', subjectType: 'personal', status: 'failed', displayName: null, createdAt: '2026-10-27T08:00:00.000Z' },
]

function Where() {
  const loc = useLocation()
  return <output aria-label="位置">{loc.pathname + loc.search}</output>
}

function renderHistory(initial = '/history') {
  const user = userEvent.setup()
  render(
    <MemoryRouter initialEntries={[initial]}>
      <Where />
      <Routes>
        <Route path="/history" element={<History />} />
        <Route path="/g/:id" element={<p>任务页面</p>} />
      </Routes>
    </MemoryRouter>,
  )
  return user
}

beforeEach(() => {
  listMock.mockResolvedValue({ items: ITEMS })
  deleteMock.mockResolvedValue(undefined)
})
afterEach(() => {
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

describe('History', () => {
  it('lists generations with name, type, status and time columns', async () => {
    renderHistory()
    const table = await screen.findByRole('table')
    for (const h of ['名称', '类型', '状态', '创建时间', '操作']) expect(within(table).getByRole('columnheader', { name: h })).toBeInTheDocument()
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('Maya Okafor')
    expect(rows[0]).toHaveTextContent('个人')
    expect(rows[0]).toHaveTextContent('已完成')
    expect(rows[1]).toHaveTextContent('企业')
    expect(rows[1]).toHaveTextContent('生成中')
    expect(rows[2]).toHaveTextContent('失败')
    expect(rows[2]).toHaveTextContent('未命名')
  })

  it('reads the filters from the query string and sends them to the api', async () => {
    renderHistory('/history?subjectType=personal&status=failed')
    await screen.findByRole('table')
    expect(listMock).toHaveBeenCalledWith({ subjectType: 'personal', status: 'failed', limit: 200 })
    expect(screen.getByLabelText('类型')).toHaveValue('personal')
    expect(screen.getByLabelText('状态')).toHaveValue('failed')
  })

  it('writes a changed filter back to the query string and reloads', async () => {
    const user = renderHistory()
    await screen.findByRole('table')
    await user.selectOptions(screen.getByLabelText('状态'), 'completed')
    await waitFor(() => expect(listMock).toHaveBeenLastCalledWith({ status: 'completed', limit: 200 }))
    expect(screen.getByLabelText('位置')).toHaveTextContent('/history?status=completed')
    await user.selectOptions(screen.getByLabelText('状态'), '')
    await waitFor(() => expect(screen.getByLabelText('位置')).toHaveTextContent(/^\/history$/))
  })

  it('opens a generation', async () => {
    const user = renderHistory()
    await screen.findByRole('table')
    await user.click(screen.getAllByRole('link', { name: '打开' })[0]!)
    expect(await screen.findByText('任务页面')).toBeInTheDocument()
  })

  it('deletes after confirmation and removes the row', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true))
    const user = renderHistory()
    await screen.findByRole('table')
    await user.click(screen.getAllByRole('button', { name: '删除' })[0]!)
    expect(window.confirm).toHaveBeenCalled()
    expect(deleteMock).toHaveBeenCalledWith('a')
    await waitFor(() => expect(screen.queryByText('Maya Okafor')).not.toBeInTheDocument())
  })

  it('does nothing when the confirmation is declined', async () => {
    vi.stubGlobal('confirm', vi.fn(() => false))
    const user = renderHistory()
    await screen.findByRole('table')
    await user.click(screen.getAllByRole('button', { name: '删除' })[0]!)
    expect(deleteMock).not.toHaveBeenCalled()
    expect(screen.getByText('Maya Okafor')).toBeInTheDocument()
  })

  it('says the job is still running when the server answers 409', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true))
    deleteMock.mockRejectedValueOnce(new ApiError(409, 'INVALID_STATE', 'A running generation cannot be deleted'))
    const user = renderHistory()
    await screen.findByRole('table')
    await user.click(screen.getAllByRole('button', { name: '删除' })[1]!)
    expect(await screen.findByRole('alert')).toHaveTextContent('任务进行中，无法删除')
    expect(screen.getByText('Lone Star Plumbing LLC')).toBeInTheDocument()
  })

  it('shows an empty state and a load error', async () => {
    listMock.mockResolvedValueOnce({ items: [] })
    renderHistory()
    expect(await screen.findByText(/还没有/)).toBeInTheDocument()
  })

  it('shows an error when the list cannot be loaded', async () => {
    listMock.mockRejectedValueOnce(new ApiError(0, 'NETWORK_ERROR', '无法连接到服务器'))
    renderHistory()
    expect(await screen.findByRole('alert')).toHaveTextContent('无法连接到服务器')
  })
})
