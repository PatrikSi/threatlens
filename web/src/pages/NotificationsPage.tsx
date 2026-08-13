import { NotificationDeliveryHistory } from './NotificationDeliveryHistory'
import { NotificationWebhookAnalytics } from './NotificationWebhookAnalytics'
import { SavedWebhooksCard, TestResultAndVariables } from './NotificationWebhookCards'
import { NotificationWebhookDialogs } from './NotificationWebhookDialogs'
import { NotificationWebhookEditor, WebhookEditorUnavailable } from './NotificationWebhookEditor'
import {
  NotificationWebhooksController,
  useNotificationWebhooksController,
} from './useNotificationWebhooksController'

function NotificationHeader({ controller }: { controller: NotificationWebhooksController }) {
  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Automation</p>
      <h2 className="mt-1 font-display text-xl">Webhook Notifications</h2>
      <p className="mt-1 text-sm text-slate dark:text-white/75">
        Configure outbound webhooks for new RSS items, alert matches, feed failures, failed deliveries, and AI Daily Briefs.
      </p>
      <p className="mt-2 text-xs text-slate dark:text-white/60">
        Variables use `{'{{ item.title }}'}` style placeholders, similar to Grafana-style notification templates.
      </p>
      {controller.accessNotice && (
        <div
          role="status"
          aria-live="polite"
          aria-atomic="true"
          className="mt-3 rounded-lg border border-slate/20 bg-slate/5 px-3 py-2 text-sm text-slate dark:border-white/10 dark:bg-white/[0.04] dark:text-white/70"
        >
          {controller.accessNotice}
        </div>
      )}
    </section>
  )
}

export function NotificationWebhooksSettings() {
  const controller = useNotificationWebhooksController()
  if (controller.currentUserQuery.isLoading) {
    return (
      <div className="rounded-xl border border-slate/20 bg-white/80 p-4 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90">
        Loading notification settings...
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <NotificationHeader controller={controller} />
      <NotificationWebhookAnalytics controller={controller} />
      <div className="grid min-w-0 gap-4 xl:grid-cols-[320px_1fr]">
        <SavedWebhooksCard controller={controller} />
        <div className="min-w-0 space-y-4">
          {controller.showWebhookEditor ? (
            <NotificationWebhookEditor controller={controller} />
          ) : (
            <WebhookEditorUnavailable controller={controller} />
          )}
          <TestResultAndVariables controller={controller} />
          <NotificationDeliveryHistory controller={controller} />
        </div>
      </div>
      <NotificationWebhookDialogs controller={controller} />
    </div>
  )
}

export function NotificationsPage() {
  return <NotificationWebhooksSettings />
}
