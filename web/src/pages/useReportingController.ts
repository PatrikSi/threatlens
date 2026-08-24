import { useEffect, useMemo, useRef, useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'

import { ApiError, ApiTransportError, apiDownload, apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { useDebouncedValue } from '../hooks/useDebouncedValue'
import { useCurrentUser } from '../hooks/useCurrentUser'
import type {
  ReportCapabilities,
  ReportDeliveryMode,
  ReportDetail,
  ReportListItem,
  ReportPreview,
  ReportPromptConfig,
  ReportQueueResponse,
  ReportSchedule,
  ReportScheduleWrite,
  ReportSectionConfig,
  ReportTemplate,
} from '../types/api'
import { triggerBrowserDownload, type ExportFilterDraft } from './exportPageModel'
import {
  DEFAULT_REPORT_PROMPT,
  DEFAULT_REPORT_SECTIONS,
  createDefaultExportFilterDraftForReports,
  reportBuilderFromTemplate,
  reportPeriodFromFilters,
  validateReportBuilder,
} from './reportingPageModel'
import {
  REPORT_CREATE_TIMEOUT_MS,
  REPORT_PREVIEW_TIMEOUT_MS,
  reportQueueFeedback,
  reportPreviewErrorBlocksCreation,
  resolveReportCreateBlockedReason,
  shouldRetryReportPreview,
} from './reportingResilience'

export type ReportingTab = 'reports' | 'templates' | 'schedules'
export type ReportingFeedback = {
  kind: 'error' | 'info' | 'success'
  message: string
} | null

type ReportDownloadFormat = 'markdown' | 'html' | 'pdf'

export function useReportingController() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { reportId: routeReportId } = useParams<{ reportId?: string }>()
  const currentUser = useCurrentUser()
  const isAdmin = currentUser.data?.role === 'admin'
  const canAuthor = currentUser.data?.role === 'admin' || currentUser.data?.role === 'analyst'
  const [activeTab, setActiveTab] = useState<ReportingTab>('reports')
  const [selectedTemplateId, setSelectedTemplateId] = useState('')
  const [filterDraft, setFilterDraft] = useState<ExportFilterDraft>(createDefaultExportFilterDraftForReports)
  const [prompt, setPrompt] = useState<ReportPromptConfig>(structuredClone(DEFAULT_REPORT_PROMPT))
  const [sections, setSections] = useState<ReportSectionConfig[]>(structuredClone(DEFAULT_REPORT_SECTIONS))
  const [excludedItemIds, setExcludedItemIds] = useState<string[]>([])
  const [title, setTitle] = useState('')
  const [deliverWhenReady, setDeliverWhenReady] = useState(false)
  const [deliveryMode, setDeliveryMode] = useState<ReportDeliveryMode>('summary')
  const [feedback, setFeedback] = useState<ReportingFeedback>(null)
  const createRequestRef = useRef<{ body: string; key: string } | null>(null)
  const retryRequestKeysRef = useRef(new Map<string, string>())
  const templateSaveRequestsRef = useRef(new Map<string, Promise<ReportTemplate>>())
  const templateCloneRequestsRef = useRef(new Map<string, Promise<ReportTemplate>>())
  const templateDeleteRequestsRef = useRef(new Map<string, Promise<void>>())
  const scheduleCreateRequestsRef = useRef(new Map<string, Promise<ReportSchedule>>())
  const scheduleUpdateRequestsRef = useRef(new Map<string, Promise<ReportSchedule>>())
  const scheduleDeleteRequestsRef = useRef(new Map<string, Promise<void>>())
  const scheduleRunRequestsRef = useRef(new Map<string, Promise<ReportQueueResponse[]>>())
  const selectedReportIdRef = useRef(routeReportId)
  const mountedRef = useRef(true)
  const activeDownloadRef = useRef<{
    reportId: string
    controller: AbortController
  } | null>(null)
  selectedReportIdRef.current = routeReportId

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      activeDownloadRef.current?.controller.abort()
      activeDownloadRef.current = null
    }
  }, [])

  useEffect(() => {
    const activeDownload = activeDownloadRef.current
    if (activeDownload && activeDownload.reportId !== routeReportId) {
      activeDownload.controller.abort()
      activeDownloadRef.current = null
    }
  }, [routeReportId])

  const capabilitiesQuery = useQuery({
    queryKey: ['reports', 'capabilities'],
    queryFn: ({ signal }) => apiFetch<ReportCapabilities>('/reports/capabilities', { signal }),
    staleTime: 5 * 60_000,
  })
  const templatesQuery = useQuery({
    queryKey: ['reports', 'templates'],
    queryFn: ({ signal }) => apiFetch<ReportTemplate[]>('/reports/templates', { signal }),
    staleTime: 60_000,
  })
  const reportsQuery = useQuery({
    queryKey: ['reports', 'library'],
    queryFn: ({ signal }) => apiFetch<ReportListItem[]>('/reports?limit=100', { signal }),
    refetchInterval: 10_000,
  })
  const schedulesQuery = useQuery({
    queryKey: ['reports', 'schedules'],
    queryFn: ({ signal }) => apiFetch<ReportSchedule[]>('/reports/schedules', { signal }),
    enabled: isAdmin,
    staleTime: 30_000,
  })
  const reportDetailQuery = useQuery({
    queryKey: ['reports', 'detail', routeReportId],
    queryFn: ({ signal }) => apiFetch<ReportDetail>(`/reports/${routeReportId}`, { signal }),
    enabled: Boolean(routeReportId),
    refetchInterval: (query) => {
      const status = (query.state.data as ReportDetail | undefined)?.status
      return status === 'queued' || status === 'running' ? 3000 : false
    },
  })

  useEffect(() => {
    if (selectedTemplateId || !templatesQuery.data?.length) return
    setSelectedTemplateId(templatesQuery.data[0].id)
  }, [selectedTemplateId, templatesQuery.data])

  useEffect(() => {
    if (!selectedTemplateId || !templatesQuery.data) return
    const template = templatesQuery.data.find((entry) => entry.id === selectedTemplateId)
    if (!template) return
    const state = reportBuilderFromTemplate(template)
    setPrompt(state.prompt)
    setSections(state.sections)
    if (Object.values(template.default_filters).some((value) => value !== null && (!Array.isArray(value) || value.length))) {
      setFilterDraft(state.filterDraft)
    }
    setExcludedItemIds([])
  }, [selectedTemplateId, templatesQuery.data])

  const validation = useMemo(
    () => validateReportBuilder(filterDraft, prompt, sections),
    [filterDraft, prompt, sections],
  )
  const previewPayload = useMemo(
    () => validation.filters ? { filters: validation.filters, excluded_item_ids: excludedItemIds, prompt, sections } : null,
    [excludedItemIds, prompt, sections, validation.filters],
  )
  const debouncedPreviewPayload = useDebouncedValue(previewPayload, 500)
  const previewQuery = useQuery({
    queryKey: ['reports', 'preview', debouncedPreviewPayload],
    queryFn: ({ signal }) => apiFetch<ReportPreview>('/reports/preview', {
      method: 'POST',
      body: JSON.stringify(debouncedPreviewPayload),
      signal,
      timeoutMs: REPORT_PREVIEW_TIMEOUT_MS,
    }),
    enabled: Boolean(
      debouncedPreviewPayload &&
      capabilitiesQuery.data?.reporting_enabled &&
      capabilitiesQuery.data?.ai_configured &&
      canAuthor,
    ),
    placeholderData: keepPreviousData,
    retry: shouldRetryReportPreview,
  })

  const createReportMutation = useMutation({
    mutationFn: async () => {
      if (!validation.filters) throw new Error('Report filters are invalid.')
      const body = JSON.stringify({
        template_id: selectedTemplateId || null,
        title: title.trim() || null,
        ...reportPeriodFromFilters(validation.filters),
        filters: validation.filters,
        excluded_item_ids: excludedItemIds,
        prompt,
        sections,
        deliver_when_ready: deliverWhenReady,
        delivery_mode: deliveryMode,
      })
      if (createRequestRef.current?.body !== body) {
        createRequestRef.current = { body, key: createIdempotencyKey() }
      }
      return apiFetch<ReportQueueResponse>('/reports', {
        method: 'POST',
        body,
        headers: { 'Idempotency-Key': createRequestRef.current.key },
        timeoutMs: REPORT_CREATE_TIMEOUT_MS,
      })
    },
    onMutate: () => setFeedback(null),
    onSuccess: (result) => {
      createRequestRef.current = null
      setFeedback(reportQueueFeedback('create', result.status))
      void queryClient.invalidateQueries({ queryKey: ['reports', 'library'] })
      navigate(`/reporting/${result.report_id}`)
    },
    onError: (error) => {
      if (!isAmbiguousQueueError(error)) createRequestRef.current = null
      setFeedback({
        kind: 'error',
        message: resolveReportQueueError(error),
      })
    },
  })

  const retryMutation = useMutation({
    mutationFn: (reportId: string) => {
      const key = retryRequestKeysRef.current.get(reportId) ?? createIdempotencyKey()
      retryRequestKeysRef.current.set(reportId, key)
      return apiFetch<ReportQueueResponse>(`/reports/${reportId}/retry`, {
        method: 'POST',
        headers: { 'Idempotency-Key': key },
      })
    },
    onMutate: () => setFeedback(null),
    onSuccess: (result) => {
      retryRequestKeysRef.current.delete(result.report_id)
      setFeedback(reportQueueFeedback('retry', result.status))
      void queryClient.invalidateQueries({ queryKey: ['reports'] })
      navigate(`/reporting/${result.report_id}`)
    },
    onError: (error, reportId) => {
      if (!isAmbiguousQueueError(error)) retryRequestKeysRef.current.delete(reportId)
      setFeedback({ kind: 'error', message: resolveReportQueueError(error) })
    },
  })
  const deleteMutation = useMutation({
    mutationFn: (reportId: string) => apiFetch<void>(`/reports/${reportId}`, { method: 'DELETE' }),
    onMutate: () => setFeedback(null),
    onSuccess: () => {
      setFeedback({ kind: 'success', message: 'Report deleted.' })
      void queryClient.invalidateQueries({ queryKey: ['reports', 'library'] })
      navigate('/reporting')
    },
    onError: (error) => setActionError(
      setFeedback,
      error,
      'The report could not be deleted',
    ),
  })
  const templateMutation = useMutation({
    mutationKey: ['reports', 'templates', 'save'],
    mutationFn: (payload: { mode: 'create' | 'update'; name: string; visibility: 'private' | 'shared' }) => {
      if (!validation.filters) throw new Error('Report filters are invalid.')
      const selected = templatesQuery.data?.find((template) => template.id === selectedTemplateId)
      const path = payload.mode === 'update' && selected ? `/reports/templates/${selected.id}` : '/reports/templates'
      const requestKey = payload.mode === 'update' && selected ? selected.id : 'create'
      return coalesceRequest(templateSaveRequestsRef.current, requestKey, () => (
        apiFetch<ReportTemplate>(path, {
          method: payload.mode === 'update' ? 'PUT' : 'POST',
          body: JSON.stringify({
            name: payload.name,
            description: selected?.description ?? 'Custom intelligence report template.',
            report_type: selected?.report_type ?? 'custom',
            visibility: payload.visibility,
            prompt,
            sections,
            default_filters: validation.filters,
          }),
        })
      ))
    },
    onMutate: () => setFeedback(null),
    onSuccess: (template) => {
      setSelectedTemplateId(template.id)
      setFeedback({ kind: 'success', message: 'Report template saved.' })
      void queryClient.invalidateQueries({ queryKey: ['reports', 'templates'] })
    },
    onError: (error) => setActionError(
      setFeedback,
      error,
      'The report template could not be saved',
    ),
  })
  const cloneTemplateMutation = useMutation({
    mutationKey: ['reports', 'templates', 'clone'],
    mutationFn: (templateId: string) => coalesceRequest(
      templateCloneRequestsRef.current,
      templateId,
      () => apiFetch<ReportTemplate>(`/reports/templates/${templateId}/clone`, { method: 'POST' }),
    ),
    onMutate: () => setFeedback(null),
    onSuccess: (template) => {
      setSelectedTemplateId(template.id)
      setActiveTab('reports')
      setFeedback({
        kind: 'success',
        message: 'Template cloned. Adjust it in the builder and save when ready.',
      })
      void queryClient.invalidateQueries({ queryKey: ['reports', 'templates'] })
    },
    onError: (error) => setActionError(
      setFeedback,
      error,
      'The report template could not be cloned',
    ),
  })
  const deleteTemplateMutation = useMutation({
    mutationKey: ['reports', 'templates', 'delete'],
    mutationFn: (templateId: string) => coalesceRequest(
      templateDeleteRequestsRef.current,
      templateId,
      () => apiFetch<void>(`/reports/templates/${templateId}`, { method: 'DELETE' }),
    ),
    onMutate: () => setFeedback(null),
    onSuccess: () => {
      setFeedback({ kind: 'success', message: 'Report template deleted.' })
      void queryClient.invalidateQueries({ queryKey: ['reports', 'templates'] })
    },
    onError: (error) => setActionError(
      setFeedback,
      error,
      'The report template could not be deleted',
    ),
  })
  const createScheduleMutation = useMutation({
    mutationKey: ['reports', 'schedules', 'create'],
    mutationFn: (payload: ReportScheduleWrite) => coalesceRequest(
      scheduleCreateRequestsRef.current,
      'create',
      () => apiFetch<ReportSchedule>('/reports/schedules', { method: 'POST', body: JSON.stringify(payload) }),
    ),
    onMutate: () => setFeedback(null),
    onSuccess: () => {
      setFeedback({ kind: 'success', message: 'Report schedule created.' })
      void queryClient.invalidateQueries({ queryKey: ['reports', 'schedules'] })
    },
    onError: (error) => setActionError(
      setFeedback,
      error,
      'The report schedule could not be created',
    ),
  })
  const updateScheduleMutation = useMutation({
    mutationKey: ['reports', 'schedules', 'update'],
    mutationFn: (schedule: ReportSchedule) => coalesceRequest(
      scheduleUpdateRequestsRef.current,
      schedule.id,
      () => apiFetch<ReportSchedule>(`/reports/schedules/${schedule.id}`, {
        method: 'PUT',
        body: JSON.stringify(schedulePayload(schedule)),
      }),
    ),
    onMutate: () => setFeedback(null),
    onSuccess: () => {
      setFeedback({ kind: 'success', message: 'Report schedule updated.' })
      void queryClient.invalidateQueries({ queryKey: ['reports', 'schedules'] })
    },
    onError: (error) => setActionError(
      setFeedback,
      error,
      'The report schedule could not be updated',
    ),
  })
  const deleteScheduleMutation = useMutation({
    mutationKey: ['reports', 'schedules', 'delete'],
    mutationFn: (scheduleId: string) => coalesceRequest(
      scheduleDeleteRequestsRef.current,
      scheduleId,
      () => apiFetch<void>(`/reports/schedules/${scheduleId}`, { method: 'DELETE' }),
    ),
    onMutate: () => setFeedback(null),
    onSuccess: () => {
      setFeedback({ kind: 'success', message: 'Report schedule deleted.' })
      void queryClient.invalidateQueries({ queryKey: ['reports', 'schedules'] })
    },
    onError: (error) => setActionError(
      setFeedback,
      error,
      'The report schedule could not be deleted',
    ),
  })
  const runScheduleMutation = useMutation({
    mutationKey: ['reports', 'schedules', 'run'],
    mutationFn: (scheduleId: string) => coalesceRequest(
      scheduleRunRequestsRef.current,
      scheduleId,
      () => apiFetch<ReportQueueResponse[]>(`/reports/schedules/${scheduleId}/run`, { method: 'POST' }),
    ),
    onMutate: () => setFeedback(null),
    onSuccess: (results) => {
      void Promise.all([
        queryClient.refetchQueries({ queryKey: ['reports', 'library'] }),
        queryClient.refetchQueries({ queryKey: ['reports', 'schedules'] }),
      ])
      setFeedback(results.length > 0
        ? {
            kind: 'success',
            message: results.length === 1
              ? 'Scheduled report run queued.'
              : `${results.length.toLocaleString()} scheduled report runs queued.`,
          }
        : {
            kind: 'info',
            message: 'No new report was queued. The schedule and report library are being refreshed because this period may already have a report.',
          })
    },
    onError: (error) => setActionError(
      setFeedback,
      error,
      'The scheduled report could not be queued',
    ),
  })
  const downloadMutation = useMutation({
    mutationFn: async ({
      reportId,
      format,
    }: {
      reportId: string
      format: ReportDownloadFormat
    }) => {
      if (!mountedRef.current || selectedReportIdRef.current !== reportId) {
        throw new ReportDownloadCanceledError()
      }
      activeDownloadRef.current?.controller.abort()
      const controller = new AbortController()
      const activeDownload = { reportId, controller }
      activeDownloadRef.current = activeDownload
      try {
        const result = await apiDownload(
          `/reports/${reportId}/download?format=${format}`,
          { timeoutMs: 60_000, signal: controller.signal },
        )
        if (
          controller.signal.aborted
          || !mountedRef.current
          || selectedReportIdRef.current !== reportId
        ) {
          throw new ReportDownloadCanceledError()
        }
        const extension = format === 'markdown' ? 'md' : format
        const filename = result.filename ?? `threatlens-report-${reportId}.${extension}`
        triggerBrowserDownload(result.blob, filename)
        return { filename, reportId }
      } catch (error) {
        if (controller.signal.aborted) throw new ReportDownloadCanceledError()
        throw error
      } finally {
        if (activeDownloadRef.current === activeDownload) {
          activeDownloadRef.current = null
        }
      }
    },
    onMutate: () => {
      if (mountedRef.current) setFeedback(null)
    },
    onSuccess: ({ filename, reportId }) => {
      if (mountedRef.current && selectedReportIdRef.current === reportId) {
        setFeedback({
          kind: 'success',
          message: `Report downloaded: ${filename}`,
        })
      }
    },
    onError: (error, { reportId }) => {
      if (
        error instanceof ReportDownloadCanceledError
        || !mountedRef.current
        || selectedReportIdRef.current !== reportId
      ) {
        return
      }
      setActionError(
        setFeedback,
        error,
        'The report download could not be prepared',
      )
    },
  })

  const selectedTemplate = templatesQuery.data?.find((entry) => entry.id === selectedTemplateId)
  const previewIsCurrent = previewPayload === debouncedPreviewPayload
  const previewErrorBlocksCreate = previewQuery.isError && reportPreviewErrorBlocksCreation(previewQuery.error)
  const previewErrorMessage = previewQuery.isError
    ? resolveApiErrorMessage(
        previewQuery.error,
        'The context estimate could not be calculated',
        { retryGuidance: previewErrorBlocksCreate ? undefined : 'You can retry the estimate or let the server validate the report when it is generated.' },
      )
    : null
  const createBlockedReason = resolveReportCreateBlockedReason({
    canAuthor,
    reportingEnabled: Boolean(capabilitiesQuery.data?.reporting_enabled),
    aiConfigured: Boolean(capabilitiesQuery.data?.ai_configured),
    validationError: validation.errors[0],
    previewIsCurrent,
    previewIsFetching: previewQuery.isFetching,
    previewError: previewQuery.error,
    selectedSourceCount: previewQuery.data?.estimate.selected_source_count,
  })

  function openReport(reportId: string) {
    navigate(`/reporting/${reportId}`)
  }

  function closeReport() {
    navigate('/reporting')
  }

  return {
    activeTab,
    setActiveTab,
    currentUser,
    isAdmin,
    canAuthor,
    capabilitiesQuery,
    templatesQuery,
    reportsQuery,
    schedulesQuery,
    reportDetailQuery,
    previewQuery,
    previewErrorBlocksCreate,
    previewErrorMessage,
    selectedTemplate,
    selectedTemplateId,
    setSelectedTemplateId,
    filterDraft,
    setFilterDraft,
    prompt,
    setPrompt,
    sections,
    setSections,
    excludedItemIds,
    setExcludedItemIds,
    title,
    setTitle,
    deliverWhenReady,
    setDeliverWhenReady,
    deliveryMode,
    setDeliveryMode,
    validationErrors: validation.errors,
    createBlockedReason,
    feedback,
    createReportMutation,
    retryMutation,
    deleteMutation,
    templateMutation,
    cloneTemplateMutation,
    deleteTemplateMutation,
    createScheduleMutation,
    updateScheduleMutation,
    deleteScheduleMutation,
    runScheduleMutation,
    downloadMutation,
    detailActionPending:
      retryMutation.isPending
      || deleteMutation.isPending
      || downloadMutation.isPending,
    openReport,
    closeReport,
  }
}

