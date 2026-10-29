import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fixtureGenerationResult } from '@bh/core/testing/result'
import type { GenerationRequest } from '@bh/core/schemas'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return { ...actual, createGeneration: vi.fn(), resumeGeneration: vi.fn() }
})

import { createGeneration, resumeGeneration, type GenerationView } from '../api'
import Result from './Result'

const createMock = vi.mocked(createGeneration)
const resumeMock = vi.mocked(resumeGeneration)

const REQUEST: GenerationRequest = {
  subjectType: 'personal',
  accounts: { checking: { min: 20, max: 40 }, credit: { min: 20, max: 40 } },
  months: 3,
  seed: 'austin-1',
}

function makeView(over: Partial<GenerationView> = {}): GenerationView {
  const result = fixtureGenerationResult()
  // Make the first checking row a recognisable debit.
  const first = result.statements[0]!.transactions[0]!
  first.amountCents = 123456
  first.direction = 'debit'
  return {
    id: 'job-1',
    subjectType: 'personal',
    status: 'completed',
    request: REQUEST,
    result,
    error: null,
    createdAt: '2026-10-29T12:00:00.000Z',
    updatedAt: '2026-10-29T12:05:00.000Z',
    ...over,
  }
}

function renderResult(view: GenerationView, onChanged = vi.fn()) {
  const user = userEvent.setup()
  render(
    <MemoryRouter initialEntries={['/g/job-1']}>
      <Routes>
        <Route path="/g/:id" element={<Result view={view} onChanged={onChanged} />} />
        <Route path="/g/new-job" element={<p>新任务页面</p>} />
      </Routes>
    </MemoryRouter>,
  )
  return { user, onChanged }
}

beforeEach(() => {
  createMock.mockResolvedValue({ id: 'new-job' })
})
afterEach(() => vi.clearAllMocks())

/** The value shown next to a label inside a card (`<dt>` then `<dd>`). */
function valueOf(card: HTMLElement, label: string) {
  return within(card).getByText(label).nextElementSibling
}

function firstBodyRow() {
  const table = screen.getByRole('table', { name: /交易明细/ })
  return within(table).getAllByRole('row')[1]!
}

