import { resolveApiErrorMessage } from '../api/errors'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { SMTPHookEditor, SMTPHookList } from './SMTPHookEditor'
import { SMTPAnalyticsPanel, SMTPDeliveryHistory } from './SMTPIntegrationHistory'
import { useSMTPIntegrationController } from './useSMTPIntegrationController'

export function SMTPIntegrationSettingsPage() {
  const controller = useSMTPIntegrationController()
  return (
    <div className="space-y-4">
      <SMTPIntegrationHeader loadError={controller.loadError} />
      <SMTPAnalyticsPanel
        analytics={controller.analyticsQuery.data}
        loading={controller.analyticsQuery.isLoading}
        error={controller.analyticsQuery.error}
      />
      <div className="grid gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
        <SMTPHookList controller={controller} />
        <div className="space-y-4">
          <SMTPHookEditor controller={controller} />
          <SMTPDeliveryHistory
            hook={controller.selectedHook}
            feeds={controller.feeds}
            deliveries={controller.deliveriesQuery.data}
            deliveryLoading={controller.deliveriesQuery.isLoading}
            deliveryError={controller.deliveriesQuery.error}
            deliveryPage={controller.deliveryPage}
            onDeliveryPageChange={controller.setDeliveryPage}
            testRuns={controller.testRunsQuery.data}
            testRunLoading={controller.testRunsQuery.isLoading}
            testRunError={controller.testRunsQuery.error}
            testRunPage={controller.testRunPage}
            onTestRunPageChange={controller.setTestRunPage}
            replaying={controller.replayDelivery.isPending}
            onReplay={controller.setPendingReplay}
          />
        </div>
      </div>
      <SMTPConfirmationDialogs controller={controller} />
      {controller.discardDialog}
    </div>
  )
}

export function IntegrationsSettingsPage() {
  return <SMTPIntegrationSettingsPage />
}

function SMTPIntegrationHeader({ loadError }: { loadError: unknown }) {
  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Automation</p>
      <h2 className="mt-1 font-display text-xl">SMTP Notifications</h2>
      <p className="mt-1 text-sm text-slate dark:text-white/75">
        Route different notification events to independent email destinations while sharing relay credentials where appropriate.
      </p>
      {Boolean(loadError) && (
        <p role="alert" className="mt-3 text-sm text-red-600">
          {resolveApiErrorMessage(loadError, 'Failed to load SMTP integrations.')}
        </p>
      )}
    </section>
  )
}

function SMTPConfirmationDialogs({
  controller,
}: {
  controller: ReturnType<typeof useSMTPIntegrationController>
}) {
  return (
    <>
      <ConfirmDialog
        open={Boolean(controller.pendingDelete)}
        title="Delete SMTP hook?"
        description={controller.pendingDelete
          ? `Delete ${controller.pendingDelete.name}? Delivery history will remain in retained integration records.`
          : undefined}
        confirmLabel="Delete hook"
        isConfirming={controller.deleteHook.isPending}
        onConfirm={() => {
          if (!controller.pendingDelete) return
          controller.setDeleteError(null)
          controller.deleteHook.mutate(controller.pendingDelete.id)
        }}
        onCancel={() => {
          controller.setPendingDelete(null)
          controller.setDeleteError(null)
        }}
      >
        {controller.deleteError && (
          <p role="alert" className="text-sm text-red-600 dark:text-red-300">
            {controller.deleteError}
          </p>
        )}
      </ConfirmDialog>
      <ConfirmDialog
        open={Boolean(controller.pendingReplay)}
        title="Replay dead-letter delivery?"
        description="This creates a new delivery using the hook's current credentials, recipients, and template."
        confirmLabel="Replay delivery"
        confirmTone="primary"
        isConfirming={controller.replayDelivery.isPending}
        onConfirm={() => {
          if (!controller.pendingReplay || !controller.selectedHookId) return
          controller.replayDelivery.mutate({
            hookId: controller.selectedHookId,
            deliveryId: controller.pendingReplay.id,
          })
        }}
        onCancel={() => controller.setPendingReplay(null)}
      />
    </>
  )
}
