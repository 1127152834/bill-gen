import { useState } from 'react'
import type { Statement } from '@bh/core/schemas'
import TransactionRow from './TransactionRow'

/** The statement's transaction list; rows expand independently. Scrolls sideways inside its own box on phones. */
export default function TransactionTable({ statement }: { statement: Statement }) {
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set())
  const showBalance = statement.accountType !== 'credit'
  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev)
      if (!next.delete(id)) next.add(id)
      return next
    })

  return (
    <div className="overflow-x-auto rounded-xl border border-line bg-surface">
      <table aria-label={`交易明细 ****${statement.accountLast4}`} className="w-full min-w-[30rem] text-sm">
        <thead className="bg-canvas text-left text-xs text-muted">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">入账日</th>
            <th scope="col" className="px-3 py-2 font-medium">描述</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">金额</th>
            {showBalance && (
              <th scope="col" className="px-3 py-2 text-right font-medium">余额</th>
            )}
          </tr>
        </thead>
        <tbody>
          {statement.transactions.map((tx) => (
            <TransactionRow key={tx.id} tx={tx} showBalance={showBalance} expanded={open.has(tx.id)} onToggle={() => toggle(tx.id)} />
          ))}
        </tbody>
      </table>
      {statement.transactions.length === 0 && <p className="p-4 text-sm text-muted">这个账户没有交易。</p>}
    </div>
  )
}
