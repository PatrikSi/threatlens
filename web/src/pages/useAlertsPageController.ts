import { FormEvent, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { AlertInterest, AlertMatchListResponse, AlertSeverity } from '../types/api'
import {
  ALERT_CATEGORIES,
  ALERT_PREVIEW_LIMIT,
  alertSuppressionInputValue,
  alertSuppressionISOString,
  getAlertSaveDisabledReason,
  getAlertSuppressionValidationError,
  groupAlertsByCategory,
  parseAlertKeywords,
} from './alertPageModel'

type AlertWritePayload = {
  id?: string
  expectedRevision?: number
  expectedRowVersion?: number
  name: string
  category: string
  keywords: string[]
  severity: AlertSeverity
  suppressionUntil: string | null
  suppressionReason: string | null
}

type AlertRevisionConflict = {
  alertId: string
  currentRowVersion: number | null
}

export function useAlertsPageController() {
  const queryClient = useQueryClient()
  const [editingAlertId, setEditingAlertId] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [category, setCategory] = useState<string>(ALERT_CATEGORIES[0].value)
  const [keywordsText, setKeywordsText] = useState('')
  const [severity, setSeverity] = useState<AlertSeverity>('medium')
  const [suppressionEnabled, setSuppressionEnabled] = useState(false)
  const [suppressionUntil, setSuppressionUntil] = useState('')
  const [suppressionReason, setSuppressionReason] = useState('')
  const [editingAlertRevision, setEditingAlertRevision] = useState<number | null>(null)
  const [editingAlertRowVersion, setEditingAlertRowVersion] = useState<number | null>(null)
  const [revisionConflict, setRevisionConflict] = useState<AlertRevisionConflict | null>(null)
  const [updateAlertError, setUpdateAlertError] = useState<string | null>(null)
  const [showDisabled, setShowDisabled] = useState(false)
  const [pendingDeleteAlert, setPendingDeleteAlert] = useState<AlertInterest | null>(null)
  const pendingDeleteAlertRef = useRef<AlertInterest | null>(null)
  const deleteReloadGenerationRef = useRef(0)
  const [deleteAlertError, setDeleteAlertError] = useState<string | null>(null)
  const [deleteConflictNeedsRefresh, setDeleteConflictNeedsRefresh] = useState(false)

  const parsedKeywords = useMemo(() => parseAlertKeywords(keywordsText), [keywordsText])
  const previewEnabled = parsedKeywords.length > 0 && category.trim().length > 0
  const saveDisabledReason = revisionConflict
    ? 'Reload the latest rule before saving this draft.'
    : (getAlertSaveDisabledReason(name, parsedKeywords) ??
      getAlertSuppressionValidationError(suppressionEnabled, suppressionUntil, suppressionReason))
  const alertsQuery = useQuery({
    queryKey: ['alerts', showDisabled],
    queryFn: () => apiFetch<AlertInterest[]>(`/alerts?include_disabled=${showDisabled}`),
  })
  const deleteConflictAlertsQuery = useQuery({
    queryKey: ['alerts', 'delete-conflict-all'],
    queryFn: () => apiFetch<AlertInterest[]>('/alerts?include_disabled=true'),
    enabled: false,
  })
  const previewQuery = useQuery({
    queryKey: ['alerts', 'preview', category, ...parsedKeywords],
    queryFn: () =>
      apiFetch<AlertMatchListResponse>('/alerts/preview', {
        method: 'POST',
        body: JSON.stringify({
          name: name.trim() || 'Preview',
          category,
          keywords: parsedKeywords,
          limit: ALERT_PREVIEW_LIMIT,
        }),
      }),
    enabled: previewEnabled,
    staleTime: 15_000,
  })

  const resetDraft = () => {
    setEditingAlertId(null)
    setEditingAlertRevision(null)
    setEditingAlertRowVersion(null)
    setName('')
    setCategory(ALERT_CATEGORIES[0].value)
    setKeywordsText('')
    setSeverity('medium')
    setSuppressionEnabled(false)
    setSuppressionUntil('')
    setSuppressionReason('')
    setRevisionConflict(null)
  }
  const replacePendingDeleteAlert = (alert: AlertInterest | null) => {
    pendingDeleteAlertRef.current = alert
    setPendingDeleteAlert(alert)
  }
  const cancelPendingDeleteAlert = () => {
    deleteReloadGenerationRef.current += 1
    replacePendingDeleteAlert(null)
    setDeleteAlertError(null)
    setDeleteConflictNeedsRefresh(false)
  }
  const saveAlert = useMutation({
    mutationKey: ['alerts', 'save'],
    mutationFn: saveAlertRequest,
    onSuccess: () => {
      resetDraft()
      void queryClient.invalidateQueries({ queryKey: ['alerts'] })
    },
    onError: (error, payload) => {
      if (payload.id && isAlertRevisionConflict(error)) {
        setRevisionConflict({
          alertId: payload.id,
          currentRowVersion: alertConflictCurrentRowVersion(error),
        })
        void queryClient.invalidateQueries({ queryKey: ['alerts'] })
      }
    },
  })
  const updateAlert = useMutation({
    mutationKey: ['alerts', 'update'],
    mutationFn: (payload: { id: string; body: Record<string, unknown> }) =>
      apiFetch<AlertInterest>(`/alerts/${payload.id}`, {
        method: 'PATCH',
        body: JSON.stringify(payload.body),
      }),
    onSuccess: () => {
      setUpdateAlertError(null)
      void queryClient.invalidateQueries({ queryKey: ['alerts'] })
    },
    onError: (error, payload) => {
      setUpdateAlertError(
        isAlertRevisionConflict(error)
          ? alertConflictMessage(error)
          : resolveApiErrorMessage(error, 'The alert rule state could not be updated'),
      )
      if (isAlertRevisionConflict(error)) {
        void queryClient.invalidateQueries({ queryKey: ['alerts'] })
        if (payload.id === editingAlertId) {
          setRevisionConflict({
            alertId: payload.id,
            currentRowVersion: alertConflictCurrentRowVersion(error),
          })
        }
      }
    },
  })
  const deleteAlert = useMutation({
    mutationKey: ['alerts', 'delete'],
    mutationFn: (alert: AlertInterest) => apiFetch(alertDeletePath(alert), { method: 'DELETE' }),
    onSuccess: (_, deletedAlert) => {
      const deletedId = deletedAlert.id
      if (editingAlertId === deletedId) {
        resetDraft()
      }
      if (pendingDeleteAlertRef.current?.id === deletedId) {
        replacePendingDeleteAlert(null)
      }
      setDeleteAlertError(null)
      void queryClient.invalidateQueries({ queryKey: ['alerts'] })
    },
    onError: (error) => {
      if (isAlertRevisionConflict(error)) {
        setDeleteConflictNeedsRefresh(true)
        setDeleteAlertError(
          'This alert rule changed before deletion. Loading the latest values so you can review and confirm again.',
        )
        void reloadPendingDeleteAfterConflict()
        return
      }
      setDeleteAlertError(resolveApiErrorMessage(error, 'Alert interest could not be deleted'))
    },
  })

  const reloadPendingDeleteAfterConflict = async () => {
    const target = pendingDeleteAlertRef.current
    if (!target) return
    const reloadGeneration = deleteReloadGenerationRef.current + 1
    deleteReloadGenerationRef.current = reloadGeneration
    setDeleteConflictNeedsRefresh(true)
    const result = await deleteConflictAlertsQuery.refetch()
    if (
      deleteReloadGenerationRef.current !== reloadGeneration ||
      pendingDeleteAlertRef.current?.id !== target.id
    ) {
      return
    }
    if (result.error) {
      setDeleteAlertError(
        resolveApiErrorMessage(
          result.error,
          'The latest alert rule could not be loaded. Deletion remains disabled; retry the refresh.',
        ),
      )
      return
    }
    const latest = result.data?.find((alert) => alert.id === target.id)
    if (!latest) {
      setDeleteAlertError(
        'This alert rule no longer exists. Close this dialog and refresh the rule list.',
      )
      return
    }
    replacePendingDeleteAlert(latest)
    setDeleteConflictNeedsRefresh(false)
    setDeleteAlertError(
      'The rule details below were refreshed. Review them, then confirm deletion again if you still want to remove this rule.',
    )
  }

  const groupedAlerts = useMemo(
    () => groupAlertsByCategory(alertsQuery.data ?? []),
    [alertsQuery.data],
  )
  const editingAlert = useMemo(
    () => (alertsQuery.data ?? []).find((alert) => alert.id === editingAlertId) ?? null,
    [alertsQuery.data, editingAlertId],
  )
  const editingSuppressionEnabled = Boolean(editingAlert?.suppression_until)
  const hasUnsavedAlertDraftChanges =
    name !== (editingAlert?.name ?? '') ||
    category !== (editingAlert?.category ?? ALERT_CATEGORIES[0].value) ||
    keywordsText !== (editingAlert?.keywords.join(', ') ?? '') ||
    severity !== (editingAlert?.severity ?? 'medium') ||
    suppressionEnabled !== editingSuppressionEnabled ||
    suppressionUntil !== alertSuppressionInputValue(editingAlert?.suppression_until) ||
    suppressionReason !== (editingAlert?.suppression_reason ?? '')
  const confirmDiscardUnsavedAlertChanges = useUnsavedChangesWarning(
    hasUnsavedAlertDraftChanges,
    'Discard unsaved alert changes?',
  )

  const resetForm = (force = false) => {
    if (force) {
      saveAlert.reset()
      resetDraft()
      return
    }
    confirmDiscardUnsavedAlertChanges(() => {
      saveAlert.reset()
      resetDraft()
    })
  }
  const onSave = (event: FormEvent) => {
    event.preventDefault()
    if (saveDisabledReason) {
      return
    }
    saveAlert.mutate({
      id: editingAlertId ?? undefined,
      expectedRevision: editingAlertRevision ?? undefined,
      expectedRowVersion: editingAlertRowVersion ?? undefined,
      name: name.trim(),
      category,
      keywords: parsedKeywords,
      severity,
      suppressionUntil: suppressionEnabled ? alertSuppressionISOString(suppressionUntil) : null,
      suppressionReason: suppressionEnabled ? suppressionReason.trim() : null,
    })
  }
  const loadAlertDraft = (alert: AlertInterest) => {
    saveAlert.reset()
    setEditingAlertId(alert.id)
    setEditingAlertRevision(alert.revision ?? 1)
    setEditingAlertRowVersion(alert.row_version ?? null)
    setName(alert.name)
    setCategory(alert.category)
    setKeywordsText(alert.keywords.join(', '))
    setSeverity(alert.severity ?? 'medium')
    setSuppressionEnabled(Boolean(alert.suppression_until))
    setSuppressionUntil(alertSuppressionInputValue(alert.suppression_until))
    setSuppressionReason(alert.suppression_reason ?? '')
    setRevisionConflict(null)
    setUpdateAlertError(null)
  }
  const onEdit = (alert: AlertInterest) => {
    if (alert.id === editingAlertId) {
      return
    }
    confirmDiscardUnsavedAlertChanges(() => {
      loadAlertDraft(alert)
    })
  }
  const reloadAlertAfterConflict = async () => {
    if (!revisionConflict) return
    const result = await alertsQuery.refetch()
    if (result.error) {
      setUpdateAlertError(
        resolveApiErrorMessage(result.error, 'The latest alert rule could not be loaded'),
      )
      return
    }
    const latest = result.data?.find((alert) => alert.id === revisionConflict.alertId)
    if (latest) {
      loadAlertDraft(latest)
      return
    }
    setUpdateAlertError(
      'The alert rule no longer exists on the server. Your draft remains available until you cancel it.',
    )
  }
  const toggleAlertState = (alert: AlertInterest) => {
    setUpdateAlertError(null)
    updateAlert.mutate({
      id: alert.id,
      body: {
        enabled: !alert.enabled,
        ...alertExpectedVersionBody(alert),
      },
    })
  }
  const onRequestDeleteAlert = (alert: AlertInterest) => {
    confirmDiscardUnsavedAlertChanges(() => {
      deleteReloadGenerationRef.current += 1
      setDeleteAlertError(null)
      setDeleteConflictNeedsRefresh(false)
      replacePendingDeleteAlert(alert)
    })
  }
  const confirmDeleteAlert = () => {
    if (
      pendingDeleteAlert &&
      !deleteConflictNeedsRefresh &&
      !deleteConflictAlertsQuery.isFetching
    ) {
      setDeleteAlertError(null)
      deleteAlert.mutate(pendingDeleteAlert)
    }
  }

  return {
    alertsQuery,
    category,
    cancelPendingDeleteAlert,
    confirmDeleteAlert,
    confirmDiscardUnsavedAlertChanges,
    deleteAlert,
    deleteConflictNeedsRefresh,
    deleteConflictReloadPending: deleteConflictAlertsQuery.isFetching,
    deleteAlertError,
    editingAlertId,
    editingAlertRevision,
    editingAlertRowVersion,
    groupedAlerts,
    hasUnsavedAlertDraftChanges,
    keywordsText,
    name,
    onEdit,
    onRequestDeleteAlert,
    onSave,
    pendingDeleteAlert,
    previewEnabled,
    previewQuery,
    reloadAlertAfterConflict,
    reloadPendingDeleteAfterConflict,
    resetForm,
    revisionConflict,
    saveAlert,
    saveDisabledReason,
    setCategory,
    setKeywordsText,
    setName,
    setSeverity,
    setShowDisabled,
    setSuppressionEnabled,
    setSuppressionReason,
    setSuppressionUntil,
    severity,
    showDisabled,
    suppressionEnabled,
    suppressionReason,
    suppressionUntil,
    toggleAlertState,
    updateAlert,
    updateAlertError,
  }
}

function saveAlertRequest(payload: AlertWritePayload): Promise<AlertInterest> {
  const body = JSON.stringify({
    name: payload.name,
    category: payload.category,
    keywords: payload.keywords,
    severity: payload.severity,
    suppression_until: payload.suppressionUntil,
    suppression_reason: payload.suppressionReason,
    ...(payload.id && payload.expectedRowVersion
      ? { expected_row_version: payload.expectedRowVersion }
      : payload.id && payload.expectedRevision
        ? { expected_revision: payload.expectedRevision }
        : {}),
    ...(payload.id ? {} : { enabled: true }),
  })
  return apiFetch<AlertInterest>(payload.id ? `/alerts/${payload.id}` : '/alerts', {
    method: payload.id ? 'PATCH' : 'POST',
    body,
  })
}

type AlertRevisionConflictError = Error & {
  status: number
  code?: string | null
  detail?: unknown
}

function isAlertRevisionConflict(error: unknown): error is AlertRevisionConflictError {
  if (!(error instanceof Error)) return false
  const candidate = error as Partial<AlertRevisionConflictError>
  return (
    error.name === 'ApiError' &&
    candidate.status === 409 &&
    candidate.code === 'alert_revision_conflict'
  )
}

function alertConflictCurrentRowVersion(error: AlertRevisionConflictError): number | null {
  if (!error.detail || typeof error.detail !== 'object') return null
  const detail = error.detail as {
    current_revision?: unknown
    current_row_version?: unknown
  }
  const value = detail.current_row_version ?? detail.current_revision
  return typeof value === 'number' && Number.isInteger(value) && value > 0 ? value : null
}

function alertConflictMessage(error: AlertRevisionConflictError): string {
  const current = alertConflictCurrentRowVersion(error)
  return current
    ? `This alert rule changed on the server (current version ${current}). Latest values were requested; review them before retrying.`
    : 'This alert rule changed on the server. Latest values were requested; review them before retrying.'
}

function alertExpectedVersionBody(alert: AlertInterest): Record<string, number> {
  if (alert.row_version) return { expected_row_version: alert.row_version }
  if (alert.revision) return { expected_revision: alert.revision }
  return {}
}

function alertDeletePath(alert: AlertInterest): string {
  const params = new URLSearchParams()
  const expectedVersion = alertExpectedVersionBody(alert)
  Object.entries(expectedVersion).forEach(([key, value]) => params.set(key, String(value)))
  const query = params.toString()
  return `/alerts/${alert.id}${query ? `?${query}` : ''}`
}

export type AlertsPageController = ReturnType<typeof useAlertsPageController>
