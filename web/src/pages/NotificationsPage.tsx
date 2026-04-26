import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { formatDateTime } from '../utils/datetime'
import {
  Feed,
  NotificationAnalyticsResponse,
  NotificationEventType,
  NotificationQueueSnapshot,
  NotificationTemplateVariable,
  NotificationWebhookDelivery,
  NotificationWebhookDeliveryListResponse,
  NotificationWebhook,
  NotificationWebhookField,
  NotificationWebhookPolicy,
  NotificationWebhookTestResponse,
  NotificationWebhookWriteRequest,
} from '../types/api'

type NotificationWebhookDraft = Omit<NotificationWebhookWriteRequest, 'body_template'> & {
  body_template: string
  content_type: string
}

const DELIVERY_HISTORY_REFRESH_MS = 30_000

const EVENT_OPTIONS: Array<{ value: NotificationEventType; label: string; description: string }> = [
  { value: 'rss_item_new', label: 'New RSS Item', description: 'Fire when a new RSS item is ingested from a feed.' },
  { value: 'alert_match', label: 'Alert Match', description: 'Fire when an item matches one or more of your alert interests.' },
  { value: 'feed_failing', label: 'Feed Failing', description: 'Fire when a feed hits repeated fetch failures.' },
  { value: 'webhook_failed', label: 'Webhook Failed', description: 'Fire when one of your other webhook deliveries fails.' },
  { value: 'daily_digest', label: 'Daily Digest', description: 'Send a once-per-day digest of the last 24 hours of matching items.' },
]

const EVENT_DEFAULT_JSON_FIELDS: Record<NotificationEventType, NotificationWebhookField[]> = {
  rss_item_new: [
    { key: 'event.type', value: '{{ event.type }}' },
    { key: 'item.title', value: '{{ item.title }}' },
    { key: 'item.url', value: '{{ item.url }}' },
    { key: 'feed.name', value: '{{ feed.name }}' },
  ],
  alert_match: [
    { key: 'event.type', value: '{{ event.type }}' },
    { key: 'alert.primary_name', value: '{{ alert.primary_name }}' },
    { key: 'alert.matched_keywords', value: '{{ alert.matched_keywords }}' },
    { key: 'item.title', value: '{{ item.title }}' },
  ],
  feed_failing: [
    { key: 'event.type', value: '{{ event.type }}' },
    { key: 'feed.name', value: '{{ feed.name }}' },
    { key: 'feed.error_count', value: '{{ feed.error_count }}' },
    { key: 'feed.last_error', value: '{{ feed.last_error }}' },
  ],
  webhook_failed: [
    { key: 'event.type', value: '{{ event.type }}' },
    { key: 'failed_webhook.name', value: '{{ failed_webhook.name }}' },
    { key: 'failed_webhook.event_type', value: '{{ failed_webhook.event_type }}' },
    { key: 'failed_webhook.error', value: '{{ failed_webhook.error }}' },
  ],
  daily_digest: [
    { key: 'event.type', value: '{{ event.type }}' },
    { key: 'digest.total_items', value: '{{ digest.total_items }}' },
    { key: 'digest.total_feeds', value: '{{ digest.total_feeds }}' },
    { key: 'digest.feed_names', value: '{{ digest.feed_names }}' },
  ],
}

