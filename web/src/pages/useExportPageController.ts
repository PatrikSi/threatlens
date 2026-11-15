import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'

import { apiDownload, apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { accessibleQueryData } from '../api/queryData'
import { useDebouncedValue } from '../hooks/useDebouncedValue'
import { useQueryAccessScope } from '../hooks/useQueryAccessScope'
import { useExportJobs } from './useExportJobs'
import type {
  ArticleExportCapabilities,
  ArticleExportFormat,
  ArticleExportOptions,
  ArticleExportPreview,
  ArticleExportRequest,
} from '../types/api'
import {
  changeExportFormat,
  createDefaultExportFilterDraft,
  createDefaultExportOptions,
  defaultExportFilename,
  exportBlockingReason,
  triggerBrowserDownload,
  validateExportFilterDraft,
  type ExportFilterDraft,
} from './exportPageModel'

export function useExportPageController() {
  const jobs = useExportJobs()
  const exportAccess = useQueryAccessScope(['exports'])
  const [filterDraft, setFilterDraft] = useState(createDefaultExportFilterDraft)
  const [format, setFormatState] = useState<ArticleExportFormat>('csv')
  const [options, setOptions] = useState<ArticleExportOptions>(() => createDefaultExportOptions('csv'))
  const [notice, setNotice] = useState<string | null>(null)
  const mountedRef = useRef(true)
  const activeExportRef = useRef<AbortController | null>(null)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      activeExportRef.current?.abort()
      activeExportRef.current = null
    }
  }, [])

  const capabilitiesQuery = useQuery({
    queryKey: ['exports', 'capabilities'],
    ...exportAccess.queryOptions(({ signal }) => apiFetch<ArticleExportCapabilities>('/exports/capabilities', { signal }), 5 * 60_000),
  })
  const capabilities = accessibleQueryData(capabilitiesQuery)

  const validation = useMemo(() => validateExportFilterDraft(filterDraft), [filterDraft])
  const debouncedFilters = useDebouncedValue(validation.filters, 450)
  const previewQuery = useQuery({
    queryKey: ['exports', 'preview', debouncedFilters],
    ...exportAccess.queryOptions(({ signal }) =>
      apiFetch<ArticleExportPreview>('/exports/preview', {
        method: 'POST',
        body: JSON.stringify({ filters: debouncedFilters }),
        signal,
      })),
    enabled: debouncedFilters !== null && capabilitiesQuery.isSuccess && Boolean(capabilities),
    // Reject retired cache envelopes before TanStack marks them as a successful
    // placeholder. Selecting that placeholder to undefined alternates its
    // pending/success snapshots and can repeatedly rerender the observer.
    placeholderData: (previous) => exportAccess.currentData(previous) === undefined ? undefined : previous,
  })
  const preview = capabilities ? accessibleQueryData(previewQuery) : undefined

  const exportMutation = useMutation({
    mutationFn: async (request: ArticleExportRequest) => {
      activeExportRef.current?.abort()
      const controller = new AbortController()
      activeExportRef.current = controller
      try {
        return await apiDownload('/exports', {
          method: 'POST',
          body: JSON.stringify(request),
          timeoutMs: 300_000,
          signal: controller.signal,
        })
      } finally {
        if (activeExportRef.current === controller) activeExportRef.current = null
      }
    },
    onMutate: () => {
      if (mountedRef.current) setNotice(null)
    },
    onSuccess: (result, request) => {
      if (!mountedRef.current) return
      const filename = result.filename ?? defaultExportFilename(request.format, request.options.filename_prefix)
      triggerBrowserDownload(result.blob, filename)
      setNotice(`Export ready: ${filename}`)
    },
  })

  useEffect(() => {
    if (!capabilities) {
      return
    }
    const feedIds = new Set(capabilities.feeds.map((entry) => entry.id))
    const tagIds = new Set(capabilities.tags.map((entry) => entry.id))
    const classifications = new Set(capabilities.classifications)
    setFilterDraft((current) => {
      const next = {
        ...current,
        feedIds: current.feedIds.filter((id) => feedIds.has(id)),
        tagIds: current.tagIds.filter((id) => tagIds.has(id)),
        classifications: current.classifications.filter((value) => classifications.has(value)),
      }
      return arraysEqual(current.feedIds, next.feedIds) &&
        arraysEqual(current.tagIds, next.tagIds) &&
        arraysEqual(current.classifications, next.classifications)
        ? current
        : next
    })
  }, [capabilities])

  const previewIsCurrent = validation.filters === debouncedFilters
  const blockingReason = capabilitiesQuery.isError || exportAccess.denied
    ? 'Export options must load before an export can be generated.'
    : validation.errors.length
      ? validation.errors[0]
      : !previewIsCurrent
        ? 'Wait for the matching article preview to update.'
        : previewQuery.isError || exportAccess.denied
          ? 'The matching article preview must load before an export can be generated.'
          : exportBlockingReason(preview, format, validation.errors)
  const canExport = Boolean(capabilities) && !blockingReason && !previewQuery.isFetching && !exportMutation.isPending && !jobs.createMutation.isPending
  const exportError = exportMutation.isError
    ? resolveApiErrorMessage(exportMutation.error, 'The article export could not be generated')
    : null

  const setFormat = (nextFormat: ArticleExportFormat) => {
    setFormatState(nextFormat)
    setOptions((current) => changeExportFormat(nextFormat, current))
    setNotice(null)
  }

  const updateOptions = (updates: Partial<ArticleExportOptions>) => {
    setOptions((current) => {
      const next = { ...current, ...updates }
      if (!next.include_user_state) {
        next.include_user_notes = false
      }
      return next
    })
  }

  const generateExport = () => {
    if (!validation.filters || !canExport) {
      return
    }
    exportMutation.mutate({ format, filters: validation.filters, options })
  }

  return {
    capabilitiesQuery,
    capabilities,
    previewQuery,
    preview,
    exportMutation,
    jobs,
    filterDraft,
    setFilterDraft,
    format,
    setFormat,
    options,
    updateOptions,
    validationErrors: validation.errors,
    blockingReason,
    canExport,
    exportError,
    notice,
    generateExport,
    queueBackgroundExport: () => {
      if (validation.filters && canExport) jobs.queueExport({ format, filters: validation.filters, options })
    },
  }
}

export type ExportPageController = ReturnType<typeof useExportPageController>

export function updateExportFilterDraft(
  setDraft: React.Dispatch<React.SetStateAction<ExportFilterDraft>>,
  updates: Partial<ExportFilterDraft>,
) {
  setDraft((current) => ({ ...current, ...updates }))
}

function arraysEqual(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index])
}
