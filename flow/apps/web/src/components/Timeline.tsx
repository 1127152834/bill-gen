import { PIPELINE, type NodeState } from '../hooks/useProgress'

const NODE_LABEL: Record<(typeof PIPELINE)[number], string> = {
  normalize: '参数规整',
  planResearch: '规划调研',
  research: '联网调研',
  lifeModel: '生活模型',
  schedule: '排期',
  descriptors: '生成描述',
  validate: '规则校验',
  audit: '审计',
  finalize: '收尾',
}

const STATE_TEXT: Record<NodeState, string> = { pending: '等待中', active: '进行中', done: '已完成' }

function Marker({ state }: { state: NodeState }) {
  if (state === 'active') return <span className="spinner" aria-hidden="true" />
  if (state === 'done') return <span aria-hidden="true" className="text-credit">✓</span>
  return <span aria-hidden="true" className="size-2 rounded-full border border-line" />
}

/** The nine workflow nodes in order; the running one is highlighted. */
export default function Timeline({ nodes }: { nodes: Record<string, NodeState> }) {
  return (
    <ol aria-label="工作流" className="space-y-1">
      {PIPELINE.map((node) => {
        const state = nodes[node] ?? 'pending'
        return (
          <li
            key={node}
            aria-label={`${node} ${NODE_LABEL[node]}`}
            aria-current={state === 'active' ? 'step' : undefined}
            data-state={state}
            className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm ${
              state === 'active' ? 'bg-accent-soft font-medium text-accent-strong' : state === 'done' ? 'text-ink' : 'text-muted'
            }`}
          >
            <span className="flex size-5 shrink-0 items-center justify-center">
              <Marker state={state} />
            </span>
            <span>{NODE_LABEL[node]}</span>
            <code className="font-mono text-xs text-muted">{node}</code>
            <span className="ml-auto text-xs text-muted">{STATE_TEXT[state]}</span>
          </li>
        )
      })}
    </ol>
  )
}
