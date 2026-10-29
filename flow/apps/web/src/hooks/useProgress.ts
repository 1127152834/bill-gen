import { useEffect, useReducer, useRef } from 'react'
import { subscribeEvents, type GenerationStatus, type ProgressEvent } from '../api'
import type { RuleResult } from '@bh/core/schemas'

/** Pipeline order; a retry re-opens its target and everything after it. */
export const PIPELINE = ['normalize', 'planResearch', 'research', 'lifeModel', 'schedule', 'descriptors', 'validate', 'audit', 'finalize'] as const

export type NodeState = 'pending' | 'active' | 'done'

export type ProgressState = {
  nodes: Record<string, NodeState>
  research: { query: string; sources: string[] }[]
  rules: RuleResult[] | null
  retries: { target: string; reason: string }[]
  warnings: string[]
  /** Informational lines (e.g. which templates the recommender picked); not problems. */
  notes: string[]
  disconnected: boolean
}

export const INITIAL_PROGRESS: ProgressState = {
  nodes: Object.fromEntries(PIPELINE.map((n) => [n, 'pending' satisfies NodeState])),
  research: [],
  rules: null,
  retries: [],
  warnings: [],
  notes: [],
  disconnected: false,
}

type Action = { type: 'reset' } | { type: 'event'; event: ProgressEvent } | { type: 'disconnected' }

export function progressReducer(state: ProgressState, action: Action): ProgressState {
  if (action.type === 'reset') return INITIAL_PROGRESS
  if (action.type === 'disconnected') return { ...state, disconnected: true }
  const e = action.event
  const next = { ...state, disconnected: false }
  switch (e.type) {
    case 'node_start':
      return { ...next, nodes: { ...state.nodes, [e.node]: 'active' } }
    case 'node_end':
      return { ...next, nodes: { ...state.nodes, [e.node]: 'done' } }
    case 'research':
      return { ...next, research: [...state.research, { query: e.query, sources: e.sources }] }
    case 'rules':
      return { ...next, rules: e.results }
    case 'retry': {
      const from = PIPELINE.indexOf(e.target as (typeof PIPELINE)[number])
      const nodes = { ...state.nodes }
      if (from >= 0) for (const n of PIPELINE.slice(from)) nodes[n] = 'pending'
      return { ...next, nodes, retries: [...state.retries, { target: e.target, reason: e.reason }] }
    }
    case 'warning':
      return { ...next, warnings: [...state.warnings, e.message] }
    case 'progress':
      return { ...next, notes: [...state.notes, e.message] }
  }
}

/** Streams a job's events into reducer state; closes the stream on unmount. */
export function useProgress(id: string, onSettled: (status: GenerationStatus) => void): ProgressState {
  const [state, dispatch] = useReducer(progressReducer, INITIAL_PROGRESS)
  const settled = useRef(onSettled)
  useEffect(() => {
    settled.current = onSettled
  })
  useEffect(() => {
    dispatch({ type: 'reset' })
    return subscribeEvents(id, (event) => dispatch({ type: 'event', event }), {
      onDone: (status) => settled.current(status),
      onError: () => dispatch({ type: 'disconnected' }),
    })
  }, [id])
  return state
}
