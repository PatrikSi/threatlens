import { useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query'

import { apiDownload, apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { useDebouncedValue } from '../hooks/useDebouncedValue'
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
  const [filterDraft, setFilterDraft] = useState(createDefaultExportFilterDraft)
  const [format, setFormatState] = useState<ArticleExportFormat>('csv')
  const [options, setOptions] = useState<ArticleExportOptions>(() => createDefaultExportOptions('csv'))
  const [notice, setNotice] = useState<string | null>(null)

  const capabilitiesQuery = useQuery({
    queryKey: ['exports', 'capabilities'],
    queryFn: ({ signal }) => apiFetch<ArticleExportCapabilities>('/exports/capabilities', { signal }),
    staleTime: 5 * 60_000,
  })

  const validation = useMemo(() => validateExportFilterDraft(filterDraft), [filterDraft])
  const debouncedFilters = useDebouncedValue(validation.filters, 450)
  const previewQuery = useQuery({
    queryKey: ['exports', 'preview', debouncedFilters],
    queryFn: ({ signal }) =>
      apiFetch<ArticleExportPreview>('/exports/preview', {
        method: 'POST',
        body: JSON.stringify({ filters: debouncedFilters }),
        signal,
      }),
    enabled: debouncedFilters !== null && capabilitiesQuery.isSuccess,
    placeholderData: keepPreviousData,
  })

  const exportMutation = useMutation({
    mutationFn: (request: ArticleExportRequest) =>
      apiDownload('/exports', {
        method: 'POST',
        body: JSON.stringify(request),
        timeoutMs: 300_000,
      }),
    onMutate: () => setNotice(null),
    onSuccess: (result, request) => {
      const filename = result.filename ?? defaultExportFilename(request.format, request.options.filename_prefix)
      triggerBrowserDownload(result.blob, filename)
      setNotice(`Export ready: ${filename}`)
    },
  })

  useEffect(() => {
    const capabilities = capabilitiesQuery.data
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
  }, [capabilitiesQuery.data])

  const previewIsCurrent = validation.filters === debouncedFilters
  const blockingReason = validation.errors.length
    ? validation.errors[0]
    : !previewIsCurrent
      ? 'Wait for the matching article preview to update.'
      : previewQuery.isError
        ? 'The matching article preview must load before an export can be generated.'
        : exportBlockingReason(previewQuery.data, format, validation.errors)
  const canExport = !blockingReason && !previewQuery.isFetching && !exportMutation.isPending
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
    previewQuery,
    exportMutation,
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
