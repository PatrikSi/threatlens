import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
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
} from './notificationWebhookDraft'

const DELIVERY_HISTORY_REFRESH_MS = 30_000

export function useNotificationWebhooksController() {
  const queryClient = useQueryClient()
  const currentUserQuery = useCurrentUser()
  const [selectedWebhookId, setSelectedWebhookId] = useState<string | null>(null)
  const [draft, setDraft] = useState(createDefaultDraft)
  const [sampleFeedId, setSampleFeedId] = useState('')
  const [formNotice, setFormNotice] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<NotificationWebhookTestResponse | null>(null)
  const [pendingWebhookDelete, setPendingWebhookDelete] = useState<NotificationWebhook | null>(null)
  const [pendingDeliveryRetry, setPendingDeliveryRetry] = useState<NotificationWebhookDelivery | null>(null)
  const [mobileVariablesOpen, setMobileVariablesOpen] = useState(false)

  const canManageWebhooks = hasRequiredPermissions(
    currentUserQuery.data?.access?.permissions ?? [],
    ['write:notifications'],
  )
  const isReadOnlyViewer = !currentUserQuery.isLoading && !canManageWebhooks
  const { availableEventOptions, unavailableDailyBriefSelected, unavailableReportSelected } = resolveNotificationEventAvailability(
    currentUserQuery.data?.features.ai_daily_brief_enabled === true,
    draft.event_type,
    currentUserQuery.data?.features.ai_reporting_enabled === true,
  )
  const accessNotice = isReadOnlyViewer
    ? 'You can review these webhooks, but changes require permission to manage notifications.'
    : null

  const webhooksQuery = useQuery({
    queryKey: ['notifications', 'webhooks'],
    queryFn: () => apiFetch<NotificationWebhook[]>('/notifications/webhooks'),
  })
  const feedsQuery = useQuery({
    queryKey: ['feeds'],
    queryFn: () => apiFetch<Feed[]>('/feeds'),
  })
  const variablesQuery = useQuery({
    queryKey: ['notifications', 'template-variables'],
    queryFn: () => apiFetch<NotificationTemplateVariable[]>('/notifications/template-variables'),
  })
  const analyticsQuery = useQuery({
    queryKey: ['notifications', 'analytics'],
    queryFn: () => apiFetch<NotificationAnalyticsResponse>('/notifications/analytics'),
    refetchInterval: DELIVERY_HISTORY_REFRESH_MS,
  })

  const saveWebhook = useMutation({
    mutationKey: ['notifications', 'webhooks', 'save'],
    mutationFn: (payload: NotificationWebhookWriteRequest) => {
      if (selectedWebhookId) {
        return apiFetch<NotificationWebhook>(`/notifications/webhooks/${selectedWebhookId}`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        })
      }
      return apiFetch<NotificationWebhook>('/notifications/webhooks', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
    },
    onSuccess: (saved) => {
      setSelectedWebhookId(saved.id)
      setDraft(createDraftFromWebhook(saved))
      setFormNotice(selectedWebhookId ? 'Webhook updated.' : 'Webhook created.')
      setTestResult(null)
      void queryClient.invalidateQueries({ queryKey: ['notifications', 'webhooks'] })
      void queryClient.invalidateQueries({ queryKey: ['notifications', 'analytics'] })
    },
  })

  const deleteWebhook = useMutation({
    mutationKey: ['notifications', 'webhooks', 'delete'],
    mutationFn: (webhookId: string) => apiFetch<void>(`/notifications/webhooks/${webhookId}`, { method: 'DELETE' }),
    onSuccess: () => {
      setSelectedWebhookId(null)
      setDraft(createDefaultDraft())
      setSampleFeedId('')
      setFormNotice('Webhook deleted.')
      setTestResult(null)
      void queryClient.invalidateQueries({ queryKey: ['notifications', 'webhooks'] })
      void queryClient.invalidateQueries({ queryKey: ['notifications', 'analytics'] })
    },
  })

  const testWebhook = useMutation({
    mutationKey: ['notifications', 'webhooks', 'test'],
    mutationFn: (payload: { webhook: NotificationWebhookWriteRequest; sample_feed_id?: string }) =>
      apiFetch<NotificationWebhookTestResponse>('/notifications/webhooks/test', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    onSuccess: (result) => {
      setTestResult(result)
      setFormNotice(result.success ? 'Webhook test succeeded.' : 'Webhook test failed.')
    },
  })

  const deliveriesQuery = useQuery({
    queryKey: ['notifications', 'webhooks', selectedWebhookId, 'deliveries'],
    queryFn: () =>
      apiFetch<NotificationWebhookDeliveryListResponse>(
        `/notifications/webhooks/${selectedWebhookId}/deliveries?page=1&page_size=10`,
      ),
    enabled: Boolean(selectedWebhookId),
    refetchInterval: selectedWebhookId ? DELIVERY_HISTORY_REFRESH_MS : false,
  })

  const retryDelivery = useMutation({
    mutationKey: ['notifications', 'webhooks', 'retry-delivery'],
    mutationFn: (payload: { webhookId: string; deliveryId: string }) =>
      apiFetch<NotificationWebhookDelivery>(
        `/notifications/webhooks/${payload.webhookId}/deliveries/${payload.deliveryId}/retry`,
        { method: 'POST' },
      ),
    onSuccess: (delivery) => {
      setPendingDeliveryRetry(null)
      setFormNotice(delivery.success ? 'Webhook retry succeeded.' : 'Webhook retry failed.')
      void queryClient.invalidateQueries({
        queryKey: ['notifications', 'webhooks', delivery.webhook_id, 'deliveries'],
      })
      void queryClient.invalidateQueries({ queryKey: ['notifications', 'analytics'] })
    },
    onError: () => setPendingDeliveryRetry(null),
  })

  const feeds = feedsQuery.data ?? []
  const webhooks = webhooksQuery.data ?? []
  const variables = variablesQuery.data ?? []
  const analytics = analyticsQuery.data
  const testableFeeds = draft.feed_scope === 'selected'
    ? feeds.filter((feed) => draft.feed_ids.includes(feed.id))
    : feeds
  const selectedWebhook = webhooks.find((webhook) => webhook.id === selectedWebhookId) ?? null
  const baselineDraft = selectedWebhook ? createDraftFromWebhook(selectedWebhook) : createDefaultDraft()
  const hasUnsavedWebhookDraftChanges = JSON.stringify(draft) !== JSON.stringify(baselineDraft)
  const confirmDiscardUnsavedWebhookChanges = useUnsavedChangesWarning(
    hasUnsavedWebhookDraftChanges,
    'Discard unsaved webhook changes?',
  )
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
    setTestResult(null)
    setPendingDeliveryRetry(null)
    retryDelivery.reset()
  }

  const onSelectWebhook = (webhook: NotificationWebhook) => {
    if (webhook.id === selectedWebhookId) return
    confirmDiscardUnsavedWebhookChanges(() => {
      setSelectedWebhookId(webhook.id)
      setDraft(createDraftFromWebhook(webhook))
      resetSelectionState()
    })
  }

  const onCreateNewWebhook = () => {
    if (!canManageWebhooks) return
    confirmDiscardUnsavedWebhookChanges(() => {
      setSelectedWebhookId(null)
      setDraft(createDefaultDraft())
      resetSelectionState()
    })
  }

  const onRequestDeleteWebhook = (webhook: NotificationWebhook | null) => {
    if (!webhook || !canManageWebhooks) return
    confirmDiscardUnsavedWebhookChanges(() => setPendingWebhookDelete(webhook))
  }

  const onConfirmDeleteWebhook = () => {
    if (!pendingWebhookDelete || !canManageWebhooks) return
    const webhookId = pendingWebhookDelete.id
    setPendingWebhookDelete(null)
    deleteWebhook.mutate(webhookId)
  }

  const onConfirmRetryDelivery = () => {
    if (!pendingDeliveryRetry || !canManageWebhooks) return
    retryDelivery.mutate({ webhookId: pendingDeliveryRetry.webhook_id, deliveryId: pendingDeliveryRetry.id })
  }

  const onSave = () => {
    if (!canManageWebhooks) return
    const normalizedDraft = normalizeDraftUrlQuery(draft)
    setDraft(normalizedDraft)
    setFormNotice(null)
    saveWebhook.mutate(createRequestFromDraft(normalizedDraft))
  }

  const onTest = () => {
    if (!canManageWebhooks) return
    const normalizedDraft = normalizeDraftUrlQuery(draft)
    setDraft(normalizedDraft)
    setFormNotice(null)
    testWebhook.mutate({
      webhook: createRequestFromDraft(normalizedDraft),
      sample_feed_id:
        sampleFeedId || (normalizedDraft.feed_scope === 'selected' ? normalizedDraft.feed_ids[0] : undefined),
    })
  }

  return {
    currentUserQuery,
    selectedWebhookId,
    draft,
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
    isReadOnlyViewer,
    canManageWebhooks,
    availableEventOptions,
    unavailableDailyBriefSelected,
    unavailableReportSelected,
    accessNotice,
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
