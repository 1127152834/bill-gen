import { useState } from 'react'
import type { RenderArtifact } from '@bh/core/schemas'
import { TEMPLATE_CHOICES } from '../lib/requestForm'

const LABELS = new Map<string, string>(
  [...TEMPLATE_CHOICES.personal, ...TEMPLATE_CHOICES.business].map((t) => [t.id, t.label]),
)

/** billgen 把 PDF 写在出片目录里，主服务按文件名发出来（`/api/v1/renders/<文件名>`）。 */
export function renderFileUrl(path: string): string {
  const name = path.split(/[\\/]/u).pop() ?? ''
  return `/api/v1/renders/${encodeURIComponent(name)}`
}

function size(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`
}

/**
 * The PDFs this job produced. The statements above are the data; these are the files — the reason
 * people run the thing at all. Preview stays inline (one iframe, switched by the row), download is a
 * plain link, so nothing here needs the blob machinery the JSON export uses.
 */
export default function RendersPanel({ renders }: { renders: readonly RenderArtifact[] }) {
  const [active, setActive] = useState(0)
  if (renders.length === 0) return null
  const current = renders[Math.min(active, renders.length - 1)]!

  return (
    <section className="card" aria-label="PDF 出片">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-semibold">PDF 出片</h2>
        <p className="text-xs text-muted">
          {renders.length} 份 · 左边点一行换预览，右边「下载」存到本地
        </p>
      </div>

      <ul className="mb-3 space-y-1">
        {renders.map((artifact, index) => {
          const url = renderFileUrl(artifact.path)
          const selected = index === Math.min(active, renders.length - 1)
          return (
            <li
              key={`${artifact.template}-${artifact.path}`}
              className={`flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg px-2 py-1.5 text-sm ${
                selected ? 'bg-canvas' : ''
              }`}
            >
              <button
                type="button"
                onClick={() => setActive(index)}
                aria-pressed={selected}
                className={`text-left font-medium ${selected ? '' : 'text-muted hover:text-ink'}`}
              >
                {LABELS.get(artifact.template) ?? artifact.template}
              </button>
              <span className="text-xs text-muted">
                {artifact.period ? `${artifact.period} · ` : ''}
                {artifact.account} · {artifact.pages} 页 · {size(artifact.bytes)}
                {artifact.modelFilled ? ' · 模型填字段' : ' · 确定性草稿'}
              </span>
              <span className="ml-auto flex items-center gap-3 text-xs">
                <a className="text-accent underline underline-offset-2 hover:text-accent-strong" href={url} target="_blank" rel="noreferrer">
                  新窗口看
                </a>
                <a className="text-accent underline underline-offset-2 hover:text-accent-strong" href={url} download>
                  下载
                </a>
              </span>
            </li>
          )
        })}
      </ul>

      <iframe
        key={current.path}
        title={`PDF 预览 ${current.template}`}
        src={renderFileUrl(current.path)}
        className="h-[38rem] w-full rounded-lg border border-line bg-white"
      />
    </section>
  )
}
