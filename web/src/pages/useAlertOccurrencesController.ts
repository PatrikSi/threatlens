import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { useCurrentUser } from '../hooks/useCurrentUser'
import type {
  AlertBackfillApplyResponse,
  AlertBackfillPreviewResponse,
  AlertClosureDisposition,
  AlertInterest,
  AlertOccurrence,
  AlertOccurrenceActivityListResponse,
  AlertOccurrenceBulkResponse,
  AlertOccurrenceListResponse,
  AlertOccurrenceState,
  AlertSeverity,
} from '../types/alerts'
import {
  ALERT_OCCURRENCE_ACTIVITY_PAGE_SIZE,
  DEFAULT_ALERT_OCCURRENCE_FILTERS,
  alertBackfillDraftKey,
  alertBackfillRequest,
  alertOccurrencePageCount,
  alertOccurrencePageStats,
  buildAlertOccurrencesPath,
  canBulkAcknowledge,
  canBulkClose,
  createDefaultBackfillDraft,
  filterLoadedOccurrences,
  getAlertOccurrenceLifecycleActions,
  isAlertOccurrenceConflict,
  isAlertOccurrencePermissionError,
  validateAlertOccurrenceFilters,
  validateAlertBackfillDraft,
  type AlertBackfillDraft,
  type AlertBooleanFilter,
  type AlertOccurrenceFilters,
  type AlertOccurrenceLifecycleAction,
} from './alertOccurrenceModel'

const OCCURRENCE_REFRESH_MS = 30_000

type CloseTarget = {
  occurrences: AlertOccurrence[]
  bulk: boolean
  mode: 'close' | 'disposition'
}

type LifecycleMutationInput = {
  occurrence: AlertOccurrence
  state: AlertOccurrenceState
  disposition?: AlertClosureDisposition
}

type BulkLifecycleMutationInput = {
  occurrences: AlertOccurrence[]
  action: 'acknowledge' | 'close'
  disposition?: AlertClosureDisposition
}

type SnoozeMutationInput = {
  occurrence: AlertOccurrence
  snoozedUntil: string | null
  reason: string | null
}

