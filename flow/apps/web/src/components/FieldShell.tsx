import type { ReactNode } from 'react'
import type { FieldKey, InputKey } from '../lib/profileDraft'
import { errorFor, type FormCtx } from './formCtx'

export function DiceButton({ label, rollKey, ctx }: { label: string; rollKey: FieldKey; ctx: FormCtx }) {
  const busy = ctx.pending.has(rollKey)
  return (
    <button
      type="button"
      className="btn-dice"
      aria-label={`随机生成 ${label}`}
      title="随机生成"
      disabled={busy}
      onClick={() => ctx.roll(rollKey)}
    >
      {busy ? <span className="spinner" role="status" aria-label="生成中" /> : <span aria-hidden="true">🎲</span>}
    </button>
  )
}

export function RollFailure({ ctx, rollKey }: { ctx: FormCtx; rollKey: FieldKey }) {
  const message = ctx.failures[rollKey]
  return message ? <p className="field-error">{message}</p> : null
}

type ShellProps = {
  id: string
  label: string
  rollKey: FieldKey
  ctx: FormCtx
  /** The input whose error is shown under the field (none for tag lists, which cannot be invalid). */
  errorKey?: InputKey
  hint?: string
  children: ReactNode
}

/** Label, 🎲 button, control and Chinese error message of one field. */
export function FieldShell({ id, label, rollKey, ctx, errorKey, hint, children }: ShellProps) {
  const error = errorKey ? errorFor(ctx, errorKey) : undefined
  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-2">
        <label htmlFor={id} className="field-label">
          {label}
        </label>
        <DiceButton label={label} rollKey={rollKey} ctx={ctx} />
      </div>
      {children}
      {hint && !error && <p className="mt-1 text-xs text-muted">{hint}</p>}
      {error && (
        <p id={`${id}-error`} className="field-error">
          {error}
        </p>
      )}
      <RollFailure ctx={ctx} rollKey={rollKey} />
    </div>
  )
}
