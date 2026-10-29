import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ApiError, deleteGeneration, listGenerations, type GenerationStatus, type GenerationSummary, type ListQuery } from '../api'
import { STATUS_LABEL, SUBJECT_LABEL, formatDateTime } from '../lib/format'

const LIMIT = 200
const SUBJECTS = Object.keys(SUBJECT_LABEL) as (keyof typeof SUBJECT_LABEL)[]
const STATUSES = Object.keys(STATUS_LABEL) as GenerationStatus[]

/** Past generations, filterable by type and status (kept in the query string), with open and delete. */
export default function History() {
  const [params, setParams] = useSearchParams()
  const subjectType = SUBJECTS.find((s) => s === params.get('subjectType'))
  const status = STATUSES.find((s) => s === params.get('status'))
  const queryKey = `${subjectType ?? ''}|${status ?? ''}`
  // Tagged with the filter it answers, so changing a filter reads as "loading" without resetting state in an effect.
  const [loaded, setLoaded] = useState<{ key: string; items: GenerationSummary[] | null; error: string | null } | null>(null)
  const items = loaded?.key === queryKey ? loaded.items : null
  const loadError = loaded?.key === queryKey ? loaded.error : null
  const setItems = (update: (cur: GenerationSummary[] | null) => GenerationSummary[] | null) =>
    setLoaded((cur) => (cur ? { ...cur, items: update(cur.items) } : cur))
  const [notice, setNotice] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const query: ListQuery = { ...(subjectType && { subjectType }), ...(status && { status }), limit: LIMIT }
    listGenerations(query)
      .then((r) => !cancelled && setLoaded({ key: queryKey, items: r.items, error: null }))
      .catch((err: unknown) => !cancelled && setLoaded({ key: queryKey, items: null, error: err instanceof Error ? err.message : '加载失败' }))
    return () => {
      cancelled = true
    }
  }, [subjectType, status, queryKey])

  const setFilter = (key: 'subjectType' | 'status', value: string) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      if (value) next.set(key, value)
      else next.delete(key)
      return next
    })

  async function remove(item: GenerationSummary) {
    const name = item.displayName ?? '未命名任务'
    if (!window.confirm(`确定删除“${name}”吗？此操作无法撤销。`)) return
    setNotice(null)
    setDeleting(item.id)
    try {
      await deleteGeneration(item.id)
      setItems((cur) => cur?.filter((i) => i.id !== item.id) ?? cur)
    } catch (err) {
      setNotice(err instanceof ApiError && err.status === 409 ? '任务进行中，无法删除' : err instanceof Error ? err.message : '删除失败')
    } finally {
      setDeleting(null)
    }
  }

  return (
    <section className="space-y-4">
      <h1 className="text-xl font-semibold">历史记录</h1>
      <div className="flex flex-wrap gap-4">
        <label className="flex items-center gap-2 text-sm">
          <span className="field-label">类型</span>
          <select className="input !w-auto" value={subjectType ?? ''} onChange={(e) => setFilter('subjectType', e.target.value)}>
            <option value="">全部</option>
            {SUBJECTS.map((s) => (
              <option key={s} value={s}>
                {SUBJECT_LABEL[s]}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <span className="field-label">状态</span>
          <select className="input !w-auto" value={status ?? ''} onChange={(e) => setFilter('status', e.target.value)}>
            <option value="">全部</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
          </select>
        </label>
      </div>

      {notice && (
        <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
          {notice}
        </p>
      )}
      {loadError && (
        <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
          {loadError}
        </p>
      )}
      {items === null && !loadError && <p className="text-sm text-muted">加载中…</p>}
      {items?.length === 0 && <p className="card text-sm text-muted">还没有符合条件的记录。</p>}
      {items && items.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-line bg-surface">
          <table className="w-full min-w-[22rem] text-sm">
            <thead className="bg-canvas text-left text-xs text-muted">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">名称</th>
                <th scope="col" className="px-3 py-2 font-medium">类型</th>
                <th scope="col" className="px-3 py-2 font-medium">状态</th>
                <th scope="col" className="hidden px-3 py-2 font-medium sm:table-cell">创建时间</th>
                <th scope="col" className="px-3 py-2 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id} className="border-t border-line">
                  <td className="px-3 py-2">
                    {item.displayName ?? <span className="text-muted">未命名</span>}
                    <span className="tabular block text-xs text-muted sm:hidden">{formatDateTime(item.createdAt)}</span>
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">{SUBJECT_LABEL[item.subjectType]}</td>
                  <td className="px-3 py-2 whitespace-nowrap">{STATUS_LABEL[item.status]}</td>
                  <td className="tabular hidden px-3 py-2 whitespace-nowrap text-muted sm:table-cell">{formatDateTime(item.createdAt)}</td>
                  <td className="px-3 py-2">
                    <span className="flex gap-2 whitespace-nowrap">
                      <Link to={`/g/${item.id}`} className="btn !min-h-8 !px-3">打开</Link>
                      <button type="button" className="btn !min-h-8 !px-3 text-danger" disabled={deleting === item.id} onClick={() => void remove(item)}>
                        删除
                      </button>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
