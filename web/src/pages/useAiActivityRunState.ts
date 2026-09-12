import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { accessibleQueryData } from '../api/queryData'
import type { AITaskRunDetailResponse } from '../types/api'
import type { ActivityTabProps } from './AiActivityTypes'
import { useAiChildRunPage } from './useAiChildRunPage'
import { AI_RUN_PAGE_SIZE, findLatestProviderExchangeEvent } from './aiSettingsUtils'

type RunStateInput = Pick<
  ActivityTabProps,
  'runPage' | 'setRunPage' | 'runsQuery' | 'selectedRunId' | 'runDetailQuery'
>

export function useAiActivityRunState({
  runPage,
  setRunPage,
  runsQuery,
  selectedRunId,
  runDetailQuery,
}: RunStateInput) {
  const selectedRun = accessibleQueryData(runDetailQuery)?.run
  const [inspectedRunId, setInspectedRunId] = useState<string | null>(null)
  const history = useRunHistoryMetrics(runPage, runsQuery)

  useEffect(() => {
    const runOffset = runPage * AI_RUN_PAGE_SIZE
    if (!runsQuery.data || runsQuery.isPlaceholderData || runPage === 0 || runsQuery.data.total > runOffset) {
      return
    }
    setRunPage(Math.max(0, Math.ceil(runsQuery.data.total / AI_RUN_PAGE_SIZE) - 1))
  }, [runPage, runsQuery.data, runsQuery.isPlaceholderData, setRunPage])

  const inspectedRunDetailQuery = useQuery({
    queryKey: ['ai', 'ops', 'inspect-run', inspectedRunId],
    queryFn: ({ signal }) => apiFetch<AITaskRunDetailResponse>(`/ai/ops/runs/${inspectedRunId}`, { signal }),
    enabled: Boolean(inspectedRunId),
    staleTime: 5000,
  })
  const inspectedDetail = accessibleQueryData(inspectedRunDetailQuery)
  const inspectedProviderEvent = useMemo(
    () => findLatestProviderExchangeEvent(inspectedDetail?.events ?? []),
    [inspectedDetail?.events],
  )

  const childRunPage = useAiChildRunPage(selectedRunId, selectedRun)

  return {
    selectedRun,
    history,
    inspectedRunId,
    setInspectedRunId,
    inspectedRun: inspectedDetail?.run ?? null,
    inspectedProviderEvent,
    inspectedRunDetailQuery,
    inspectedRunErrorMessage: inspectedRunDetailQuery.isError
      ? resolveApiErrorMessage(inspectedRunDetailQuery.error, 'Inspected AI run details could not be loaded')
      : '',
    childRunPage,
  }
}

function useRunHistoryMetrics(runPage: number, runsQuery: ActivityTabProps['runsQuery']) {
  const data = accessibleQueryData(runsQuery)
  return useMemo(
    () =>
      buildRunHistoryMetrics({
        runPage,
        runTotal: data?.total ?? 0,
        dataOffset: data?.offset,
        runCount: data?.items.length ?? 0,
        hasData: Boolean(data),
        isFetching: runsQuery.isFetching,
        isLoading: runsQuery.isLoading,
        isPlaceholderData: runsQuery.isPlaceholderData,
      }),
    [runPage, data, runsQuery.isFetching, runsQuery.isLoading, runsQuery.isPlaceholderData],
  )
}

export function buildRunHistoryMetrics({
  runPage,
  runTotal,
  dataOffset,
  runCount,
  hasData,
  isFetching,
  isLoading,
  isPlaceholderData,
}: {
  runPage: number
  runTotal: number
  dataOffset: number | undefined
  runCount: number
  hasData: boolean
  isFetching: boolean
  isLoading: boolean
  isPlaceholderData: boolean
}) {
  const runOffset = runPage * AI_RUN_PAGE_SIZE
  const visibleRunOffset = isPlaceholderData ? (dataOffset ?? runOffset) : runOffset
  const runListLoading = isLoading && !hasData
  const isPageLoading = isFetching && isPlaceholderData
  const isRefreshing = isFetching && hasData && !isPlaceholderData
  return {
    runTotal,
    visibleRunOffset,
    runCount,
    totalPages: Math.max(1, Math.ceil(runTotal / AI_RUN_PAGE_SIZE)),
    isLoading: runListLoading,
    isPageLoading,
    isRefreshing,
    statusMessage: getRunListStatusMessage(runListLoading, isPageLoading, isRefreshing, runPage),
  }
}

function getRunListStatusMessage(
  isLoading: boolean,
  isPageLoading: boolean,
  isRefreshing: boolean,
  runPage: number,
) {
  if (isLoading) {
    return 'Loading AI run history...'
  }
  if (isPageLoading) {
    return `Loading page ${runPage + 1}...`
  }
  return isRefreshing ? 'Refreshing run history...' : null
}

export type AiActivityRunState = ReturnType<typeof useAiActivityRunState>
