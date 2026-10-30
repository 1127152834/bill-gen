// Display helpers shared by the progress, result and history pages. UI text is Chinese; statement content
// (amounts, dates, bank names) follows US conventions.
import type { AuditFinding, Statement } from '@bh/core/schemas'
import type { GenerationStatus } from '../api'

const USD = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })

/** `123456` -> `$1,234.56`. */
export function formatMoney(cents: number): string {
  return USD.format(cents / 100)
}

/** `2026-07-01` -> `07/01`. */
export function formatMonthDay(date: string): string {
  const [, m, d] = date.split('-')
  return `${m}/${d}`
}

/** `2026-09-30` -> `09/30/2026`. */
export function formatFullDate(date: string): string {
  const [y, m, d] = date.split('-')
  return `${m}/${d}/${y}`
}

/** `0.2499` -> `24.99%`. */
export function formatPercent(rate: number): string {
  return `${(rate * 100).toFixed(2)}%`
}

/** An ISO timestamp as local `YYYY-MM-DD HH:mm` (history list). */
export function formatDateTime(iso: string): string {
  const t = new Date(iso)
  if (Number.isNaN(t.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())} ${p(t.getHours())}:${p(t.getMinutes())}`
}

/** The URL if it is http(s), else null: evidence and source links must never be `javascript:` and friends. */
export function safeHttpUrl(raw: string): string | null {
  try {
    const u = new URL(raw)
    return u.protocol === 'http:' || u.protocol === 'https:' ? raw : null
  } catch {
    return null
  }
}

/** `https://www.example.com/a` -> `example.com`; anything unparsable comes back unchanged. */
export function domainOf(raw: string): string {
  try {
    return new URL(raw).hostname.replace(/^www\./, '')
  } catch {
    return raw
  }
}

const SEVERITY_RANK = { high: 0, medium: 1, low: 2 } as const

/** High to low; equal severities keep their order. */
export function sortFindings(findings: AuditFinding[]): AuditFinding[] {
  return findings
    .map((f, i) => [f, i] as const)
    .sort(([a, i], [b, j]) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || i - j)
    .map(([f]) => f)
}

export const SEVERITY_LABEL = { high: '高', medium: '中', low: '低' } as const

export const ACCOUNT_LABEL: Record<Statement['accountType'], { zh: string; en: string }> = {
  checking: { zh: '支票账户', en: 'Checking' },
  business_checking: { zh: '企业支票账户', en: 'Business Checking' },
  credit: { zh: '信用卡', en: 'Credit Card' },
}

export const RULE_LABEL: Record<string, string> = {
  R1_count_in_range: '交易条数在区间内',
  R2_balance_continuity: '余额连续',
  R3_dates_in_period: '日期落在账单期内',
  R4_recurring_consistency: '周期性交易一致',
  R5_card_payment_match: '信用卡还款对得上',
  R6_amount_within_range: '金额在合理区间',
  R7_geo_consistency: '地理位置一致',
  R8_no_duplicates: '没有重复交易',
  R9_evidence_present: '有调研依据',
  R10_no_payroll_income_personal: '个人账户无工资式收入',
  R11_credit_within_limit: '信用卡余额未超额度',
  R12_no_placeholder_names: '没有占位式公司名',
  R13_occupation_category_fit: '消费类目符合职业',
  R14_variety_floor: '商户足够多样',
  R15_no_gateway_income: '没有线上支付/平台结算收入',
}

export const STATUS_LABEL: Record<GenerationStatus, string> = {
  queued: '排队中',
  running: '生成中',
  completed: '已完成',
  completed_with_warnings: '完成（有警告）',
  failed: '失败',
  interrupted: '已中断',
}

export const SUBJECT_LABEL = { personal: '个人', business: '企业' } as const

const FAILURE_REASON: Record<string, string> = {
  BUDGET_EXCEEDED: '本次任务的模型调用、搜索次数或时长超出了上限',
  LLM_CALL_FAILED: '语言模型请求失败（已重试）',
  LLM_OUTPUT_INVALID: '语言模型多次返回了无法使用的结果',
  SCHEDULE_INFEASIBLE: '在给定的条数区间内排不出合理的交易',
  LIFE_MODEL_INVALID: '生活模型多次不符合要求',
  INFEASIBLE_REQUEST: '请求的参数无法满足',
  GENERATION_FAILED: '生成过程中出现未知错误',
}

/** A stored job error as one Chinese sentence that still names the machine code. */
export function describeFailure(error: { code?: string; message?: string } | null): string {
  if (!error?.code) return '生成失败，没有记录具体原因。'
  const reason = FAILURE_REASON[error.code] ?? error.message ?? '未知原因'
  return `生成失败：${reason}（${error.code}）`
}
