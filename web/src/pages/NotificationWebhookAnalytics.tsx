import { resolveApiErrorMessage } from '../api/errors'
import { describeEventType } from './notificationWebhookDraft'
import {
  describeQueueStatusLabel,
  describeQueueStatusMessage,
  formatAgeSeconds,
  formatFailureRate,
  queueStatusBadgeClass,
} from './notificationWebhookPresentation'
import { MetricCard } from './NotificationWebhookShared'
import { NotificationWebhooksController } from './useNotificationWebhooksController'

export function NotificationWebhookAnalytics({ controller }: { controller: NotificationWebhooksController }) {
  const { analytics, analyticsQuery } = controller
  return (
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
        <p className="mt-3 text-sm text-red-600">
          {resolveApiErrorMessage(analyticsQuery.error, 'Failed to load notification analytics.')}
        </p>
      )}

      {analytics && (
        <div className="mt-4 space-y-4">
          {analytics.queue.status !== 'healthy' && (
            <div
              className={`rounded-lg border px-4 py-3 text-sm ${
                analytics.queue.status === 'critical'
                  ? 'border-red-200 bg-red-50 text-red-800 dark:border-red-900/50 dark:bg-red-950/35 dark:text-red-200'
                  : 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/35 dark:text-amber-200'
              }`}
            >
              <p className="font-semibold">Notification queue needs attention</p>
              <p className="mt-1">{describeQueueStatusMessage(analytics.queue)}</p>
            </div>
          )}

          <div className="grid grid-cols-2 gap-2 sm:gap-3 md:grid-cols-5">
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
                    <div
                      key={eventSummary.event_type}
                      className="flex items-center justify-between gap-3 rounded-lg bg-slate/5 px-3 py-2 dark:bg-white/5"
                    >
                      <div>
                        <p className="text-sm font-semibold">{describeEventType(eventSummary.event_type)}</p>
                        <p className="text-xs text-slate dark:text-white/60">
                          {eventSummary.failed_deliveries} failed of {eventSummary.total_deliveries}
                        </p>
                      </div>
                      <p className="text-sm font-semibold">
                        {formatFailureRate(eventSummary.failed_deliveries, eventSummary.total_deliveries)}
                      </p>
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
                <span className={`tl-chip ${queueStatusBadgeClass(analytics.queue)}`}>
                  {describeQueueStatusLabel(analytics.queue)}
                </span>
              </div>
              <div className="mt-3 space-y-2 text-sm">
                <p>Pending deliveries: <span className="font-semibold">{analytics.queue.pending_deliveries}</span></p>
                <p>In-flight deliveries: <span className="font-semibold">{analytics.queue.sending_deliveries}</span></p>
                <p>Stale claims: <span className="font-semibold">{analytics.queue.stale_sending_deliveries}</span></p>
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
  )
}
