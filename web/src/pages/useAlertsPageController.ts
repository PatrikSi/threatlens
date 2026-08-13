import { FormEvent, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { AlertInterest, AlertMatchListResponse } from '../types/api'
import {
  ALERT_CATEGORIES,
  ALERT_PREVIEW_LIMIT,
  getAlertSaveDisabledReason,
  groupAlertsByCategory,
  parseAlertKeywords,
} from './alertPageModel'

type AlertWritePayload = {
  id?: string
  name: string
  category: string
  keywords: string[]
}

export function useAlertsPageController() {
  const queryClient = useQueryClient()
  const [editingAlertId, setEditingAlertId] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [category, setCategory] = useState<string>(ALERT_CATEGORIES[0].value)
  const [keywordsText, setKeywordsText] = useState('')
  const [showDisabled, setShowDisabled] = useState(false)
  const [pendingDeleteAlert, setPendingDeleteAlert] = useState<AlertInterest | null>(null)
  const [deleteAlertError, setDeleteAlertError] = useState<string | null>(null)

  const parsedKeywords = useMemo(() => parseAlertKeywords(keywordsText), [keywordsText])
  const previewEnabled = parsedKeywords.length > 0 && category.trim().length > 0
  const saveDisabledReason = getAlertSaveDisabledReason(name, parsedKeywords)
  const alertsQuery = useQuery({
    queryKey: ['alerts', showDisabled],
    queryFn: () => apiFetch<AlertInterest[]>(`/alerts?include_disabled=${showDisabled}`),
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
    setName('')
    setCategory(ALERT_CATEGORIES[0].value)
    setKeywordsText('')
  }
  const saveAlert = useMutation({
    mutationKey: ['alerts', 'save'],
    mutationFn: saveAlertRequest,
    onSuccess: () => {
      resetDraft()
      void queryClient.invalidateQueries({ queryKey: ['alerts'] })
    },
  })
  const updateAlert = useMutation({
    mutationKey: ['alerts', 'update'],
    mutationFn: (payload: { id: string; body: Record<string, unknown> }) =>
      apiFetch<AlertInterest>(`/alerts/${payload.id}`, {
        method: 'PATCH',
        body: JSON.stringify(payload.body),
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['alerts'] }),
  })
  const deleteAlert = useMutation({
    mutationKey: ['alerts', 'delete'],
    mutationFn: (id: string) => apiFetch(`/alerts/${id}`, { method: 'DELETE' }),
    onSuccess: (_, deletedId) => {
      if (editingAlertId === deletedId) {
        resetDraft()
      }
      setPendingDeleteAlert((current) => (current?.id === deletedId ? null : current))
      setDeleteAlertError(null)
      void queryClient.invalidateQueries({ queryKey: ['alerts'] })
    },
    onError: (error) => setDeleteAlertError(resolveApiErrorMessage(error, 'Alert interest could not be deleted')),
  })

  const groupedAlerts = useMemo(() => groupAlertsByCategory(alertsQuery.data ?? []), [alertsQuery.data])
  const editingAlert = useMemo(
    () => (alertsQuery.data ?? []).find((alert) => alert.id === editingAlertId) ?? null,
    [alertsQuery.data, editingAlertId],
  )
  const hasUnsavedAlertDraftChanges =
    name !== (editingAlert?.name ?? '') ||
    category !== (editingAlert?.category ?? ALERT_CATEGORIES[0].value) ||
    keywordsText !== (editingAlert?.keywords.join(', ') ?? '')
  const confirmDiscardUnsavedAlertChanges = useUnsavedChangesWarning(
    hasUnsavedAlertDraftChanges,
    'Discard unsaved alert changes?',
  )

  const resetForm = (force = false) => {
    if (force) {
      resetDraft()
      return
    }
    confirmDiscardUnsavedAlertChanges(resetDraft)
  }
  const onSave = (event: FormEvent) => {
    event.preventDefault()
    if (saveDisabledReason) {
      return
    }
    saveAlert.mutate({ id: editingAlertId ?? undefined, name: name.trim(), category, keywords: parsedKeywords })
  }
  const onEdit = (alert: AlertInterest) => {
    if (alert.id === editingAlertId) {
      return
    }
    confirmDiscardUnsavedAlertChanges(() => {
      setEditingAlertId(alert.id)
      setName(alert.name)
      setCategory(alert.category)
      setKeywordsText(alert.keywords.join(', '))
    })
  }
  const onRequestDeleteAlert = (alert: AlertInterest) => {
    confirmDiscardUnsavedAlertChanges(() => {
      setDeleteAlertError(null)
      setPendingDeleteAlert(alert)
    })
  }
  const confirmDeleteAlert = () => {
    if (pendingDeleteAlert) {
      setDeleteAlertError(null)
      deleteAlert.mutate(pendingDeleteAlert.id)
    }
  }

  return {
    alertsQuery,
    category,
    confirmDeleteAlert,
    confirmDiscardUnsavedAlertChanges,
    deleteAlert,
    deleteAlertError,
    editingAlertId,
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
    resetForm,
    saveAlert,
    saveDisabledReason,
    setCategory,
    setDeleteAlertError,
    setKeywordsText,
    setName,
    setPendingDeleteAlert,
    setShowDisabled,
    showDisabled,
    updateAlert,
  }
}

function saveAlertRequest(payload: AlertWritePayload): Promise<AlertInterest> {
  const body = JSON.stringify({
    name: payload.name,
    category: payload.category,
    keywords: payload.keywords,
    ...(payload.id ? {} : { enabled: true }),
  })
  return apiFetch<AlertInterest>(payload.id ? `/alerts/${payload.id}` : '/alerts', {
    method: payload.id ? 'PATCH' : 'POST',
    body,
  })
}

export type AlertsPageController = ReturnType<typeof useAlertsPageController>
