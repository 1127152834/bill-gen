import type { Transaction } from '@bh/core/schemas'
import { domainOf, formatMoney, formatMonthDay, safeHttpUrl } from '../lib/format'

type Props = {
  tx: Transaction
  showBalance: boolean
  expanded: boolean
  onToggle: () => void
}

function Detail({ tx }: { tx: Transaction }) {
  const link = tx.evidence ? safeHttpUrl(tx.evidence.sourceUrl) : null
  return (
    <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[auto_1fr]">
      <dt className="text-muted">交易日 / 类型</dt>
      <dd>
        {formatMonthDay(tx.transactionDate)} · {tx.type}
      </dd>
      {tx.memo && (
        <>
          <dt className="text-muted">备注</dt>
          <dd>{tx.memo}</dd>
        </>
      )}
      {tx.merchant && (
        <>
          <dt className="text-muted">商户</dt>
          <dd>
            <span>
              {tx.merchant.name} · {tx.merchant.city}, {tx.merchant.state}
            </span>{' '}
            <span className="text-muted">（{tx.merchant.category}）</span>
          </dd>
        </>
      )}
      {tx.evidence && (
        <>
          <dt className="text-muted">依据</dt>
          <dd>
            {link ? (
              <a href={link} target="_blank" rel="noopener noreferrer" className="text-accent underline-offset-2 hover:underline">
                {domainOf(tx.evidence.sourceUrl)}
              </a>
            ) : (
              <span className="break-all text-muted">{tx.evidence.sourceUrl}</span>
            )}
            <p className="mt-0.5 text-muted">{tx.evidence.note}</p>
          </dd>
        </>
      )}
    </dl>
  )
}

/** One transaction line plus, when expanded, its memo, merchant and evidence. The whole row is clickable. */
export default function TransactionRow({ tx, showBalance, expanded, onToggle }: Props) {
  const credit = tx.direction === 'credit'
  const detailId = `tx-${tx.id}`
  return (
    <>
      <tr onClick={onToggle} className={`cursor-pointer border-t border-line hover:bg-canvas ${expanded ? 'bg-canvas' : ''}`}>
        <td className="tabular px-3 py-2 whitespace-nowrap text-muted">{formatMonthDay(tx.postedDate)}</td>
        <td className="px-3 py-2">
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={detailId}
            className="text-left font-mono text-[13px] leading-snug break-words"
          >
            {tx.description}
          </button>
        </td>
        <td className={`tabular px-3 py-2 text-right whitespace-nowrap ${credit ? 'font-medium text-credit' : ''}`}>
          <span>{credit ? '+' : '−'}</span>
          {formatMoney(tx.amountCents)}
        </td>
        {showBalance && (
          <td className="tabular px-3 py-2 text-right whitespace-nowrap text-muted">
            {tx.runningBalanceCents !== undefined ? formatMoney(tx.runningBalanceCents) : ''}
          </td>
        )}
      </tr>
      {expanded && (
        <tr id={detailId} className="bg-canvas">
          <td colSpan={showBalance ? 4 : 3} className="px-3 pt-1 pb-3">
            <Detail tx={tx} />
          </td>
        </tr>
      )}
    </>
  )
}
