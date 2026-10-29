import type { RollKey } from '../hooks/usePersonaRandomizer'
import type { Draft, InputErrors, InputKey } from '../lib/profileDraft'

/** Everything a field needs from the profile form: the draft, its errors and the 🎲 state. */
export type FormCtx = {
  draft: Draft
  set: (patch: Partial<Draft>) => void
  errors: InputErrors
  /** "Required" errors stay quiet until the user tries to move on. */
  showMissing: boolean
  pending: ReadonlySet<RollKey>
  failures: Partial<Record<RollKey, string>>
  roll: (key: RollKey) => void
}

export function errorFor(ctx: FormCtx, key: InputKey): string | undefined {
  const e = ctx.errors[key]
  return e && (e.kind === 'invalid' || ctx.showMissing) ? e.message : undefined
}

/** Props shared by every plain input inside a `FieldShell`. */
export function inputProps(ctx: FormCtx, id: string, key: InputKey) {
  const error = errorFor(ctx, key)
  return {
    id,
    className: 'input',
    'aria-invalid': error ? true : undefined,
    'aria-describedby': error ? `${id}-error` : undefined,
  } as const
}
