import { describe, expect, it } from 'vitest'
import { EMPTY_DRAFT, applyField, buildPartial, buildProfile, inputErrors, type Draft } from './profileDraft'
import {
  DEFAULT_ACCOUNTS,
  DEFAULT_RENDER,
  buildRequest,
  endMonthError,
  previousMonth,
  rangeError,
  renderError,
} from './requestForm'

const FULL: Draft = {
  ...EMPTY_DRAFT,
  name: 'Alice Brown',
  age: '41',
  occupation: 'Plumber',
  maritalStatus: 'single',
  children: '0',
  street: '12 Oak St',
  city: 'Austin',
  state: 'TX',
  zip: '78701',
}

describe('profileDraft', () => {
  it('sends household only when both halves are usable and neither is being rerolled', () => {
    expect(buildPartial('personal', FULL)).toMatchObject({ household: { maritalStatus: 'single', children: 0 } })
    expect(buildPartial('personal', FULL, 'children')).not.toHaveProperty('household')
    expect(buildPartial('personal', { ...FULL, children: '' })).not.toHaveProperty('household')
  })

  it('leaves out invalid fields so the server never rejects the partial', () => {
    const partial = buildPartial('personal', { ...FULL, age: '17', zip: '1' })
    expect(partial).not.toHaveProperty('age')
    expect(partial).not.toHaveProperty('address')
    expect(partial).toHaveProperty('name', 'Alice Brown')
  })

  it('builds a schema-valid profile only from a complete draft', () => {
    expect(buildProfile('personal', FULL)).toMatchObject({ name: 'Alice Brown', age: 41, household: { children: 0 } })
    expect(buildProfile('personal', { ...FULL, state: '' })).toBeUndefined()
    expect(inputErrors('business', EMPTY_DRAFT).legalName?.kind).toBe('missing')
  })

  it('replaces one field and nothing else', () => {
    const next = applyField(FULL, 'maritalStatus', {
      name: 'X', age: 30, occupation: 'Y', hobbies: [],
      address: { street: 's', city: 'c', state: 'CA', zip: '90001' },
      household: { maritalStatus: 'married', children: 3 },
    })
    expect(next).toEqual({ ...FULL, maritalStatus: 'married' })
  })
})

describe('requestForm', () => {
  it('uses last month, wrapping over the new year', () => {
    expect(previousMonth(new Date(2026, 0, 15))).toBe('2025-12')
    expect(previousMonth(new Date(2026, 9, 29))).toBe('2026-09')
    expect(endMonthError('2026-10', new Date(2026, 9, 29))).toBe('结束月份不能晚于上个月')
  })

  it('validates ranges', () => {
    expect(rangeError({ enabled: true, min: '9', max: '8' })).toBe('最小值不能大于最大值')
    expect(rangeError({ enabled: true, min: '0', max: '8' })).toBe('请输入 1 到 200 之间的整数')
    expect(rangeError({ enabled: true, min: '1', max: '200' })).toBeUndefined()
  })

  it('builds nothing while no account is ticked', () => {
    const none = { ...DEFAULT_ACCOUNTS, checking: { ...DEFAULT_ACCOUNTS.checking, enabled: false } }
    expect(buildRequest({ subjectType: 'business', profile: undefined, accounts: none, months: 1, endMonth: '2020-01', seed: '' })).toBeUndefined()
  })

  it('carries the layout choice, and drops the auto count when a bank is pinned', () => {
    const auto = buildRequest({
      subjectType: 'personal', profile: undefined, accounts: DEFAULT_ACCOUNTS,
      months: 1, endMonth: '2020-01', seed: '',
    })
    expect(auto).toMatchObject({ templates: ['auto'], templateLimit: 2 })

    const pinned = buildRequest({
      subjectType: 'personal', profile: undefined, accounts: DEFAULT_ACCOUNTS, months: 1, endMonth: '2020-01', seed: '',
      render: { ...DEFAULT_RENDER, template: 'chase-personal', autoLimit: '4' },
    })
    expect(pinned).toMatchObject({ templates: ['chase-personal'] })
    expect(pinned).not.toHaveProperty('bankName')
    expect(pinned).not.toHaveProperty('templateLimit')
  })

  it('rejects an auto pick count outside 1-11', () => {
    expect(renderError({ ...DEFAULT_RENDER, autoLimit: '0' })).toBe('自动挑选个数要在 1–11 之间')
    expect(renderError({ ...DEFAULT_RENDER, autoLimit: '' })).toBe('自动挑选个数要在 1–11 之间')
    expect(renderError({ ...DEFAULT_RENDER, autoLimit: '12' })).toBe('自动挑选个数要在 1–11 之间')
    expect(renderError({ ...DEFAULT_RENDER, template: 'sofi', autoLimit: '0' })).toBeUndefined()
  })
})
