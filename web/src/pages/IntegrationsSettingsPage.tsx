import { resolveApiErrorMessage } from '../api/errors'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { SettingsPageHeader, SettingsReadOnlyNotice } from '../components/SettingsPageHeader'
import { SMTPHookEditor, SMTPHookList } from './SMTPHookEditor'
import { SMTPAnalyticsPanel, SMTPDeliveryHistory } from './SMTPIntegrationHistory'
import { useSMTPIntegrationController } from './useSMTPIntegrationController'

export function SMTPIntegrationSettingsPage() {
  const controller = useSMTPIntegrationController()
  return (
    <div className="space-y-3">
      <SMTPIntegrationHeader accessNotice={controller.accessNotice} loadError={controller.loadError} />
      <SMTPAnalyticsPanel
        analytics={controller.analyticsQuery.data}
        loading={controller.analyticsQuery.isLoading}
        error={controller.analyticsQuery.error}
      />
      <div className="grid gap-3 xl:grid-cols-[320px_minmax(0,1fr)]">
        <SMTPHookList controller={controller} />
        <div className="space-y-3">
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
            canReplay={controller.canManageEmailDelivery}
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

function SMTPIntegrationHeader({
  accessNotice,
  loadError,
}: {
  accessNotice: string | null
  loadError: unknown
}) {
  return (
    <SettingsPageHeader
      scope="Organization"
      title="Email delivery"
      description="Route organization-wide notification events to email destinations and monitor delivery health."
    >
      {(accessNotice || Boolean(loadError)) && (
        <div className="space-y-3 py-3">
          {accessNotice && <SettingsReadOnlyNotice permission="permission to manage email delivery" />}
          {Boolean(loadError) && (
            <p role="alert" className="text-sm text-red-600">
              {resolveApiErrorMessage(loadError, 'Failed to load email delivery settings.')}
            </p>
          )}
        </div>
      )}
    </SettingsPageHeader>
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
        title="Delete email destination?"
        description={controller.pendingDelete
          ? `Delete ${controller.pendingDelete.name}? Delivery history will remain in retained integration records.`
          : undefined}
        confirmLabel="Delete destination"
        isConfirming={controller.deleteHook.isPending}
        confirmDisabled={!controller.canManageEmailDelivery}
        onConfirm={() => {
          if (!controller.pendingDelete || !controller.canManageEmailDelivery) return
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
        description="This creates a new delivery using the destination's current credentials, recipients, and template."
        confirmLabel="Replay delivery"
        confirmTone="primary"
        isConfirming={controller.replayDelivery.isPending}
        confirmDisabled={!controller.canManageEmailDelivery}
        onConfirm={() => {
          if (!controller.pendingReplay || !controller.selectedHookId || !controller.canManageEmailDelivery) return
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
