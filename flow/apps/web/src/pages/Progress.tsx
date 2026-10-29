import Timeline from '../components/Timeline'
import { useProgress } from '../hooks/useProgress'
import { domainOf, safeHttpUrl } from '../lib/format'
import type { GenerationStatus } from '../api'

/** Live view of a queued or running job. `onSettled` fires when the server says the job is over. */
export default function Progress({ id, onSettled }: { id: string; onSettled: (status: GenerationStatus) => void }) {
  const { nodes, research, rules, retries, warnings, notes, disconnected } = useProgress(id, onSettled)
  const passed = rules?.filter((r) => r.passed).length

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <section className="card">
        <h1 className="mb-1 text-xl font-semibold">正在生成</h1>
        <p className="mb-4 text-sm text-muted">
          任务编号 <code className="font-mono">{id}</code>。可以离开此页，稍后在“历史”里回来查看。
        </p>
        {disconnected && (
          <p role="status" className="mb-3 rounded-lg bg-warn-soft px-3 py-2 text-sm text-warn">
            连接中断，正在重新连接…
          </p>
        )}
        <Timeline nodes={nodes} />
      </section>

      {retries.length > 0 && (
        <section role="status" aria-label="打回" className="rounded-xl border border-warn/30 bg-warn-soft p-4 text-sm text-warn">
          <h2 className="mb-1 font-semibold">校验未通过，已打回重做</h2>
          <ul className="space-y-1">
            {retries.map((r, i) => (
              <li key={i}>
                打回到 <code className="font-mono">{r.target}</code>：{r.reason}
              </li>
            ))}
          </ul>
        </section>
      )}

      {passed !== undefined && rules && (
        <p className="px-1 text-sm text-muted">
          最近一次规则校验：{passed}/{rules.length} 条通过
        </p>
      )}

      {research.length > 0 && (
        <section className="card">
          <h2 className="mb-3 font-semibold">联网调研</h2>
          <ul className="space-y-3">
            {research.map((r, i) => (
              <li key={i}>
                <p className="text-sm">{r.query}</p>
                <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
                  {r.sources.map((src) => {
                    const href = safeHttpUrl(src)
                    return (
                      <li key={src} className="text-sm">
                        {href ? (
                          <a href={href} target="_blank" rel="noopener noreferrer" className="text-accent underline-offset-2 hover:underline">
                            {domainOf(src)}
                          </a>
                        ) : (
                          <span className="text-muted">{domainOf(src)}</span>
                        )}
                      </li>
                    )
                  })}
                </ul>
              </li>
            ))}
          </ul>
        </section>
      )}

      {notes.length > 0 && (
        <section aria-label="进度" className="rounded-xl border border-line bg-panel p-4 text-sm text-muted">
          <h2 className="mb-1 font-semibold">进度</h2>
          <ul className="list-disc space-y-1 pl-5">
            {notes.map((note, i) => (
              <li key={i}>{note}</li>
            ))}
          </ul>
        </section>
      )}

      {warnings.length > 0 && (
        <section aria-label="警告" className="rounded-xl border border-warn/30 bg-warn-soft p-4 text-sm text-warn">
          <h2 className="mb-1 font-semibold">警告</h2>
          <ul className="list-disc space-y-1 pl-5">
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
