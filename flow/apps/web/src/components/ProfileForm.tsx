import type { Dispatch, SetStateAction } from 'react'
import type { SubjectType } from '@bh/core/schemas'
import { usePersonaRandomizer } from '../hooks/usePersonaRandomizer'
import { inputErrors, type Draft } from '../lib/profileDraft'
import { BusinessFields } from './BusinessFields'
import type { FormCtx } from './formCtx'
import { PersonalFields } from './PersonalFields'

type Props = {
  subject: SubjectType
  draft: Draft
  setDraft: Dispatch<SetStateAction<Draft>>
  /** Submit without a profile and let the server make one up. */
  systemPersona: boolean
  onSystemPersona: (on: boolean) => void
  showMissing: boolean
}

/** Step 2: the persona fields with a 🎲 beside each, "全部随机" on top, and the "let the system do it" switch. */
export function ProfileForm({ subject, draft, setDraft, systemPersona, onSystemPersona, showMissing }: Props) {
  const { pending, failures, roll } = usePersonaRandomizer(subject, draft, setDraft)
  const ctx: FormCtx = {
    draft,
    set: (patch) => setDraft((d) => ({ ...d, ...patch })),
    errors: inputErrors(subject, draft),
    showMissing,
    pending,
    failures,
    roll: (key) => void roll(key),
  }
  const allBusy = pending.has('all')

  return (
    <div className="grid gap-5">
      <label className="choice">
        <input
          type="checkbox"
          className="mt-0.5 size-4 accent-accent"
          checked={systemPersona}
          onChange={(e) => onSystemPersona(e.target.checked)}
          aria-label="让系统随机生成人设"
        />
        <span>
          <span className="block text-sm font-medium">让系统随机生成人设</span>
          <span className="block text-sm text-muted">跳过填写，提交后由系统生成完整的人设。</span>
        </span>
      </label>

      {systemPersona ? (
        <p className="rounded-lg bg-canvas p-4 text-sm text-muted">
          资料将在生成时随机决定。取消勾选即可手动填写，或用 🎲 逐项随机。
        </p>
      ) : (
        <>
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-muted">已填的项会作为随机生成的参考；点各项旁的 🎲 只替换该项。</p>
            <button type="button" className="btn shrink-0" disabled={allBusy} onClick={() => ctx.roll('all')}>
              {allBusy ? <span className="spinner" role="status" aria-label="生成中" /> : <span aria-hidden="true">🎲</span>}
              全部随机
            </button>
          </div>
          {failures.all && <p className="field-error">{failures.all}</p>}
          {subject === 'personal' ? <PersonalFields ctx={ctx} /> : <BusinessFields ctx={ctx} />}
        </>
      )}
    </div>
  )
}
