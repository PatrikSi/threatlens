import { useEffect, useMemo, useState } from 'react'
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
  reportPreviewErrorBlocksCreation,
  resolveReportCreateBlockedReason,
  shouldRetryReportPreview,
} from './reportingResilience'

export type ReportingTab = 'reports' | 'templates' | 'schedules'

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
  const [notice, setNotice] = useState<string | null>(null)

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
      return apiFetch<ReportQueueResponse>('/reports', {
        method: 'POST',
        body: JSON.stringify({
          template_id: selectedTemplateId || null,
          title: title.trim() || null,
          ...reportPeriodFromFilters(validation.filters),
          filters: validation.filters,
          excluded_item_ids: excludedItemIds,
          prompt,
          sections,
          deliver_when_ready: deliverWhenReady,
          delivery_mode: deliveryMode,
        }),
        timeoutMs: REPORT_CREATE_TIMEOUT_MS,
      })
    },
    onSuccess: (result) => {
      setNotice('Report queued. Progress and provider history are now available.')
      void queryClient.invalidateQueries({ queryKey: ['reports', 'library'] })
      navigate(`/reporting/${result.report_id}`)
    },
  })

  const retryMutation = useMutation({
    mutationFn: (reportId: string) => apiFetch<ReportQueueResponse>(`/reports/${reportId}/retry`, { method: 'POST' }),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['reports'] })
      navigate(`/reporting/${result.report_id}`)
    },
  })
  const deleteMutation = useMutation({
    mutationFn: (reportId: string) => apiFetch<void>(`/reports/${reportId}`, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['reports', 'library'] })
      navigate('/reporting')
    },
  })
  const templateMutation = useMutation({
    mutationFn: (payload: { mode: 'create' | 'update'; name: string; visibility: 'private' | 'shared' }) => {
      if (!validation.filters) throw new Error('Report filters are invalid.')
      const selected = templatesQuery.data?.find((template) => template.id === selectedTemplateId)
      const path = payload.mode === 'update' && selected ? `/reports/templates/${selected.id}` : '/reports/templates'
      return apiFetch<ReportTemplate>(path, {
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
    },
    onSuccess: (template) => {
      setSelectedTemplateId(template.id)
      setNotice('Report template saved.')
      void queryClient.invalidateQueries({ queryKey: ['reports', 'templates'] })
    },
  })
  const cloneTemplateMutation = useMutation({
    mutationFn: (templateId: string) => apiFetch<ReportTemplate>(`/reports/templates/${templateId}/clone`, { method: 'POST' }),
    onSuccess: (template) => {
      setSelectedTemplateId(template.id)
      setActiveTab('reports')
      setNotice('Template cloned. Adjust it in the builder and save when ready.')
      void queryClient.invalidateQueries({ queryKey: ['reports', 'templates'] })
    },
  })
  const deleteTemplateMutation = useMutation({
    mutationFn: (templateId: string) => apiFetch<void>(`/reports/templates/${templateId}`, { method: 'DELETE' }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['reports', 'templates'] }),
  })
  const createScheduleMutation = useMutation({
    mutationFn: (payload: Omit<ReportSchedule, 'id' | 'owner_user_id' | 'next_run_at' | 'last_run_at' | 'created_at' | 'updated_at'>) =>
      apiFetch<ReportSchedule>('/reports/schedules', { method: 'POST', body: JSON.stringify(payload) }),
    onSuccess: () => {
      setNotice('Report schedule created.')
      void queryClient.invalidateQueries({ queryKey: ['reports', 'schedules'] })
    },
  })
  const updateScheduleMutation = useMutation({
    mutationFn: (schedule: ReportSchedule) => apiFetch<ReportSchedule>(`/reports/schedules/${schedule.id}`, {
      method: 'PUT',
      body: JSON.stringify(schedulePayload(schedule)),
    }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['reports', 'schedules'] }),
  })
  const deleteScheduleMutation = useMutation({
    mutationFn: (scheduleId: string) => apiFetch<void>(`/reports/schedules/${scheduleId}`, { method: 'DELETE' }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['reports', 'schedules'] }),
  })
  const runScheduleMutation = useMutation({
    mutationFn: (scheduleId: string) => apiFetch<ReportQueueResponse[]>(`/reports/schedules/${scheduleId}/run`, { method: 'POST' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['reports'] })
      setNotice('Scheduled report run queued.')
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

  async function downloadReport(reportId: string, format: 'markdown' | 'html' | 'pdf') {
    const result = await apiDownload(`/reports/${reportId}/download?format=${format}`, { timeoutMs: 60_000 })
    triggerBrowserDownload(result.blob, result.filename ?? `threatlens-report-${reportId}.${format === 'markdown' ? 'md' : format}`)
  }

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
    notice,
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
    downloadReport,
    openReport,
    closeReport,
    actionError: resolveMutationError(createReportMutation, [
      retryMutation,
      deleteMutation,
      templateMutation,
      cloneTemplateMutation,
      deleteTemplateMutation,
      createScheduleMutation,
      updateScheduleMutation,
      deleteScheduleMutation,
      runScheduleMutation,
    ]),
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

function resolveMutationError(
  createMutation: { isError: boolean; error: unknown },
  mutations: Array<{ isError: boolean; error: unknown }>,
): string | null {
  if (createMutation.isError) {
    const ambiguous = createMutation.error instanceof ApiTransportError
      || (createMutation.error instanceof ApiError && createMutation.error.status >= 500)
    return resolveApiErrorMessage(
      createMutation.error,
      'The report could not be queued',
      ambiguous
        ? { retryGuidance: 'Check the report library before submitting again because the server may have accepted the request.' }
        : {},
    )
  }
  const failed = mutations.find((mutation) => mutation.isError)
  return failed ? resolveApiErrorMessage(failed.error, 'The reporting action could not be completed') : null
}