export function NotificationsPage() {
  const queryClient = useQueryClient()
  const currentUserQuery = useCurrentUser()
  const [selectedWebhookId, setSelectedWebhookId] = useState<string | null>(null)
  const [draft, setDraft] = useState<NotificationWebhookDraft>(() => createDefaultDraft())
  const [sampleFeedId, setSampleFeedId] = useState('')
  const [formNotice, setFormNotice] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<NotificationWebhookTestResponse | null>(null)
  const [pendingWebhookDelete, setPendingWebhookDelete] = useState<NotificationWebhook | null>(null)
  const [pendingDeliveryRetry, setPendingDeliveryRetry] = useState<NotificationWebhookDelivery | null>(null)
  const currentUserRole = currentUserQuery.data?.role
  const isReadOnlyViewer = currentUserRole === 'viewer' || (!currentUserRole && !currentUserQuery.isLoading)

  const webhookPolicyQuery = useQuery({
    queryKey: ['notifications', 'webhook-policy'],
    queryFn: () => apiFetch<NotificationWebhookPolicy>('/notifications/webhook-policy'),
  })

  const policyErrorNotice = webhookPolicyQuery.isError
    ? resolveApiMessage(
        webhookPolicyQuery.error,
        'Failed to load webhook policy. Webhook writes are disabled until the policy can be checked.',
      )
    : null
  const canManageWebhooks = webhookPolicyQuery.data?.can_manage_webhooks ?? false
  const accessNotice = policyErrorNotice ?? webhookPolicyQuery.data?.reason ?? null

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

  const onConfirmDeleteWebhook = () => {
    if (!pendingWebhookDelete || !canManageWebhooks) {
      return
    }

    const webhookId = pendingWebhookDelete.id
    setPendingWebhookDelete(null)
    deleteWebhook.mutate(webhookId)
  }

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
      void queryClient.invalidateQueries({ queryKey: ['notifications', 'webhooks', delivery.webhook_id, 'deliveries'] })
      void queryClient.invalidateQueries({ queryKey: ['notifications', 'analytics'] })
    },
    onError: () => {
      setPendingDeliveryRetry(null)
    },
  })

  const feeds = feedsQuery.data ?? []
  const webhooks = webhooksQuery.data ?? []
  const variables = variablesQuery.data ?? []
  const analytics = analyticsQuery.data
  const testableFeeds = draft.feed_scope === 'selected' ? feeds.filter((feed) => draft.feed_ids.includes(feed.id)) : feeds
  const selectedWebhook = webhooks.find((webhook) => webhook.id === selectedWebhookId) ?? null
  const baselineDraft = selectedWebhook ? createDraftFromWebhook(selectedWebhook) : createDefaultDraft()
  const hasUnsavedWebhookDraftChanges = JSON.stringify(draft) !== JSON.stringify(baselineDraft)
  const confirmDiscardUnsavedWebhookChanges = useUnsavedChangesWarning(
    hasUnsavedWebhookDraftChanges,
    'Discard unsaved webhook changes?',
  )

  useEffect(() => {
    if (!sampleFeedId) {
      return
    }

    if (!testableFeeds.some((feed) => feed.id == sampleFeedId)) {
      setSampleFeedId('')
    }
  }, [sampleFeedId, testableFeeds])

  const onSelectWebhook = (webhook: NotificationWebhook) => {
    if (webhook.id === selectedWebhookId) {
      return
    }
    confirmDiscardUnsavedWebhookChanges(() => {
      setSelectedWebhookId(webhook.id)
      setDraft(createDraftFromWebhook(webhook))
      setSampleFeedId('')
      setFormNotice(null)
      setTestResult(null)
      setPendingDeliveryRetry(null)
      retryDelivery.reset()
    })
  }

  const onCreateNewWebhook = () => {
    if (!canManageWebhooks) {
      return
    }
    confirmDiscardUnsavedWebhookChanges(() => {
      setSelectedWebhookId(null)
      setDraft(createDefaultDraft())
      setSampleFeedId('')
      setFormNotice(null)
      setTestResult(null)
      setPendingDeliveryRetry(null)
      retryDelivery.reset()
    })
  }

  const onConfirmRetryDelivery = () => {
    if (!pendingDeliveryRetry || !canManageWebhooks) {
      return
    }

    const { webhook_id: webhookId, id: deliveryId } = pendingDeliveryRetry
    retryDelivery.mutate({ webhookId, deliveryId })
  }

  const onRequestDeleteWebhook = (webhook: NotificationWebhook | null) => {
    if (!webhook || !canManageWebhooks) {
      return
    }

    confirmDiscardUnsavedWebhookChanges(() => {
      setPendingWebhookDelete(webhook)
    })
  }

  const onSave = () => {
    if (!canManageWebhooks) {
      return
    }
    const normalizedDraft = normalizeDraftUrlQuery(draft)
    setDraft(normalizedDraft)
    setFormNotice(null)
    saveWebhook.mutate(createRequestFromDraft(normalizedDraft))
  }

  const onTest = () => {
    if (!canManageWebhooks) {
      return
    }
    const normalizedDraft = normalizeDraftUrlQuery(draft)
    setDraft(normalizedDraft)
    setFormNotice(null)
    testWebhook.mutate({
      webhook: createRequestFromDraft(normalizedDraft),
      sample_feed_id: sampleFeedId || (normalizedDraft.feed_scope === 'selected' ? normalizedDraft.feed_ids[0] : undefined),
    })
  }

  if (currentUserQuery.isLoading || webhookPolicyQuery.isLoading) {
    return (
      <div className="rounded-xl border border-slate/20 bg-white/80 p-4 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90">
        Loading notification settings...
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Automation</p>
        <h2 className="mt-1 font-display text-xl">Webhook Notifications</h2>
        <p className="mt-1 text-sm text-slate dark:text-white/75">
          Configure outbound webhooks for new RSS items, alert matches, feed failures, failed deliveries, and daily digests.
        </p>
        <p className="mt-2 text-xs text-slate dark:text-white/60">
          Variables use `{'{{ item.title }}'}` style placeholders, similar to Grafana-style notification templates.
        </p>
        {accessNotice && (
          <div
            role={policyErrorNotice ? 'alert' : 'status'}
            aria-live={policyErrorNotice ? 'assertive' : 'polite'}
            aria-atomic="true"
            className={`mt-3 rounded-lg border px-3 py-2 text-sm ${
              policyErrorNotice
                ? 'border-red-200 bg-red-50 text-red-700 dark:border-red-900/50 dark:bg-red-950/35 dark:text-red-200'
                : canManageWebhooks
                ? 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/35 dark:text-amber-200'
                : 'border-slate/20 bg-slate/5 text-slate dark:border-white/10 dark:bg-white/[0.04] dark:text-white/70'
            }`}
          >
            {accessNotice}
          </div>
        )}
      </section>

      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="font-display text-lg">Notification Analytics</h3>
            <p className="mt-1 text-sm text-slate dark:text-white/75">
              Track delivery health across all of your notification webhooks.
            </p>
          </div>
          {analyticsQuery.isLoading && <span className="text-sm text-slate dark:text-white/70">Loading analytics...</span>}
        </div>

        {analyticsQuery.isError && (
          <p className="mt-3 text-sm text-red-600">{resolveApiMessage(analyticsQuery.error, 'Failed to load notification analytics.')}</p>
        )}

        {analytics && (
          <div className="mt-4 space-y-4">
            {analytics.queue.status !== 'healthy' && (
              <div className={`rounded-lg border px-4 py-3 text-sm ${
                analytics.queue.status === 'critical'
                  ? 'border-red-200 bg-red-50 text-red-800 dark:border-red-900/50 dark:bg-red-950/35 dark:text-red-200'
                  : 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/35 dark:text-amber-200'
              }`}>
                <p className="font-semibold">Notification queue needs attention</p>
                <p className="mt-1">
                  {describeQueueStatusMessage(analytics.queue)}
                </p>
              </div>
            )}

            <div className="grid gap-3 md:grid-cols-5">
              <MetricCard label="Total Deliveries" value={String(analytics.total_deliveries)} />
              <MetricCard label="Success Rate" value={`${analytics.success_rate_pct.toFixed(1)}%`} />
              <MetricCard label="Failures 24h" value={String(analytics.failures_last_24h)} />
              <MetricCard label="Queue Status" value={describeQueueStatusLabel(analytics.queue)} />
              <MetricCard
                label="Most Failing Webhook"
                value={analytics.most_failing_webhook ? analytics.most_failing_webhook.webhook_name : 'None'}
              />
            </div>

            <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
              <div className="rounded-lg border border-slate/20 p-4 dark:border-cyan-900/40">
                <h4 className="font-semibold">Event Breakdown</h4>
                <div className="mt-3 space-y-2">
                  {analytics.events.length ? (
                    analytics.events.map((eventSummary) => (
                      <div key={eventSummary.event_type} className="flex items-center justify-between gap-3 rounded-lg bg-slate/5 px-3 py-2 dark:bg-white/5">
                        <div>
                          <p className="text-sm font-semibold">{describeEventType(eventSummary.event_type)}</p>
                          <p className="text-xs text-slate dark:text-white/60">
                            {eventSummary.failed_deliveries} failed of {eventSummary.total_deliveries}
                          </p>
                        </div>
                        <p className="text-sm font-semibold">{formatFailureRate(eventSummary.failed_deliveries, eventSummary.total_deliveries)}</p>
                      </div>
                    ))
                  ) : (
                    <p className="text-sm text-slate dark:text-white/70">No deliveries recorded yet.</p>
                  )}
                </div>
              </div>

              <div className="rounded-lg border border-slate/20 p-4 dark:border-cyan-900/40">
                <div className="flex items-center justify-between gap-3">
                  <h4 className="font-semibold">Delivery Queue</h4>
                  <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${queueStatusBadgeClass(analytics.queue)}`}>
                    {describeQueueStatusLabel(analytics.queue)}
                  </span>
                </div>
                <div className="mt-3 space-y-2 text-sm">
                  <p>
                    Pending deliveries: <span className="font-semibold">{analytics.queue.pending_deliveries}</span>
                  </p>
                  <p>
                    In-flight deliveries: <span className="font-semibold">{analytics.queue.sending_deliveries}</span>
                  </p>
                  <p>
                    Stale claims: <span className="font-semibold">{analytics.queue.stale_sending_deliveries}</span>
                  </p>
                  {analytics.queue.oldest_pending_age_seconds != null && (
                    <p className="text-xs text-slate dark:text-white/60">
                      Oldest pending age: {formatAgeSeconds(analytics.queue.oldest_pending_age_seconds)}
                    </p>
                  )}
                  {analytics.queue.oldest_sending_age_seconds != null && (
                    <p className="text-xs text-slate dark:text-white/60">
                      Oldest in-flight age: {formatAgeSeconds(analytics.queue.oldest_sending_age_seconds)}
                    </p>
                  )}
                  <p className="text-xs text-slate dark:text-white/60">
                    Queue enters degraded state after {formatAgeSeconds(analytics.queue.degraded_after_seconds)}.
                  </p>
                </div>
              </div>
            </div>
          </div>
        )}
      </section>

      <div className="grid gap-4 xl:grid-cols-[320px_1fr]">
        <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
          <div className="flex items-center justify-between gap-3">
            <h3 className="font-display text-lg">Saved Webhooks</h3>
            {canManageWebhooks && (
              <button
                className="rounded border border-slate/30 px-3 py-1.5 text-sm font-semibold dark:border-cyan-900/40"
                onClick={onCreateNewWebhook}
              >
                New webhook
              </button>
            )}
          </div>

          <div className="mt-3 max-h-[28rem] overflow-auto rounded-lg border border-slate/20 dark:border-cyan-900/40">
            {webhooks.map((webhook) => {
              const selected = webhook.id === selectedWebhookId
              return (
                <button
                  key={webhook.id}
                  type="button"
                  aria-pressed={selected}
                  className={`grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-slate/10 px-3 py-2 text-left text-sm transition last:border-b-0 dark:border-cyan-900/30 ${
                    selected
                      ? 'bg-cyan/10 dark:bg-cyan-950/50'
                      : 'hover:bg-slate/5 dark:hover:bg-white/[0.03]'
                  }`}
                  onClick={() => onSelectWebhook(webhook)}
                >
                  <div className="min-w-0">
                    <p className="truncate font-semibold">{webhook.name}</p>
                    <p className="mt-0.5 truncate text-xs text-slate dark:text-white/65">
                      {describeEventType(webhook.event_type)} · {webhook.method} · {describeFeedScope(webhook.feed_scope, webhook.feed_ids.length)}
                    </p>
                    <p className="mt-0.5 truncate text-xs text-slate dark:text-white/55">{webhook.url_template}</p>
                  </div>
                  <span
                    className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                      webhook.enabled
                        ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
                        : 'bg-slate/10 text-slate-700 dark:bg-white/10 dark:text-white/65'
                    }`}
                  >
                    {webhook.enabled ? 'Enabled' : 'Disabled'}
                  </span>
                </button>
              )
            })}
          </div>

          {webhooksQuery.isLoading && <p className="mt-3 text-sm text-slate dark:text-white/70">Loading webhooks...</p>}
          {webhooksQuery.isError && <p className="mt-3 text-sm text-red-600">{resolveApiMessage(webhooksQuery.error, 'Failed to load webhooks.')}</p>}
          {!webhooksQuery.isLoading && !webhooks.length && (
            <p className="mt-3 rounded-lg border border-dashed border-slate/25 p-3 text-sm text-slate dark:border-cyan-900/40 dark:text-white/70">
              No webhooks yet. Create one to call external systems when ThreatLens sees new items, alert matches, feed failures, or digest windows.
            </p>
          )}
        </section>

        <div className="space-y-4">
          <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="font-display text-lg">{selectedWebhookId ? 'Edit Webhook' : 'Create Webhook'}</h3>
                <p className="mt-1 text-sm text-slate dark:text-white/75">{describeEventDescription(draft.event_type)}</p>
              </div>
              <label
                className={`flex items-center gap-2 rounded-full border px-3 py-1 text-sm ${
                  canManageWebhooks
                    ? 'border-slate/20 dark:border-cyan-900/40'
                    : 'border-slate/15 text-slate/70 dark:border-white/10 dark:text-white/55'
                }`}
              >
                <input
                  type="checkbox"
                  checked={draft.enabled}
                  disabled={!canManageWebhooks}
                  onChange={(event) => setDraft((current) => ({ ...current, enabled: event.target.checked }))}
                />
                Enabled
              </label>
            </div>

            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <div>
                <label htmlFor="notification-webhook-name" className="text-sm font-semibold">
                  Name
                </label>
                <input
                  id="notification-webhook-name"
                  className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
                  disabled={!canManageWebhooks}
                  value={draft.name}
                  onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))}
                  placeholder="Slack ingest webhook"
                />
              </div>
              <div>
                <label htmlFor="notification-webhook-event-type" className="text-sm font-semibold">
                  Event Type
                </label>
                <select
                  id="notification-webhook-event-type"
                  className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
                  disabled={!canManageWebhooks}
                  value={draft.event_type}
                  onChange={(event) =>
                    setDraft((current) => applyEventType(current, event.target.value as NotificationEventType))
                  }
                >
                  {EVENT_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <p className="mt-1 text-xs text-slate dark:text-white/60">{describeEventDescription(draft.event_type)}</p>
              </div>
              <div>
                <label htmlFor="notification-webhook-method" className="text-sm font-semibold">
                  HTTP Method
                </label>
                <select
                  id="notification-webhook-method"
                  className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
                  disabled={!canManageWebhooks}
                  value={draft.method}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      method: event.target.value as NotificationWebhookDraft['method'],
                    }))
                  }
                >
                  <option value="POST">POST</option>
                  <option value="PUT">PUT</option>
                  <option value="PATCH">PATCH</option>
                  <option value="GET">GET</option>
                  <option value="DELETE">DELETE</option>
                </select>
              </div>
              <div className="md:col-span-2">
                <label htmlFor="notification-webhook-url" className="text-sm font-semibold">
                  Webhook URL
                </label>
                <input
                  id="notification-webhook-url"
                  className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
                  disabled={!canManageWebhooks}
                  value={draft.url_template}
                  onChange={(event) => setDraft((current) => ({ ...current, url_template: event.target.value }))}
                  onBlur={() => setDraft((current) => normalizeDraftUrlQuery(current))}
                  placeholder="https://hooks.example.com/notify?source={{ feed.name }}"
                />
                <p className="mt-1 text-xs text-slate dark:text-white/60">
                  Query strings like `?token=abc` are automatically moved into Query Parameters.
                </p>
              </div>
              <div>
                <label htmlFor="notification-webhook-timeout" className="text-sm font-semibold">
                  Timeout (seconds)
                </label>
                <input
                  id="notification-webhook-timeout"
                  className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
                  type="number"
                  min={1}
                  max={60}
                  disabled={!canManageWebhooks}
                  value={draft.timeout_seconds}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      timeout_seconds: Number(event.target.value) || 10,
                    }))
                  }
                />
              </div>
              <div>
                <label htmlFor="notification-webhook-body-mode" className="text-sm font-semibold">
                  Body Mode
                </label>
                <select
                  id="notification-webhook-body-mode"
                  className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
                  disabled={!canManageWebhooks}
                  value={draft.body_mode}
                  onChange={(event) => setDraft((current) => applyBodyMode(current, event.target.value as NotificationWebhookDraft['body_mode']))}
                >
                  <option value="json">JSON object</option>
                  <option value="form">Form fields</option>
                  <option value="raw">Raw body</option>
                  <option value="none">No body</option>
                </select>
              </div>
              <div>
                <label htmlFor="notification-webhook-content-type" className="text-sm font-semibold">
                  Content Type
                </label>
                <input
                  id="notification-webhook-content-type"
                  className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
                  list="notification-content-types"
                  disabled={!canManageWebhooks}
                  value={draft.content_type}
                  onChange={(event) => setDraft((current) => ({ ...current, content_type: event.target.value }))}
                  placeholder={`Auto (${resolveDefaultContentTypeLabel(draft.body_mode)})`}
                />
                <datalist id="notification-content-types">
                  <option value="application/json" />
                  <option value="application/x-www-form-urlencoded" />
                  <option value="text/plain; charset=utf-8" />
                  <option value="text/markdown; charset=utf-8" />
                </datalist>
                <p className="mt-1 text-xs text-slate dark:text-white/60">
                  Leave blank to use the default for the selected body mode.
                </p>
              </div>
            </div>

            <div className="mt-5 rounded-lg border border-slate/20 p-4 dark:border-cyan-900/40">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h4 className="font-semibold">Feed Scope</h4>
                  <p className="mt-1 text-xs text-slate dark:text-white/65">Choose whether this webhook fires for any feed or only selected feeds.</p>
                </div>
                <div
                  role="group"
                  aria-label="Webhook feed scope"
                  className={`flex rounded-lg border p-1 ${
                    canManageWebhooks ? 'border-slate/20 dark:border-cyan-900/40' : 'border-slate/15 dark:border-white/10'
                  }`}
                >
                  <button
                    type="button"
                    aria-pressed={draft.feed_scope === 'all'}
                    disabled={!canManageWebhooks}
                    className={`rounded px-3 py-1 text-sm disabled:cursor-not-allowed disabled:opacity-50 ${
                      draft.feed_scope === 'all' ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]' : 'text-slate dark:text-white/75'
                    }`}
                    onClick={() => setDraft((current) => ({ ...current, feed_scope: 'all', feed_ids: [] }))}
                  >
                    Any feed
                  </button>
                  <button
                    type="button"
                    aria-pressed={draft.feed_scope === 'selected'}
                    disabled={!canManageWebhooks}
                    className={`rounded px-3 py-1 text-sm disabled:cursor-not-allowed disabled:opacity-50 ${
                      draft.feed_scope === 'selected'
                        ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]'
                        : 'text-slate dark:text-white/75'
                    }`}
                    onClick={() => setDraft((current) => ({ ...current, feed_scope: 'selected' }))}
                  >
                    Selected feeds
                  </button>
                </div>
              </div>

              {draft.feed_scope === 'selected' && (
                <div className="mt-4 grid gap-2 md:grid-cols-2">
                  {feeds.map((feed) => {
                    const checked = draft.feed_ids.includes(feed.id)
                    return (
                      <label
                        key={feed.id}
                        className={`flex items-start gap-3 rounded border p-3 text-sm ${
                          canManageWebhooks ? 'border-slate/20 dark:border-cyan-900/40' : 'border-slate/15 dark:border-white/10'
                        }`}
                      >
                        <input
                          className="mt-1"
                          type="checkbox"
                          checked={checked}
                          disabled={!canManageWebhooks}
                          onChange={() => setDraft((current) => toggleFeedSelection(current, feed.id))}
                        />
                        <span>
                          <span className="block font-semibold">{feed.name}</span>
                          <span className="text-xs text-slate dark:text-white/60">{feed.url}</span>
                        </span>
                      </label>
                    )
                  })}
                  {feedsQuery.isLoading && <p className="text-sm text-slate dark:text-white/70">Loading feeds...</p>}
                  {feedsQuery.isError && <p className="text-sm text-red-600">{resolveApiMessage(feedsQuery.error, 'Failed to load feeds.')}</p>}
                </div>
              )}
            </div>

            <div className="mt-5 grid gap-4 xl:grid-cols-2">
              <KeyValueEditor
                title="Query Parameters"
                description="Append rendered arguments to the webhook URL."
                fields={draft.query_params}
                addLabel="Add parameter"
                keyPlaceholder="title"
                valuePlaceholder="{{ item.title }}"
                disabled={!canManageWebhooks}
                onChange={(fields) => setDraft((current) => ({ ...current, query_params: fields }))}
              />
              <KeyValueEditor
                title="Headers"
                description="Custom headers such as authorization, routing, or tenant IDs. Content-Type is managed separately above."
                fields={draft.headers}
                addLabel="Add header"
                keyPlaceholder="Authorization"
                valuePlaceholder="Bearer {{ user.email }}"
                disabled={!canManageWebhooks}
                onChange={(fields) => setDraft((current) => ({ ...current, headers: fields }))}
              />
            </div>

            {draft.body_mode === 'json' || draft.body_mode === 'form' ? (
              <div className="mt-4">
                <KeyValueEditor
                  title={draft.body_mode === 'json' ? 'JSON Body Fields' : 'Form Fields'}
                  description={
                    draft.body_mode === 'json'
                      ? 'Use dotted keys like `item.title` to build nested JSON safely.'
                      : 'Rendered form fields are sent as `application/x-www-form-urlencoded`.'
                  }
                  fields={draft.body_fields}
                  addLabel={draft.body_mode === 'json' ? 'Add JSON field' : 'Add form field'}
                  keyPlaceholder={draft.body_mode === 'json' ? 'item.title' : 'title'}
                  valuePlaceholder="{{ item.title }}"
                  disabled={!canManageWebhooks}
                  onChange={(fields) => setDraft((current) => ({ ...current, body_fields: fields }))}
                />
              </div>
            ) : null}

            {draft.body_mode === 'raw' && (
              <div className="mt-4">
                <label htmlFor="notification-webhook-raw-body" className="text-sm font-semibold">
                  Raw Body Template
                </label>
                <textarea
                  id="notification-webhook-raw-body"
                  className="mt-1 h-40 w-full rounded border border-slate/30 bg-white px-3 py-2 font-mono text-sm disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
                  disabled={!canManageWebhooks}
                  value={draft.body_template}
                  onChange={(event) => setDraft((current) => ({ ...current, body_template: event.target.value }))}
                  placeholder={`{"title":"{{ item.title }}","url":"{{ item.url }}"}`}
                />
              </div>
            )}

            {!isReadOnlyViewer && (
              <div className="mt-5 flex flex-wrap items-center gap-2">
                <button
                  className="rounded bg-ink px-3 py-2 text-white disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e]"
                  disabled={saveWebhook.isPending || !canManageWebhooks}
                  onClick={onSave}
                >
                  {selectedWebhookId ? 'Save changes' : 'Create webhook'}
                </button>
                <button
                  className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-cyan-900/40"
                  disabled={testWebhook.isPending || !canManageWebhooks || (draft.feed_scope === 'selected' && !draft.feed_ids.length)}
                  onClick={onTest}
                >
                  Test webhook
                </button>
                {selectedWebhookId && (
                  <button
                    className="rounded border border-red-300 px-3 py-2 text-sm font-semibold text-red-700 disabled:opacity-50 dark:border-red-900/60 dark:text-red-300"
                    disabled={deleteWebhook.isPending || Boolean(pendingWebhookDelete) || !canManageWebhooks}
                    onClick={() => onRequestDeleteWebhook(webhooks.find((entry) => entry.id === selectedWebhookId) ?? null)}
                  >
                    Delete webhook
                  </button>
                )}
                {(draft.feed_scope === 'all' || draft.feed_ids.length > 1) && (
                  <>
                    <label htmlFor="notification-sample-feed" className="sr-only">
                      Sample feed
                    </label>
                    <select
                      id="notification-sample-feed"
                      className="rounded border border-slate/30 bg-white px-3 py-2 text-sm disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
                      value={sampleFeedId}
                      disabled={!canManageWebhooks}
                      onChange={(event) => setSampleFeedId(event.target.value)}
                    >
                      <option value="">Auto sample feed</option>
                      {testableFeeds.map((feed) => (
                        <option key={feed.id} value={feed.id}>
                          {feed.name}
                        </option>
                      ))}
                    </select>
                  </>
                )}
              </div>
            )}

            {formNotice && (
              <p
                role={testResult && !testResult.success ? 'alert' : 'status'}
                aria-live={testResult && !testResult.success ? 'assertive' : 'polite'}
                aria-atomic="true"
                className={`mt-3 text-sm ${testResult && !testResult.success ? 'text-red-600' : 'text-emerald-700 dark:text-emerald-300'}`}
              >
                {formNotice}
              </p>
            )}
            {saveWebhook.isError && (
              <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-sm text-red-600">
                {resolveApiMessage(saveWebhook.error, 'Failed to save webhook.')}
              </p>
            )}
            {deleteWebhook.isError && (
              <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-sm text-red-600">
                {resolveApiMessage(deleteWebhook.error, 'Failed to delete webhook.')}
              </p>
            )}
            {testWebhook.isError && (
              <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-sm text-red-600">
                {resolveApiMessage(testWebhook.error, 'Failed to test webhook.')}
              </p>
            )}
          </section>

          <section className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
            <div className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
              <div className="flex items-center justify-between gap-3">
                <h3 className="font-display text-lg">Test Result</h3>
                {testResult && (
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                      testResult.success
                        ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
                        : 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300'
                    }`}
                  >
                    {testResult.success ? 'Success' : 'Failed'}
                  </span>
                )}
              </div>

              {testResult ? (
                <div className="mt-3 space-y-3 text-sm">
                  <p className="text-xs text-slate dark:text-white/60">
                    Sensitive URL parameters, headers, request bodies, and response previews are redacted before display.
                  </p>
                  <div className="grid gap-3 md:grid-cols-3">
                    <MetricCard label="Method" value={testResult.rendered_method} />
                    <MetricCard label="Status" value={testResult.status_code ? String(testResult.status_code) : 'n/a'} />
                    <MetricCard label="Duration" value={testResult.duration_ms != null ? `${testResult.duration_ms} ms` : 'n/a'} />
                  </div>

                  <div>
                    <p className="text-xs font-semibold uppercase text-slate dark:text-white/60">Rendered URL</p>
                    <code className="mt-1 block rounded bg-slate/10 px-3 py-2 text-xs dark:bg-white/5">{testResult.rendered_url}</code>
                  </div>

                  {testResult.rendered_headers.length > 0 && (
                    <div>
                      <p className="text-xs font-semibold uppercase text-slate dark:text-white/60">Headers</p>
                      <div className="mt-1 space-y-1">
                        {testResult.rendered_headers.map((header, index) => (
                          <code key={`${header.key}-${index}`} className="block rounded bg-slate/10 px-3 py-2 text-xs dark:bg-white/5">
                            {header.key}: {header.value}
                          </code>
                        ))}
                      </div>
                    </div>
                  )}

                  {testResult.rendered_body && (
                    <div>
                      <p className="text-xs font-semibold uppercase text-slate dark:text-white/60">Rendered Body</p>
                      <pre className="mt-1 overflow-x-auto rounded bg-slate/10 px-3 py-2 text-xs dark:bg-white/5">{testResult.rendered_body}</pre>
                    </div>
                  )}

                  {testResult.response_body_preview && (
                    <div>
                      <p className="text-xs font-semibold uppercase text-slate dark:text-white/60">Response Preview</p>
                      <pre className="mt-1 overflow-x-auto rounded bg-slate/10 px-3 py-2 text-xs dark:bg-white/5">
                        {testResult.response_body_preview}
                      </pre>
                    </div>
                  )}

                  {testResult.error && (
                    <p role="alert" aria-live="assertive" aria-atomic="true" className="text-sm text-red-600">
                      {testResult.error}
                    </p>
                  )}
                </div>
              ) : (
                <p className="mt-3 text-sm text-slate dark:text-white/70">
                  Run a test delivery to inspect a redacted request preview and the webhook response summary.
                </p>
              )}
            </div>

            <div className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
              <h3 className="font-display text-lg">Available Variables</h3>
              <p className="mt-1 text-sm text-slate dark:text-white/75">Use these placeholders anywhere in the URL, headers, query parameters, or body.</p>

              <div className="mt-3 space-y-2">
                {variables.map((variable) => (
                  <div key={variable.key} className="rounded-lg border border-slate/20 p-3 dark:border-cyan-900/40">
                    <code className="text-xs font-semibold">{`{{ ${variable.key} }}`}</code>
                    <p className="mt-1 text-sm">{variable.description}</p>
                    <p className="mt-1 text-xs text-slate dark:text-white/60">Example: {variable.example}</p>
                  </div>
                ))}

                {variablesQuery.isLoading && <p className="text-sm text-slate dark:text-white/70">Loading variables...</p>}
                {variablesQuery.isError && (
                  <p className="text-sm text-red-600">{resolveApiMessage(variablesQuery.error, 'Failed to load template variables.')}</p>
                )}
              </div>
            </div>
          </section>

          <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="font-display text-lg">Delivery History</h3>
                <p className="mt-1 text-sm text-slate dark:text-white/75">
                  Review the last deliveries for this webhook, including the rendered request and response preview.
                </p>
              </div>
              {deliveriesQuery.data?.deliveries[0] && (
                <span
                  className={`rounded-full px-3 py-1 text-xs font-semibold ${deliveryStatusBadgeClass(
                    deliveriesQuery.data.deliveries[0],
                  )}`}
                >
                  Last status: {describeDeliveryStatus(deliveriesQuery.data.deliveries[0])}
                </span>
              )}
            </div>

            {!selectedWebhookId && (
              <p className="mt-3 text-sm text-slate dark:text-white/70">
                Select a webhook to see its recent delivery attempts.
              </p>
            )}

            {selectedWebhookId && deliveriesQuery.isLoading && (
              <p className="mt-3 text-sm text-slate dark:text-white/70">Loading delivery history...</p>
            )}

            {selectedWebhookId && deliveriesQuery.isError && (
              <p className="mt-3 text-sm text-red-600">{resolveApiMessage(deliveriesQuery.error, 'Failed to load delivery history.')}</p>
            )}

            {selectedWebhookId && retryDelivery.isError && (
              <p className="mt-3 text-sm text-red-600">{resolveApiMessage(retryDelivery.error, 'Failed to retry webhook delivery.')}</p>
            )}

            {selectedWebhookId && deliveriesQuery.data?.deliveries.length ? (
              <div className="mt-4 space-y-3">
                <div className="grid gap-3 md:grid-cols-4">
                  <MetricCard label="Attempts" value={String(deliveriesQuery.data.total)} />
                  <MetricCard
                    label="Last Code"
                    value={deliveriesQuery.data.deliveries[0].status_code != null ? String(deliveriesQuery.data.deliveries[0].status_code) : 'n/a'}
                  />
                  <MetricCard
                    label="Last Duration"
                    value={
                      deliveriesQuery.data.deliveries[0].duration_ms != null
                        ? `${deliveriesQuery.data.deliveries[0].duration_ms} ms`
                        : 'n/a'
                    }
                  />
                  <MetricCard label="Last Attempt" value={formatTimestamp(deliveriesQuery.data.deliveries[0].attempted_at)} />
                </div>

                {deliveriesQuery.data.deliveries.map((delivery) => (
                  <details key={delivery.id} className="rounded-lg border border-slate/20 bg-white/70 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/70">
                    <summary className="cursor-pointer list-none">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <div className="flex flex-wrap items-center gap-2">
                            <span
                              className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${deliveryStatusBadgeClass(
                                delivery,
                              )}`}
                            >
                              {describeDeliveryStatus(delivery)}
                            </span>
                            <span className="rounded-full bg-slate/10 px-2 py-0.5 text-[11px] font-semibold text-slate-700 dark:bg-white/10 dark:text-white/70">
                              {delivery.delivery_kind === 'retry' ? 'Retry' : 'Live'}
                            </span>
                            <span className="rounded-full bg-slate/10 px-2 py-0.5 text-[11px] font-semibold text-slate-700 dark:bg-white/10 dark:text-white/70">
                              {describeEventType(delivery.event_type)}
                            </span>
                          </div>
                          <p className="mt-2 font-semibold">{delivery.item_title || 'Webhook delivery'}</p>
                          <p className="mt-1 text-xs text-slate dark:text-white/60">
                            {delivery.feed_name || 'Unknown feed'} • {formatTimestamp(delivery.attempted_at)}
                          </p>
                        </div>
                        <div className="text-right text-xs text-slate dark:text-white/60">
                          <p>{delivery.rendered_method}</p>
                          <p>{delivery.duration_ms != null ? `${delivery.duration_ms} ms` : describeDeliverySecondaryStatus(delivery)}</p>
                          <p>Attempt {delivery.attempt_count}</p>
                        </div>
                      </div>
                    </summary>

                    <div className="mt-4 space-y-3 text-sm">
                      <div className="flex flex-wrap items-center gap-2">
                        {!isReadOnlyViewer && isRetryableDelivery(delivery) ? (
                          <button
                            className="rounded border border-slate/30 px-3 py-1.5 text-xs font-semibold disabled:opacity-50 dark:border-cyan-900/40"
                            disabled={retryDelivery.isPending || !canManageWebhooks}
                            onClick={() => {
                              retryDelivery.reset()
                              setPendingDeliveryRetry(delivery)
                            }}
                          >
                            Retry failed delivery
                          </button>
                        ) : (
                          <span className="text-xs text-slate dark:text-white/60">{describeRetryAvailability(delivery)}</span>
                        )}
                        <span className="text-xs text-slate dark:text-white/60">Timeout: {delivery.timeout_seconds}s</span>
                        {delivery.claimed_at && (
                          <span className="text-xs text-slate dark:text-white/60">Claimed: {formatTimestamp(delivery.claimed_at)}</span>
                        )}
                      </div>

                      <div>
                        <p className="text-xs font-semibold uppercase text-slate dark:text-white/60">Rendered URL</p>
                        <code className="mt-1 block rounded bg-slate/10 px-3 py-2 text-xs dark:bg-white/5">{delivery.rendered_url}</code>
                      </div>

                      {delivery.rendered_headers.length > 0 && (
                        <div>
                          <p className="text-xs font-semibold uppercase text-slate dark:text-white/60">Headers</p>
                          <div className="mt-1 space-y-1">
                            {delivery.rendered_headers.map((header, index) => (
                              <code key={`${delivery.id}-header-${index}`} className="block rounded bg-slate/10 px-3 py-2 text-xs dark:bg-white/5">
                                {header.key}: {header.value}
                              </code>
                            ))}
                          </div>
                        </div>
                      )}

                      {delivery.rendered_body && (
                        <div>
                          <p className="text-xs font-semibold uppercase text-slate dark:text-white/60">Rendered Body</p>
                          <pre className="mt-1 overflow-x-auto rounded bg-slate/10 px-3 py-2 text-xs dark:bg-white/5">{delivery.rendered_body}</pre>
                        </div>
                      )}

                      {delivery.response_body_preview && (
                        <div>
                          <p className="text-xs font-semibold uppercase text-slate dark:text-white/60">Response Preview</p>
                          <pre className="mt-1 overflow-x-auto rounded bg-slate/10 px-3 py-2 text-xs dark:bg-white/5">
                            {delivery.response_body_preview}
                          </pre>
                        </div>
                      )}

                      {delivery.error && <p className="text-sm text-red-600">{delivery.error}</p>}
                    </div>
                  </details>
                ))}

                {deliveriesQuery.data.total > deliveriesQuery.data.deliveries.length && (
                  <p className="text-xs text-slate dark:text-white/60">
                    Showing the latest {deliveriesQuery.data.deliveries.length} deliveries out of {deliveriesQuery.data.total}.
                  </p>
                )}
              </div>
            ) : null}

            {selectedWebhookId && deliveriesQuery.data && deliveriesQuery.data.deliveries.length === 0 && (
              <p className="mt-3 text-sm text-slate dark:text-white/70">
                No deliveries yet. Matching events will queue here automatically after the first live delivery reservation.
              </p>
            )}
          </section>
        </div>
      </div>

      <ConfirmDialog
        open={Boolean(pendingWebhookDelete)}
        title="Delete webhook?"
        description="This removes the webhook and its delivery history."
        confirmLabel="Delete webhook"
        onCancel={() => setPendingWebhookDelete(null)}
        onConfirm={onConfirmDeleteWebhook}
        confirmDisabled={deleteWebhook.isPending || !canManageWebhooks}
        isConfirming={deleteWebhook.isPending}
      >
        {pendingWebhookDelete && (
          <div className="space-y-3">
            <p className="font-semibold text-ink dark:text-white">{pendingWebhookDelete.name}</p>
            <p className="text-xs text-slate dark:text-white/70">Event: {describeEventType(pendingWebhookDelete.event_type)}</p>
            <p className="break-all font-mono text-xs text-slate dark:text-white/70">{pendingWebhookDelete.url_template}</p>
          </div>
        )}
      </ConfirmDialog>
      <ConfirmDialog
        open={Boolean(pendingDeliveryRetry)}
        title="Retry failed delivery?"
        description="ThreatLens will send the saved request again. Successful deliveries are not replayed by default."
        confirmLabel="Retry delivery"
        onCancel={() => setPendingDeliveryRetry(null)}
        onConfirm={onConfirmRetryDelivery}
        confirmDisabled={retryDelivery.isPending || !canManageWebhooks}
        isConfirming={retryDelivery.isPending}
      >
        {pendingDeliveryRetry ? (
          <div className="space-y-3">
            <p className="font-semibold text-ink dark:text-white">
              {pendingDeliveryRetry.item_title || pendingDeliveryRetry.feed_name || 'Webhook delivery'}
            </p>
            <p className="text-xs text-slate dark:text-white/70">
              {describeEventType(pendingDeliveryRetry.event_type)} at {formatTimestamp(pendingDeliveryRetry.attempted_at)}
            </p>
            <p className="break-all font-mono text-xs text-slate dark:text-white/70">{pendingDeliveryRetry.rendered_url}</p>
          </div>
        ) : null}
      </ConfirmDialog>
      {confirmDiscardUnsavedWebhookChanges.discardDialog}
    </div>
  )
}

