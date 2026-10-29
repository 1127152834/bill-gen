const STEPS = ['选择类型', '填写资料', '账户与周期'] as const

/** 1 选择类型 → 2 填写资料 → 3 账户与周期. `current` is 1-based. */
export function StepIndicator({ current }: { current: 1 | 2 | 3 }) {
  return (
    <ol className="mb-6 flex items-start" aria-label="步骤">
      {STEPS.map((title, i) => {
        const n = i + 1
        const state = n < current ? 'done' : n === current ? 'current' : 'todo'
        return (
          <li
            key={title}
            className="relative flex min-w-0 flex-1 flex-col items-center gap-1.5 text-center"
            aria-current={state === 'current' ? 'step' : undefined}
          >
            {i > 0 && (
              <span
                aria-hidden="true"
                className={`absolute top-4 right-1/2 left-[-50%] h-0.5 ${n <= current ? 'bg-accent' : 'bg-line'}`}
              />
            )}
            <span
              className={`tabular relative z-10 grid size-8 place-items-center rounded-full border-2 text-sm font-semibold ${
                state === 'todo' ? 'border-line bg-surface text-muted' : 'border-accent bg-accent text-white'
              }`}
            >
              {state === 'done' ? '✓' : n}
            </span>
            <span className={`text-xs sm:text-sm ${state === 'current' ? 'font-semibold text-ink' : 'text-muted'}`}>
              <span className="sr-only">第 {n} 步：</span>
              {title}
            </span>
          </li>
        )
      })}
    </ol>
  )
}
