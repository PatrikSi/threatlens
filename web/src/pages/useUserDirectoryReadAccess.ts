import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { useQueryAccessScope } from '../hooks/useQueryAccessScope'
import type { AdminUser, UserDirectoryResponse } from '../types/api'

type MfaTarget = { target: AdminUser; verifiedLookup: boolean }

export function useUserDirectoryReadAccess(queryKey: readonly unknown[], path: string) {
  const client = useQueryClient()
  const access = useQueryAccessScope(['users', 'directory'])
  const read = access.queryOptions(({ signal }) => apiFetch<UserDirectoryResponse>(path, { signal }))
  const query = useQuery({ queryKey, ...read })
  const [selection, setSelection] = useState<Awaited<ReturnType<typeof access.read<MfaTarget>>> | null>(null)
  const selectionRef = useRef(selection)
  const selectionEpoch = useRef(0)
  const selected = access.currentData(selection ?? undefined)

  function readableUser(id: string) {
    const state = client.getQueryState<Awaited<ReturnType<typeof read.queryFn>>>(queryKey)
    if (state?.status !== 'success' || state.fetchStatus !== 'idle') return undefined
    return access.currentData(state.data)?.users.find((user) => user.id === id)
  }
  function canActOnMfaTarget(target: AdminUser) {
    const current = access.currentData(selectionRef.current ?? undefined)
    if (!current || current.target.id !== target.id) return false
    return current.verifiedLookup || Boolean(readableUser(target.id))
  }
  function setMfaResetTarget(target: AdminUser | null) {
    if (target && !readableUser(target.id)) return
    const next = target ? access.captureResult()({ target, verifiedLookup: false }) : null
    selectionEpoch.current += 1
    selectionRef.current = next
    setSelection(next)
  }
  function captureRestoredTarget() {
    const captureResult = access.captureResult()
    const generation = captureResult(true).generation
    const epoch = selectionEpoch.current
    const restore = (target: AdminUser) => {
      const result = captureResult({ target, verifiedLookup: true })
      if (!access.currentData(result) || epoch !== selectionEpoch.current) return false
      selectionEpoch.current += 1
      selectionRef.current = result
      setSelection(result)
      return true
    }
    return Object.assign(restore, { generation })
  }

  const needsVerification = access.denied || access.generation > 0 && query.data === undefined
  return {
    query, access, readableUser, canActOnMfaTarget, captureRestoredTarget, setMfaResetTarget,
    isMfaTargetCurrent: (target: AdminUser) => access.currentData(selectionRef.current ?? undefined)?.target.id === target.id,
    mfaResetTarget: selected?.target ?? null,
    targetUnavailable: Boolean(selected && !canActOnMfaTarget(selected.target)),
    ready: query.isSuccess && query.data !== undefined && !needsVerification,
    unavailable: query.isError || needsVerification,
    error: query.error ?? (needsVerification ? new Error('Verify current directory access before using saved account metadata.') : null),
  }
}