function KeyValueEditor({
  title,
  description,
  fields,
  addLabel,
  keyPlaceholder,
  valuePlaceholder,
  disabled,
  onChange,
}: {
  title: string
  description: string
  fields: NotificationWebhookField[]
  addLabel: string
  keyPlaceholder: string
  valuePlaceholder: string
  disabled: boolean
  onChange: (fields: NotificationWebhookField[]) => void
}) {
  const fieldIdPrefix = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')

  return (
    <section className="rounded-lg border border-slate/20 p-4 dark:border-cyan-900/40">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h4 className="font-semibold">{title}</h4>
          <p className="mt-1 text-xs text-slate dark:text-white/65">{description}</p>
        </div>
        <button
          type="button"
          className="rounded border border-slate/30 px-3 py-1.5 text-xs font-semibold disabled:opacity-50 dark:border-cyan-900/40"
          disabled={disabled}
          onClick={() => onChange([...fields, emptyField()])}
        >
          {addLabel}
        </button>
      </div>

      <div className="mt-3 space-y-2">
        {fields.length === 0 && <p className="text-sm text-slate dark:text-white/70">No entries yet.</p>}
        {fields.map((field, index) => {
          const keyId = `${fieldIdPrefix}-${index}-key`
          const valueId = `${fieldIdPrefix}-${index}-value`

          return (
          <div key={`${title}-${index}`} className="grid gap-2 md:grid-cols-[1fr_1.4fr_auto]">
            <label htmlFor={keyId} className="sr-only">
              {title} row {index + 1} key
            </label>
            <input
              id={keyId}
              className="rounded border border-slate/30 bg-white px-3 py-2 text-sm disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
              disabled={disabled}
              value={field.key}
              onChange={(event) => onChange(updateField(fields, index, { key: event.target.value }))}
              placeholder={keyPlaceholder}
            />
            <label htmlFor={valueId} className="sr-only">
              {title} row {index + 1} value
            </label>
            <input
              id={valueId}
              className="rounded border border-slate/30 bg-white px-3 py-2 text-sm disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
              disabled={disabled}
              value={field.value}
              onChange={(event) => onChange(updateField(fields, index, { value: event.target.value }))}
              placeholder={valuePlaceholder}
            />
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-2 text-sm disabled:opacity-50 dark:border-cyan-900/40"
              aria-label={`Remove ${title} row ${index + 1}`}
              disabled={disabled}
              onClick={() => onChange(fields.filter((_, candidateIndex) => candidateIndex !== index))}
            >
              Remove
            </button>
          </div>
          )
        })}
      </div>
    </section>
  )
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate/20 p-3 dark:border-cyan-900/40">
      <p className="text-xs font-semibold uppercase text-slate dark:text-white/60">{label}</p>
      <p className="mt-1 text-sm font-semibold">{value}</p>
    </div>
  )
}

