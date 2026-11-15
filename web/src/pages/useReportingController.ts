import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate, useParams } from 'react-router-dom'

import { ApiError, apiDownload, apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { useDebouncedValue } from '../hooks/useDebouncedValue'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useQueryAccessScope } from '../hooks/useQueryAccessScope'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
import type {
  ReportDetail,
  ReportPreview,
  ReportSchedule,
  ReportScheduleWrite,
  ReportTemplate,
} from '../types/api'
import { useReportLibrary } from './useReportLibrary'
import { useReportMetadataAccess } from './useReportMetadataAccess'
import { useReportBuilderDraft } from './useReportBuilderDraft'
import { triggerBrowserDownload } from './exportPageModel'
import {
  reportPeriodFromFilters,
  validateReportBuilder,
} from './reportingPageModel'
import {
  REPORT_CREATE_TIMEOUT_MS,
  REPORT_PREVIEW_TIMEOUT_MS,
  isAmbiguousReportingMutationError,
  reportResourceVersionHeader,
  requireReportQueueResponse,
  requireReportQueueResponseList,
  requireClonedReportingResource,
  requireReportingResource,
  reportQueueFeedback,
  reportPreviewErrorBlocksCreation,
  resolveReportCreateBlockedReason,
  shouldRetryReportPreview,
} from './reportingResilience'
import { idempotentReportingFetch } from './reportingApi'
import {
  coalesceReportingRequest,
  reportMutationRequestKey,
  reportingRequestScope,
  serializeReportingWrite,
} from './reportingRequestCoordinator'

export type ReportingTab = 'reports' | 'templates' | 'schedules'
export type ReportingFeedback = {
  kind: 'error' | 'info' | 'success'
  message: string
} | null

type ReportDownloadFormat = 'markdown' | 'html' | 'pdf'

function reportPreviewNeedsVerification(generation: number, denied: boolean, preview: ReportPreview | undefined) {
  return denied || generation > 0 && preview === undefined
}

