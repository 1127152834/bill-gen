import { ACCOUNT_LABELS, COUNT_MAX, COUNT_MIN, rangeError, type AccountKey, type RangeDraft } from '../lib/requestForm'

type Props = {
  account: AccountKey
  value: RangeDraft
  onChange: (next: RangeDraft) => void
}

const HINT: Record<AccountKey, string> = {
  checking: '日常收支账户，含工资/收款与各项扣款',
}

function asCount(s: string): number | undefined {
  return /^\d+$/.test(s.trim()) ? Number(s) : undefined
}

const clamp = (n: number) => Math.min(COUNT_MAX, Math.max(COUNT_MIN, n))

/** One account: a checkbox, and once ticked its monthly transaction-count range (two numbers and a dual-thumb slider). */
export function AccountRangeInput({ account, value, onChange }: Props) {
  const label = ACCOUNT_LABELS[account]
  const error = value.enabled ? rangeError(value) : undefined
  const minNum = asCount(value.min)
  const maxNum = asCount(value.max)
  // Slider positions follow the numbers when they are usable, and sit at the ends otherwise.
  const lo = clamp(minNum ?? COUNT_MIN)
  const hi = clamp(maxNum ?? COUNT_MAX)
  const pct = (n: number) => ((n - COUNT_MIN) / (COUNT_MAX - COUNT_MIN)) * 100
  const errId = `${account}-range-error`

  return (
    <div className="rounded-xl border border-line bg-surface p-4 has-[:checked]:border-accent/60">
      <label className="flex cursor-pointer items-start gap-3">
        <input
          type="checkbox"
          className="mt-1 size-4 accent-accent"
          checked={value.enabled}
          onChange={(e) => onChange({ ...value, enabled: e.target.checked })}
          aria-label={label}
        />
        <span>
          <span className="block text-sm font-medium">{label}</span>
          <span className="block text-sm text-muted">{HINT[account]}</span>
        </span>
      </label>

      {value.enabled && (
        <div className="mt-4 border-t border-line pt-4">
          <p className="mb-2 text-sm text-muted">每月交易条数区间（{COUNT_MIN}–{COUNT_MAX}）</p>
          <div className="dual-range mb-3" role="group" aria-label={`${label} 条数区间`}>
            <div className="track" />
            <div className="fill" style={{ left: `${pct(lo)}%`, width: `${Math.max(0, pct(hi) - pct(lo))}%` }} />
            <input
              type="range"
              min={COUNT_MIN}
              max={COUNT_MAX}
              step={1}
              value={lo}
              aria-label={`${label} 最小值滑块`}
              onChange={(e) => onChange({ ...value, min: String(Math.min(Number(e.target.value), maxNum ?? COUNT_MAX)) })}
            />
            <input
              type="range"
              min={COUNT_MIN}
              max={COUNT_MAX}
              step={1}
              value={hi}
              aria-label={`${label} 最大值滑块`}
              onChange={(e) => onChange({ ...value, max: String(Math.max(Number(e.target.value), minNum ?? COUNT_MIN)) })}
            />
          </div>
          <div className="flex items-center gap-2 sm:gap-3">
            <label className="flex items-center gap-2 text-sm whitespace-nowrap">
              <span className="text-muted">最少</span>
              <input
                type="number"
                inputMode="numeric"
                className="input w-20 sm:w-24"
                min={COUNT_MIN}
                max={COUNT_MAX}
                value={value.min}
                aria-label={`${label} 最小值`}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? errId : undefined}
                onChange={(e) => onChange({ ...value, min: e.target.value })}
              />
            </label>
            <span aria-hidden="true" className="text-muted">
              –
            </span>
            <label className="flex items-center gap-2 text-sm whitespace-nowrap">
              <span className="text-muted">最多</span>
              <input
                type="number"
                inputMode="numeric"
                className="input w-20 sm:w-24"
                min={COUNT_MIN}
                max={COUNT_MAX}
                value={value.max}
                aria-label={`${label} 最大值`}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? errId : undefined}
                onChange={(e) => onChange({ ...value, max: e.target.value })}
              />
            </label>
          </div>
          {error && (
            <p id={errId} className="field-error">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