function describeQueueStatusLabel(queue: NotificationQueueSnapshot): string {
  if (queue.status === 'critical') {
    return 'Critical'
  }
  if (queue.status === 'degraded') {
    return 'Degraded'
  }
  return 'Healthy'
}

function describeQueueStatusMessage(queue: NotificationQueueSnapshot): string {
  if (queue.status === 'critical') {
    return `${queue.stale_sending_deliveries} delivery claim${queue.stale_sending_deliveries === 1 ? '' : 's'} look stranded. The recovery sweep should retry them, but the worker path needs attention.`
  }
  if (queue.oldest_pending_age_seconds != null) {
    return `The oldest queued delivery has been waiting ${formatAgeSeconds(queue.oldest_pending_age_seconds)}, which is beyond the ${formatAgeSeconds(queue.degraded_after_seconds)} backlog target.`
  }
  return 'Deliveries are flowing normally.'
}

function queueStatusBadgeClass(queue: NotificationQueueSnapshot): string {
  if (queue.status === 'critical') {
    return 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300'
  }
  if (queue.status === 'degraded') {
    return 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'
  }
  return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
}

function formatAgeSeconds(value: number): string {
  if (value < 60) {
    return `${value}s`
  }
  if (value < 3600) {
    return `${Math.floor(value / 60)}m`
  }
  return `${Math.floor(value / 3600)}h ${Math.floor((value % 3600) / 60)}m`
}

