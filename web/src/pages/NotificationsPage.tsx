import { SettingsPageHeader, SettingsReadOnlyNotice } from '../components/SettingsPageHeader'
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
    <SettingsPageHeader
      scope="Personal"
      title="My webhooks"
      description="Send your ThreatLens events to external systems through configurable webhooks."
    >
      <div className="space-y-3 py-3">
        <p className="text-xs text-slate dark:text-white/60">
          Template variables use `{'{{ item.title }}'}` style placeholders.
        </p>
        {controller.accessNotice && (
          <SettingsReadOnlyNotice permission="permission to manage notifications" />
        )}
      </div>
    </SettingsPageHeader>
  )
}

export function NotificationWebhooksSettings() {
  const controller = useNotificationWebhooksController()
  if (controller.currentUserQuery.isLoading) {
    return (
      <div className="space-y-3">
        <NotificationHeader controller={controller} />
        <div role="status" className="rounded-xl border border-slate/20 bg-white/80 p-3 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90">
          Loading webhook settings...
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <NotificationHeader controller={controller} />
      <NotificationWebhookAnalytics controller={controller} />
      <div className="grid min-w-0 gap-3 xl:grid-cols-[320px_1fr]">
        <SavedWebhooksCard controller={controller} />
        <div className="min-w-0 space-y-3">
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
