import { describe, expect, it } from 'vitest'
import { formatMoney, formatMonthDay, formatFullDate, domainOf, safeHttpUrl, sortFindings, describeFailure } from './format'

describe('formatMoney', () => {
  it('formats US dollars with grouping', () => {
    expect(formatMoney(123456)).toBe('$1,234.56')
    expect(formatMoney(5)).toBe('$0.05')
    expect(formatMoney(-2500)).toBe('-$25.00')
  })
})

describe('dates', () => {
  it('shows MM/DD for rows and MM/DD/YYYY for the period', () => {
    expect(formatMonthDay('2026-07-01')).toBe('07/01')
    expect(formatFullDate('2026-09-30')).toBe('09/30/2026')
  })
})

describe('links', () => {
  it('only lets http(s) urls through', () => {
    expect(safeHttpUrl('https://example.com/a')).toBe('https://example.com/a')
    expect(safeHttpUrl('javascript:alert(1)')).toBeNull()
    expect(safeHttpUrl('not a url')).toBeNull()
  })
  it('reduces a url to its domain', () => {
    expect(domainOf('https://www.camdenliving.com/x/y')).toBe('camdenliving.com')
    expect(domainOf('garbage')).toBe('garbage')
  })
})

describe('sortFindings', () => {
  it('orders high before medium before low and keeps ties stable', () => {
    const f = (severity: 'low' | 'medium' | 'high', problem: string) => ({ severity, problem, transactionIds: [], suggestedTarget: 'schedule' as const })
    const sorted = sortFindings([f('low', 'a'), f('high', 'b'), f('medium', 'c'), f('high', 'd')])
    expect(sorted.map((x) => x.problem)).toEqual(['b', 'd', 'c', 'a'])
  })
})

describe('describeFailure', () => {
  it('maps known codes to Chinese and falls back to the code', () => {
    expect(describeFailure({ code: 'BUDGET_EXCEEDED', message: 'x' })).toMatch(/预算|上限/)
    expect(describeFailure({ code: 'WEIRD_CODE', message: 'boom' })).toContain('WEIRD_CODE')
    expect(describeFailure(null)).toMatch(/失败/)
  })
})