function createDefaultDraft(): NotificationWebhookDraft {
  return {
    name: '',
    enabled: true,
    event_type: 'rss_item_new',
    url_template: '',
    method: 'POST',
    feed_scope: 'all',
    feed_ids: [],
    query_params: [],
    headers: [],
    body_mode: 'json',
    body_fields: buildDefaultJsonFields('rss_item_new'),
    body_template: '',
    content_type: '',
    timeout_seconds: 10,
  }
}

function createDraftFromWebhook(webhook: NotificationWebhook): NotificationWebhookDraft {
  const { contentType, headers } = splitContentTypeHeader(webhook.headers)
  return {
    name: webhook.name,
    enabled: webhook.enabled,
    event_type: webhook.event_type,
    url_template: webhook.url_template,
    method: webhook.method,
    feed_scope: webhook.feed_scope,
    feed_ids: [...webhook.feed_ids],
    query_params: webhook.query_params.map((field) => ({ ...field })),
    headers: headers.map((field) => ({ ...field })),
    body_mode: webhook.body_mode,
    body_fields: webhook.body_fields.map((field) => ({ ...field })),
    body_template: webhook.body_template ?? '',
    content_type: contentType,
    timeout_seconds: webhook.timeout_seconds,
  }
}

function createRequestFromDraft(draft: NotificationWebhookDraft): NotificationWebhookWriteRequest {
  const normalizedDraft = normalizeDraftUrlQuery(draft)
  return {
    name: normalizedDraft.name.trim(),
    enabled: normalizedDraft.enabled,
    event_type: normalizedDraft.event_type,
    url_template: normalizedDraft.url_template.trim(),
    method: normalizedDraft.method,
    feed_scope: normalizedDraft.feed_scope,
    feed_ids: normalizedDraft.feed_scope === 'selected' ? normalizedDraft.feed_ids : [],
    query_params: sanitizeFields(normalizedDraft.query_params),
    headers: buildRequestHeaders(normalizedDraft),
    body_mode: normalizedDraft.body_mode,
    body_fields: normalizedDraft.body_mode === 'json' || normalizedDraft.body_mode === 'form' ? sanitizeFields(normalizedDraft.body_fields) : [],
    body_template: normalizedDraft.body_mode === 'raw' ? normalizedDraft.body_template : null,
    timeout_seconds: normalizedDraft.timeout_seconds,
  }
}