export function useAlertOccurrencesController(active = true) {
  const queryClient = useQueryClient()
  const currentUserQuery = useCurrentUser()
  const [filters, setFilters] = useState<AlertOccurrenceFilters>(DEFAULT_ALERT_OCCURRENCE_FILTERS)
  const [page, setPageState] = useState(1)
  const [pageSize, setPageSizeState] = useState(25)
  const [loadedPageSearch, setLoadedPageSearchState] = useState('')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set())
  const [selectedOccurrenceId, setSelectedOccurrenceId] = useState<string | null>(null)
  const [activityPage, setActivityPage] = useState(1)
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionFeedback, setActionFeedback] = useState<string | null>(null)
  const [conflictNotice, setConflictNotice] = useState<string | null>(null)
  const [writeDenied, setWriteDenied] = useState(false)
  const [closeTarget, setCloseTarget] = useState<CloseTarget | null>(null)
  const [closeDisposition, setCloseDisposition] = useState<AlertClosureDisposition>('true_positive')
  const [snoozeTarget, setSnoozeTarget] = useState<AlertOccurrence | null>(null)
  const [snoozeUntil, setSnoozeUntil] = useState('')
  const [snoozeReason, setSnoozeReason] = useState('')
  const detailReturnTargetRef = useRef<HTMLButtonElement | null>(null)

  const rulesQuery = useQuery({
    queryKey: ['alerts', 'occurrences', 'rules'],
    queryFn: () => apiFetch<AlertInterest[]>('/alerts?include_disabled=true'),
    enabled: active,
    staleTime: 30_000,
  })
  const occurrencePath = useMemo(
    () => buildAlertOccurrencesPath(filters, page, pageSize),
    [filters, page, pageSize],
  )
  const filterValidationError = validateAlertOccurrenceFilters(filters)
  const occurrencesQuery = useQuery({
    queryKey: ['alerts', 'occurrences', 'list', occurrencePath],
    queryFn: () => apiFetch<AlertOccurrenceListResponse>(occurrencePath),
    placeholderData: (previous) => previous,
    enabled: active && !filterValidationError,
    refetchInterval: OCCURRENCE_REFRESH_MS,
    retry: retryOperationalQuery,
  })
  const detailQuery = useQuery({
    queryKey: ['alerts', 'occurrences', 'detail', selectedOccurrenceId],
    queryFn: () => apiFetch<AlertOccurrence>(`/alerts/occurrences/${selectedOccurrenceId}`),
    enabled: active && Boolean(selectedOccurrenceId),
    refetchInterval: OCCURRENCE_REFRESH_MS,
    retry: retryOperationalQuery,
  })
  const activityQuery = useQuery({
    queryKey: ['alerts', 'occurrences', 'activity', selectedOccurrenceId, activityPage],
    queryFn: () => {
      const params = new URLSearchParams({
        page: String(activityPage),
        page_size: String(ALERT_OCCURRENCE_ACTIVITY_PAGE_SIZE),
      })
      return apiFetch<AlertOccurrenceActivityListResponse>(
        `/alerts/occurrences/${selectedOccurrenceId}/activity?${params.toString()}`,
      )
    },
    enabled: active && Boolean(selectedOccurrenceId),
    retry: retryOperationalQuery,
  })

  const applyOccurrenceUpdates = (updates: AlertOccurrence[]) => {
    const byId = new Map(updates.map((occurrence) => [occurrence.id, occurrence]))
    queryClient.setQueriesData<AlertOccurrenceListResponse>(
      { queryKey: ['alerts', 'occurrences', 'list'] },
      (current) =>
        current
          ? { ...current, items: current.items.map((item) => byId.get(item.id) ?? item) }
          : current,
    )
    updates.forEach((occurrence) => {
      queryClient.setQueryData(['alerts', 'occurrences', 'detail', occurrence.id], occurrence)
    })
    void queryClient.invalidateQueries({ queryKey: ['alerts', 'occurrences', 'list'] })
    void queryClient.invalidateQueries({ queryKey: ['alerts', 'occurrences', 'activity'] })
  }

  const handleMutationFailure = (error: unknown, fallback: string) => {
    setActionFeedback(null)
    if (isAlertOccurrenceConflict(error)) {
      setConflictNotice(
        'This occurrence changed after it was loaded. The latest state has been requested; review it before retrying.',
      )
      setActionError(null)
      setCloseTarget(null)
      setSnoozeTarget(null)
      void queryClient.invalidateQueries({ queryKey: ['alerts', 'occurrences'] })
      return
    }
    if (isAlertOccurrencePermissionError(error)) setWriteDenied(true)
    setActionError(
      resolveApiErrorMessage(error, fallback, {
        retryGuidance: 'Refresh the occurrence and retry only after reviewing its current state.',
      }),
    )
  }

  const lifecycleMutation = useMutation({
    mutationKey: ['alerts', 'occurrences', 'lifecycle'],
    mutationFn: ({ occurrence, state, disposition }: LifecycleMutationInput) =>
      apiFetch<AlertOccurrence>(`/alerts/occurrences/${occurrence.id}/lifecycle`, {
        method: 'PATCH',
        body: JSON.stringify({
          expected_version: occurrence.version,
          state,
          ...(disposition ? { disposition } : {}),
        }),
      }),
    onSuccess: (occurrence, input) => {
      applyOccurrenceUpdates([occurrence])
      setActivityPage(1)
      setActionError(null)
      setConflictNotice(null)
      setCloseTarget(null)
      setActionFeedback(
        input.occurrence.lifecycle_state === 'closed' && input.state === 'closed'
          ? 'Closure disposition updated.'
          : `Occurrence moved to ${occurrence.lifecycle_state}.`,
      )
    },
    onError: (error) =>
      handleMutationFailure(error, 'The occurrence lifecycle could not be updated'),
  })
  const bulkLifecycleMutation = useMutation({
    mutationKey: ['alerts', 'occurrences', 'bulk-lifecycle'],
    mutationFn: ({ occurrences, action, disposition }: BulkLifecycleMutationInput) =>
      apiFetch<AlertOccurrenceBulkResponse>(`/alerts/occurrences/bulk/${action}`, {
        method: 'POST',
        body: JSON.stringify({
          items: occurrences.map((occurrence) => ({
            occurrence_id: occurrence.id,
            expected_version: occurrence.version,
          })),
          ...(disposition ? { disposition } : {}),
        }),
      }),
    onSuccess: (response, input) => {
      applyOccurrenceUpdates(response.items)
      setSelectedIds(new Set())
      setActivityPage(1)
      setActionError(null)
      setConflictNotice(null)
      setCloseTarget(null)
      const action = input.action === 'acknowledge' ? 'acknowledged' : 'closed'
      setActionFeedback(
        `${response.updated} occurrence${response.updated === 1 ? '' : 's'} ${action}.`,
      )
    },
    onError: (error) =>
      handleMutationFailure(error, 'The selected occurrences could not be updated'),
  })
  const snoozeMutation = useMutation({
    mutationKey: ['alerts', 'occurrences', 'snooze'],
    mutationFn: ({ occurrence, snoozedUntil, reason }: SnoozeMutationInput) =>
      apiFetch<AlertOccurrence>(`/alerts/occurrences/${occurrence.id}/snooze`, {
        method: 'PATCH',
        body: JSON.stringify({
          expected_version: occurrence.version,
          snoozed_until: snoozedUntil,
          ...(reason ? { reason } : {}),
        }),
      }),
    onSuccess: (occurrence) => {
      applyOccurrenceUpdates([occurrence])
      setActivityPage(1)
      setActionError(null)
      setConflictNotice(null)
      setSnoozeTarget(null)
      setActionFeedback(
        occurrence.is_snoozed ? 'Occurrence snoozed.' : 'Occurrence snooze cleared.',
      )
    },
    onError: (error) => handleMutationFailure(error, 'The occurrence snooze could not be updated'),
  })

  const data = occurrencesQuery.data
  const loadedOccurrences = useMemo(() => data?.items ?? [], [data?.items])
  const visibleOccurrences = useMemo(
    () => filterLoadedOccurrences(loadedOccurrences, loadedPageSearch),
    [loadedOccurrences, loadedPageSearch],
  )
  const selectedOccurrences = useMemo(
    () => loadedOccurrences.filter((occurrence) => selectedIds.has(occurrence.id)),
    [loadedOccurrences, selectedIds],
  )
  const pageCount = alertOccurrencePageCount(data?.total ?? 0, data?.page_size ?? pageSize)
  const stats = alertOccurrencePageStats(visibleOccurrences, data?.total ?? 0)
  const mutationPending =
    lifecycleMutation.isPending || bulkLifecycleMutation.isPending || snoozeMutation.isPending

  useEffect(() => {
    if (!data || occurrencesQuery.isPlaceholderData) return
    const finalPage = alertOccurrencePageCount(data.total, data.page_size)
    if (page > finalPage) setPageState(finalPage)
  }, [data, occurrencesQuery.isPlaceholderData, page])

  const resetCollectionContext = () => {
    setSelectedIds(new Set())
    setSelectedOccurrenceId(null)
    setActivityPage(1)
    setActionError(null)
    setActionFeedback(null)
    setConflictNotice(null)
  }
  const updateFilters = (changes: Partial<AlertOccurrenceFilters>) => {
    setFilters((current) => ({ ...current, ...changes }))
    setPageState(1)
    resetCollectionContext()
  }
  const setPage = (nextPage: number) => {
    setPageState(Math.max(1, Math.min(nextPage, pageCount)))
    resetCollectionContext()
  }
  const setPageSize = (nextPageSize: number) => {
    setPageSizeState(nextPageSize)
    setPageState(1)
    resetCollectionContext()
  }
  const setLoadedPageSearch = (value: string) => {
    setLoadedPageSearchState(value.slice(0, 255))
    setSelectedIds(new Set())
  }
  const refreshCollection = () => {
    setWriteDenied(false)
    setActionError(null)
    setConflictNotice(null)
    return occurrencesQuery.refetch()
  }
  const clearFilters = () => {
    setFilters(DEFAULT_ALERT_OCCURRENCE_FILTERS)
    setLoadedPageSearchState('')
    setPageState(1)
    resetCollectionContext()
  }
  const toggleStateFilter = (state: AlertOccurrenceState) =>
    updateFilters({
      lifecycleStates: toggleValue(filters.lifecycleStates, state),
    })
  const toggleSeverityFilter = (severity: AlertSeverity) =>
    updateFilters({
      severities: toggleValue(filters.severities, severity),
    })
  const toggleSelection = (occurrenceId: string) => {
    setSelectedIds((current) => {
      const next = new Set(current)
      if (next.has(occurrenceId)) next.delete(occurrenceId)
      else next.add(occurrenceId)
      return next
    })
  }
  const toggleSelectAllVisible = () => {
    const visibleIds = visibleOccurrences.map((occurrence) => occurrence.id)
    const allSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedIds.has(id))
    setSelectedIds((current) => {
      const next = new Set(current)
      visibleIds.forEach((id) => (allSelected ? next.delete(id) : next.add(id)))
      return next
    })
  }
  const clearSelection = () => setSelectedIds(new Set())
  const selectOccurrence = (
    occurrenceId: string | null,
    returnTarget?: HTMLButtonElement | null,
  ) => {
    if (occurrenceId && returnTarget) detailReturnTargetRef.current = returnTarget
    setSelectedOccurrenceId(occurrenceId)
    setActivityPage(1)
    setActionError(null)
    setConflictNotice(null)
  }
  const closeOccurrenceDetail = () => {
    const returnTarget = detailReturnTargetRef.current
    detailReturnTargetRef.current = null
    selectOccurrence(null)
    window.setTimeout(() => {
      if (returnTarget?.isConnected && !returnTarget.disabled) {
        returnTarget.focus()
        if (document.activeElement === returnTarget) return
      }
      document.querySelector<HTMLButtonElement>('#alert-occurrences-refresh')?.focus()
    }, 0)
  }
  const runLifecycleAction = (
    occurrence: AlertOccurrence,
    action: AlertOccurrenceLifecycleAction,
  ) => {
    if (!getAlertOccurrenceLifecycleActions(occurrence.lifecycle_state).includes(action)) return
    setActionError(null)
    setActionFeedback(null)
    if (action === 'close') {
      setCloseDisposition('true_positive')
      setCloseTarget({ occurrences: [occurrence], bulk: false, mode: 'close' })
      return
    }
    if (action === 'change_disposition') {
      setCloseDisposition(supportedDisposition(occurrence.closure_disposition))
      setCloseTarget({ occurrences: [occurrence], bulk: false, mode: 'disposition' })
      return
    }
    lifecycleMutation.mutate({
      occurrence,
      state: action === 'acknowledge' ? 'acknowledged' : 'investigating',
    })
  }
  const acknowledgeSelected = () => {
    if (!canBulkAcknowledge(selectedOccurrences)) return
    setActionError(null)
    setActionFeedback(null)
    bulkLifecycleMutation.mutate({ occurrences: selectedOccurrences, action: 'acknowledge' })
  }
  const requestCloseSelected = () => {
    if (!canBulkClose(selectedOccurrences)) return
    setCloseDisposition('true_positive')
    setCloseTarget({ occurrences: selectedOccurrences, bulk: true, mode: 'close' })
  }
  const confirmClose = () => {
    if (!closeTarget) return
    if (closeTarget.bulk) {
      bulkLifecycleMutation.mutate({
        occurrences: closeTarget.occurrences,
        action: 'close',
        disposition: closeDisposition,
      })
      return
    }
    const occurrence = closeTarget.occurrences[0]
    if (!occurrence) return
    lifecycleMutation.mutate({
      occurrence,
      state: 'closed',
      disposition: closeDisposition,
    })
  }
  const requestSnooze = (occurrence: AlertOccurrence) => {
    const fourHoursFromNow = new Date(Date.now() + 4 * 60 * 60 * 1000)
    setSnoozeTarget(occurrence)
    setSnoozeUntil(toLocalDateTimeInput(fourHoursFromNow))
    setSnoozeReason('')
    setActionError(null)
  }
  const confirmSnooze = () => {
    if (!snoozeTarget || snoozeValidationError) return
    snoozeMutation.mutate({
      occurrence: snoozeTarget,
      snoozedUntil: new Date(snoozeUntil).toISOString(),
      reason: snoozeReason.trim(),
    })
  }
  const clearSnooze = (occurrence: AlertOccurrence) => {
    setActionError(null)
    snoozeMutation.mutate({ occurrence, snoozedUntil: null, reason: null })
  }
  const snoozeValidationError = validateSnooze(snoozeUntil, snoozeReason)
  const closeConfirmationDisabled =
    !closeTarget ||
    closeTarget.occurrences.length === 0 ||
    (closeTarget.mode === 'disposition' &&
      closeTarget.occurrences[0]?.closure_disposition === closeDisposition)
  const backfill = useAlertBackfillController(queryClient)

  return {
    acknowledgeSelected,
    actionError,
    actionFeedback,
    activityPage,
    activityQuery,
    backfill,
    bulkLifecycleMutation,
    canBulkAcknowledge: canBulkAcknowledge(selectedOccurrences),
    canBulkClose: canBulkClose(selectedOccurrences),
    clearSelection,
    clearFilters,
    clearSnooze,
    closeOccurrenceDetail,
    closeDisposition,
    closeConfirmationDisabled,
    closeTarget,
    confirmClose,
    confirmSnooze,
    conflictNotice,
    currentUserQuery,
    detailQuery,
    filters,
    filterValidationError,
    lifecycleMutation,
    loadedPageSearch,
    mutationPending,
    occurrencesQuery,
    page,
    pageCount,
    pageSize,
    requestCloseSelected,
    requestSnooze,
    refreshCollection,
    rulesQuery,
    runLifecycleAction,
    selectOccurrence,
    selectedIds,
    selectedOccurrenceId,
    selectedOccurrences,
    setActionError,
    setActionFeedback,
    setActivityPage,
    setCloseDisposition,
    setCloseTarget,
    setLoadedPageSearch,
    setPage,
    setPageSize,
    setSnoozeReason,
    setSnoozeTarget,
    setSnoozeUntil,
    snoozeMutation,
    snoozeReason,
    snoozeTarget,
    snoozeUntil,
    snoozeValidationError,
    stats,
    toggleSelectAllVisible,
    toggleSelection,
    toggleSeverityFilter,
    toggleStateFilter,
    updateFilters,
    visibleOccurrences,
    writeDenied,
  }
}