describe('Result: statement preview', () => {
  it('renders one tab per account and shows the bank header', () => {
    renderResult(makeView())
    const tabs = screen.getAllByRole('tab')
    expect(tabs).toHaveLength(2)
    expect(tabs[0]).toHaveTextContent('****1347')
    expect(tabs[1]).toHaveTextContent('****7210')
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')
    const header = screen.getByRole('region', { name: '账单抬头' })
    expect(within(header).getByText('Harborline Federal Savings')).toBeInTheDocument()
    expect(within(header).getByText(/支票账户/)).toBeInTheDocument()
    expect(within(header).getByText('****1347')).toBeInTheDocument()
    expect(within(header).getByText(/07\/01\/2026/)).toBeInTheDocument()
    expect(within(header).getByText(/09\/30\/2026/)).toBeInTheDocument()
  })

  it('formats the first row amount as $1,234.56 and the date as MM/DD', () => {
    renderResult(makeView())
    const row = firstBodyRow()
    expect(within(row).getByText('$1,234.56')).toBeInTheDocument()
    expect(within(row).getByText('07/01')).toBeInTheDocument()
    expect(row).toHaveTextContent('−$1,234.56')
  })

  it('shows credits in green with a plus and debits plain with a minus', () => {
    renderResult(makeView())
    const table = screen.getByRole('table', { name: /交易明细/ })
    const rows = within(table).getAllByRole('row').slice(1)
    const credit = rows.find((r) => /^\+\$/.test(within(r).getAllByRole('cell')[2]!.textContent ?? ''))
    expect(credit).toBeDefined()
    expect(within(credit!).getAllByRole('cell')[2]).toHaveClass('text-credit')
    expect(within(firstBodyRow()).getAllByRole('cell')[2]).not.toHaveClass('text-credit')
  })

  it('renders descriptions in a monospace font and a running balance column for checking', () => {
    renderResult(makeView())
    const table = screen.getByRole('table', { name: /交易明细/ })
    expect(within(table).getByRole('columnheader', { name: '余额' })).toBeInTheDocument()
    expect(within(firstBodyRow()).getByText('CAMDEN LIVING RENT PMT 0701')).toHaveClass('font-mono')
  })

  it('shows one summary card per month', () => {
    renderResult(makeView())
    const cards = screen.getAllByRole('group', { name: /账期/ })
    expect(cards).toHaveLength(3)
    expect(valueOf(cards[0]!, '期初')).toHaveTextContent('$8,423.16')
    expect(valueOf(cards[0]!, '收入')).toHaveTextContent('$3,097.07')
    expect(valueOf(cards[0]!, '支出')).toHaveTextContent('$3,843.88')
    expect(valueOf(cards[0]!, '期末')).toHaveTextContent('$7,676.35')
  })

  it('expands a row to memo, merchant and an evidence link that opens safely in a new tab', async () => {
    const { user } = renderResult(makeView())
    expect(screen.queryByRole('link', { name: /camdenliving\.com/ })).not.toBeInTheDocument()
    await user.click(within(firstBodyRow()).getByText('$1,234.56'))
    expect(screen.getByText('Monthly apartment rent')).toBeInTheDocument()
    expect(screen.getByText(/Camden Lamar Heights · Austin, TX/)).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /camdenliving\.com/ })
    expect(link).toHaveAttribute('href', 'https://www.camdenliving.com/austin-tx-apartments/camden-lamar-heights')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.getByText(/1 bedroom monthly rent/)).toBeInTheDocument()
    // Second click collapses again.
    await user.click(within(firstBodyRow()).getByText('$1,234.56'))
    expect(screen.queryByText('Monthly apartment rent')).not.toBeInTheDocument()
  })

  it('does not link an evidence url that is not http(s) and never renders fact text as HTML', async () => {
    const view = makeView()
    const t = (view.result as ReturnType<typeof fixtureGenerationResult>).statements[0]!.transactions[0]!
    t.evidence = { factId: 'f', sourceUrl: 'javascript:alert(1)', note: '<img src=x onerror=alert(1)>' }
    const { user } = renderResult(view)
    await user.click(within(firstBodyRow()).getByText('$1,234.56'))
    expect(screen.queryByRole('link', { name: /javascript/ })).not.toBeInTheDocument()
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
  })

  it('shows credit-card terms on the credit tab, without a running balance column', async () => {
    const { user } = renderResult(makeView())
    await user.click(screen.getAllByRole('tab')[1]!)
    expect(screen.getAllByRole('tab')[1]).toHaveAttribute('aria-selected', 'true')
    const header = screen.getByRole('region', { name: '账单抬头' })
    expect(within(header).getByText('****7210')).toBeInTheDocument()
    expect(within(header).getByText('$12,000.00')).toBeInTheDocument() // credit limit
    expect(within(header).getByText('24.99%')).toBeInTheDocument() // APR
    const card = screen.getAllByRole('group', { name: /账期/ })[0]!
    expect(valueOf(card, '应还')).toHaveTextContent('$1,394.86')
    expect(valueOf(card, '最低还款')).toHaveTextContent('$35.00')
    expect(valueOf(card, '到期日')).toHaveTextContent('08/05')
    expect(screen.queryByRole('columnheader', { name: '余额' })).not.toBeInTheDocument()
  })
})

describe('Result: audit side panel', () => {
  it('lists R1-R13 with Chinese names and pass marks', () => {
    renderResult(makeView())
    const panel = screen.getByRole('complementary', { name: '审计报告' })
    const rules = within(panel).getAllByRole('listitem').filter((li) => /^R\d+/.test(li.textContent ?? ''))
    expect(rules).toHaveLength(13)
    expect(rules[0]).toHaveTextContent('R1')
    expect(rules[0]).toHaveTextContent('✓')
    expect(rules[1]).toHaveTextContent('余额连续')
    expect(rules[9]).toHaveTextContent('R10')
    expect(rules[10]).toHaveTextContent('R11')
    expect(rules[10]).toHaveTextContent('信用卡余额未超额度')
    expect(rules[11]).toHaveTextContent('占位式公司名')
    expect(rules[12]).toHaveTextContent('消费类目符合职业')
  })

  it('marks failed rules with ✗ and shows findings high to low with severity badges', () => {
    const view = makeView()
    const r = view.result as ReturnType<typeof fixtureGenerationResult>
    r.audit.rules[1] = { ...r.audit.rules[1]!, passed: false, violations: [{ transactionIds: ['x'], message: 'Balance jumps by $10' }] }
    r.audit.findings = [
      { severity: 'low', transactionIds: [], problem: 'low one', suggestedTarget: 'schedule' },
      { severity: 'high', transactionIds: ['a'], problem: 'high one', suggestedTarget: 'lifeModel' },
      { severity: 'medium', transactionIds: [], problem: 'medium one', suggestedTarget: 'descriptors' },
    ]
    r.warnings = ['Tavily quota low']
    r.status = 'completed_with_warnings'
    renderResult({ ...view, status: 'completed_with_warnings' })
    const panel = screen.getByRole('complementary', { name: '审计报告' })
    expect(within(panel).getAllByText('✗')).toHaveLength(1)
    expect(within(panel).getByText('Balance jumps by $10')).toBeInTheDocument()
    const findings = within(within(panel).getByRole('list', { name: '审计问题' })).getAllByRole('listitem')
    expect(findings.map((li) => li.textContent)).toEqual([
      expect.stringContaining('high one'),
      expect.stringContaining('medium one'),
      expect.stringContaining('low one'),
    ])
    expect(findings[0]).toHaveTextContent('高')
    expect(within(panel).getByText('Tavily quota low')).toBeInTheDocument()
  })
})