function sanitizeFields(fields: NotificationWebhookField[]): NotificationWebhookField[] {
  return fields
    .map((field) => ({ key: field.key.trim(), value: field.value }))
    .filter((field) => field.key.length > 0)
}

function applyBodyMode(current: NotificationWebhookDraft, nextBodyMode: NotificationWebhookDraft['body_mode']): NotificationWebhookDraft {
  if (nextBodyMode === 'json') {
    return {
      ...current,
      body_mode: 'json',
      body_fields: current.body_fields.length ? current.body_fields : buildDefaultJsonFields(current.event_type),
      body_template: '',
    }
  }

  if (nextBodyMode === 'form') {
    return {
      ...current,
      body_mode: 'form',
      body_fields: current.body_fields.length ? current.body_fields : [emptyField()],
      body_template: '',
    }
  }

  if (nextBodyMode === 'raw') {
    return {
      ...current,
      body_mode: 'raw',
      body_template: current.body_template,
    }
  }

  return {
    ...current,
    body_mode: 'none',
    body_fields: [],
    body_template: '',
  }
}

function applyEventType(current: NotificationWebhookDraft, eventType: NotificationEventType): NotificationWebhookDraft {
  const next = { ...current, event_type: eventType }
  if (current.body_mode !== 'json') {
    return next
  }
  const currentFields = JSON.stringify(current.body_fields)
  const currentDefaultFields = JSON.stringify(buildDefaultJsonFields(current.event_type))
  if (!current.body_fields.length || currentFields === currentDefaultFields) {
    return { ...next, body_fields: buildDefaultJsonFields(eventType) }
  }
  return next
}