function useAlertBackfillController(queryClient: ReturnType<typeof useQueryClient>) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<AlertBackfillDraft>(() => createDefaultBackfillDraft())
  const [previewedKey, setPreviewedKey] = useState<string | null>(null)
  const [appliedPreviewToken, setAppliedPreviewToken] = useState<string | null>(null)
  const preview = useMutation({
    mutationKey: ['alerts', 'occurrences', 'backfill-preview'],
    mutationFn: (input: { request: ReturnType<typeof alertBackfillRequest>; key: string }) =>
      apiFetch<AlertBackfillPreviewResponse>('/alerts/occurrences/reconciliation/preview', {
        method: 'POST',
        body: JSON.stringify(input.request),
      }),
    onSuccess: (_response, input) => {
      setPreviewedKey(input.key)
      setAppliedPreviewToken(null)
      apply.reset()
    },
  })
  const apply = useMutation({
    mutationKey: ['alerts', 'occurrences', 'backfill-apply'],
    mutationFn: (previewToken: string) =>
      apiFetch<AlertBackfillApplyResponse>('/alerts/occurrences/reconciliation/apply', {
        method: 'POST',
        body: JSON.stringify({ preview_token: previewToken }),
      }),
    onSuccess: (_response, previewToken) => {
      setAppliedPreviewToken(previewToken)
      void queryClient.invalidateQueries({ queryKey: ['alerts', 'occurrences', 'list'] })
    },
  })
  const validationError = validateAlertBackfillDraft(draft)
  const currentKey = alertBackfillDraftKey(draft)
  const canApply =
    Boolean(preview.data) &&
    Boolean(preview.data?.returned_count) &&
    Boolean(previewedKey) &&
    preview.data?.preview_token !== appliedPreviewToken &&
    !validationError &&
    !preview.isPending &&
    !apply.isPending

  const openDialog = () => {
    setDraft(createDefaultBackfillDraft())
    setPreviewedKey(null)
    setAppliedPreviewToken(null)
    preview.reset()
    apply.reset()
    setOpen(true)
  }
  const closeDialog = () => {
    if (preview.isPending || apply.isPending) return
    setOpen(false)
  }
  const updateDraft = (changes: Partial<AlertBackfillDraft>) => {
    setDraft((current) => ({ ...current, ...changes }))
    setPreviewedKey(null)
    setAppliedPreviewToken(null)
    preview.reset()
    apply.reset()
  }
  const previewBackfill = () => {
    if (validationError) return
    preview.mutate({ request: alertBackfillRequest(draft), key: currentKey })
  }
  const applyBackfill = () => {
    if (!canApply || !preview.data) return
    apply.mutate(preview.data.preview_token)
  }
  const canContinue =
    Boolean(apply.data?.has_more) &&
    Boolean(apply.data?.next_cursor_first_seen_at) &&
    Boolean(apply.data?.next_cursor_item_id) &&
    !preview.isPending &&
    !apply.isPending
  const continueBackfill = () => {
    if (!canContinue || !apply.data?.next_cursor_first_seen_at || !apply.data.next_cursor_item_id)
      return
    const cursor = {
      firstSeenAt: apply.data.next_cursor_first_seen_at,
      itemId: apply.data.next_cursor_item_id,
    }
    const key = `${currentKey}|${cursor.firstSeenAt}|${cursor.itemId}`
    preview.mutate({ request: alertBackfillRequest(draft, cursor), key })
  }

  return {
    apply,
    applyBackfill,
    canApply,
    canContinue,
    closeDialog,
    continueBackfill,
    draft,
    open,
    openDialog,
    preview,
    previewBackfill,
    updateDraft,
    validationError,
  }
}

