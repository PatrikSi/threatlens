import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { captureSessionLease } from '../api/sessionLifecycle'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useQueryAccessScope } from '../hooks/useQueryAccessScope'
import type { ProcessingPage, ProcessingRecoveryRequest, ProcessingRun, ProcessingWork } from '../types/processing'
import { createSecureRequestId } from '../utils/secureRandomId'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
import { processingAccessError, processingConflict, processingScope, processingWorkKey, processingWorkPath } from './processingModel'

const RUNS_PATH = '/processing/recovery-runs'
type ReadScope = ReturnType<typeof useQueryAccessScope>
type ReadAdmission = ReturnType<ReturnType<ReadScope['captureResult']>>
type Selection = { scope: string; rows: ProcessingWork[]; admission: ReadAdmission }
type Review = Selection & { request: ProcessingRecoveryRequest; location: string }
type RunWrite = { key: string[]; capture: ReturnType<ReadScope['captureResult']>; current: ReadScope['currentData']; allowed: () => boolean }

function readNeedsVerification(scope: { denied: boolean; generation: number }, data: unknown) {
  return scope.denied || scope.generation > 0 && !data
}

export function useProcessingWorkspace() {
  const client = useQueryClient()
  const user = useCurrentUser()
  const [params, setParams] = useSearchParams()
  const scope = processingScope(params)
  const workPath = processingWorkPath(scope)
  const workAccess = useQueryAccessScope(['processing', 'work'])
  const runsAccess = useQueryAccessScope(['processing', 'runs'])
  const runAccess = useQueryAccessScope(['processing', 'run', scope.run])
  const emptySelection = (): Selection => ({ scope: '', rows: [], admission: workAccess.captureResult()(true) })
  const [selection, setSelection] = useState<Selection>(emptySelection)
  const [review, setReview] = useState<Review | null>(null)
  const [cancelIntent, setCancelIntent] = useState<{ run: ProcessingRun; admission: ReadAdmission } | null>(null)
  const cancelReview = cancelIntent && cancelIntent.run.id === scope.run && runAccess.currentData(cancelIntent.admission) ? cancelIntent.run : null
  const [notice, setNotice] = useState('')
  const [preparationError, setPreparationError] = useState('')
  const recoveryRequest = useRef<{ serialized: string; request: ProcessingRecoveryRequest } | null>(null)
  const mounted = useRef(true)
  const location = params.toString()
  const previousLocation = useRef(location)
  const current = useRef({ location, workPath, setParams })
  current.current = { location, workPath, setParams }
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => {
    if (previousLocation.current === location) return
    previousLocation.current = location
    setSelection((previous) => ({ scope: '', rows: [], admission: previous.admission }))
    setReview(null)
    setCancelIntent(null)
    setPreparationError('')
  }, [location])
  const permissions = user.data?.access?.permissions ?? []
  const canRead = hasRequiredPermissions(permissions, ['read:operations', 'read:items'])
  const canWrite = canRead && !user.isError && hasRequiredPermissions(permissions, ['write:operations'])
  const workQuery = useQuery({
    queryKey: ['processing', 'work', workPath],
    ...workAccess.queryOptions(({ signal }) => apiFetch<ProcessingPage<ProcessingWork>>(workPath, { signal })),
    enabled: canRead,
    refetchInterval: review ? false : 15_000,
    refetchIntervalInBackground: false,
  })
  const runsQuery = useQuery({
    queryKey: ['processing', 'runs', scope.runCursor],
    ...runsAccess.queryOptions(({ signal }) => apiFetch<ProcessingPage<ProcessingRun>>(`${RUNS_PATH}?${new URLSearchParams({ limit: '25', ...(scope.runCursor ? { cursor: scope.runCursor } : {}) })}`, { signal })),
    enabled: canRead,
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
  })
  const runKey = ['processing', 'run', scope.run]
  const runRead = runAccess.queryOptions(({ signal }) => apiFetch<ProcessingRun>(`${RUNS_PATH}/${encodeURIComponent(scope.run)}`, { signal }))
  const runQuery = useQuery({
    queryKey: runKey,
    ...runRead,
    enabled: canRead && Boolean(scope.run),
    refetchInterval: (query) => {
      const run = runAccess.currentData(query.state.data)
      return run && ['queued', 'running'].includes(run.status) ? 5_000 : false
    },
    refetchIntervalInBackground: false,
  })
  const rows = canRead && !processingAccessError(workQuery.error) ? workQuery.data?.items ?? [] : []
  const selected = canRead && selection.scope === workPath && workAccess.currentData(selection.admission) ? selection.rows : []
  const staleSelection = selected.some((selectedRow) => !rows.some((row) =>
    processingWorkKey(row) === processingWorkKey(selectedRow) && row.revision === selectedRow.revision && row.can_retry))

  function currentWork() {
    const key = ['processing', 'work', current.current.workPath]
    return client.getQueryState(key)?.status === 'error' ? undefined
      : workAccess.currentData<ProcessingPage<ProcessingWork>>(client.getQueryData(key))
  }
  function currentRun(run: ProcessingRun) {
    if (scope.run !== run.id || client.getQueryState(runKey)?.status === 'error') return undefined
    const value = runAccess.currentData<ProcessingRun>(client.getQueryData(runKey))
    return value?.id === run.id && value.version === run.version && value.can_cancel ? value : undefined
  }
  const rememberRun = async (run: ProcessingRun, write?: RunWrite) => {
    if (write && write.allowed()) {
      await client.cancelQueries({ queryKey: write.key })
      if (write.allowed() && client.getQueryState(write.key)?.status !== 'error') {
        client.setQueryData<Awaited<ReturnType<typeof runRead.queryFn>>>(write.key, (current) =>
          write.current<ProcessingRun>(current)?.id === run.id ? write.capture(run) : current)
      }
    }
    // Creation returns a previously unknown ID. Its response cannot establish
    // read authority for that resource; the destination performs a canonical GET.
    void client.invalidateQueries({ queryKey: ['processing', 'run', run.id] })
    void client.invalidateQueries({ queryKey: ['processing', 'runs'] })
    void client.invalidateQueries({ queryKey: ['processing', 'work'] })
  }
  const createRun = useMutation({
    mutationKey: ['processing', 'recover'],
    mutationFn: async (submitted: Review) => {
      const work = currentWork()
      if (!canWrite || submitted.location !== current.current.location || submitted.scope !== current.current.workPath
        || !workAccess.currentData(submitted.admission) || !work || !submitted.request.items.length
        || !submitted.request.items.every((item) => work.items.some((row) => row.can_retry
          && row.item_id === item.item_id && row.stage === item.stage && row.revision === item.revision))) {
        throw new Error('Refresh the work and review the current selection before recovering it.')
      }
      const lease = captureSessionLease()
      const admitted = () => Boolean(workAccess.currentData(submitted.admission))
      const run = await apiFetch<ProcessingRun>(RUNS_PATH, { method: 'POST', body: JSON.stringify(submitted.request), signal: lease.signal })
      lease.assertCurrent()
      return { run, lease, admitted }
    },
    onSuccess: async ({ run, lease, admitted }, submitted) => {
      lease.assertCurrent()
      if (recoveryRequest.current?.request === submitted.request) recoveryRequest.current = null
      await rememberRun(run)
      lease.assertCurrent()
      if (!admitted() || !mounted.current || current.current.location !== submitted.location) return
      setReview(null)
      setSelection(emptySelection())
      setNotice('Recovery queued. You can leave this page and return to the run below.')
      const next = new URLSearchParams(current.current.location)
      next.set('work_run', run.id)
      current.current.setParams(next)
    },
    onError: (error, submitted) => {
      if (processingConflict(error) || processingAccessError(error)) {
        if (recoveryRequest.current?.request === submitted.request) recoveryRequest.current = null
        void client.invalidateQueries({ queryKey: ['processing'] })
      }
    },
  })
  const cancelRun = useMutation({
    mutationKey: ['processing', 'cancel'],
    mutationFn: async (submitted: ProcessingRun) => {
      if (!canWrite || cancelReview !== submitted || !cancelIntent || !runAccess.currentData(cancelIntent.admission) || !currentRun(submitted)) {
        throw new Error('Refresh the run and select cancellation again before continuing.')
      }
      const lease = captureSessionLease()
      const origin = current.current.location
      const capture = runAccess.captureResult()
      const admission = capture(true)
      const write: RunWrite = { key: runKey, capture, current: runAccess.currentData, allowed: () => Boolean(runAccess.currentData(admission)) }
      const run = await apiFetch<ProcessingRun>(`${RUNS_PATH}/${submitted.id}/cancel`, {
        method: 'POST', body: JSON.stringify({ expected_version: submitted.version }), signal: lease.signal,
      })
      lease.assertCurrent()
      return { run, lease, origin, write }
    },
    onSuccess: async ({ run, lease, origin, write }) => {
      lease.assertCurrent()
      await rememberRun(run, write)
      lease.assertCurrent()
      if (!write.allowed() || !mounted.current || current.current.location !== origin) return
      setCancelIntent(null)
      setNotice('Recovery cancellation recorded. Committed results are preserved; normal background processing may continue independently.')
    },
    onError: (error) => {
      if (processingConflict(error) || processingAccessError(error)) {
        setCancelIntent(null)
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
    setSelection(emptySelection())
    setReview(null)
    setCancelIntent(null)
    createRun.reset()
    cancelRun.reset()
    setNotice('')
    setPreparationError('')
    setParams(next)
  }
  const error = cancelRun.isError ? resolveApiErrorMessage(cancelRun.error, 'Recovery cancellation could not be recorded. Refresh the run before retrying.') : ''
  return {
    scope, rows, selected, staleSelection, workQuery, runsQuery, runQuery, user, canRead, canWrite, busy, notice,
    workAccessDenied: readNeedsVerification(workAccess, workQuery.data),
    runsAccessDenied: readNeedsVerification(runsAccess, runsQuery.data),
    runAccessDenied: readNeedsVerification(runAccess, runQuery.data),
    recoveryUnavailable: workQuery.isError || !workQuery.data,
    cancellationUnavailable: runQuery.isError || !runQuery.data,
    error: preparationError || error,
    review: review?.location === location && canRead && workAccess.currentData(review.admission) ? review : null,
    cancelReview: cancelReview?.id === scope.run && canRead ? cancelReview : null,
    createRun, cancelRun, changeScope,
    toggle: (row: ProcessingWork) => {
      if (!canWrite || busy || !row.can_retry || !currentWork()?.items.some((entry) => processingWorkKey(entry) === processingWorkKey(row) && entry.revision === row.revision)) return
      setSelection({ scope: workPath, rows: selected.some((entry) => processingWorkKey(entry) === processingWorkKey(row))
        ? selected.filter((entry) => processingWorkKey(entry) !== processingWorkKey(row)) : [...selected, row], admission: workAccess.captureResult()(true) })
    },
    selectPage: () => setSelection({ scope: workPath, rows: canWrite && !busy ? currentWork()?.items.filter((row) => row.can_retry) ?? [] : [], admission: workAccess.captureResult()(true) }),
    clearSelection: () => setSelection(emptySelection()),
    openReview: () => {
      if (!canWrite || busy || !selected.length || staleSelection || workQuery.isError || !currentWork() || !workAccess.currentData(selection.admission)) return
      createRun.reset()
      setPreparationError('')
      try {
        const items = selected.map(({ item_id, stage, revision }) => ({ item_id, stage, revision }))
          .sort((left, right) => `${left.item_id}:${left.stage}`.localeCompare(`${right.item_id}:${right.stage}`))
        const serialized = JSON.stringify(items)
        // Closing and reopening a review must not duplicate ambiguously accepted work.
        if (recoveryRequest.current?.serialized !== serialized) {
          recoveryRequest.current = { serialized, request: { idempotency_key: createSecureRequestId(), items } }
        }
        setReview({ rows: selected, scope: workPath, location, request: recoveryRequest.current.request, admission: workAccess.captureResult()(true) })
      } catch (error) {
        setPreparationError(resolveApiErrorMessage(error, 'Recovery could not be prepared. Your selection has been kept and no request was sent.'))
      }
    },
    closeReview: () => { if (!createRun.isPending) setReview(null) },
    openCancel: (run: ProcessingRun) => { if (currentRun(run)) { cancelRun.reset(); setCancelIntent({ run, admission: runAccess.captureResult()(true) }) } },
    closeCancel: () => { if (!cancelRun.isPending) setCancelIntent(null) },
    refresh: () => {
      void workQuery.refetch()
      void runsQuery.refetch()
      if (scope.run) void runQuery.refetch()
    },
  }
}

export type ProcessingWorkspaceController = ReturnType<typeof useProcessingWorkspace>
