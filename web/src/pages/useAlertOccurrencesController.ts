import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { useAlertUrlState } from './alertUrlState'
import { useAlertTeamAccess } from './useAlertTeamAccess'
import { captureSessionLease } from '../api/sessionLifecycle'
import { ApiError, apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { accessibleQueryData } from '../api/queryData'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useQueryAccessScope } from '../hooks/useQueryAccessScope'
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
  const urlState = useAlertUrlState()
  const { filters, page, pageSize, loadedPageSearch, selectedOccurrenceId, activityPage, update: updateUrlState } = urlState
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set())
  const [shareFeedback, setShareFeedback] = useState<string | null>(null)
  const setActivityPage = (value: number) => updateUrlState({ activityPage: value }, true)
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
  const canonicalRefreshCountRef = useRef(0)
  const [canonicalRefreshCount, setCanonicalRefreshCount] = useState(0)
  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])
  const {
    rulesQuery, occurrencesQuery, detailQuery, activityQuery, occurrencePath,
    filterValidationError, accessWithdrawn, listReadUnavailable, detailReadUnavailable, independentDetailAvailable,
  } = useOccurrenceReads(active, filters, page, pageSize, selectedOccurrenceId, activityPage)

  const applyOccurrenceUpdates = async (updates: AlertOccurrence[]) => {
    // Committed writes do not authorize cached evidence. Re-read current server
    // state before enabling another version-sensitive action.
    canonicalRefreshCountRef.current += 1
    if (mountedRef.current) setCanonicalRefreshCount((count) => count + 1)
    try {
      await Promise.allSettled([
        queryClient.invalidateQueries({ queryKey: ['alerts', 'occurrences', 'list'] }),
        ...[...new Set(updates.map((occurrence) => occurrence.id))].map((id) =>
          queryClient.invalidateQueries({ queryKey: ['alerts', 'occurrences', 'detail', id] })),
        queryClient.invalidateQueries({ queryKey: ['alerts', 'occurrences', 'activity'] }),
      ])
    } finally {
      canonicalRefreshCountRef.current -= 1
      if (mountedRef.current) setCanonicalRefreshCount((count) => Math.max(0, count - 1))
    }
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
    onSuccess: async (occurrence, input) => {
      await applyOccurrenceUpdates([occurrence])
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
    onSuccess: async (response, input) => {
      await applyOccurrenceUpdates(response.items)
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
    onSuccess: async (occurrence) => {
      await applyOccurrenceUpdates([occurrence])
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
    lifecycleMutation.isPending || bulkLifecycleMutation.isPending || snoozeMutation.isPending || canonicalRefreshCount > 0
  const canWriteDetail = (occurrence: AlertOccurrence) =>
    !mutationPending && canonicalRefreshCountRef.current === 0 && !writeDenied && !accessWithdrawn && !detailReadUnavailable &&
    detailQuery.data?.id === occurrence.id && detailQuery.data.version === occurrence.version
  const canWriteCollection = (occurrences: AlertOccurrence[]) =>
    !mutationPending && canonicalRefreshCountRef.current === 0 && !writeDenied && !accessWithdrawn && !listReadUnavailable &&
    occurrences.every((occurrence) => loadedOccurrences.some((current) => current.id === occurrence.id && current.version === occurrence.version))
  const mutationPendingMessage = resolveMutationPendingMessage(
    lifecycleMutation.isPending ? lifecycleMutation.variables : undefined,
    bulkLifecycleMutation.isPending ? bulkLifecycleMutation.variables : undefined,
    snoozeMutation.isPending ? snoozeMutation.variables : undefined,
  )

  useEffect(() => {
    if (!data || occurrencesQuery.isPlaceholderData) return
    const finalPage = alertOccurrencePageCount(data.total, data.page_size)
    if (page > finalPage) updateUrlState({ page: finalPage }, true)
  }, [data, occurrencesQuery.isPlaceholderData, page, updateUrlState])

  useEffect(() => {
    setSelectedIds(new Set())
    setActionError(null)
    setActionFeedback(null)
    setConflictNotice(null)
    setShareFeedback(null)
  }, [occurrencePath, selectedOccurrenceId])

  useEffect(() => {
    if (!accessWithdrawn) return
    setSelectedIds(new Set())
    setCloseTarget(null)
    setSnoozeTarget(null)
  }, [accessWithdrawn])

  const copyTriageLink = async () => {
    const lease = captureSessionLease()
    try {
      await navigator.clipboard.writeText(urlState.shareUrl)
      lease.assertCurrent()
      setShareFeedback('Triage link copied. Recipients need access to the same occurrences and sources.')
    } catch {
      if (lease.signal.aborted) return
      setShareFeedback('Copy is unavailable. Select the triage link below and copy it manually.')
    }
  }

  const resetCollectionContext = () => {
    setSelectedIds(new Set())
    setActionError(null)
    setActionFeedback(null)
    setConflictNotice(null)
  }
  const updateFilters = (changes: Partial<AlertOccurrenceFilters>) => {
    updateUrlState({ filters: { ...filters, ...changes }, page: 1, selectedOccurrenceId: null, activityPage: 1 })
    resetCollectionContext()
  }
  const setPage = (nextPage: number) => {
    updateUrlState({ page: Math.max(1, Math.min(nextPage, pageCount)), selectedOccurrenceId: null, activityPage: 1 })
    resetCollectionContext()
  }
  const setPageSize = (nextPageSize: number) => {
    updateUrlState({ pageSize: nextPageSize, page: 1, selectedOccurrenceId: null, activityPage: 1 })
    resetCollectionContext()
  }
  const setLoadedPageSearch = (value: string) => {
    updateUrlState({ loadedPageSearch: value.slice(0, 255) }, true)
    setSelectedIds(new Set())
  }
  const refreshCollection = () => {
    setWriteDenied(false)
    setActionError(null)
    setConflictNotice(null)
    return occurrencesQuery.refetch()
  }
  const clearFilters = () => {
    urlState.reset()
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
    updateUrlState({ selectedOccurrenceId: occurrenceId, activityPage: 1 })
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
    if (!canWriteDetail(occurrence)) return
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
    if (!canWriteCollection(selectedOccurrences) || !canBulkAcknowledge(selectedOccurrences)) return
    setActionError(null)
    setActionFeedback(null)
    bulkLifecycleMutation.mutate({ occurrences: selectedOccurrences, action: 'acknowledge' })
  }
  const requestCloseSelected = () => {
    if (!canWriteCollection(selectedOccurrences) || !canBulkClose(selectedOccurrences)) return
    setCloseDisposition('true_positive')
    setCloseTarget({ occurrences: selectedOccurrences, bulk: true, mode: 'close' })
  }
  const confirmClose = () => {
    if (!closeTarget || accessWithdrawn) return
    if (closeTarget.bulk) {
      if (!canWriteCollection(closeTarget.occurrences)) return
      bulkLifecycleMutation.mutate({
        occurrences: closeTarget.occurrences,
        action: 'close',
        disposition: closeDisposition,
      })
      return
    }
    const occurrence = closeTarget.occurrences[0]
    if (!occurrence || !canWriteDetail(occurrence)) return
    lifecycleMutation.mutate({
      occurrence,
      state: 'closed',
      disposition: closeDisposition,
    })
  }
  const requestSnooze = (occurrence: AlertOccurrence) => {
    if (!canWriteDetail(occurrence)) return
    const fourHoursFromNow = new Date(Date.now() + 4 * 60 * 60 * 1000)
    setSnoozeTarget(occurrence)
    setSnoozeUntil(toLocalDateTimeInput(fourHoursFromNow))
    setSnoozeReason('')
    setActionError(null)
  }
  const confirmSnooze = () => {
    if (!snoozeTarget || snoozeValidationError || !canWriteDetail(snoozeTarget)) return
    snoozeMutation.mutate({
      occurrence: snoozeTarget,
      snoozedUntil: new Date(snoozeUntil).toISOString(),
      reason: snoozeReason.trim(),
    })
  }
  const clearSnooze = (occurrence: AlertOccurrence) => {
    if (!canWriteDetail(occurrence)) return
    setActionError(null)
    snoozeMutation.mutate({ occurrence, snoozedUntil: null, reason: null })
  }
  const snoozeValidationError = validateSnooze(snoozeUntil, snoozeReason)
  const closeConfirmationDisabled =
    !closeTarget ||
    (closeTarget.bulk ? !canWriteCollection(closeTarget.occurrences) : !closeTarget.occurrences[0] || !canWriteDetail(closeTarget.occurrences[0])) ||
    closeTarget.occurrences.length === 0 ||
    (closeTarget.mode === 'disposition' &&
      closeTarget.occurrences[0]?.closure_disposition === closeDisposition)
  const backfill = useAlertBackfillController(queryClient)

  return {
    acknowledgeSelected,
    applyOccurrenceUpdates,
    copyTriageLink,
    shareFeedback,
    shareUrl: urlState.shareUrl,
    actionError,
    actionFeedback,
    activityPage,
    activityQuery,
    backfill,
    bulkLifecycleMutation,
    canBulkAcknowledge: !listReadUnavailable && canBulkAcknowledge(selectedOccurrences),
    canBulkClose: !listReadUnavailable && canBulkClose(selectedOccurrences),
    clearSelection,
    clearFilters,
    clearSnooze,
    closeOccurrenceDetail,
    closeDisposition,
    closeConfirmationDisabled,
    closeTarget: accessWithdrawn ? null : closeTarget,
    confirmClose,
    confirmSnooze,
    conflictNotice,
    currentUserQuery,
    detailQuery,
    detailReadUnavailable,
    independentDetailAvailable,
    filters,
    filterValidationError,
    lifecycleMutation,
    loadedPageSearch,
    mutationPending,
    mutationPendingMessage,
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
    snoozeTarget: accessWithdrawn || detailReadUnavailable ? null : snoozeTarget,
    snoozeUntil,
    snoozeValidationError,
    stats,
    toggleSelectAllVisible,
    toggleSelection,
    toggleSeverityFilter,
    toggleStateFilter,
    updateFilters,
    visibleOccurrences,
    writeDenied: writeDenied || accessWithdrawn,
  }
}

function useOccurrenceReads(
  active: boolean,
  filters: AlertOccurrenceFilters,
  page: number,
  pageSize: number,
  selectedOccurrenceId: string | null,
  activityPage: number,
) {
  const queryClient = useQueryClient()
  const rulesAccess = useQueryAccessScope(['alerts', 'occurrences', 'rules', filters.teamId ?? '', filters.queueScope ?? 'all'])
  const listAccess = useQueryAccessScope(['alerts', 'occurrences', 'list', filters.teamId ?? '', filters.queueScope ?? 'all'])
  const detailAccess = useQueryAccessScope(['alerts', 'occurrences', 'detail', selectedOccurrenceId ?? ''])
  const activityAccess = useQueryAccessScope(['alerts', 'occurrences', 'activity', selectedOccurrenceId ?? ''])

  const rulesResult = useQuery({
    queryKey: ['alerts', 'occurrences', 'rules', filters.teamId, filters.queueScope],
    ...rulesAccess.queryOptions(() => {
      const params = new URLSearchParams({ include_disabled: 'true' })
      if (filters.teamId) params.set('team_id', filters.teamId)
      if (filters.queueScope && filters.queueScope !== 'all') params.set('queue_scope', filters.queueScope)
      return apiFetch<AlertInterest[]>(`/alerts?${params}`)
    }, 30_000),
    enabled: active,
  })
  const occurrencePath = useMemo(
    () => buildAlertOccurrencesPath(filters, page, pageSize),
    [filters, page, pageSize],
  )
  const filterValidationError = validateAlertOccurrenceFilters(filters)
  const listKey = ['alerts', 'occurrences', 'list', occurrencePath]
  const occurrencesResult = useQuery({
    queryKey: listKey,
    ...listAccess.queryOptions(() => apiFetch<AlertOccurrenceListResponse>(occurrencePath)),
    placeholderData: (previous) => listAccess.currentData(previous) === undefined ? undefined : previous,
    enabled: active && !filterValidationError,
    refetchInterval: OCCURRENCE_REFRESH_MS,
    retry: retryOperationalQuery,
  })
  const detailKey = ['alerts', 'occurrences', 'detail', selectedOccurrenceId]
  const detailResult = useQuery({
    queryKey: detailKey,
    ...detailAccess.queryOptions(() => apiFetch<AlertOccurrence>(`/alerts/occurrences/${encodeURIComponent(selectedOccurrenceId ?? '')}`)),
    enabled: active && Boolean(selectedOccurrenceId),
    refetchInterval: OCCURRENCE_REFRESH_MS,
    retry: retryOperationalQuery,
  })
  const activityKey = ['alerts', 'occurrences', 'activity', selectedOccurrenceId, activityPage, detailAccess.generation]
  const activityResult = useQuery({
    queryKey: activityKey,
    ...activityAccess.queryOptions(() => {
      const params = new URLSearchParams({
        page: String(activityPage),
        page_size: String(ALERT_OCCURRENCE_ACTIVITY_PAGE_SIZE),
      })
      return apiFetch<AlertOccurrenceActivityListResponse>(
        `/alerts/occurrences/${encodeURIComponent(selectedOccurrenceId ?? '')}/activity?${params.toString()}`,
      )
    }),
    enabled: active && Boolean(selectedOccurrenceId) && !detailAccess.denied,
    retry: retryOperationalQuery,
  })
  const teamAccess = useAlertTeamAccess(active, detailResult.data?.team_id, {
    detail: detailKey, activity: activityKey, list: listKey,
    listTeams: (occurrencesResult.data?.items ?? []).flatMap((item) => item.team_id ? [item.team_id] : []),
  })
  const accessLoss = teamAccess.accessLoss
  const collectionAccessLoss = teamAccess.collectionAccessLoss
  const activityAccessLoss = teamAccess.activityAccessLoss
  useEffect(() => {
    if (!teamAccess.currentLoss) return
    void queryClient.invalidateQueries({ queryKey: ['alerts', 'occurrences'] })
  }, [queryClient, teamAccess.currentLoss])
  const rulesQuery = { ...rulesResult, data: accessibleQueryData(rulesResult) }
  const occurrencesQuery = {
    ...occurrencesResult, data: collectionAccessLoss ? undefined : accessibleQueryData(occurrencesResult),
    error: collectionAccessLoss ?? occurrencesResult.error, isError: Boolean(collectionAccessLoss) || occurrencesResult.isError,
    refetch: async () => { if (collectionAccessLoss && detailResult.data?.team_id) await teamAccess.refetch(); return occurrencesResult.refetch() },
  }
  const detailQuery = {
    ...detailResult, data: accessLoss ? undefined : accessibleQueryData(detailResult),
    error: accessLoss ?? detailResult.error, isError: Boolean(accessLoss) || detailResult.isError,
    refetch: async () => { if (accessLoss) await teamAccess.refetch(); return detailResult.refetch() },
  }
  const activityQuery = {
    ...activityResult, data: activityAccessLoss || detailAccess.denied ? undefined : accessibleQueryData(activityResult),
    error: activityAccessLoss ?? activityResult.error, isError: Boolean(activityAccessLoss) || activityResult.isError,
  }
  const accessWithdrawn = Boolean(accessLoss) || listAccess.denied || detailAccess.denied || [occurrencesResult, detailResult].some(
    (query) => query.error instanceof ApiError && [401, 403, 404].includes(query.error.status),
  )

  const listReadUnavailable = occurrencesQuery.isError || listAccess.denied
  const detailReadUnavailable = detailQuery.isError || detailAccess.denied
  const independentDetailAvailable = Boolean(collectionAccessLoss && detailQuery.data)
  return {
    rulesQuery, occurrencesQuery, detailQuery, activityQuery, occurrencePath,
    filterValidationError, accessWithdrawn, listReadUnavailable, detailReadUnavailable, independentDetailAvailable,
  }
}

function resolveMutationPendingMessage(
  lifecycle: LifecycleMutationInput | undefined,
  bulk: BulkLifecycleMutationInput | undefined,
  snooze: SnoozeMutationInput | undefined,
): string | null {
  if (lifecycle) {
    if (lifecycle.state === 'acknowledged') return 'Acknowledging alert occurrence...'
    if (lifecycle.state === 'investigating') return 'Starting alert occurrence investigation...'
    return lifecycle.occurrence.lifecycle_state === 'closed'
      ? 'Updating alert occurrence closure disposition...'
      : 'Closing alert occurrence...'
  }
  if (bulk) {
    const count = bulk.occurrences.length
    return bulk.action === 'acknowledge'
      ? `Acknowledging ${count} selected alert occurrence${count === 1 ? '' : 's'}...`
      : `Closing ${count} selected alert occurrence${count === 1 ? '' : 's'}...`
  }
  if (snooze) {
    return snooze.snoozedUntil
      ? 'Snoozing alert occurrence...'
      : 'Clearing alert occurrence snooze...'
  }
  return null
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
