import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createGeneration, exportUrl, resumeGeneration, type GenerationView } from '../api'

type Props = {
  view: GenerationView
  /** The result is a complete `GenerationResult` (the only kind that can be exported). */
  exportable: boolean
  /** Seed stored on the result, used when the request did not keep one. */
  resultSeed?: string
  onChanged: () => void
}

/**
 * Download, re-run with the same or a new seed, and resume for interrupted jobs. A same-seed
 * re-run repeats the request; the model and research answers are not replayed, so it can differ.
 */
export default function ResultActions({ view, exportable, resultSeed, onChanged }: Props) {
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const request = view.request
  const seed = request?.seed ?? resultSeed

  async function run(action: () => Promise<{ id: string }>, then: (id: string) => void) {
    setBusy(true)
    setError(null)
    try {
      then((await action()).id)
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  const sameSeed = () => {
    if (!request) return Promise.reject(new Error('没有保存请求参数'))
    return createGeneration(seed !== undefined ? { ...request, seed } : request)
  }
  const newSeed = () => {
    if (!request) return Promise.reject(new Error('没有保存请求参数'))
    const { seed: _dropped, ...rest } = request
    return createGeneration(rest)
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {view.status === 'interrupted' && (
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => run(() => resumeGeneration(view.id), onChanged)}>
            继续生成
          </button>
        )}
        {exportable && (
          <a className="btn btn-primary" href={exportUrl(view.id)} download>
            下载 JSON
          </a>
        )}
        <button
          type="button"
          className="btn"
          aria-describedby="same-seed-hint"
          disabled={busy || !request || seed === undefined}
          onClick={() => run(sameSeed, (id) => navigate(`/g/${id}`))}
        >
          用相同参数和 seed 重新生成
        </button>
        <button type="button" className="btn" disabled={busy || !request} onClick={() => run(newSeed, (id) => navigate(`/g/${id}`))}>
          换 seed 重新生成
        </button>
      </div>
      <p id="same-seed-hint" className="text-xs text-muted">
        seed 固定排程和金额的随机数；调研和模型输出可能不同，所以结果不一定完全一样。
      </p>
      {error && (
        <p role="alert" className="field-error">
          {error}
        </p>
      )}
    </div>
  )
}
