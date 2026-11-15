import { useEffect, useRef, useState } from 'react'

type CopyState = 'copied' | 'failed'

/** Clipboard results belong to the displayed value and its latest copy attempt. */
export function useClipboardFeedback(value: string | null, identity: unknown = value) {
  const current = useRef({ value, identity, revision: 0 })
  if (current.current.value !== value || current.current.identity !== identity) {
    current.current = { value, identity, revision: current.current.revision + 1 }
  }
  const scope = current.current
  const [feedback, setFeedback] = useState<{ revision: number; state: CopyState } | null>(null)
  const attempt = useRef(0)
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; attempt.current += 1 }
  }, [])

  const copy = async () => {
    if (!mounted.current || current.current !== scope || scope.value === null) return false
    const request = ++attempt.current
    const stillCurrent = () => mounted.current && current.current === scope && attempt.current === request
    try {
      await navigator.clipboard.writeText(scope.value)
      if (!stillCurrent()) return false
      setFeedback({ revision: scope.revision, state: 'copied' })
      return true
    } catch {
      if (stillCurrent()) setFeedback({ revision: scope.revision, state: 'failed' })
      return false
    }
  }

  return { copy, copyState: feedback?.revision === scope.revision ? feedback.state : 'idle' as const }
}
