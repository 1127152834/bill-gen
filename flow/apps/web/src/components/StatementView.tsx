import { useId, useState, type KeyboardEvent } from 'react'
import type { Statement } from '@bh/core/schemas'
import { ACCOUNT_LABEL, formatFullDate, formatMoney, formatPercent } from '../lib/format'
import MonthCard from './MonthCard'
import TransactionTable from './TransactionTable'

function tabLabel(s: Statement) {
  return `${ACCOUNT_LABEL[s.accountType].zh} ****${s.accountLast4}`
}

function Header({ statement: s }: { statement: Statement }) {
  const label = ACCOUNT_LABEL[s.accountType]
  return (
    <section aria-label="账单抬头" className="overflow-hidden rounded-xl border border-line bg-surface">
      <div className="h-1.5 bg-accent" aria-hidden="true" />
      <div className="flex flex-wrap items-start justify-between gap-x-8 gap-y-3 p-4 sm:p-5">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">{s.bankName || `${label.zh}对账单`}</h2>
          <p className="text-sm text-muted">
            {label.zh} · {label.en}
          </p>
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          <dt className="text-muted">账户尾号</dt>
          <dd className="tabular font-mono">****{s.accountLast4}</dd>
          <dt className="text-muted">账单周期</dt>
          <dd className="tabular">
            {formatFullDate(s.periodStart)} – {formatFullDate(s.periodEnd)}
          </dd>
          {s.credit && (
            <>
              <dt className="text-muted">信用额度</dt>
              <dd className="tabular">{formatMoney(s.credit.creditLimitCents)}</dd>
              <dt className="text-muted">年利率 APR</dt>
              <dd className="tabular">{formatPercent(s.credit.apr)}</dd>
            </>
          )}
        </dl>
      </div>
    </section>
  )
}

/** One tab per account, each previewed as a fictional-bank statement: header, monthly cards, transactions. */
export default function StatementView({ statements }: { statements: Statement[] }) {
  const [index, setIndex] = useState(0)
  const base = useId()
  const current = statements[Math.min(index, statements.length - 1)]
  if (!current) return null
  const active = Math.min(index, statements.length - 1)

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0
    if (!step) return
    e.preventDefault()
    const next = (active + step + statements.length) % statements.length
    setIndex(next)
    document.getElementById(`${base}-tab-${next}`)?.focus()
  }

  return (
    <div className="min-w-0 space-y-4">
      <div role="tablist" aria-label="账户" onKeyDown={onKeyDown} className="flex gap-1 overflow-x-auto overflow-y-hidden border-b border-line">
        {statements.map((s, i) => (
          <button
            key={`${s.accountType}-${s.accountLast4}`}
            id={`${base}-tab-${i}`}
            role="tab"
            type="button"
            aria-selected={i === active}
            aria-controls={`${base}-panel`}
            tabIndex={i === active ? 0 : -1}
            onClick={() => setIndex(i)}
            className={`-mb-px shrink-0 border-b-2 px-4 py-2 text-sm font-medium whitespace-nowrap ${
              i === active ? 'border-accent text-accent-strong' : 'border-transparent text-muted hover:text-ink'
            }`}
          >
            {tabLabel(s)}
          </button>
        ))}
      </div>
      <div id={`${base}-panel`} role="tabpanel" aria-labelledby={`${base}-tab-${active}`} className="space-y-4">
        <Header statement={current} />
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {current.months.map((m) => (
            <MonthCard key={m.cycleStart} month={m} type={current.accountType} />
          ))}
        </div>
        <TransactionTable key={`${current.accountType}-${current.accountLast4}`} statement={current} />
      </div>
    </div>
  )
}
