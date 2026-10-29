import type { MonthSummary, Statement } from '@bh/core/schemas'
import { formatMoney, formatMonthDay } from '../lib/format'

function Item({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div>
      <dt className="text-xs text-muted">{label}</dt>
      <dd className={`tabular ${strong ? 'font-semibold' : ''}`}>{value}</dd>
    </div>
  )
}

/** One statement cycle: opening, money in, money out, closing - plus the card's amounts due. */
export default function MonthCard({ month, type }: { month: MonthSummary; type: Statement['accountType'] }) {
  const credit = type === 'credit'
  return (
    <div
      role="group"
      aria-label={`账期 ${formatMonthDay(month.cycleStart)} – ${formatMonthDay(month.cycleEnd)}`}
      className="rounded-xl border border-line bg-surface p-4"
    >
      <h3 className="mb-3 text-sm font-semibold">
        {formatMonthDay(month.cycleStart)} – {formatMonthDay(month.cycleEnd)}
        <span className="ml-2 text-xs font-normal text-muted">{month.transactionCount} 笔</span>
      </h3>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        <Item label="期初" value={formatMoney(month.openingBalanceCents)} />
        <Item label={credit ? '还款与退款' : '收入'} value={formatMoney(month.totalCreditsCents)} />
        <Item label="支出" value={formatMoney(month.totalDebitsCents)} />
        <Item label="期末" value={formatMoney(month.closingBalanceCents)} strong />
      </dl>
      {month.credit && (
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 border-t border-line pt-3 text-sm">
          <Item label="应还" value={formatMoney(month.credit.statementBalanceCents)} strong />
          <Item label="最低还款" value={formatMoney(month.credit.minimumDueCents)} />
          <Item label="到期日" value={formatMonthDay(month.credit.dueDate)} />
          {month.credit.interestChargedCents > 0 && <Item label="利息" value={formatMoney(month.credit.interestChargedCents)} />}
        </dl>
      )}
    </div>
  )
}