function retryOperationalQuery(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && [401, 403, 404, 422].includes(error.status)) return false
  return failureCount < 1
}

function toggleValue<T>(values: T[], value: T): T[] {
  return values.includes(value) ? values.filter((entry) => entry !== value) : [...values, value]
}

function validateSnooze(until: string, reason: string): string | null {
  const parsed = new Date(until)
  if (!until || Number.isNaN(parsed.getTime())) return 'Choose a valid snooze end time.'
  if (parsed.getTime() <= Date.now()) return 'The snooze end time must be in the future.'
  const normalizedReason = reason.trim()
  if (!normalizedReason) return 'Enter a reason for the snooze.'
  if (normalizedReason.length > 500) return 'The snooze reason cannot exceed 500 characters.'
  return null
}

function toLocalDateTimeInput(value: Date): string {
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60_000)
  return local.toISOString().slice(0, 16)
}

function supportedDisposition(value: string | null): AlertClosureDisposition {
  const supported: AlertClosureDisposition[] = [
    'true_positive',
    'false_positive',
    'benign',
    'duplicate',
    'informational',
    'other',
  ]
  return supported.includes(value as AlertClosureDisposition)
    ? (value as AlertClosureDisposition)
    : 'other'
}

export type AlertOccurrencesController = ReturnType<typeof useAlertOccurrencesController>
export type { AlertBooleanFilter, CloseTarget }