class ReportDownloadCanceledError extends Error {
  constructor() {
    super('The report download was canceled because the report view changed.')
    this.name = 'ReportDownloadCanceledError'
  }
}

export type ReportingController = ReturnType<typeof useReportingController>

function schedulePayload(schedule: ReportSchedule) {
  return {
    template_id: schedule.template_id,
    name: schedule.name,
    enabled: schedule.enabled,
    cadence: schedule.cadence,
    day_of_week: schedule.day_of_week,
    day_of_month: schedule.day_of_month,
    hour: schedule.hour,
    minute: schedule.minute,
    timezone: schedule.timezone,
    window_type: schedule.window_type,
    rolling_days: schedule.rolling_days,
    filters: schedule.filters,
    custom_instructions: schedule.custom_instructions,
    delivery_enabled: schedule.delivery_enabled,
    delivery_mode: schedule.delivery_mode,
    skip_empty: schedule.skip_empty,
    missed_run_policy: schedule.missed_run_policy,
  }
}

function createIdempotencyKey(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return globalThis.crypto.randomUUID()
  }
  if (typeof globalThis.crypto?.getRandomValues === 'function') {
    const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16))
    return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function isAmbiguousQueueError(error: unknown): boolean {
  return error instanceof ApiTransportError
    || (error instanceof ApiError && error.status >= 500)
}

function resolveReportQueueError(error: unknown): string {
  return resolveApiErrorMessage(
    error,
    'The report could not be queued',
    isAmbiguousQueueError(error)
      ? {
          retryGuidance:
            'Retry safely with the same request, or check the report library because the server may already have accepted it.',
        }
      : {},
  )
}

function setActionError(
  setter: (feedback: ReportingFeedback) => void,
  error: unknown,
  fallback: string,
) {
  setter({
    kind: 'error',
    message: resolveApiErrorMessage(error, fallback),
  })
}

function coalesceRequest<Key, Result>(
  requests: Map<Key, Promise<Result>>,
  key: Key,
  createRequest: () => Promise<Result>,
): Promise<Result> {
  const activeRequest = requests.get(key)
  if (activeRequest) return activeRequest

  const request = createRequest()
  requests.set(key, request)
  const clear = () => {
    if (requests.get(key) === request) requests.delete(key)
  }
  void request.then(clear, clear)
  return request
}
