import type { MonthFile } from '@bh/core/schemas'
import { TEMPLATE_CHOICES } from '../lib/requestForm'
import { renderFileUrl } from './RendersPanel'

const LABELS = new Map<string, string>(
  [...TEMPLATE_CHOICES.personal, ...TEMPLATE_CHOICES.business].map((t) => [t.id, t.label]),
)

function size(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`
}

/** 校验报告与清单就在同一层目录里，名字固定。 */
const REPORT = '/api/v1/renders/validation-report.json'
const MANIFEST = '/api/v1/renders/manifest.json'

/**
 * 这个任务交付的模板 JSON：一个月一份，导进对应模板就能出账单。
 *
 * 这里不做 PDF 预览——PDF 不再是交付物；能看的只有「哪个月、哪套版式、多少行、过没过校验」，
 * 以及把 JSON / 校验报告 / 清单下载下来。没过校验的月份照样列出来（红字标出），
 * 否则用户会以为那一份不存在。
 */
export default function MonthFilesPanel({ files }: { files: readonly MonthFile[] }) {
  if (files.length === 0) return null
  const failed = files.filter((file) => !file.ok)
  const months = [...new Set(files.map((file) => file.month))].sort()

  return (
    <section className="card" aria-label="模板 JSON 交付">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-semibold">模板 JSON</h2>
        <p className="text-xs text-muted">
          {files.length} 份 · {months.length} 个月 · 每月一份，导进模板即可出账单
          {failed.length > 0 && ` · ${failed.length} 份未通过校验`}
        </p>
      </div>

      <ul className="mb-3 space-y-1">
        {files.map((file) => (
          <li
            key={`${file.template}-${file.month}-${file.path}`}
            className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg px-2 py-1.5 text-sm"
          >
            <span className="font-medium">{LABELS.get(file.template) ?? file.template}</span>
            <span className="text-muted">{file.month}</span>
            <span className="text-xs text-muted">
              {file.account} · {file.rows} 行 · {size(file.bytes)}
            </span>
            {file.ok ? (
              <span className="rounded-full bg-credit-soft px-2 py-0.5 text-xs text-credit">校验通过</span>
            ) : (
              <span className="rounded-full bg-warn-soft px-2 py-0.5 text-xs text-warn">
                未通过（见报告）
              </span>
            )}
            <a className="ml-auto text-accent hover:underline" href={renderFileUrl(file.path)} download>
              下载 JSON
            </a>
          </li>
        ))}
      </ul>

      <div className="flex flex-wrap items-center gap-3 text-xs">
        <a className="text-accent hover:underline" href={REPORT} download>
          校验报告 validation-report.json
        </a>
        <a className="text-accent hover:underline" href={MANIFEST} download>
          清单 manifest.json
        </a>
      </div>

      {failed.length > 0 && (
        <p role="status" className="mt-3 rounded-lg bg-warn-soft px-3 py-2 text-xs text-warn">
          这几份没过三层校验（结构 / 内容 / 跨月），照旧交付：{failed.map((file) => `${file.template} ${file.month}`).join('、')}。
          报告里写了具体规则码与月份。
        </p>
      )}
    </section>
  )
}
