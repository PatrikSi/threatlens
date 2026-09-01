import { resolveApiErrorMessage } from '../api/errors'
import { NotificationWebhookDelivery } from '../types/api'
import { describeEventType } from './notificationWebhookDraft'
import {
  deliveryStatusBadgeClass,
  describeDeliverySecondaryStatus,
  describeDeliveryStatus,
  describeRetryAvailability,
  formatTimestamp,
  isRetryableDelivery,
} from './notificationWebhookPresentation'
import { MetricCard } from './NotificationWebhookShared'
import { NotificationWebhooksController } from './useNotificationWebhooksController'

function DeliveryDetails({
  controller,
  delivery,
}: {
  controller: NotificationWebhooksController
  delivery: NotificationWebhookDelivery
}) {
  const { canManageWebhooks, isReadOnly, retryDelivery, setPendingDeliveryRetry } = controller
  return (
    <details className="rounded-lg border border-slate/20 bg-white/70 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/70">
      <summary className="cursor-pointer list-none">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <span className={`tl-chip ${deliveryStatusBadgeClass(delivery)}`}>{describeDeliveryStatus(delivery)}</span>
              <span className="tl-chip tl-chip-neutral">{delivery.delivery_kind === 'retry' ? 'Retry' : 'Live'}</span>
              <span className="tl-chip tl-chip-neutral">{describeEventType(delivery.event_type)}</span>
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

      <div className="mt-3 space-y-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          {!isReadOnly && isRetryableDelivery(delivery) ? (
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
            <p className="text-xs font-semibold uppercase text-slate dark:text-white/60">Rendered body</p>
            <pre className="mt-1 overflow-x-auto rounded bg-slate/10 px-3 py-2 text-xs dark:bg-white/5">{delivery.rendered_body}</pre>
          </div>
        )}
        {delivery.response_body_preview && (
          <div>
            <p className="text-xs font-semibold uppercase text-slate dark:text-white/60">Response preview</p>
            <pre className="mt-1 overflow-x-auto rounded bg-slate/10 px-3 py-2 text-xs dark:bg-white/5">
              {delivery.response_body_preview}
            </pre>
          </div>
        )}
        {delivery.error && <p className="text-sm text-red-600">{delivery.error}</p>}
      </div>
    </details>
  )
}

export function NotificationDeliveryHistory({ controller }: { controller: NotificationWebhooksController }) {
  const { deliveriesQuery, retryDelivery, selectedWebhookId } = controller
  const firstDelivery = deliveriesQuery.data?.deliveries[0]
  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-3 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-display text-lg">Delivery history</h2>
          <p className="mt-1 text-sm text-slate dark:text-white/75">
            Review the last deliveries for this webhook, including the rendered request and response preview.
          </p>
        </div>
        {firstDelivery && (
          <span className={`tl-chip tl-chip-md ${deliveryStatusBadgeClass(firstDelivery)}`}>
            Last status: {describeDeliveryStatus(firstDelivery)}
          </span>
        )}
      </div>

      {!selectedWebhookId && (
        <p className="mt-3 text-sm text-slate dark:text-white/70">Select a webhook to see its recent delivery attempts.</p>
      )}
      {selectedWebhookId && deliveriesQuery.isLoading && (
        <p className="mt-3 text-sm text-slate dark:text-white/70">Loading delivery history...</p>
      )}
      {selectedWebhookId && deliveriesQuery.isError && (
        <p className="mt-3 text-sm text-red-600">
          {resolveApiErrorMessage(deliveriesQuery.error, 'Failed to load delivery history.')}
        </p>
      )}
      {selectedWebhookId && retryDelivery.isError && (
        <p className="mt-3 text-sm text-red-600">
          {resolveApiErrorMessage(retryDelivery.error, 'Failed to retry webhook delivery.')}
        </p>
      )}

      {selectedWebhookId && deliveriesQuery.data?.deliveries.length ? (
        <div className="mt-3 space-y-3">
          <div className="grid gap-3 md:grid-cols-4">
            <MetricCard label="Attempts" value={String(deliveriesQuery.data.total)} />
            <MetricCard label="Last code" value={firstDelivery?.status_code != null ? String(firstDelivery.status_code) : 'n/a'} />
            <MetricCard
              label="Last duration"
              value={firstDelivery?.duration_ms != null ? `${firstDelivery.duration_ms} ms` : 'n/a'}
            />
            <MetricCard label="Last attempt" value={firstDelivery ? formatTimestamp(firstDelivery.attempted_at) : 'n/a'} />
          </div>
          {deliveriesQuery.data.deliveries.map((delivery) => (
            <DeliveryDetails key={delivery.id} controller={controller} delivery={delivery} />
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
  )
}
