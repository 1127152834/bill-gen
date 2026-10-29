import type { GenerationResult, RuleResult } from '@bh/core/schemas'
import { RULE_LABEL, SEVERITY_LABEL, sortFindings } from '../lib/format'

const SEVERITY_STYLE = {
  high: 'bg-danger-soft text-danger',
  medium: 'bg-warn-soft text-warn',
  low: 'bg-canvas text-muted',
} as const

function ruleNumber(r: RuleResult): number {
  return Number.parseInt(r.ruleId.slice(1), 10)
}

/** R1-R11 results, audit findings (high first), warnings and usage. */
export default function AuditPanel({ result }: { result: GenerationResult }) {
  const rules = [...result.audit.rules].sort((a, b) => ruleNumber(a) - ruleNumber(b))
  const findings = sortFindings(result.audit.findings)
  const passed = rules.filter((r) => r.passed).length

  return (
    <aside aria-label="审计报告" className="min-w-0 space-y-4">
      <section className="card !p-4">
        <h2 className="mb-1 font-semibold">规则校验</h2>
        <p className="mb-3 text-xs text-muted">
          {passed}/{rules.length} 条通过
        </p>
        <ul className="space-y-2 text-sm">
          {rules.map((r) => (
            <li key={r.ruleId}>
              <div className="flex items-baseline gap-2">
                <span className="font-mono text-xs text-muted">R{ruleNumber(r)}</span>
                <span className="flex-1">{RULE_LABEL[r.ruleId] ?? r.ruleId}</span>
                <span className={r.passed ? 'text-credit' : 'text-danger'} role="img" aria-label={r.passed ? '通过' : '未通过'}>
                  {r.passed ? '✓' : '✗'}
                </span>
              </div>
              {!r.passed && r.violations.length > 0 && (
                <ul className="mt-1 ml-6 list-disc space-y-0.5 text-xs text-danger">
                  {r.violations.map((v, i) => (
                    <li key={i}>{v.message}</li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      </section>

      <section className="card !p-4">
        <h2 className="mb-3 font-semibold">审计问题</h2>
        {findings.length === 0 ? (
          <p className="text-sm text-muted">没有发现问题。</p>
        ) : (
          <ul aria-label="审计问题" className="space-y-3 text-sm">
            {findings.map((f, i) => (
              <li key={i} className="flex gap-2">
                <span className={`badge shrink-0 self-start ${SEVERITY_STYLE[f.severity]}`}>{SEVERITY_LABEL[f.severity]}</span>
                <div className="min-w-0">
                  <p>{f.problem}</p>
                  <p className="text-xs text-muted">
                    涉及 {f.transactionIds.length} 笔 · 建议回到 {f.suggestedTarget}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {result.warnings.length > 0 && (
        <section className="rounded-xl border border-warn/30 bg-warn-soft p-4">
          <h2 className="mb-2 font-semibold text-warn">警告</h2>
          <ul aria-label="警告" className="list-disc space-y-1 pl-5 text-sm text-warn">
            {result.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </section>
      )}

      <p className="px-1 text-xs text-muted">
        模型调用 {result.usage.llmCalls} 次 · 搜索 {result.usage.tavilyQueries} 次
      </p>
    </aside>
  )
}
