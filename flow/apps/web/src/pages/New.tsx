import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import type { SubjectType } from '@bh/core/schemas'
import { ApiError, createGeneration } from '../api'
import { AccountRangeInput } from '../components/AccountRangeInput'
import { ProfileForm } from '../components/ProfileForm'
import { StepIndicator } from '../components/StepIndicator'
import { EMPTY_DRAFT, buildProfile, hasErrors, inputErrors, type Draft } from '../lib/profileDraft'
import {
  DEFAULT_ACCOUNTS,
  DEFAULT_RENDER,
  TEMPLATE_CHOICES,
  accountsError,
  buildRequest,
  endMonthError,
  previousMonth,
  renderError,
  type AccountsDraft,
  type RenderDraft,
} from '../lib/requestForm'

type Step = 1 | 2 | 3
const MONTH_CHOICES = [1, 3, 6] as const
const SUBJECTS: ReadonlyArray<{ value: SubjectType; title: string; text: string }> = [
  { value: 'personal', title: '个人', text: '自由职业者或个体户，收入来自多个客户与平台' },
  { value: 'business', title: '企业', text: '小微企业，含收款、供应商货款与薪资支出' },
]
const STEP_TITLES: Record<Step, string> = { 1: '选择账单主体', 2: '填写资料', 3: '账户与周期' }

