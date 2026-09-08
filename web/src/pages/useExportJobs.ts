import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiDownload, apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { captureSessionLease } from '../api/sessionLifecycle'
import type { ArticleExportJob, ArticleExportJobList, ArticleExportJobRequest, ArticleExportRequest } from '../types/exports'
import { defaultExportFilename, triggerBrowserDownload } from './exportPageModel'

export function useExportJobs() {
  const client = useQueryClient()
  const [page, setPage] = useState(0)
  const [notice, setNotice] = useState<string | null>(null)
  const requestRef = useRef<{ serialized: string; request: ArticleExportJobRequest } | null>(null)
  const mounted = useRef(true)
  const downloadAbort = useRef<AbortController | null>(null)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      downloadAbort.current?.abort()
    }
  }, [])

  const jobsQuery = useQuery({
    queryKey: ['exports', 'jobs', page],
    queryFn: ({ signal }) => apiFetch<ArticleExportJobList>(`/exports/jobs?limit=25&offset=${page * 25}`, { signal }),
    refetchInterval: (query) => query.state.data?.items.some((job) => ['queued', 'running'].includes(job.status)) ? 5_000 : 30_000,
  })
  const refresh = () => client.invalidateQueries({ queryKey: ['exports', 'jobs'] })
  const createMutation = useMutation({
    mutationFn: (request: ArticleExportJobRequest) => apiFetch<ArticleExportJob>('/exports/jobs', {
      method: 'POST', body: JSON.stringify(request),
    }),
    onSuccess: () => {
      requestRef.current = null
      if (mounted.current) {
        setPage(0)
        setNotice('Background export accepted. You can leave this page and return to download it.')
      }
      void refresh()
    },
    onError: () => { void refresh() },
  })
  const cancelMutation = useMutation({
    mutationFn: (id: string) => apiFetch<ArticleExportJob>(`/exports/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
    onSuccess: () => { void refresh() },
  })
  const downloadMutation = useMutation({
    mutationFn: async (job: ArticleExportJob) => {
      const lease = captureSessionLease()
      downloadAbort.current?.abort()
      const controller = new AbortController()
      downloadAbort.current = controller
      try {
        const result = await apiDownload(`/exports/jobs/${encodeURIComponent(job.id)}/download`, {
          timeoutMs: 300_000, signal: controller.signal,
        })
        lease.assertCurrent()
        return { result, lease, job }
      } finally {
        if (downloadAbort.current === controller) downloadAbort.current = null
      }
    },
    onSuccess: ({ result, lease, job }) => {
      lease.assertCurrent()
      if (!mounted.current) return
      triggerBrowserDownload(result.blob, result.filename ?? job.filename ?? defaultExportFilename(job.format, null))
      setNotice('Background export downloaded.')
    },
    onError: () => { void refresh() },
  })
  const queueExport = (request: ArticleExportRequest) => {
    if (createMutation.isPending) return
    const serialized = JSON.stringify(request)
    // An ambiguous response is retried with the exact accepting operation key.
    // Filter edits form a new request, while the durable list reveals prior work.
    if (requestRef.current?.serialized !== serialized) {
      requestRef.current = { serialized, request: { ...request, idempotency_key: crypto.randomUUID() } }
    }
    setNotice(null)
    createMutation.mutate(requestRef.current.request)
  }

  const error = createMutation.error ?? cancelMutation.error ?? downloadMutation.error
  return {
    page, setPage, jobsQuery, createMutation, cancelMutation, downloadMutation, queueExport,
    notice, error: error ? resolveApiErrorMessage(error, 'The background export request could not be completed') : null,
  }
}

export type ExportJobsController = ReturnType<typeof useExportJobs>
