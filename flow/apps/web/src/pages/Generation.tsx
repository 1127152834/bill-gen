import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { ApiError, getGeneration, type GenerationView } from '../api'
import Progress from './Progress'
import Result from './Result'

type Loaded = { kind: 'ready'; view: GenerationView } | { kind: 'error'; message: string }
/** Tagged with the id it was fetched for, so a different id reads as "loading" without resetting state in an effect. */
type State = { id: string; loaded: Loaded } | null

const POLL_MS = 4000
const live = (v: GenerationView) => v.status === 'queued' || v.status === 'running'

async function fetchLoaded(id: string): Promise<Loaded> {
  try {
    return { kind: 'ready', view: await getGeneration(id) }
  } catch (err) {
    const notFound = err instanceof ApiError && err.status === 404
    return { kind: 'error', message: notFound ? '找不到这个任务，它可能已被删除。' : err instanceof Error ? err.message : '加载失败' }
  }
}

/** `/g/:id`: the progress timeline while the job is live, the result once it has settled. */
export default function Generation() {
  const { id = '' } = useParams()
  const [current, setState] = useState<State>(null)

  const reload = () => void fetchLoaded(id).then((loaded) => setState({ id, loaded }))

  useEffect(() => {
    let cancelled = false
    void fetchLoaded(id).then((loaded) => !cancelled && setState({ id, loaded }))
    return () => {
      cancelled = true
    }
  }, [id])

  const state: Loaded | null = current?.id === id ? current.loaded : null

  // Safety net: if the stream's final frame is missed, notice the job settling anyway.
  const isLive = state?.kind === 'ready' && live(state.view)
  useEffect(() => {
    if (!isLive) return
    const timer = setInterval(() => {
      getGeneration(id)
        .then((view) => setState((cur) => (cur?.id === id && cur.loaded.kind === 'ready' && cur.loaded.view.status !== view.status ? { id, loaded: { kind: 'ready', view } } : cur)))
        .catch(() => {})
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [id, isLive])

  if (state === null) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted" role="status">
        <span className="spinner" aria-hidden="true" /> 加载中…
      </p>
    )
  }
  if (state.kind === 'error') {
    return (
      <p role="alert" className="card text-danger">
        {state.message}
      </p>
    )
  }
  return live(state.view) ? <Progress key={id} id={id} onSettled={reload} /> : <Result view={state.view} onChanged={reload} />
}
