import { useCallback, useState } from 'react'

type PendingCounts = Readonly<Record<string, number>>

function pendingKey(action: string, entityId: string) {
  return `${action}\u0000${entityId}`
}

export function changePendingEntityCount(
  current: PendingCounts,
  action: string,
  entityId: string,
  delta: 1 | -1,
): PendingCounts {
  const key = pendingKey(action, entityId)
  const nextCount = Math.max(0, (current[key] ?? 0) + delta)
  if (nextCount === (current[key] ?? 0)) {
    return current
  }

  const next = { ...current }
  if (nextCount === 0) {
    delete next[key]
  } else {
    next[key] = nextCount
  }
  return next
}

export function usePendingEntityActions() {
  const [pendingCounts, setPendingCounts] = useState<PendingCounts>({})

  const begin = useCallback((action: string, entityId: string) => {
    setPendingCounts((current) => changePendingEntityCount(current, action, entityId, 1))
  }, [])

  const finish = useCallback((action: string, entityId: string) => {
    setPendingCounts((current) => changePendingEntityCount(current, action, entityId, -1))
  }, [])

  const isPending = useCallback(
    (action: string, entityId: string) => (pendingCounts[pendingKey(action, entityId)] ?? 0) > 0,
    [pendingCounts],
  )

  return { begin, finish, isPending }
}
