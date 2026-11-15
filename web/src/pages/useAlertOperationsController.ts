import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import { useQueryAccessScope } from '../hooks/useQueryAccessScope'
import type {
  AlertEvaluationActivityListResponse,
  AlertEvaluationListResponse,
  AlertEvaluationReplayResponse,
  AlertEvaluationRequest,
  AlertOccurrenceMetricListResponse,
} from '../types/alerts'

export type AlertOperationsStateFilter = 'failures' | 'dead_letter' | 'retry_wait' | 'all'

const OPERATIONS_REFRESH_MS = 30_000

export function useAlertOperationsController(active: boolean) {
  const queryClient = useQueryClient()
  const [params] = useSearchParams()
  const metricsTeamId = params.get('team_id') ?? ''
  const [stateFilter, setStateFilter] = useState<AlertOperationsStateFilter>('failures')
  const [page, setPage] = useState(1)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [activityPage, setActivityPage] = useState(1)
  const [feedback, setFeedback] = useState<string | null>(null)
  const listAccess = useQueryAccessScope(['alerts', 'operations', 'evaluations'])
  const detailAccess = useQueryAccessScope(['alerts', 'operations', 'evaluation', selectedId ?? ''])
  const activityAccess = useQueryAccessScope(['alerts', 'operations', 'evaluation', selectedId ?? '', 'activity'])
  const captureReplay = (request: AlertEvaluationRequest) => ({
    request, parent: listAccess.captureResult()(true), detail: detailAccess.captureResult()(true),
  })
  const [replayIntent, setReplayIntent] = useState<ReturnType<typeof captureReplay> | null>(null)
  const replayTarget = replayIntent && replayIntent.request.id === selectedId && listAccess.currentData(replayIntent.parent) && detailAccess.currentData(replayIntent.detail)
    ? replayIntent.request : null

  const listPath = useMemo(() => {
    const params = new URLSearchParams({ page: String(page), page_size: '25' })
    if (stateFilter === 'failures') {
      params.set('needs_attention', 'true')
    } else if (stateFilter !== 'all') {
      params.append('states', stateFilter)
    }
    return `/alerts/occurrences/evaluations?${params.toString()}`
  }, [page, stateFilter])

  const evaluationsQuery = useQuery({
    queryKey: ['alerts', 'operations', 'evaluations', listPath],
    ...listAccess.queryOptions(({ signal }) => apiFetch<AlertEvaluationListResponse>(listPath, { signal })),
    enabled: active,
    refetchInterval: OPERATIONS_REFRESH_MS,
    retry: retryOperationsQuery,
  })
  const metricsQuery = useQuery({
    queryKey: ['alerts', 'operations', 'metrics', metricsTeamId],
    queryFn: () => {
      const until = new Date()
      const since = new Date(until.getTime() - 30 * 24 * 60 * 60 * 1000)
      const params = new URLSearchParams({
        since: since.toISOString(),
        until: until.toISOString(),
        limit: '1000',
      })
      if (metricsTeamId) params.set('team_id', metricsTeamId)
      return apiFetch<AlertOccurrenceMetricListResponse>(`/alerts/occurrences/metrics?${params.toString()}`)
    },
    enabled: active,
    refetchInterval: OPERATIONS_REFRESH_MS,
    retry: retryOperationsQuery,
  })
  const detailKey = ['alerts', 'operations', 'evaluation', selectedId, listAccess.generation]
  const detailQuery = useQuery({
    queryKey: detailKey,
    ...detailAccess.queryOptions(({ signal }) => apiFetch<AlertEvaluationRequest>(`/alerts/occurrences/evaluations/${selectedId}`, { signal })),
    enabled: active && Boolean(selectedId) && !listAccess.denied,
    retry: retryOperationsQuery,
  })
  const activityQuery = useQuery({
    queryKey: ['alerts', 'operations', 'evaluation', selectedId, 'activity', activityPage, listAccess.generation, detailAccess.generation],
    ...activityAccess.queryOptions(({ signal }) => apiFetch<AlertEvaluationActivityListResponse>(
      `/alerts/occurrences/evaluations/${selectedId}/activity?page=${activityPage}&page_size=50`,
      { signal },
    )),
    enabled: active && Boolean(selectedId) && Boolean(detailQuery.data) && !listAccess.denied,
    retry: retryOperationsQuery,
  })
  function currentEvaluation(request: AlertEvaluationRequest) {
    if (queryClient.getQueryState(detailKey)?.status === 'error' || listAccess.denied) return undefined
    const current = detailAccess.currentData<AlertEvaluationRequest>(queryClient.getQueryData(detailKey))
    return current?.id === request.id && current.version === request.version && current.state === 'dead_letter' ? current : undefined
  }
  const replayUnavailable = detailQuery.isError || !detailQuery.data || Boolean(replayTarget && !currentEvaluation(replayTarget))
  const replay = useMutation({
    mutationKey: ['alerts', 'operations', 'replay'],
    mutationFn: async (request: AlertEvaluationRequest) => {
      if (request !== replayTarget || !replayIntent || !listAccess.currentData(replayIntent.parent)
        || !detailAccess.currentData(replayIntent.detail) || !currentEvaluation(request)) {
        throw new Error('Refresh the evaluation and select replay again before continuing.')
      }
      const capture = detailAccess.captureResult()
      const admission = capture(true)
      const parent = listAccess.captureResult()(true)
      const acceptsResult = () => Boolean(detailAccess.currentData(admission) && listAccess.currentData(parent))
      const response = await apiFetch<AlertEvaluationReplayResponse>(`/alerts/occurrences/evaluations/${request.id}/replay`, {
        method: 'POST',
        body: JSON.stringify({ expected_version: request.version }),
      })
      return { response, capture, acceptsResult, key: detailKey, requestId: request.id }
    },
    onSuccess: ({ response, capture, acceptsResult, key, requestId }) => {
      void queryClient.invalidateQueries({ queryKey: ['alerts', 'operations'] })
      if (!acceptsResult() || response.request.id !== requestId) return
      if (queryClient.getQueryState(key)?.status === 'success') queryClient.setQueryData(key, capture(response.request))
      if (selectedId !== requestId) return
      setReplayIntent(null)
      setFeedback(response.enqueue_failed
        ? 'Replay was accepted durably, but immediate worker enqueue failed. Background reconciliation will retry it.'
        : 'Replay accepted and queued.')
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === 'alert_evaluation_version_conflict') {
        setReplayIntent(null)
        setFeedback('The evaluation changed after it was loaded. Fresh state has been requested; review it before replaying.')
        void queryClient.invalidateQueries({ queryKey: ['alerts', 'operations'] })
      }
    },
  })

  useEffect(() => {
    const items = evaluationsQuery.data?.items
    if (!items || items.length === 0) {
      setSelectedId(null)
      return
    }
    if (!selectedId || !items.some((item) => item.id === selectedId)) {
      setSelectedId(items[0].id)
    }
  }, [evaluationsQuery.data?.items, selectedId])

  useEffect(() => {
    setActivityPage(1)
    setReplayIntent(null)
  }, [selectedId])

  const metrics = useMemo(() => {
    const rows = metricsQuery.data?.items ?? []
    return rows.reduce(
      (total, row) => {
        total.total += row.occurrence_count
        if (row.suppressed) total.suppressed += row.occurrence_count
        if (row.severity === 'critical') total.critical += row.occurrence_count
        if (row.lifecycle_state !== 'closed') total.open += row.occurrence_count
        return total
      },
      { total: 0, open: 0, critical: 0, suppressed: 0 },
    )
  }, [metricsQuery.data?.items])

  const changeStateFilter = (next: AlertOperationsStateFilter) => {
    setStateFilter(next)
    setPage(1)
    setSelectedId(null)
    setReplayIntent(null)
    setFeedback(null)
  }

  return {
    metricsTeamId,
    activityQuery,
    activityPage,
    changeStateFilter,
    detailQuery,
    evaluationsQuery,
    feedback,
    metrics,
    metricsQuery,
    page,
    replay,
    replayUnavailable,
    replayTarget,
    select: setSelectedId,
    selectedId,
    setFeedback,
    setActivityPage,
    setPage,
    setReplayTarget: (request: AlertEvaluationRequest | null) => {
      if (!request) setReplayIntent(null)
      else if (currentEvaluation(request)) setReplayIntent(captureReplay(request))
    },
    stateFilter,
  }
}

function retryOperationsQuery(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && [401, 403, 404, 422].includes(error.status)) return false
  return failureCount < 1
}

export type AlertOperationsController = ReturnType<typeof useAlertOperationsController>
