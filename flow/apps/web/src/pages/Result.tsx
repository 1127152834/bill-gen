import { StatementSchema, type GenerationResult, type Statement } from '@bh/core/schemas'
import type { GenerationView } from '../api'
import AuditPanel from '../components/AuditPanel'
import MonthFilesPanel from '../components/MonthFilesPanel'
import RendersPanel from '../components/RendersPanel'
import ResultActions from '../components/ResultActions'
import StatementView from '../components/StatementView'
import { STATUS_LABEL, describeFailure, formatDateTime } from '../lib/format'

function isFullResult(r: GenerationView['result']): r is GenerationResult {
  return r !== null && !('partial' in r)
}

/** Statements of a budget-stopped job: whatever still parses as a statement. */
function partialStatements(r: GenerationView['result']): Statement[] {
  if (r === null || !('partial' in r)) return []
  return r.statements.flatMap((s) => {
    const parsed = StatementSchema.safeParse(s)
    return parsed.success ? [parsed.data] : []
  })
}

/** A settled job: statement preview with audit side panel, or the reason there is none. */
export default function Result({ view, onChanged }: { view: GenerationView; onChanged: () => void }) {
  const full = isFullResult(view.result) ? view.result : null
  const statements = full ? full.statements : partialStatements(view.result)
  const partial = !full && view.result !== null
  const exportable = full !== null && (view.status === 'completed' || view.status === 'completed_with_warnings')
  const subject = full?.subject
  const title = subject ? ('name' in subject ? subject.name : subject.legalName) : '生成结果'

  return (
    <div className="space-y-4">
      <section className="card">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-xl font-semibold">{title}</h1>
            <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
              <span>{STATUS_LABEL[view.status]}</span>
              <span>{formatDateTime(view.createdAt)}</span>
              {(view.request?.seed ?? full?.seed) && (
                <span>
                  seed <code className="font-mono">{view.request?.seed ?? full?.seed}</code>
                </span>
              )}
            </p>
          </div>
          <ResultActions view={view} exportable={exportable} {...(full && { resultSeed: full.seed })} onChanged={onChanged} />
        </div>

        {view.status === 'failed' && (
          <p role="alert" className="mt-4 rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
            {describeFailure(view.error)}
          </p>
        )}
        {view.status === 'interrupted' && (
          <p role="status" className="mt-4 rounded-lg bg-warn-soft px-3 py-2 text-sm text-warn">
            服务重启时这个任务被中断了。点击“继续生成”可以从上一个检查点接着跑。
          </p>
        )}
        {partial && (
          <p role="status" className="mt-4 rounded-lg bg-warn-soft px-3 py-2 text-sm text-warn">
            这是部分结果：任务在用量上限处停止，下面的账单只读，未经完整校验，不能导出。
          </p>
        )}
      </section>

      {full && <MonthFilesPanel files={full.monthFiles} />}
      {full && <RendersPanel renders={full.renders} />}

      {statements.length > 0 && (
        <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <StatementView statements={statements} />
          {full && <AuditPanel result={full} />}
        </div>
      )}
    </div>
  )
}
