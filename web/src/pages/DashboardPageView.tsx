import { DashboardDialogs } from './DashboardDialogs'
import { DashboardToolbar } from './DashboardToolbar'
import { DashboardWorkspace } from './DashboardWorkspace'
import type { DashboardPageController } from './useDashboardPageController'

export function DashboardPageView({ controller }: { controller: DashboardPageController }) {
  return (
    <div className="w-full">
      <DashboardToolbar controller={controller} />
      {controller.workspaceDefaultsDegraded && (
        <div
          role="status"
          className="mx-3 mt-3 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-800/50 dark:bg-amber-950/30 dark:text-amber-100 sm:mx-4"
        >
          Workspace defaults are temporarily unavailable. This dashboard was initialized with safe local defaults and your changes will be preserved.
        </div>
      )}
      <DashboardWorkspace controller={controller} />
      <DashboardDialogs controller={controller} />
    </div>
  )
}
