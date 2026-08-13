import { formatDateTime } from '../utils/datetime'
import { NotificationQueueSnapshot, NotificationWebhook, NotificationWebhookDelivery } from '../types/api'

export function describeQueueStatusLabel(queue: NotificationQueueSnapshot): string {
  if (queue.status === 'critical') return 'Critical'
  if (queue.status === 'degraded') return 'Degraded'
  return 'Healthy'
}

export function describeQueueStatusMessage(queue: NotificationQueueSnapshot): string {
  if (queue.status === 'critical') {
    return `${queue.stale_sending_deliveries} delivery claim${queue.stale_sending_deliveries === 1 ? '' : 's'} look stranded. The recovery sweep should retry them, but the worker path needs attention.`
  }
  if (queue.oldest_pending_age_seconds != null) {
    return `The oldest queued delivery has been waiting ${formatAgeSeconds(queue.oldest_pending_age_seconds)}, which is beyond the ${formatAgeSeconds(queue.degraded_after_seconds)} backlog target.`
  }
  return 'Deliveries are flowing normally.'
}

export function queueStatusBadgeClass(queue: NotificationQueueSnapshot): string {
  if (queue.status === 'critical') return 'tl-chip-danger'
  if (queue.status === 'degraded') return 'tl-chip-warning'
  return 'tl-chip-success'
}

export function formatAgeSeconds(value: number): string {
  if (value < 60) return `${value}s`
  if (value < 3600) return `${Math.floor(value / 60)}m`
  return `${Math.floor(value / 3600)}h ${Math.floor((value % 3600) / 60)}m`
}

export function describeFeedScope(scope: NotificationWebhook['feed_scope'], count: number): string {
  if (scope === 'all') return 'all feeds'
  return `${count} selected feed${count === 1 ? '' : 's'}`
}

export function describeDeliveryStatus(delivery: NotificationWebhookDelivery): string {
  if (delivery.delivery_state === 'pending') return 'Queued'
  if (delivery.delivery_state === 'sending') return 'Sending'
  if (delivery.status_code != null) return `${delivery.success ? 'Success' : 'Failed'} · HTTP ${delivery.status_code}`
  return delivery.success ? 'Success' : 'Failed'
}

export function describeDeliverySecondaryStatus(delivery: NotificationWebhookDelivery): string {
  if (delivery.delivery_state === 'pending') return 'Waiting for worker'
  if (delivery.delivery_state === 'sending') return 'In progress'
  return 'n/a'
}

export function deliveryStatusBadgeClass(delivery: NotificationWebhookDelivery): string {
  if (delivery.delivery_state === 'pending' || delivery.delivery_state === 'sending') return 'tl-chip-info'
  return delivery.success ? 'tl-chip-success' : 'tl-chip-danger'
}

export function isRetryableDelivery(delivery: NotificationWebhookDelivery): boolean {
  return delivery.delivery_state === 'failed'
}

export function describeRetryAvailability(delivery: NotificationWebhookDelivery): string {
  if (delivery.delivery_state === 'succeeded') return 'Successful deliveries are not replayed by default.'
  return 'This delivery is already queued or in progress.'
}

export function formatTimestamp(value: string): string {
  return formatDateTime(value)
}

export function formatFailureRate(failedDeliveries: number, totalDeliveries: number): string {
  if (totalDeliveries <= 0) return '0.0% failed'
  return `${((failedDeliveries / totalDeliveries) * 100).toFixed(1)}% failed`
}
