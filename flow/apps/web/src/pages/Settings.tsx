import { useEffect, useState } from 'react'
import { ApiError, getSettings, testModel, updateSettings, type ModelSettings, type ModelSettingsPatch } from '../api'

const MODES: ModelSettings['structuredMode'][] = ['json_schema', 'function_calling', 'json_mode']

const MODE_HINT: Record<ModelSettings['structuredMode'], string> = {
  json_schema: '优先。服务端支持 response_format=json_schema 时最稳。',
  function_calling: '模型不支持 json_schema 时用它，走 tool call。',
  json_mode: '最宽松，只要求返回 JSON 对象。',
}

type Draft = {
  baseUrl: string
  apiKey: string
  model: string
  structuredMode: ModelSettings['structuredMode']
  tavilyApiKey: string
  llmFake: boolean
}

const EMPTY: Draft = {
  baseUrl: '',
  apiKey: '',
  model: '',
  structuredMode: 'json_schema',
  tavilyApiKey: '',
  llmFake: false,
}

/** 把服务端状态填进表单；密钥留空（不回填明文），只显示打码提示。 */
function toDraft(s: ModelSettings): Draft {
  return {
    baseUrl: s.baseUrl ?? '',
    apiKey: '',
    model: s.model ?? '',
    structuredMode: s.structuredMode,
    tavilyApiKey: '',
    llmFake: s.llmFake,
  }
}

/** 只把改动过的字段发给服务端；密钥空着表示「保持原样」。 */
function diff(loaded: Draft, draft: Draft): ModelSettingsPatch {
  const patch: ModelSettingsPatch = {}
  if (draft.baseUrl.trim() !== loaded.baseUrl) patch.baseUrl = draft.baseUrl.trim()
  if (draft.model.trim() !== loaded.model) patch.model = draft.model.trim()
  if (draft.structuredMode !== loaded.structuredMode) patch.structuredMode = draft.structuredMode
  if (draft.llmFake !== loaded.llmFake) patch.llmFake = draft.llmFake
  if (draft.apiKey.trim()) patch.apiKey = draft.apiKey.trim()
  if (draft.tavilyApiKey.trim()) patch.tavilyApiKey = draft.tavilyApiKey.trim()
  return patch
}

const SOURCE_LABEL = { db: '页面设置', env: '配置文件', none: '未设置' } as const