export function useReportingController() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const location = useLocation()
  const viewKeyRef = useRef(location.key)
  viewKeyRef.current = location.key
  const { reportId: routeReportId } = useParams<{ reportId?: string }>()
  const currentUser = useCurrentUser()
  const isAdmin = !currentUser.isError && currentUser.data?.role === 'admin'
  const canAuthor = !currentUser.isError && hasRequiredPermissions(
    currentUser.data?.access?.permissions ?? [],
    ['write:reports'],
  )
  const requestOwnerId = currentUser.data?.id
  const [activeTab, setActiveTab] = useState<ReportingTab>('reports')
  const [feedback, setFeedback] = useState<ReportingFeedback>(null)
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

  const metadata = useReportMetadataAccess(isAdmin, canAuthor)
  const { capabilitiesQuery, templatesQuery, schedulesQuery } = metadata
  const [editorialDirty, setEditorialDirty] = useState(false)
  const builderDraft = useReportBuilderDraft(templatesQuery.data, editorialDirty, metadata.templateGeneration)
  const { selectedTemplateId, setSelectedTemplateId, selectedTemplate, filterDraft, setFilterDraft, prompt,
    setPrompt, sections, setSections, excludedItemIds, setExcludedItemIds, title, setTitle,
    deliverWhenReady, setDeliverWhenReady, deliveryMode, setDeliveryMode } = builderDraft
  const [queuedReportToOpen, setQueuedReportToOpen] = useState<string | null>(null)
  useEffect(() => {
    if (!queuedReportToOpen || builderDraft.dirty) return
    setQueuedReportToOpen(null)
    navigate(`/reporting/${queuedReportToOpen}`)
  }, [queuedReportToOpen, builderDraft.dirty, navigate])
  const reportLibrary = useReportLibrary()
  const reportsQuery = reportLibrary.query
  const detailAccess = useQueryAccessScope(['reports', 'detail', routeReportId ?? ''])
  const reportDetailKey = ['reports', 'detail', routeReportId]
  const reportDetailQuery = useQuery({
    queryKey: reportDetailKey,
    ...detailAccess.queryOptions(({ signal }) => apiFetch<ReportDetail>(`/reports/${routeReportId}`, { signal })),
    enabled: Boolean(routeReportId),
    refetchInterval: (query) => {
      const status = detailAccess.currentData(query.state.data)?.status
      return status === 'queued' || status === 'running' ? 3000 : false
    },
  })
  function currentDetail(reportId: string) {
    if (routeReportId !== reportId || currentUser.isError || queryClient.getQueryState(reportDetailKey)?.status === 'error') return undefined
    const report = detailAccess.currentData<ReportDetail>(queryClient.getQueryData(reportDetailKey))
    return report?.id === reportId ? report : undefined
  }
  function assertDetailAction(reportId: string, write: boolean) {
    const report = currentDetail(reportId)
    if (!report || write && (!canAuthor || !isAdmin && report.owner_user_id !== currentUser.data?.id)) {
      throw new Error('Refresh this report and verify access before continuing.')
    }
    return detailAccess.captureResult()(true)
  }

  const validation = useMemo(
    () => validateReportBuilder(filterDraft, prompt, sections),
    [filterDraft, prompt, sections],
  )
  const previewPayload = useMemo(
    () => validation.filters ? { filters: validation.filters, excluded_item_ids: excludedItemIds, prompt, sections } : null,
    [excludedItemIds, prompt, sections, validation.filters],
  )
  const debouncedPreviewPayload = useDebouncedValue(previewPayload, 500)
  const previewAccess = useQueryAccessScope(['reports', 'preview'])
  const previewKey = ['reports', 'preview', debouncedPreviewPayload]
  const previewQuery = useQuery({
    queryKey: previewKey,
    ...previewAccess.queryOptions(({ signal }) => apiFetch<ReportPreview>('/reports/preview', {
      method: 'POST',
      body: JSON.stringify(debouncedPreviewPayload),
      signal,
      timeoutMs: REPORT_PREVIEW_TIMEOUT_MS,
    })),
    enabled: Boolean(
      debouncedPreviewPayload &&
      capabilitiesQuery.data?.reporting_enabled &&
      capabilitiesQuery.data?.ai_configured &&
      canAuthor,
    ),
    placeholderData: (previous) => previewAccess.currentData(previous) === undefined ? undefined : previous,
    retry: shouldRetryReportPreview,
  })
  const previewAdmission = previewAccess.captureResult()(true)
  const previewAccessUnverified = reportPreviewNeedsVerification(previewAccess.generation, previewAccess.denied, previewQuery.data)
  function previewAllowsCreation() {
    const current = previewAccess.currentData<ReportPreview>(queryClient.getQueryData(previewKey))
    return Boolean(previewAccess.currentData(previewAdmission)) && (previewAccess.generation === 0 || Boolean(current))
  }

  const createReportMutation = useMutation({
    mutationFn: async () => {
      if (!canAuthor || !metadata.currentCapabilities() || !previewAllowsCreation() || createBlockedReason) throw new Error('Verify the current report estimate and access before generating.')
      if (selectedTemplate && !metadata.templateActionCurrent(builderDraft.loadedTemplateGeneration)) throw new Error('Reload current template access before generating from this saved baseline.')
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
      const path = '/reports'
      const requestScope = reportingRequestScope(requestOwnerId, 'report:create', body)
      return coalesceReportingRequest(
        requestScope,
        () => idempotentReportingFetch(
          path,
          requestScope,
          {
            method: 'POST',
            body,
            timeoutMs: REPORT_CREATE_TIMEOUT_MS,
          },
          (value) => requireReportQueueResponse(value, path),
        ),
      )
    },
    onMutate: () => { setFeedback(null); return builderDraft.fingerprint },
    onSuccess: (result, _variables, submittedFingerprint) => {
      setFeedback(reportQueueFeedback('create', result.status))
      void queryClient.invalidateQueries({ queryKey: ['reports', 'library'] })
      if (builderDraft.acceptSubmission(submittedFingerprint)) setQueuedReportToOpen(result.report_id)
    },
    onError: (error) => {
      setFeedback({
        kind: 'error',
        message: resolveReportQueueError(error),
      })
    },
  })

  const retryMutation = useMutation({
    mutationFn: async (reportId: string) => {
      const permission = assertDetailAction(reportId, true)
      const path = `/reports/${reportId}/retry`
      const requestScope = reportingRequestScope(requestOwnerId, 'report:retry', reportId)
      const result = await coalesceReportingRequest(
        requestScope,
        () => idempotentReportingFetch(
          path,
          requestScope,
          { method: 'POST' },
          (value) => requireReportQueueResponse(value, path, reportId),
        ),
      )
      return { result, permission }
    },
    onMutate: () => { setFeedback(null); return viewKeyRef.current },
    onSuccess: ({ result, permission }, reportId, viewKey) => {
      void queryClient.invalidateQueries({ queryKey: ['reports'] })
      if (!mountedRef.current || viewKeyRef.current !== viewKey || selectedReportIdRef.current !== reportId || !detailAccess.currentData(permission)) return
      setFeedback(reportQueueFeedback('retry', result.status))
      navigate(`/reporting/${result.report_id}`)
    },
    onError: (error, reportId, viewKey) => {
      if (!mountedRef.current || viewKeyRef.current !== viewKey || selectedReportIdRef.current !== reportId) return
      setFeedback({ kind: 'error', message: resolveReportQueueError(error) })
    },
  })
  const deleteMutation = useMutation({
    mutationFn: async (reportId: string) => {
      const permission = assertDetailAction(reportId, true)
      await coalesceReportingRequest(
        reportingRequestScope(requestOwnerId, 'report:delete', reportId),
        () => apiFetch<void>(`/reports/${reportId}`, { method: 'DELETE' }),
      )
      return permission
    },
    onMutate: () => { setFeedback(null); return viewKeyRef.current },
    onSuccess: (permission, reportId, viewKey) => {
      void queryClient.invalidateQueries({ queryKey: ['reports', 'library'] })
      if (!mountedRef.current || viewKeyRef.current !== viewKey || selectedReportIdRef.current !== reportId || !detailAccess.currentData(permission)) return
      setFeedback({ kind: 'success', message: 'Report deleted.' })
      navigate('/reporting')
    },
    onError: (error, reportId, viewKey) => {
      if (!mountedRef.current || viewKeyRef.current !== viewKey || selectedReportIdRef.current !== reportId) return
      setActionError(setFeedback, error, 'The report could not be deleted')
    },
  })
  const templateMutation = useMutation({
    mutationKey: ['reports', 'templates', 'save'],
    mutationFn: (payload: { mode: 'create' | 'update'; name: string; visibility: 'private' | 'shared' }) => {
      metadata.assertTemplate(selectedTemplate?.id)
      if (selectedTemplate && !metadata.templateActionCurrent(builderDraft.loadedTemplateGeneration)) throw new Error('Load the current template baseline before saving this draft.')
      if (!validation.filters) throw new Error('Report filters are invalid.')
      const selected = selectedTemplate
      const updateTarget = payload.mode === 'update' ? selected : undefined
      if (payload.mode === 'update' && !updateTarget) {
        throw new Error('The selected report template is no longer available. Refresh the template list and try again.')
      }
      const path = updateTarget ? `/reports/templates/${updateTarget.id}` : '/reports/templates'
      const entityKey = updateTarget?.id ?? 'create'
      const body = JSON.stringify({
        name: payload.name,
        description: selected?.description ?? 'Custom intelligence report template.',
        report_type: selected?.report_type ?? 'custom',
        visibility: payload.visibility,
        prompt,
        sections,
        default_filters: validation.filters,
      })
      const requestKey = reportMutationRequestKey(entityKey, body)
      if (updateTarget) {
        const writeScope = reportingRequestScope(
          requestOwnerId,
          'report:template:update',
          entityKey,
        )
        return serializeReportingWrite(
          writeScope,
          requestKey,
          async () => requireReportingResource<ReportTemplate>(
            await apiFetch<unknown>(path, {
              method: 'PUT',
              body,
              headers: {
                'If-Match': reportResourceVersionHeader(
                  updateTarget.resource_version ?? updateTarget.updated_at,
                ),
              },
            }),
            path,
            'report template update',
            200,
            entityKey,
          ),
        )
      }
      const createScope = reportingRequestScope(
        requestOwnerId,
        'report:template:create',
        body,
      )
      return coalesceReportingRequest(
        createScope,
        () => idempotentReportingFetch(
          path,
          createScope,
          { method: 'POST', body },
          (value) => requireReportingResource<ReportTemplate>(
            value,
            path,
            'report template creation',
            201,
          ),
        ),
      )
    },
    onMutate: () => { setFeedback(null); return { id: selectedTemplateId, generation: metadata.captureTemplateGeneration() } },
    onSuccess: (template, _payload, submitted) => {
      if (!submitted || !metadata.storeTemplate(template, submitted.generation)) { void queryClient.invalidateQueries({ queryKey: ['reports', 'templates'] }); return }
      builderDraft.adoptSavedTemplate(template, submitted.id, submitted.generation)
      setFeedback({ kind: 'success', message: 'Report template saved.' })
      void queryClient.invalidateQueries({ queryKey: ['reports', 'templates'] })
    },
    onError: async (error, payload) => {
      if (payload.mode === 'create') {
        setIdempotentActionError(
          setFeedback,
          error,
          'The report template could not be saved',
        )
        return
      }
      setActionError(
        setFeedback,
        error,
        'The report template could not be saved',
      )
      await queryClient.resetQueries({
        queryKey: ['reports', 'templates'],
        exact: true,
      })
    },
  })
  const cloneTemplateMutation = useMutation({
    mutationKey: ['reports', 'templates', 'clone'],
    mutationFn: (templateId: string) => {
      metadata.assertTemplate(templateId)
      const path = `/reports/templates/${templateId}/clone`
      const requestScope = reportingRequestScope(
        requestOwnerId,
        'report:template:clone',
        templateId,
      )
      return coalesceReportingRequest(
        requestScope,
        () => idempotentReportingFetch(
          path,
          requestScope,
          { method: 'POST' },
          (value) => requireClonedReportingResource<ReportTemplate>(
            value,
            path,
            'report template clone',
            templateId,
          ),
        ),
      )
    },
    onMutate: () => { setFeedback(null); return metadata.captureTemplateGeneration() },
    onSuccess: (template, _id, generation) => {
      if (generation === undefined || !metadata.templateActionCurrent(generation)) { void queryClient.invalidateQueries({ queryKey: ['reports', 'templates'] }); return }
      builderDraft.selectClonedTemplate(template, generation)
      setActiveTab('reports')
      setFeedback({
        kind: 'success',
        message: 'Template cloned. Adjust it in the builder and save when ready.',
      })
      void queryClient.invalidateQueries({ queryKey: ['reports', 'templates'] })
    },
    onError: (error) => setIdempotentActionError(
      setFeedback,
      error,
      'The report template could not be cloned',
    ),
  })
  const deleteTemplateMutation = useMutation({
    mutationKey: ['reports', 'templates', 'delete'],
    mutationFn: (templateId: string) => {
      const template = metadata.assertTemplate(templateId)
      if (!template) {
        throw new Error('The report template is no longer available. Refresh the template list and try again.')
      }
      return coalesceReportingRequest(
        reportingRequestScope(requestOwnerId, 'report:template:delete', templateId),
        () => apiFetch<void>(`/reports/templates/${templateId}`, {
          method: 'DELETE',
          headers: {
            'If-Match': reportResourceVersionHeader(
              template.resource_version ?? template.updated_at,
            ),
          },
        }),
      )
    },
    onMutate: () => { setFeedback(null); return metadata.captureTemplateGeneration() },
    onSuccess: (_result, _id, generation) => {
      if (generation === undefined || !metadata.templateActionCurrent(generation)) { void queryClient.invalidateQueries({ queryKey: ['reports', 'templates'] }); return }
      setFeedback({ kind: 'success', message: 'Report template deleted.' })
      void queryClient.invalidateQueries({ queryKey: ['reports', 'templates'] })
    },
    onError: async (error) => {
      setActionError(
        setFeedback,
        error,
        'The report template could not be deleted',
      )
      if (isResourceVersionConflict(error)) {
        await queryClient.resetQueries({
          queryKey: ['reports', 'templates'],
          exact: true,
        })
      }
    },
  })
  const createScheduleMutation = useMutation({
    mutationKey: ['reports', 'schedules', 'create'],
    mutationFn: (payload: ReportScheduleWrite) => {
      metadata.assertSchedule()
      metadata.assertTemplate(payload.template_id)
      const body = JSON.stringify(payload)
      const path = '/reports/schedules'
      const requestScope = reportingRequestScope(
        requestOwnerId,
        'report:schedule:create',
        body,
      )
      return coalesceReportingRequest(
        requestScope,
        () => idempotentReportingFetch(
          path,
          requestScope,
          { method: 'POST', body },
          (value) => requireReportingResource<ReportSchedule>(
            value,
            path,
            'report schedule creation',
            201,
          ),
        ),
      )
    },
    onMutate: () => { setFeedback(null); return metadata.captureScheduleGeneration() },
    onSuccess: (_result, _payload, generation) => {
      if (generation === undefined || !metadata.scheduleActionCurrent(generation)) { void queryClient.invalidateQueries({ queryKey: ['reports', 'schedules'] }); return }
      setFeedback({ kind: 'success', message: 'Report schedule created.' })
      void queryClient.invalidateQueries({ queryKey: ['reports', 'schedules'] })
    },
    onError: (error) => setIdempotentActionError(
      setFeedback,
      error,
      'The report schedule could not be created',
    ),
  })
  const updateScheduleMutation = useMutation({
    mutationKey: ['reports', 'schedules', 'update'],
    mutationFn: (schedule: ReportSchedule & { readGeneration?: number }) => {
      metadata.assertSchedule(schedule.id)
      const generation = schedule.readGeneration ?? metadata.captureScheduleGeneration()
      if (!metadata.scheduleActionCurrent(generation)) throw new Error('Reload this schedule baseline before saving its draft.')
      const body = JSON.stringify(schedulePayload(schedule))
      const path = `/reports/schedules/${schedule.id}`
      const writeScope = reportingRequestScope(
        requestOwnerId,
        'report:schedule:update',
        schedule.id,
      )
      return serializeReportingWrite(
        writeScope,
        reportMutationRequestKey(schedule.id, body),
        async () => {
          if (!metadata.scheduleActionCurrent(generation)) throw new Error('Reload this schedule baseline before saving its draft.')
          return requireReportingResource<ReportSchedule>(
            await apiFetch<unknown>(path, {
              method: 'PUT',
              body,
              headers: {
                'If-Match': reportResourceVersionHeader(
                  schedule.resource_version ?? schedule.updated_at,
                ),
              },
            }),
            path,
            'report schedule update',
            200,
            schedule.id,
          )
        },
      )
    },
    onMutate: () => { setFeedback(null); return metadata.captureScheduleGeneration() },
    onSuccess: (schedule, _payload, generation) => {
      if (generation === undefined || !metadata.storeSchedule(schedule, generation)) { void queryClient.invalidateQueries({ queryKey: ['reports', 'schedules'] }); return }
      setFeedback({ kind: 'success', message: 'Report schedule updated.' })
      void queryClient.invalidateQueries({ queryKey: ['reports', 'schedules'] })
    },
    onError: async (error) => {
      setActionError(
        setFeedback,
        error,
        'The report schedule could not be updated',
      )
      // Retain mounted editors and their original preconditions while refreshing.
      await queryClient.invalidateQueries({
        queryKey: ['reports', 'schedules'],
        exact: true,
      })
    },
  })
  const deleteScheduleMutation = useMutation({
    mutationKey: ['reports', 'schedules', 'delete'],
    mutationFn: (scheduleId: string) => {
      const schedule = metadata.assertSchedule(scheduleId)
      if (!schedule) {
        throw new Error('The report schedule is no longer available. Refresh the schedule list and try again.')
      }
      return coalesceReportingRequest(
        reportingRequestScope(requestOwnerId, 'report:schedule:delete', scheduleId),
        () => apiFetch<void>(`/reports/schedules/${scheduleId}`, {
          method: 'DELETE',
          headers: {
            'If-Match': reportResourceVersionHeader(
              schedule.resource_version ?? schedule.updated_at,
            ),
          },
        }),
      )
    },
    onMutate: () => { setFeedback(null); return metadata.captureScheduleGeneration() },
    onSuccess: (_result, _id, generation) => {
      if (generation === undefined || !metadata.scheduleActionCurrent(generation)) { void queryClient.invalidateQueries({ queryKey: ['reports', 'schedules'] }); return }
      setFeedback({ kind: 'success', message: 'Report schedule deleted.' })
      void queryClient.invalidateQueries({ queryKey: ['reports', 'schedules'] })
    },
    onError: async (error) => {
      setActionError(
        setFeedback,
        error,
        'The report schedule could not be deleted',
      )
      if (isResourceVersionConflict(error)) {
        await queryClient.invalidateQueries({
          queryKey: ['reports', 'schedules'],
          exact: true,
        })
      }
    },
  })
  const runScheduleMutation = useMutation({
    mutationKey: ['reports', 'schedules', 'run'],
    mutationFn: (scheduleId: string) => {
      const schedule = metadata.assertSchedule(scheduleId)
      if (!schedule) {
        throw new Error('The report schedule is no longer available. Refresh the schedule list and try again.')
      }
      const path = `/reports/schedules/${scheduleId}/run`
      const requestScope = reportingRequestScope(
        requestOwnerId,
        'report:schedule:run',
        scheduleId,
      )
      return coalesceReportingRequest(
        requestScope,
        () => idempotentReportingFetch(
          path,
          requestScope,
          {
            method: 'POST',
            headers: {
              'If-Match': reportResourceVersionHeader(
                schedule.resource_version ?? schedule.updated_at,
              ),
            },
          },
          (value) => requireReportQueueResponseList(value, path, scheduleId),
        ),
      )
    },
    onMutate: () => { setFeedback(null); return metadata.captureScheduleGeneration() },
    onSuccess: async (results, _id, generation) => {
      if (generation === undefined || !metadata.scheduleActionCurrent(generation)) { void queryClient.invalidateQueries({ queryKey: ['reports', 'schedules'] }); return }
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
      await Promise.all([
        queryClient.refetchQueries({ queryKey: ['reports', 'library'] }),
        queryClient.refetchQueries({ queryKey: ['reports', 'schedules'] }),
      ])
    },
    onError: async (error) => {
      setFeedback({ kind: 'error', message: resolveReportQueueError(error) })
      if (isResourceVersionConflict(error)) {
        await queryClient.invalidateQueries({
          queryKey: ['reports', 'schedules'],
          exact: true,
        })
      }
    },
  })
  const downloadMutation = useMutation({
    mutationFn: async ({
      reportId,
      format,
    }: {
      reportId: string
      format: ReportDownloadFormat
    }) => {
      const permission = assertDetailAction(reportId, false)
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
          || !detailAccess.currentData(permission)
          || !currentDetail(reportId)
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

  const previewIsCurrent = previewPayload === debouncedPreviewPayload
  const previewErrorBlocksCreate = previewAccessUnverified || previewQuery.isError && reportPreviewErrorBlocksCreation(previewQuery.error)
  const previewErrorMessage = previewQuery.isError
    ? resolveApiErrorMessage(
        previewQuery.error,
        'The context estimate could not be calculated',
        { retryGuidance: previewErrorBlocksCreate ? undefined : 'You can retry the estimate or let the server validate the report when it is generated.' },
      )
    : null
  const createBlockedReason = capabilitiesQuery.isError ? 'Refresh reporting capabilities before generating this report.'
    : builderDraft.templateBaselineRetired ? 'Load the current template baseline before generating this report.'
      : builderDraft.templateUnavailable ? 'Select an available template before generating this report.'
    : previewAccessUnverified ? 'Refresh the report estimate and verify access before generating.' : resolveReportCreateBlockedReason({
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
    metadata,
    activeTab,
    setActiveTab,
    builderDraft,
    setEditorialDirty,
    currentUser,
    isAdmin,
    canAuthor,
    capabilitiesQuery,
    templatesQuery,
    reportsQuery,
    reportLibrary,
    schedulesQuery,
    reportDetailQuery,
    detailSelected: Boolean(routeReportId),
    detailReadAvailable: Boolean(routeReportId && currentDetail(routeReportId)),
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

function schedulePayload(schedule: ReportSchedule): ReportScheduleWrite {
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
    review_required: schedule.review_required,
    delivery_enabled: schedule.delivery_enabled,
    delivery_mode: schedule.delivery_mode,
    skip_empty: schedule.skip_empty,
    missed_run_policy: schedule.missed_run_policy,
  }
}

function resolveReportQueueError(error: unknown): string {
  return resolveApiErrorMessage(
    error,
    'The report could not be queued',
    isAmbiguousReportingMutationError(error)
      ? {
          retryGuidance:
            'Retry safely with the same request, or check the report library because the server may already have accepted it.',
        }
      : {},
  )
}

function setIdempotentActionError(
  setter: (feedback: ReportingFeedback) => void,
  error: unknown,
  fallback: string,
) {
  setter({
    kind: 'error',
    message: resolveApiErrorMessage(
      error,
      fallback,
      isAmbiguousReportingMutationError(error)
        ? {
            retryGuidance:
              'Retry safely with the same request. ThreatLens will return the original result if the server already completed it.',
          }
        : {},
    ),
  })
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

function isResourceVersionConflict(error: unknown): boolean {
  return error instanceof ApiError && error.status === 412
}
