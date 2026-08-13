import { AlertDeleteDialog, AlertEditorPanel, ConfiguredAlertsPanel } from './AlertsPagePanels'
import { useAlertsPageController } from './useAlertsPageController'

export function AlertsPage() {
  const controller = useAlertsPageController()

  return (
    <>
      <div className="grid gap-4 xl:grid-cols-[480px_1fr]">
        <AlertEditorPanel controller={controller} />
        <ConfiguredAlertsPanel controller={controller} />
      </div>
      <AlertDeleteDialog controller={controller} />
      {controller.confirmDiscardUnsavedAlertChanges.discardDialog}
    </>
  )
}
