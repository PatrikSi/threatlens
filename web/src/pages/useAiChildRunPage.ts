import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import type { AITaskRunListResponse, AITaskRunResponse } from '../types/api'

export const AI_CHILD_RUN_PAGE_SIZE = 50

export function useAiChildRunPage(parentId: string | null, parent: AITaskRunResponse | undefined) {
  const [selection, setSelection] = useState({ parentId, page: 0 })
  const [lastResult, setLastResult] = useState<{ parentId: string | null; data: AITaskRunListResponse } | null>(null)
  const page = selection.parentId === parentId ? selection.page : 0
  useEffect(() => { setSelection({ parentId, page: 0 }) }, [parentId])
  const query = useQuery({
    queryKey: ['ai', 'ops', 'child-runs', parentId, page],
    queryFn: ({ signal }) => apiFetch<AITaskRunListResponse>(
      `/ai/ops/runs?parent_run_id=${parentId}&limit=${AI_CHILD_RUN_PAGE_SIZE}&offset=${page * AI_CHILD_RUN_PAGE_SIZE}`,
      { signal },
    ),
    enabled: Boolean(parentId && parent?.id === parentId && parent.task_type === 'reprocess'),
    refetchInterval: parent && ['queued', 'running'].includes(parent.status) ? 10000 : false,
    staleTime: 5000,
  })

  const accessDenied = query.error instanceof ApiError && [401, 403, 404].includes(query.error.status)
  useEffect(() => {
    if (accessDenied) { setLastResult(null); return }
    if (!query.data) return
    setLastResult({ parentId, data: query.data })
    if (page > 0 && query.data.total <= page * AI_CHILD_RUN_PAGE_SIZE) {
      setSelection({ parentId, page: Math.max(0, Math.ceil(query.data.total / AI_CHILD_RUN_PAGE_SIZE) - 1) })
    }
  }, [parentId, page, query.data, accessDenied])

  // Retain only this parent's last accepted page during loading or failure.
  // A failed new query has no data; placeholderData alone does not retain it.
  const data = accessDenied ? undefined : query.data ?? (lastResult?.parentId === parentId ? lastResult.data : undefined)
  return {
    query,
    data,
    page,
    visiblePage: Math.floor((data?.offset ?? 0) / AI_CHILD_RUN_PAGE_SIZE),
    goToPage: (nextPage: number) => setSelection({ parentId, page: Math.max(0, nextPage) }),
  }
}

export type AiChildRunPage = ReturnType<typeof useAiChildRunPage>
