import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
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
  const [stateFilter, setStateFilter] = useState<AlertOperationsStateFilter>('failures')
  const [page, setPage] = useState(1)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [activityPage, setActivityPage] = useState(1)
  const [replayTarget, setReplayTarget] = useState<AlertEvaluationRequest | null>(null)
  const [feedback, setFeedback] = useState<string | null>(null)

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
    queryFn: () => apiFetch<AlertEvaluationListResponse>(listPath),
    enabled: active,
    refetchInterval: OPERATIONS_REFRESH_MS,
    retry: retryOperationsQuery,
  })
  const metricsQuery = useQuery({
    queryKey: ['alerts', 'operations', 'metrics'],
    queryFn: () => {
      const until = new Date()
      const since = new Date(until.getTime() - 30 * 24 * 60 * 60 * 1000)
      const params = new URLSearchParams({
        since: since.toISOString(),
        until: until.toISOString(),
        limit: '1000',
      })
      return apiFetch<AlertOccurrenceMetricListResponse>(`/alerts/occurrences/metrics?${params.toString()}`)
    },
    enabled: active,
    refetchInterval: OPERATIONS_REFRESH_MS,
    retry: retryOperationsQuery,
  })
  const detailQuery = useQuery({
    queryKey: ['alerts', 'operations', 'evaluation', selectedId],
    queryFn: () => apiFetch<AlertEvaluationRequest>(`/alerts/occurrences/evaluations/${selectedId}`),
    enabled: active && Boolean(selectedId),
    retry: retryOperationsQuery,
  })
  const activityQuery = useQuery({
    queryKey: ['alerts', 'operations', 'evaluation', selectedId, 'activity', activityPage],
    queryFn: () => apiFetch<AlertEvaluationActivityListResponse>(
      `/alerts/occurrences/evaluations/${selectedId}/activity?page=${activityPage}&page_size=50`,
    ),
    enabled: active && Boolean(selectedId),
    retry: retryOperationsQuery,
  })
  const replay = useMutation({
    mutationKey: ['alerts', 'operations', 'replay'],
    mutationFn: (request: AlertEvaluationRequest) =>
      apiFetch<AlertEvaluationReplayResponse>(`/alerts/occurrences/evaluations/${request.id}/replay`, {
        method: 'POST',
        body: JSON.stringify({ expected_version: request.version }),
      }),
    onSuccess: (response) => {
      setReplayTarget(null)
      setFeedback(response.enqueue_failed
        ? 'Replay was accepted durably, but immediate worker enqueue failed. Background reconciliation will retry it.'
        : 'Replay accepted and queued.')
      queryClient.setQueryData(
        ['alerts', 'operations', 'evaluation', response.request.id],
        response.request,
      )
      void queryClient.invalidateQueries({ queryKey: ['alerts', 'operations'] })
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === 'alert_evaluation_version_conflict') {
        setReplayTarget(null)
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
    setFeedback(null)
  }

  return {
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
    replayTarget,
    select: setSelectedId,
    selectedId,
    setFeedback,
    setActivityPage,
    setPage,
    setReplayTarget,
    stateFilter,
  }
}

function retryOperationsQuery(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && [401, 403, 404, 422].includes(error.status)) return false
  return failureCount < 1
}

export type AlertOperationsController = ReturnType<typeof useAlertOperationsController>