function normalizeDraftUrlQuery(current: NotificationWebhookDraft): NotificationWebhookDraft {
  const extracted = extractUrlQueryParams(current.url_template)
  if (extracted == null) {
    return current
  }

  return {
    ...current,
    url_template: extracted.baseUrl,
    query_params: mergeQueryParams(current.query_params, extracted.queryParams),
  }
}

function toggleFeedSelection(current: NotificationWebhookDraft, feedId: string): NotificationWebhookDraft {
  const alreadySelected = current.feed_ids.includes(feedId)
  return {
    ...current,
    feed_scope: 'selected',
    feed_ids: alreadySelected ? current.feed_ids.filter((candidate) => candidate !== feedId) : [...current.feed_ids, feedId],
  }
}

function updateField(fields: NotificationWebhookField[], index: number, patch: Partial<NotificationWebhookField>): NotificationWebhookField[] {
  return fields.map((field, candidateIndex) => (candidateIndex === index ? { ...field, ...patch } : field))
}

function emptyField(): NotificationWebhookField {
  return { key: '', value: '' }
}

function splitContentTypeHeader(headers: NotificationWebhookField[]): { contentType: string; headers: NotificationWebhookField[] } {
  let contentType = ''
  const remainingHeaders: NotificationWebhookField[] = []

  for (const header of headers) {
    if (header.key.trim().toLowerCase() === 'content-type') {
      if (!contentType) {
        contentType = header.value
      }
      continue
    }
    remainingHeaders.push({ ...header })
  }

  return { contentType, headers: remainingHeaders }
}