/** The three-step "new generation" wizard: type → persona → accounts and period. */
export default function New() {
  const navigate = useNavigate()
  const [step, setStep] = useState<Step>(1)
  const [subject, setSubject] = useState<SubjectType | null>(null)
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT)
  const [systemPersona, setSystemPersona] = useState(false)
  const [showMissing, setShowMissing] = useState(false)
  const [accounts, setAccounts] = useState<AccountsDraft>(DEFAULT_ACCOUNTS)
  const [render, setRender] = useState<RenderDraft>(DEFAULT_RENDER)
  const [months, setMonths] = useState<1 | 3 | 6>(3)
  const [endMonth, setEndMonth] = useState(() => previousMonth())
  const [seed, setSeed] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [serverError, setServerError] = useState<string | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const firstRender = useRef(true)

  useEffect(() => {
    if (firstRender.current) firstRender.current = false
    else heading.current?.focus()
  }, [step])

  const goto = (next: Step) => {
    setServerError(null)
    setStep(next)
  }

  const next2 = () => {
    if (subject === null) return
    if (!systemPersona && hasErrors(inputErrors(subject, draft))) {
      setShowMissing(true)
      // Wait for the errors to render, then put the cursor on the first bad field.
      requestAnimationFrame(() => document.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus())
      return
    }
    goto(3)
  }

  const profile = subject && !systemPersona ? buildProfile(subject, draft) : undefined
  // 换主体类型时，原来选的版式可能不属于它（个人版/企业版是两套底稿），回落到该主体的第一项。
  const templateChoices = TEMPLATE_CHOICES[subject ?? 'personal']
  const activeTemplate = templateChoices.some((c) => c.id === render.template)
    ? render.template
    : templateChoices[0]!.id
  const effectiveRender: RenderDraft = { template: activeTemplate }
  const request =
    subject && (systemPersona || profile)
      ? buildRequest({
          subjectType: subject,
          profile,
          accounts,
          months,
          endMonth,
          seed,
          render: effectiveRender,
        })
      : undefined
  const noAccount = accountsError(accounts)
  const monthError = endMonthError(endMonth)
  const templateError = renderError(effectiveRender)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!request || submitting) return
    setSubmitting(true)
    setServerError(null)
    try {
      const { id } = await createGeneration(request)
      navigate(`/g/${id}`)
    } catch (err) {
      setServerError(err instanceof ApiError ? err.message : '提交失败，请重试')
      setSubmitting(false)
    }
  }

  return (
    <section>
      <StepIndicator current={step} />
      <div className="card">
        <h1 ref={heading} tabIndex={-1} className="mb-1 text-xl font-semibold outline-none">
          {STEP_TITLES[step]}
        </h1>

        {serverError && (
          <div role="alert" className="mt-3 rounded-lg border border-danger/30 bg-danger-soft p-3 text-sm text-danger">
            <p className="font-medium">无法按当前参数生成</p>
            <p className="mt-0.5">{serverError}</p>
          </div>
        )}

        {step === 1 && (
          <>
            <p className="mb-4 text-sm text-muted">账单属于谁？之后可以单独调整每个字段。</p>
            <fieldset className="m-0 grid min-w-0 gap-3 border-0 p-0 sm:grid-cols-2">
              <legend className="sr-only">账单主体类型</legend>
              {SUBJECTS.map((s) => (
                <label key={s.value} className="choice">
                  <input
                    type="radio"
                    name="subjectType"
                    className="mt-1 size-4 accent-accent"
                    checked={subject === s.value}
                    onChange={() => setSubject(s.value)}
                  />
                  <span>
                    <span className="block font-medium">{s.title}</span>
                    <span className="block text-sm text-muted">{s.text}</span>
                  </span>
                </label>
              ))}
            </fieldset>
            <div className="mt-6 flex justify-end">
              <button type="button" className="btn btn-primary" disabled={subject === null} onClick={() => goto(2)}>
                下一步
              </button>
            </div>
          </>
        )}

        {step === 2 && subject && (
          <>
            <div className="mt-4">
              <ProfileForm
                key={subject}
                subject={subject}
                draft={draft}
                setDraft={setDraft}
                systemPersona={systemPersona}
                onSystemPersona={setSystemPersona}
                showMissing={showMissing}
              />
            </div>
            <div className="mt-6 flex justify-between">
              <button type="button" className="btn" onClick={() => goto(1)}>
                上一步
              </button>
              <button type="button" className="btn btn-primary" onClick={next2}>
                下一步
              </button>
            </div>
          </>
        )}

        {step === 3 && (
          <form onSubmit={submit} noValidate>
            <p className="mb-4 text-sm text-muted">选择要生成的账户，并为每个账户设定每月交易条数。</p>
            <div className="grid gap-3">
              <AccountRangeInput
                account="checking"
                value={accounts.checking}
                onChange={(v) => setAccounts((a) => ({ ...a, checking: v }))}
              />
            </div>
            {noAccount && <p className="field-error">{noAccount}</p>}

            <fieldset className="m-0 mt-6 min-w-0 border-0 p-0">
              <legend className="field-label mb-2">出哪家银行的账单</legend>
              <div className="grid gap-3 sm:items-start">
                <label className="block">
                  <select
                    className="input"
                    aria-label="目标版式"
                    value={activeTemplate}
                    onChange={(e) => setRender((r) => ({ ...r, template: e.target.value }))}
                  >
                    {templateChoices.map((choice) => (
                      <option key={choice.id} value={choice.id}>
                        {choice.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              {templateError ? (
                <p className="field-error">{templateError}</p>
              ) : (
                <p className="mt-1 text-xs text-muted">
                  选哪家银行就出哪家的账单：版式和银行是同一个东西，纸上的行名、电话、信箱都随版式走。
                </p>
              )}
            </fieldset>

            <fieldset className="m-0 mt-6 min-w-0 border-0 p-0">
              <legend className="field-label mb-2">覆盖月数</legend>
              <div className="flex gap-2">
                {MONTH_CHOICES.map((m) => (
                  <label key={m} className="choice flex-1 items-center justify-center gap-2 px-2 py-2.5 whitespace-nowrap">
                    <input
                      type="radio"
                      name="months"
                      className="size-4 accent-accent"
                      checked={months === m}
                      onChange={() => setMonths(m)}
                    />
                    <span className="tabular text-sm font-medium">{m} 个月</span>
                  </label>
                ))}
              </div>
            </fieldset>

            <div className="mt-6">
              <label htmlFor="f-endMonth" className="field-label mb-1 block">
                结束月份
              </label>
              <input
                id="f-endMonth"
                type="month"
                className="input sm:max-w-56"
                max={previousMonth()}
                value={endMonth}
                aria-invalid={monthError ? true : undefined}
                aria-describedby={monthError ? 'f-endMonth-error' : undefined}
                onChange={(e) => setEndMonth(e.target.value)}
              />
              {monthError ? (
                <p id="f-endMonth-error" className="field-error">
                  {monthError}
                </p>
              ) : (
                <p className="mt-1 text-xs text-muted">账单周期的最后一个月，默认为上个月。</p>
              )}
            </div>

            <details className="mt-6 rounded-lg border border-line px-3 py-2">
              <summary className="cursor-pointer text-sm font-medium">高级选项</summary>
              <div className="pt-3">
                <label htmlFor="f-seed" className="field-label mb-1 block">
                  随机种子
                </label>
                <input
                  id="f-seed"
                  className="input"
                  autoComplete="off"
                  value={seed}
                  onChange={(e) => setSeed(e.target.value)}
                  placeholder="留空则自动生成"
                />
                <p className="mt-1 text-xs text-muted">相同的种子与参数会得到相同的结果。</p>
              </div>
            </details>

            <div className="mt-6 flex justify-between">
              <button type="button" className="btn" onClick={() => goto(2)} disabled={submitting}>
                上一步
              </button>
              <button type="submit" className="btn btn-primary" disabled={!request || submitting}>
                {submitting ? (
                  <>
                    <span className="spinner" aria-hidden="true" />
                    正在提交…
                  </>
                ) : (
                  '生成账单'
                )}
              </button>
            </div>
          </form>
        )}
      </div>
    </section>
  )
}