describe('Result: actions', () => {
  it('links the JSON download to the export endpoint', () => {
    renderResult(makeView())
    const link = screen.getByRole('link', { name: '下载 JSON' })
    expect(link).toHaveAttribute('href', '/api/generations/job-1/export.json')
  })

  it('re-runs with the same parameters and seed, saying the model output can differ', async () => {
    const { user } = renderResult(makeView())
    expect(screen.queryByRole('button', { name: '同 seed 复现' })).not.toBeInTheDocument()
    const button = screen.getByRole('button', { name: '用相同参数和 seed 重新生成' })
    expect(button).toHaveAccessibleDescription(/模型输出可能不同/)
    await user.click(button)
    expect(createMock).toHaveBeenCalledWith(REQUEST)
    expect(await screen.findByText('新任务页面')).toBeInTheDocument()
  })

  it('re-runs with a new seed: the submitted request has no seed', async () => {
    const { user } = renderResult(makeView())
    await user.click(screen.getByRole('button', { name: '换 seed 重新生成' }))
    expect(createMock).toHaveBeenCalledTimes(1)
    const sent = createMock.mock.calls[0]![0]
    expect(sent).not.toHaveProperty('seed')
    expect(sent.accounts).toEqual(REQUEST.accounts)
    expect(await screen.findByText('新任务页面')).toBeInTheDocument()
  })

  it('shows an error and stays put when the re-run is rejected', async () => {
    createMock.mockRejectedValueOnce(new Error('boom'))
    const { user } = renderResult(makeView())
    await user.click(screen.getByRole('button', { name: '换 seed 重新生成' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.queryByText('新任务页面')).not.toBeInTheDocument()
  })
})

describe('Result: non-success states', () => {
  it('failed: shows the error in Chinese wording and offers no download', () => {
    renderResult(makeView({ status: 'failed', result: null, error: { code: 'LLM_CALL_FAILED', message: 'upstream 502' } }))
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('生成失败')
    expect(alert).toHaveTextContent('LLM_CALL_FAILED')
    expect(screen.queryByRole('link', { name: '下载 JSON' })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab')).not.toBeInTheDocument()
  })

  it('budget failure with partial statements: read-only preview, notice, no export', () => {
    const full = fixtureGenerationResult()
    renderResult(
      makeView({
        status: 'failed',
        result: { partial: true, statements: full.statements },
        error: { code: 'BUDGET_EXCEEDED', message: 'budget' },
      }),
    )
    expect(screen.getByText(/部分结果/)).toBeInTheDocument()
    expect(screen.getAllByRole('tab')).toHaveLength(2)
    expect(screen.queryByRole('link', { name: '下载 JSON' })).not.toBeInTheDocument()
    expect(screen.queryByRole('complementary', { name: '审计报告' })).not.toBeInTheDocument()
  })

  it('interrupted: offers 继续生成, which resumes and reports back', async () => {
    resumeMock.mockResolvedValue({ id: 'job-1' })
    const { user, onChanged } = renderResult(makeView({ status: 'interrupted', result: null }))
    await user.click(screen.getByRole('button', { name: '继续生成' }))
    expect(resumeMock).toHaveBeenCalledWith('job-1')
    await vi.waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('does not offer 继续生成 for a completed job', () => {
    renderResult(makeView())
    expect(screen.queryByRole('button', { name: '继续生成' })).not.toBeInTheDocument()
  })
})