function buildRequestHeaders(draft: NotificationWebhookDraft): NotificationWebhookField[] {
  const headers = sanitizeFields(draft.headers).filter((header) => header.key.trim().toLowerCase() !== 'content-type')
  const contentType = draft.content_type.trim()
  if (contentType) {
    headers.push({ key: 'Content-Type', value: contentType })
  }
  return headers
}

function extractUrlQueryParams(urlTemplate: string): { baseUrl: string; queryParams: NotificationWebhookField[] } | null {
  const trimmedUrl = urlTemplate.trim()
  const queryIndex = trimmedUrl.indexOf('?')
  if (queryIndex === -1) {
    return null
  }

  const hashIndex = trimmedUrl.indexOf('#', queryIndex)
  const baseUrl = hashIndex === -1 ? trimmedUrl.slice(0, queryIndex) : `${trimmedUrl.slice(0, queryIndex)}${trimmedUrl.slice(hashIndex)}`
  const rawQuery = hashIndex === -1 ? trimmedUrl.slice(queryIndex + 1) : trimmedUrl.slice(queryIndex + 1, hashIndex)
  const queryParams = rawQuery
    .split('&')
    .map((segment) => segment.trim())
    .filter(Boolean)
    .map((segment) => {
      const separatorIndex = segment.indexOf('=')
      const rawKey = separatorIndex === -1 ? segment : segment.slice(0, separatorIndex)
      const rawValue = separatorIndex === -1 ? '' : segment.slice(separatorIndex + 1)
      return {
        key: decodeQueryComponent(rawKey),
        value: decodeQueryComponent(rawValue),
      }
    })
    .filter((field) => field.key.trim().length > 0)

  if (!queryParams.length) {
    return { baseUrl, queryParams: [] }
  }

  return { baseUrl, queryParams }
}

function mergeQueryParams(existing: NotificationWebhookField[], extracted: NotificationWebhookField[]): NotificationWebhookField[] {
  if (!extracted.length) {
    return existing
  }

  const extractedKeys = new Set(extracted.map((field) => field.key))
  return [...existing.filter((field) => !extractedKeys.has(field.key)), ...extracted]
}

function decodeQueryComponent(value: string): string {
  const normalized = value.replace(/\+/g, ' ')
  try {
    return decodeURIComponent(normalized)
  } catch {
    return normalized
  }
}

function resolveDefaultContentTypeLabel(bodyMode: NotificationWebhookDraft['body_mode']): string {
  if (bodyMode === 'json') {
    return 'application/json'
  }
  if (bodyMode === 'form') {
    return 'application/x-www-form-urlencoded'
  }
  if (bodyMode === 'raw') {
    return 'auto detect'
  }
  return 'none'
}

function buildDefaultJsonFields(eventType: NotificationEventType): NotificationWebhookField[] {
  return EVENT_DEFAULT_JSON_FIELDS[eventType].map((field) => ({ ...field }))
}

function describeEventType(eventType: NotificationEventType): string {
  return EVENT_OPTIONS.find((option) => option.value === eventType)?.label ?? eventType
}

function describeEventDescription(eventType: NotificationEventType): string {
  return EVENT_OPTIONS.find((option) => option.value === eventType)?.description ?? eventType
}

function describeFeedScope(scope: NotificationWebhook['feed_scope'], count: number): string {
  if (scope === 'all') {
    return 'all feeds'
  }
  return `${count} selected feed${count === 1 ? '' : 's'}`
}

function describeDeliveryStatus(delivery: NotificationWebhookDelivery): string {
  if (delivery.delivery_state === 'pending') {
    return 'Queued'
  }
  if (delivery.delivery_state === 'sending') {
    return 'Sending'
  }
  if (delivery.status_code != null) {
    return `${delivery.success ? 'Success' : 'Failed'} · HTTP ${delivery.status_code}`
  }
  return delivery.success ? 'Success' : 'Failed'
}

function describeDeliverySecondaryStatus(delivery: NotificationWebhookDelivery): string {
  if (delivery.delivery_state === 'pending') {
    return 'Waiting for worker'
  }
  if (delivery.delivery_state === 'sending') {
    return 'In progress'
  }
  return 'n/a'
}

function deliveryStatusBadgeClass(delivery: NotificationWebhookDelivery): string {
  if (delivery.delivery_state === 'pending') {
    return 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'
  }
  if (delivery.delivery_state === 'sending') {
    return 'bg-cyan-100 text-cyan-700 dark:bg-cyan-900/40 dark:text-cyan-200'
  }
  if (delivery.success) {
    return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
  }
  return 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300'
}

function isRetryableDelivery(delivery: NotificationWebhookDelivery): boolean {
  return delivery.delivery_state === 'failed'
}

function describeRetryAvailability(delivery: NotificationWebhookDelivery): string {
  if (delivery.delivery_state === 'succeeded') {
    return 'Successful deliveries are not replayed by default.'
  }
  return 'This delivery is already queued or in progress.'
}

function formatTimestamp(value: string): string {
  return formatDateTime(value)
}

function formatFailureRate(failedDeliveries: number, totalDeliveries: number): string {
  if (totalDeliveries <= 0) {
    return '0.0% failed'
  }
  return `${((failedDeliveries / totalDeliveries) * 100).toFixed(1)}% failed`
}

function resolveApiMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError && typeof error.message === 'string' && error.message.trim()) {
    return error.message
  }
  return fallback
}
