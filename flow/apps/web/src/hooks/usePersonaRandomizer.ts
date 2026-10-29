import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import type { SubjectType } from '@bh/core/schemas'
import { ApiError, randomPersona } from '../api'
import { applyField, buildPartial, draftFromProfile, type Draft, type FieldKey } from '../lib/profileDraft'

export type RollKey = FieldKey | 'all'

const FAILED = '随机生成失败，请重试'

function failureMessage(err: unknown): string {
  return err instanceof ApiError && err.code === 'INVALID_LOCATION' ? '地址中的州无效，请检查后重试' : FAILED
}

/**
 * Drives the 🎲 buttons. `roll(key)` asks the server for a persona with every other filled field as
 * `partial` and replaces only that field; `roll('all')` sends an empty partial and replaces the lot.
 * Each key tracks its own in-flight call and failure. The form is keyed by subject type, so leaving
 * the step (or switching type) unmounts it, and answers still in flight are then dropped.
 */
export function usePersonaRandomizer(subject: SubjectType, draft: Draft, setDraft: Dispatch<SetStateAction<Draft>>) {
  const [pending, setPending] = useState<ReadonlySet<RollKey>>(new Set())
  const [failures, setFailures] = useState<Partial<Record<RollKey, string>>>({})
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const roll = useCallback(
    async (key: RollKey) => {
      const partial = key === 'all' ? {} : buildPartial(subject, draft, key)
      setPending((p) => new Set(p).add(key))
      setFailures((f) => ({ ...f, [key]: undefined }))
      try {
        const profile = await randomPersona(subject, partial)
        if (mounted.current) {
          setDraft((cur) => (key === 'all' ? draftFromProfile(subject, cur, profile) : applyField(cur, key, profile)))
        }
      } catch (err) {
        if (mounted.current) setFailures((f) => ({ ...f, [key]: failureMessage(err) }))
      } finally {
        if (mounted.current) {
          setPending((p) => {
            const next = new Set(p)
            next.delete(key)
            return next
          })
        }
      }
    },
    [subject, draft, setDraft],
  )

  return { pending, failures, roll }
}
