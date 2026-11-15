import { useQueryAccessScope } from '../hooks/useQueryAccessScope'
import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { rebaseSavedDraft } from '../hooks/rebaseSavedDraft'
import { apiFetch } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
import { validateConditions } from './webhookConditionModel'
import { admittedRecordValue } from './notificationReadPresentation'
import {
  Feed,
  NotificationAnalyticsResponse,
  NotificationTemplateVariable,
  NotificationWebhook,
  NotificationWebhookDelivery,
  NotificationWebhookDeliveryListResponse,
  NotificationWebhookTestResponse,
  NotificationWebhookWriteRequest,
} from '../types/api'
import {
  createDefaultDraft,
  createDraftFromWebhook,
  createRequestFromDraft,
  normalizeDraftUrlQuery,
  resolveNotificationEventAvailability,
  type NotificationWebhookDraft,
} from './notificationWebhookDraft'

const DELIVERY_HISTORY_REFRESH_MS = 30_000

export function useNotificationWebhooksController(extraDirty = false) {
  const queryClient = useQueryClient()
  const currentUserQuery = useCurrentUser()
  const [selectedWebhookId, setSelectedWebhookId] = useState<string | null>(null)
  const selectedWebhookIdRef = useRef(selectedWebhookId)
  selectedWebhookIdRef.current = selectedWebhookId
  const collectionAccess = useQueryAccessScope(['notifications', 'webhooks'])
  const analyticsAccess = useQueryAccessScope(['notifications', 'analytics'])
  const deliveryAccess = useQueryAccessScope(['notifications', 'webhooks', selectedWebhookId ?? '', 'deliveries'])
  const [draft, setDraftState] = useState(createDefaultDraft)
  const [baselineDraft, setBaselineDraft] = useState(createDefaultDraft)
  const [baselineAdmission, setBaselineAdmission] = useState(() => collectionAccess.captureResult()(true))
  const [sampleFeedId, setSampleFeedId] = useState('')
  const [formNotice, setFormNotice] = useState<string | null>(null)
  const [testReceipt, setTestReceipt] = useState<{ value: NotificationWebhookTestResponse; admission: typeof baselineAdmission; webhookId: string | null } | null>(null)
  const [deleteIntent, setDeleteIntent] = useState<{ value: NotificationWebhook; admission: typeof baselineAdmission } | null>(null)
  const [retryIntent, setRetryIntent] = useState<{ value: NotificationWebhookDelivery; admission: typeof baselineAdmission; collection: typeof baselineAdmission } | null>(null)
  const [mobileVariablesOpen, setMobileVariablesOpen] = useState(false)

  const baseRoleCanManage = currentUserQuery.data?.role === 'admin' ||
    currentUserQuery.data?.role === 'analyst'
  const canManageWebhooks = !currentUserQuery.isError && baseRoleCanManage && hasRequiredPermissions(
    currentUserQuery.data?.access?.permissions ?? [],
    ['write:notifications'],
  )
  const isReadOnly = !currentUserQuery.isLoading && !canManageWebhooks
  const { availableEventOptions, unavailableDailyBriefSelected, unavailableReportSelected } = resolveNotificationEventAvailability(
    currentUserQuery.data?.features.ai_daily_brief_enabled === true,
    draft.event_type,
    currentUserQuery.data?.features.ai_reporting_enabled === true,
  )
  const accessNotice = isReadOnly
    ? 'You can review these webhooks, but changes require an Administrator or Analyst base role and permission to manage notifications.'
    : null

  const webhooksQuery = useQuery({
    queryKey: ['notifications', 'webhooks'],
    ...collectionAccess.queryOptions(({ signal }) => apiFetch<NotificationWebhook[]>('/notifications/webhooks', { signal })),
  })
  const feedAccess = useQueryAccessScope(['feeds'])
  const feedsQuery = useQuery({
    queryKey: ['feeds'],
    ...feedAccess.queryOptions(({ signal }) => apiFetch<Feed[]>('/feeds', { signal })),
  })
  const variablesQuery = useQuery({
    queryKey: ['notifications', 'template-variables'],
    queryFn: () => apiFetch<NotificationTemplateVariable[]>('/notifications/template-variables'),
  })
  const analyticsQuery = useQuery({
    queryKey: ['notifications', 'analytics'],
    ...analyticsAccess.queryOptions(({ signal }) => apiFetch<NotificationAnalyticsResponse>('/notifications/analytics', { signal })),
    refetchInterval: DELIVERY_HISTORY_REFRESH_MS,
  })

  const collectionKey = ['notifications', 'webhooks'] as const
  function currentWebhooks() {
    return queryClient.getQueryState(collectionKey)?.status === 'error' ? undefined
      : collectionAccess.currentData<NotificationWebhook[]>(queryClient.getQueryData(collectionKey))
  }
  function assertSavedWebhook(webhookId: string | null) {
    if (!canManageWebhooks || webhookId !== selectedWebhookIdRef.current) throw new Error('Verify the selected webhook before continuing.')
    if (webhookId && (!collectionAccess.currentData(baselineAdmission) || !currentWebhooks()?.some((row) => row.id === webhookId))) {
      throw new Error('Reload the saved webhook before continuing. Your local draft is kept.')
    }
  }
  const saveWebhook = useMutation({
    mutationKey: ['notifications', 'webhooks', 'save'],
    onMutate: () => collectionAccess.captureResult(),
    mutationFn: ({ payload, webhookId }: { payload: NotificationWebhookWriteRequest; webhookId: string | null; submittedDraft: typeof draft }) => {
      assertSavedWebhook(webhookId)
      return apiFetch<NotificationWebhook>(webhookId ? `/notifications/webhooks/${webhookId}` : '/notifications/webhooks', {
        method: webhookId ? 'PATCH' : 'POST', body: JSON.stringify(payload),
      })
    },
    onSuccess: (saved, variables, capture) => {
      setFormNotice(variables.webhookId ? 'Webhook updated.' : 'Webhook created.')
      if (capture && collectionAccess.currentData(capture(saved))) {
        if (queryClient.getQueryState(collectionKey)?.status !== 'error') {
          queryClient.setQueryData<ReturnType<typeof capture<NotificationWebhook[]>>>(collectionKey, (current) => {
            const rows = collectionAccess.currentData(current)
            return rows ? capture([...rows.filter((row) => row.id !== saved.id), saved]) : current
          })
        }
        if (selectedWebhookIdRef.current === variables.webhookId) {
          const nextBaseline = createDraftFromWebhook(saved)
          setSelectedWebhookId(saved.id)
          setBaselineDraft(nextBaseline)
          setBaselineAdmission(capture(true))
          setDraftState((current) => rebaseSavedDraft(variables.submittedDraft, current, nextBaseline))
          setTestReceipt(null)
        }
      }
      void queryClient.invalidateQueries({ queryKey: collectionKey })
      void queryClient.invalidateQueries({ queryKey: ['notifications', 'analytics'] })
    },
  })

  const deleteWebhook = useMutation({
    mutationKey: ['notifications', 'webhooks', 'delete'],
    onMutate: () => collectionAccess.captureResult(),
    mutationFn: (webhookId: string) => {
      assertSavedWebhook(webhookId)
      if (!deleteIntent || deleteIntent.value.id !== webhookId || !collectionAccess.currentData(deleteIntent.admission)) throw new Error('Review the current webhook deletion again.')
      return apiFetch<void>(`/notifications/webhooks/${webhookId}`, { method: 'DELETE' })
    },
    onSuccess: (_result, webhookId, capture) => {
      if (selectedWebhookIdRef.current === webhookId && capture && collectionAccess.currentData(capture(true))) {
        setSelectedWebhookId(null)
        setDraftState(createDefaultDraft())
        setBaselineDraft(createDefaultDraft())
        setBaselineAdmission(capture(true))
        setSampleFeedId('')
      }
      setDeleteIntent(null)
      setFormNotice('Webhook deleted.')
      setTestReceipt(null)
      void queryClient.invalidateQueries({ queryKey: ['notifications', 'webhooks'] })
      void queryClient.invalidateQueries({ queryKey: ['notifications', 'analytics'] })
    },
  })

  const testWebhook = useMutation({
    mutationKey: ['notifications', 'webhooks', 'test'],
    onMutate: (variables) => ({ capture: collectionAccess.captureResult(), webhookId: variables.webhookId }),
    mutationFn: ({ webhookId, ...payload }: { webhookId: string | null; webhook: NotificationWebhookWriteRequest; sample_feed_id?: string }) => {
      assertSavedWebhook(webhookId)
      return apiFetch<NotificationWebhookTestResponse>('/notifications/webhooks/test', { method: 'POST', body: JSON.stringify(payload) })
    },
    onSuccess: (result, _variables, context) => {
      if (!context || context.webhookId !== selectedWebhookIdRef.current || !collectionAccess.currentData(context.capture(true))) return
      setTestReceipt({ value: result, admission: context.capture(true), webhookId: context.webhookId })
      setFormNotice(result.success ? 'Webhook test succeeded.' : 'Webhook test failed.')
    },
  })

  const deliveriesKey = ['notifications', 'webhooks', selectedWebhookId, 'deliveries', collectionAccess.generation] as const
  const deliveriesQuery = useQuery({
    queryKey: deliveriesKey,
    ...deliveryAccess.queryOptions(({ signal }) => apiFetch<NotificationWebhookDeliveryListResponse>(
      `/notifications/webhooks/${selectedWebhookId}/deliveries?page=1&page_size=10`, { signal },
    )),
    enabled: Boolean(selectedWebhookId) && !collectionAccess.denied,
    refetchInterval: selectedWebhookId ? DELIVERY_HISTORY_REFRESH_MS : false,
  })
  const retryDelivery = useMutation({
    mutationKey: ['notifications', 'webhooks', 'retry-delivery'],
    mutationFn: (payload: { webhookId: string; deliveryId: string }) => {
      assertSavedWebhook(payload.webhookId)
      const current = queryClient.getQueryState(deliveriesKey)?.status === 'error' ? undefined
        : deliveryAccess.currentData<NotificationWebhookDeliveryListResponse>(queryClient.getQueryData(deliveriesKey))
      if (!retryIntent || !deliveryAccess.currentData(retryIntent.admission) || !collectionAccess.currentData(retryIntent.collection)
        || retryIntent.value.id !== payload.deliveryId || !current?.deliveries.some((row) => row.id === payload.deliveryId && !row.success)) {
        throw new Error('Refresh the current delivery and review the retry again.')
      }
      return apiFetch<NotificationWebhookDelivery>(`/notifications/webhooks/${payload.webhookId}/deliveries/${payload.deliveryId}/retry`, { method: 'POST' })
    },
    onSuccess: (delivery, variables) => {
      if (selectedWebhookIdRef.current === variables.webhookId) setRetryIntent(null)
      setFormNotice(delivery.success ? 'Webhook retry succeeded.' : 'Webhook retry failed.')
      void queryClient.invalidateQueries({ queryKey: ['notifications', 'webhooks', variables.webhookId, 'deliveries'] })
      void queryClient.invalidateQueries({ queryKey: ['notifications', 'analytics'] })
    },
    onError: () => setRetryIntent(null),
  })

  const feeds = feedsQuery.data ?? []
  const webhooks = webhooksQuery.data ?? []
  const variables = variablesQuery.data ?? []
  const analytics = analyticsQuery.data
  const testableFeeds = draft.feed_scope === 'selected'
    ? feeds.filter((feed) => draft.feed_ids.includes(feed.id))
    : feeds
  const selectedWebhook = webhooks.find((webhook) => webhook.id === selectedWebhookId) ?? null
  const hasUnsavedWebhookDraftChanges = JSON.stringify(draft) !== JSON.stringify(baselineDraft)
  const confirmDiscardUnsavedWebhookChanges = useUnsavedChangesWarning(
    hasUnsavedWebhookDraftChanges || extraDirty,
    'Discard unsaved webhook or credential changes?',
  )
  const baselineAuthorized = Boolean(collectionAccess.currentData(baselineAdmission))
  const editorReadHidden = Boolean(selectedWebhookId) && collectionAccess.generation > 0 && !selectedWebhook
  const editorReadUnavailable = Boolean(selectedWebhookId) && (webhooksQuery.isError || !selectedWebhook || !baselineAuthorized)
  const visibleDraft = selectedWebhookId && !baselineAuthorized && !hasUnsavedWebhookDraftChanges ? createDefaultDraft() : draft
  const pendingWebhookDelete = admittedRecordValue(deleteIntent,
    (intent) => collectionAccess.currentData(intent.admission) && intent.value.id === selectedWebhookId)
  const pendingDeliveryRetry = admittedRecordValue(retryIntent,
    (intent) => deliveryAccess.currentData(intent.admission) && collectionAccess.currentData(intent.collection) && intent.value.webhook_id === selectedWebhookId)
  const testResult = admittedRecordValue(testReceipt,
    (receipt) => receipt.webhookId === selectedWebhookId && collectionAccess.currentData(receipt.admission))
  const retryReadUnavailable = editorReadUnavailable || deliveriesQuery.isError || !deliveriesQuery.data
  const setDraft: Dispatch<SetStateAction<NotificationWebhookDraft>> = (value) => {
    if (!canManageWebhooks || editorReadUnavailable) return
    setDraftState(value)
  }
  const setPendingWebhookDelete = (value: NotificationWebhook | null) => setDeleteIntent(value ? { value, admission: collectionAccess.captureResult()(true) } : null)
  const setPendingDeliveryRetry = (value: NotificationWebhookDelivery | null) => {
    if (value && retryReadUnavailable) return
    setRetryIntent(value ? { value, admission: deliveryAccess.captureResult()(true), collection: collectionAccess.captureResult()(true) } : null)
  }
  const adoptWebhook = useCallback((webhook: NotificationWebhook) => {
    const baseline = createDraftFromWebhook(webhook)
    setSelectedWebhookId(webhook.id)
    setDraftState(baseline)
    setBaselineDraft(baseline)
    setBaselineAdmission(collectionAccess.captureResult()(true))
  }, [collectionAccess])
  useEffect(() => {
    if (!selectedWebhook || hasUnsavedWebhookDraftChanges || baselineAuthorized) return
    adoptWebhook(selectedWebhook)
  }, [adoptWebhook, baselineAuthorized, hasUnsavedWebhookDraftChanges, selectedWebhook])
  const reloadSavedWebhook = () => {
    const row = currentWebhooks()?.find((entry) => entry.id === selectedWebhookIdRef.current)
    if (row) confirmDiscardUnsavedWebhookChanges(() => adoptWebhook(row))
    else void webhooksQuery.refetch()
  }
  const showWebhookEditor = canManageWebhooks || Boolean(selectedWebhookId)
  const webhookEditorBlockedNotice = accessNotice ?? 'Changes require permission to manage notifications.'

  useEffect(() => {
    if (sampleFeedId && !testableFeeds.some((feed) => feed.id == sampleFeedId)) {
      setSampleFeedId('')
    }
  }, [sampleFeedId, testableFeeds])

  const resetSelectionState = () => {
    setSampleFeedId('')
    setFormNotice(null)
    setTestReceipt(null)
    setPendingDeliveryRetry(null)
    retryDelivery.reset()
  }

  const onSelectWebhook = (webhook: NotificationWebhook) => {
    if (webhook.id === selectedWebhookId) return
    confirmDiscardUnsavedWebhookChanges(() => {
      const current = currentWebhooks()?.find((row) => row.id === webhook.id)
      if (!current) return
      adoptWebhook(current)
      resetSelectionState()
    })
  }

  const onCreateNewWebhook = () => {
    if (!canManageWebhooks) return
    confirmDiscardUnsavedWebhookChanges(() => {
      setSelectedWebhookId(null)
      setDraftState(createDefaultDraft())
      setBaselineDraft(createDefaultDraft())
      setBaselineAdmission(collectionAccess.captureResult()(true))
      resetSelectionState()
    })
  }

  const onRequestDeleteWebhook = (webhook: NotificationWebhook | null) => {
    if (!webhook || !canManageWebhooks || editorReadUnavailable) return
    confirmDiscardUnsavedWebhookChanges(() => setPendingWebhookDelete(webhook))
  }

  const onConfirmDeleteWebhook = () => {
    if (!pendingWebhookDelete || !canManageWebhooks || editorReadUnavailable) return
    const webhookId = pendingWebhookDelete.id
    deleteWebhook.mutate(webhookId)
  }

  const onConfirmRetryDelivery = () => {
    if (!pendingDeliveryRetry || !canManageWebhooks || retryReadUnavailable) return
    retryDelivery.mutate({ webhookId: pendingDeliveryRetry.webhook_id, deliveryId: pendingDeliveryRetry.id })
  }

  const onSave = () => {
    if (!canManageWebhooks || editorReadUnavailable) return
    const validation = validateConditions(draft.conditions ?? null)
    if (validation) { setFormNotice(validation); return }
    const normalizedDraft = normalizeDraftUrlQuery(draft)
    setDraftState(normalizedDraft)
    setFormNotice(null)
    saveWebhook.mutate({ payload: createRequestFromDraft(normalizedDraft), webhookId: selectedWebhookId, submittedDraft: normalizedDraft })
  }

  const onTest = () => {
    if (!canManageWebhooks || editorReadUnavailable) return
    const validation = validateConditions(draft.conditions ?? null)
    if (validation) { setFormNotice(validation); return }
    const normalizedDraft = normalizeDraftUrlQuery(draft)
    setDraftState(normalizedDraft)
    setFormNotice(null)
    testWebhook.mutate({
      webhookId: selectedWebhookId,
      webhook: createRequestFromDraft(normalizedDraft),
      sample_feed_id:
        sampleFeedId || (normalizedDraft.feed_scope === 'selected' ? normalizedDraft.feed_ids[0] : undefined),
    })
  }

  return {
    currentUserQuery,
    selectedWebhookId,
    draft: visibleDraft,
    setDraft,
    sampleFeedId,
    setSampleFeedId,
    formNotice,
    testResult,
    pendingWebhookDelete,
    setPendingWebhookDelete,
    pendingDeliveryRetry,
    setPendingDeliveryRetry,
    mobileVariablesOpen,
    setMobileVariablesOpen,
    isReadOnly,
    canManageWebhooks,
    availableEventOptions,
    unavailableDailyBriefSelected,
    unavailableReportSelected,
    accessNotice,
    editorReadUnavailable,
    editorReadHidden,
    retryReadUnavailable,
    baselineAuthorized,
    reloadSavedWebhook,
    webhooksQuery,
    feedsQuery,
    variablesQuery,
    analyticsQuery,
    saveWebhook,
    deleteWebhook,
    testWebhook,
    deliveriesQuery,
    retryDelivery,
    feeds,
    webhooks,
    variables,
    analytics,
    testableFeeds,
    showWebhookEditor,
    webhookEditorBlockedNotice,
    confirmDiscardUnsavedWebhookChanges,
    onSelectWebhook,
    onCreateNewWebhook,
    onRequestDeleteWebhook,
    onConfirmDeleteWebhook,
    onConfirmRetryDelivery,
    onSave,
    onTest,
  }
}

export type NotificationWebhooksController = ReturnType<typeof useNotificationWebhooksController>
