import { ConfirmDialog } from '../components/ConfirmDialog'
import { describeEventType } from './notificationWebhookDraft'
import { formatTimestamp } from './notificationWebhookPresentation'
import { NotificationWebhooksController } from './useNotificationWebhooksController'

export function NotificationWebhookDialogs({ controller }: { controller: NotificationWebhooksController }) {
  const {
    canManageWebhooks,
    confirmDiscardUnsavedWebhookChanges,
    deleteWebhook,
    onConfirmDeleteWebhook,
    onConfirmRetryDelivery,
    pendingDeliveryRetry,
    pendingWebhookDelete,
    retryDelivery,
    setPendingDeliveryRetry,
    setPendingWebhookDelete,
  } = controller
  return (
    <>
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
    </>
  )
}
