import { GenerationRequestSchema, type GenerationRequest, type Profile, type SubjectType } from '@bh/core/schemas'

export const COUNT_MIN = 1
export const COUNT_MAX = 200

export type RangeDraft = { enabled: boolean; min: string; max: string }
/** 只出存款账户版式，所以页面只提供支票账户；`credit` 仍留在请求类型里兼容历史数据。 */
export type AccountKey = 'checking'
export type AccountsDraft = Record<AccountKey, RangeDraft>

export const ACCOUNT_LABELS: Record<AccountKey, string> = { checking: '支票账户' }

export const DEFAULT_ACCOUNTS: AccountsDraft = {
  checking: { enabled: true, min: '20', max: '40' },
}

const isCount = (s: string) => /^\d+$/.test(s.trim()) && Number(s) >= COUNT_MIN && Number(s) <= COUNT_MAX

/** A Chinese message for a bad range, or `undefined` when it is fine. Only ticked accounts are checked. */
export function rangeError(r: RangeDraft): string | undefined {
  if (!isCount(r.min) || !isCount(r.max)) return `请输入 ${COUNT_MIN} 到 ${COUNT_MAX} 之间的整数`
  return Number(r.min) > Number(r.max) ? '最小值不能大于最大值' : undefined
}

export function accountsError(accounts: AccountsDraft): string | undefined {
  return accounts.checking.enabled ? undefined : '请至少选择一个账户'
}

/** 版式选项：一套版式就是一家银行，按账单主体分个人/企业版。 */
export type TemplateChoice = { id: string; label: string }

/** 引擎里 `templates: []` 仍表示「交给推荐器」，但界面只让人手选，这里不再暴露该选项。 */
export const TEMPLATE_AUTO = 'auto'

export const TEMPLATE_CHOICES: Record<SubjectType, readonly TemplateChoice[]> = {
  personal: [
    { id: 'boa-personal', label: '美国银行 · 个人账单' },
    { id: 'chase-personal', label: '大通银行 · 个人账单' },
    { id: 'usbank-personal', label: '美国合众银行 · 个人账单' },
    { id: 'wellsfargo', label: '富国银行' },
    { id: 'capitalone', label: '第一资本银行' },
    { id: 'mercury', label: '水星银行' },
    { id: 'sofi', label: '索菲银行' },
  ],
  business: [
    { id: 'boa-business', label: '美国银行 · 商业账单' },
    { id: 'chase-business', label: '大通银行 · 商业账单' },
    { id: 'usbank-business', label: '美国合众银行 · 商业账单' },
  ],
}

/** 出片选择：一套版式。个人/企业各有一套清单，默认取该主体的第一项。 */
export type RenderDraft = { template: string }

export function defaultRender(subjectType: SubjectType): RenderDraft {
  return { template: TEMPLATE_CHOICES[subjectType][0]!.id }
}

export const DEFAULT_RENDER: RenderDraft = defaultRender('personal')

/** 只有「没选」才算错：下拉里只剩具体版式，选中即合法。 */
export function renderError(draft: RenderDraft): string | undefined {
  if (!draft.template || draft.template === TEMPLATE_AUTO) return '请选择要出的账单版式'
  return undefined
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
  /** 出哪套版式 —— 属于这一次生成。 */
  render?: RenderDraft
}

/** The request body, or `undefined` while anything on the form is invalid. */
export function buildRequest(input: RequestInput): GenerationRequest | undefined {
  const { accounts } = input
  const render = input.render ?? DEFAULT_RENDER
  if (accountsError(accounts) || endMonthError(input.endMonth) || renderError(render)) return undefined
  const ranges: GenerationRequest['accounts'] = {}
  const checking = accounts.checking
  if (checking.enabled) {
    if (rangeError(checking)) return undefined
    ranges.checking = { min: Number(checking.min), max: Number(checking.max) }
  }
  const seed = input.seed.trim()
  const parsed = GenerationRequestSchema.safeParse({
    subjectType: input.subjectType,
    ...(input.profile && { profile: input.profile }),
    accounts: ranges,
    months: input.months,
    endMonth: input.endMonth,
    templates: [render.template],
    ...(seed !== '' && { seed }),
  })
  return parsed.success ? parsed.data : undefined
}