/** 模型设置：改完保存，下一次生成立即用新配置（不用重启）。 */
export default function Settings() {
  const [loaded, setLoaded] = useState<Draft | null>(null)
  const [server, setServer] = useState<ModelSettings | null>(null)
  const [draft, setDraft] = useState<Draft>(EMPTY)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState<'save' | 'test' | null>(null)

  useEffect(() => {
    let cancelled = false
    getSettings()
      .then((s) => {
        if (cancelled) return
        setServer(s)
        const d = toDraft(s)
        setLoaded(d)
        setDraft(d)
      })
      .catch((err: unknown) => !cancelled && setError(err instanceof Error ? err.message : '读取设置失败'))
    return () => {
      cancelled = true
    }
  }, [])

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft((d) => ({ ...d, [key]: value }))
  const dirty = loaded !== null && JSON.stringify(loaded) !== JSON.stringify(draft)

  const save = async () => {
    setBusy('save')
    setError(null)
    setNotice(null)
    try {
      const patch = loaded ? diff(loaded, draft) : {}
      const next = await updateSettings(patch)
      setServer(next)
      const d = toDraft(next)
      setLoaded(d)
      setDraft(d)
      setNotice(next.ready ? '已保存，下一次生成就用这套模型。' : '已保存，但还缺密钥：现在会走离线 fixture。')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '保存失败')
    } finally {
      setBusy(null)
    }
  }

  const test = async () => {
    setBusy('test')
    setError(null)
    setNotice(null)
    try {
      const patch = loaded ? diff(loaded, draft) : {}
      const r = await testModel(patch)
      // 测试顺带保存了，刷新一次服务端状态（密钥已存进去）。
      const next = await getSettings()
      setServer(next)
      const d = toDraft(next)
      setLoaded(d)
      setDraft(d)
      setNotice(`连通：${r.model} 回了 ${r.ok ? 'ok' : '非 ok'}，用时 ${r.ms}ms，${r.tokens} tokens。`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '测试失败')
    } finally {
      setBusy(null)
    }
  }

  const field = (label: string, hint: string, control: React.ReactNode) => (
    <div className="space-y-1">
      <label className="field-label">{label}</label>
      {control}
      <p className="text-xs text-muted">{hint}</p>
    </div>
  )

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold">模型设置</h1>
        <p className="mt-1 text-sm text-muted">
          这里改完直接生效，不用动 <code>.env</code>，也不用重启。每次开始生成都会重新读一次。
        </p>
      </div>

      {server && (
        <div className="card flex flex-wrap items-center gap-3 text-sm">
          <span className={`badge ${server.ready ? 'bg-accent-soft text-accent-strong' : 'bg-danger/10 text-danger'}`}>
            {server.ready ? '真实模型' : '离线 fixture'}
          </span>
          {server.ready ? (
            <span className="text-muted">
              {server.model} · baseUrl {SOURCE_LABEL[server.source.baseUrl]} · apiKey {SOURCE_LABEL[server.source.apiKey]} ·
              tavily {SOURCE_LABEL[server.source.tavily]}
            </span>
          ) : (
            <span className="text-muted">补齐 baseUrl / apiKey / tavily key 三样就会切到真实模型。</span>
          )}
        </div>
      )}

      <div className="card space-y-4">
        {field(
          'Base URL',
          'OpenAI 兼容端点，例如 https://api.openai.com/v1 或你的中转地址。',
          <input
            className="input"
            value={draft.baseUrl}
            onChange={(e) => set('baseUrl', e.target.value)}
            placeholder="https://api.openai.com/v1"
            autoComplete="off"
            spellCheck={false}
          />,
        )}
        {field(
          'API Key',
          server?.hasApiKey ? `已保存：${server.apiKeyMasked}。留空表示不修改。` : '还没设置。填一次就存进本地数据库。',
          <input
            className="input"
            type="password"
            value={draft.apiKey}
            onChange={(e) => set('apiKey', e.target.value)}
            placeholder={server?.hasApiKey ? '留空则不修改' : 'sk-...'}
            autoComplete="off"
            spellCheck={false}
          />,
        )}
        {field(
          '模型名',
          '要带工具调用 / 结构化输出能力的对话模型。',
          <input
            className="input"
            value={draft.model}
            onChange={(e) => set('model', e.target.value)}
            placeholder="gpt-4o"
            autoComplete="off"
            spellCheck={false}
          />,
        )}
        {field(
          '结构化输出方式',
          MODE_HINT[draft.structuredMode],
          <select
            className="input"
            value={draft.structuredMode}
            onChange={(e) => set('structuredMode', e.target.value as ModelSettings['structuredMode'])}
          >
            {MODES.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>,
        )}
        {field(
          'Tavily Key',
          server?.hasTavilyKey ? `已保存：${server.tavilyKeyMasked}。留空表示不修改。` : '调研阶段要用它搜本地商户与价格。',
          <input
            className="input"
            type="password"
            value={draft.tavilyApiKey}
            onChange={(e) => set('tavilyApiKey', e.target.value)}
            placeholder={server?.hasTavilyKey ? '留空则不修改' : 'tvly-...'}
            autoComplete="off"
            spellCheck={false}
          />,
        )}
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={draft.llmFake} onChange={(e) => set('llmFake', e.target.checked)} />
          强制走离线 fixture（不调模型，用内置样例跑通流程）
        </label>

        {error && <p className="field-error">{error}</p>}
        {notice && <p className="text-sm text-accent-strong">{notice}</p>}

        <div className="flex flex-wrap gap-2">
          <button className="btn btn-primary" onClick={save} disabled={busy !== null || !dirty}>
            {busy === 'save' ? <span className="spinner" role="status" aria-label="保存中" /> : null}
            保存
          </button>
          <button className="btn" onClick={test} disabled={busy !== null}>
            {busy === 'test' ? <span className="spinner" role="status" aria-label="测试中" /> : null}
            测试连接
          </button>
          {dirty && <span className="self-center text-xs text-muted">有未保存的改动</span>}
        </div>
      </div>
    </div>
  )
}
