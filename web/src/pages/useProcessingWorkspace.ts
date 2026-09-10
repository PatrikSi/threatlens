import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { captureSessionLease } from '../api/sessionLifecycle'
import { useCurrentUser } from '../hooks/useCurrentUser'
import type { ProcessingPage, ProcessingRecoveryRequest, ProcessingRun, ProcessingWork } from '../types/processing'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
import { processingAccessError, processingConflict, processingScope, processingWorkKey, processingWorkPath } from './processingModel'

const RUNS_PATH = '/processing/recovery-runs'
type Review = { request: ProcessingRecoveryRequest; rows: ProcessingWork[]; scope: string; location: string }

export function useProcessingWorkspace() {
  const client = useQueryClient()
  const user = useCurrentUser()
  const [params, setParams] = useSearchParams()
  const scope = processingScope(params)
  const workPath = processingWorkPath(scope)
  const [selection, setSelection] = useState<{ scope: string; rows: ProcessingWork[] }>({ scope: '', rows: [] })
  const [review, setReview] = useState<Review | null>(null)
  const [cancelReview, setCancelReview] = useState<ProcessingRun | null>(null)
  const [notice, setNotice] = useState('')
  const mounted = useRef(true)
  const location = params.toString()
  const previousLocation = useRef(location)
  const current = useRef({ location, workPath, setParams })
  current.current = { location, workPath, setParams }
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => {
    if (previousLocation.current === location) return
    previousLocation.current = location
    setSelection({ scope: '', rows: [] })
    setReview(null)
    setCancelReview(null)
  }, [location])
  const permissions = user.data?.access?.permissions ?? []
  const canRead = hasRequiredPermissions(permissions, ['read:operations', 'read:items'])
  const canWrite = canRead && !user.isError && hasRequiredPermissions(permissions, ['write:operations'])
  const workQuery = useQuery({
    queryKey: ['processing', 'work', workPath],
    queryFn: ({ signal }) => apiFetch<ProcessingPage<ProcessingWork>>(workPath, { signal }),
    enabled: canRead,
    refetchInterval: review ? false : 15_000,
    refetchIntervalInBackground: false,
  })
  const runsQuery = useQuery({
    queryKey: ['processing', 'runs', scope.runCursor],
    queryFn: ({ signal }) => apiFetch<ProcessingPage<ProcessingRun>>(`${RUNS_PATH}?${new URLSearchParams({ limit: '25', ...(scope.runCursor ? { cursor: scope.runCursor } : {}) })}`, { signal }),
    enabled: canRead,
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
  })
  const runQuery = useQuery({
    queryKey: ['processing', 'run', scope.run],
    queryFn: ({ signal }) => apiFetch<ProcessingRun>(`${RUNS_PATH}/${encodeURIComponent(scope.run)}`, { signal }),
    enabled: canRead && Boolean(scope.run),
    refetchInterval: (query) => query.state.data && ['queued', 'running'].includes(query.state.data.status) ? 5_000 : false,
    refetchIntervalInBackground: false,
  })
  const rows = canRead && !processingAccessError(workQuery.error) ? workQuery.data?.items ?? [] : []
  const selected = canRead && selection.scope === workPath && !processingAccessError(workQuery.error) ? selection.rows : []
  const staleSelection = selected.some((selectedRow) => !rows.some((row) =>
    processingWorkKey(row) === processingWorkKey(selectedRow) && row.revision === selectedRow.revision && row.can_retry))

  const rememberRun = async (run: ProcessingRun) => {
    await client.cancelQueries({ queryKey: ['processing', 'run', run.id] })
    client.setQueryData(['processing', 'run', run.id], run)
    void client.invalidateQueries({ queryKey: ['processing', 'runs'] })
    void client.invalidateQueries({ queryKey: ['processing', 'work'] })
  }
  const createRun = useMutation({
    mutationKey: ['processing', 'recover'],
    mutationFn: async (submitted: Review) => {
      const lease = captureSessionLease()
      const run = await apiFetch<ProcessingRun>(RUNS_PATH, { method: 'POST', body: JSON.stringify(submitted.request), signal: lease.signal })
      lease.assertCurrent()
      return { run, lease }
    },
    onSuccess: async ({ run, lease }, submitted) => {
      lease.assertCurrent()
      await rememberRun(run)
      lease.assertCurrent()
      if (!mounted.current || current.current.location !== submitted.location) return
      setReview(null)
      setSelection({ scope: '', rows: [] })
      setNotice('Recovery queued. You can leave this page and return to the run below.')
      const next = new URLSearchParams(current.current.location)
      next.set('work_run', run.id)
      current.current.setParams(next)
    },
    onError: (error) => {
      if (processingConflict(error) || processingAccessError(error)) void client.invalidateQueries({ queryKey: ['processing'] })
    },
  })
  const cancelRun = useMutation({
    mutationKey: ['processing', 'cancel'],
    mutationFn: async (submitted: ProcessingRun) => {
      const lease = captureSessionLease()
      const origin = current.current.location
      const run = await apiFetch<ProcessingRun>(`${RUNS_PATH}/${submitted.id}/cancel`, {
        method: 'POST', body: JSON.stringify({ expected_version: submitted.version }), signal: lease.signal,
      })
      lease.assertCurrent()
      return { run, lease, origin }
    },
    onSuccess: async ({ run, lease, origin }) => {
      lease.assertCurrent()
      await rememberRun(run)
      lease.assertCurrent()
      if (!mounted.current || current.current.location !== origin) return
      setCancelReview(null)
      setNotice('Cancellation recorded. Already committed processing results are preserved.')
    },
    onError: (error) => {
      if (processingConflict(error) || processingAccessError(error)) {
        setCancelReview(null)
        void client.invalidateQueries({ queryKey: ['processing'] })
      }
    },
  })
  const busy = createRun.isPending || cancelRun.isPending
  const changeScope = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    if (['work_stage', 'work_state', 'work_feed'].includes(key)) next.delete('work_cursor')
    setSelection({ scope: '', rows: [] })
    setReview(null)
    setCancelReview(null)
    createRun.reset()
    cancelRun.reset()
    setNotice('')
    setParams(next)
  }
  const error = cancelRun.isError ? resolveApiErrorMessage(cancelRun.error, 'Recovery cancellation could not be recorded. Refresh the run before retrying.') : ''
  return {
    scope, rows, selected, staleSelection, workQuery, runsQuery, runQuery, user, canRead, canWrite, busy, notice, error,
    review: review?.location === location && canRead && !processingAccessError(workQuery.error) ? review : null,
    cancelReview: cancelReview?.id === scope.run && canRead && !processingAccessError(runQuery.error) ? cancelReview : null,
    createRun, cancelRun, changeScope,
    toggle: (row: ProcessingWork) => {
      if (!canWrite || busy || !row.can_retry) return
      setSelection({ scope: workPath, rows: selected.some((entry) => processingWorkKey(entry) === processingWorkKey(row))
        ? selected.filter((entry) => processingWorkKey(entry) !== processingWorkKey(row)) : [...selected, row] })
    },
    selectPage: () => setSelection({ scope: workPath, rows: canWrite && !busy ? rows.filter((row) => row.can_retry) : [] }),
    clearSelection: () => setSelection({ scope: '', rows: [] }),
    openReview: () => {
      if (!canWrite || busy || !selected.length || staleSelection || workQuery.isError) return
      createRun.reset()
      setReview({ rows: selected, scope: workPath, location, request: {
        idempotency_key: crypto.randomUUID(),
        items: selected.map(({ item_id, stage, revision }) => ({ item_id, stage, revision })),
      } })
    },
    closeReview: () => { if (!createRun.isPending) setReview(null) },
    openCancel: (run: ProcessingRun) => { cancelRun.reset(); setCancelReview(run) },
    closeCancel: () => { if (!cancelRun.isPending) setCancelReview(null) },
    refresh: () => {
      void workQuery.refetch()
      void runsQuery.refetch()
      if (scope.run) void runQuery.refetch()
    },
  }
}

export type ProcessingWorkspaceController = ReturnType<typeof useProcessingWorkspace>
