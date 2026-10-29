import { GenerationRequestSchema, type GenerationRequest, type Profile, type SubjectType } from '@bh/core/schemas'

export const COUNT_MIN = 1
export const COUNT_MAX = 200

export type RangeDraft = { enabled: boolean; min: string; max: string }
export type AccountKey = 'checking' | 'credit'
export type AccountsDraft = Record<AccountKey, RangeDraft>

export const ACCOUNT_LABELS: Record<AccountKey, string> = { checking: '支票账户', credit: '信用卡' }

export const DEFAULT_ACCOUNTS: AccountsDraft = {
  checking: { enabled: false, min: '20', max: '40' },
  credit: { enabled: false, min: '10', max: '25' },
}

const isCount = (s: string) => /^\d+$/.test(s.trim()) && Number(s) >= COUNT_MIN && Number(s) <= COUNT_MAX

/** A Chinese message for a bad range, or `undefined` when it is fine. Only ticked accounts are checked. */
export function rangeError(r: RangeDraft): string | undefined {
  if (!isCount(r.min) || !isCount(r.max)) return `请输入 ${COUNT_MIN} 到 ${COUNT_MAX} 之间的整数`
  return Number(r.min) > Number(r.max) ? '最小值不能大于最大值' : undefined
}

export function accountsError(accounts: AccountsDraft): string | undefined {
  return accounts.checking.enabled || accounts.credit.enabled ? undefined : '请至少选择一个账户'
}

/** `YYYY-MM` of the month before `now` (local time: it is the user's calendar that counts). */
export function previousMonth(now: Date = new Date()): string {
  const d = new Date(now.getFullYear(), now.getMonth() - 1, 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

export function endMonthError(value: string, now: Date = new Date()): string | undefined {
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(value)) return '请选择结束月份'
  return value > previousMonth(now) ? '结束月份不能晚于上个月' : undefined
}

export type RequestInput = {
  subjectType: SubjectType
  /** `undefined` asks the server to generate the persona. */
  profile: Profile | undefined
  accounts: AccountsDraft
  months: 1 | 3 | 6
  endMonth: string
  seed: string
}

/** The request body, or `undefined` while anything on the form is invalid. */
export function buildRequest(input: RequestInput): GenerationRequest | undefined {
  const { accounts } = input
  if (accountsError(accounts) || endMonthError(input.endMonth)) return undefined
  const ranges: GenerationRequest['accounts'] = {}
  for (const key of ['checking', 'credit'] as const) {
    const r = accounts[key]
    if (!r.enabled) continue
    if (rangeError(r)) return undefined
    ranges[key] = { min: Number(r.min), max: Number(r.max) }
  }
  const seed = input.seed.trim()
  const parsed = GenerationRequestSchema.safeParse({
    subjectType: input.subjectType,
    ...(input.profile && { profile: input.profile }),
    accounts: ranges,
    months: input.months,
    endMonth: input.endMonth,
    ...(seed !== '' && { seed }),
  })
  return parsed.success ? parsed.data : undefined
}
