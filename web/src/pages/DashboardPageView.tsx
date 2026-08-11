import { DashboardDialogs } from './DashboardDialogs'
import { DashboardToolbar } from './DashboardToolbar'
import { DashboardWorkspace } from './DashboardWorkspace'
import type { DashboardPageController } from './useDashboardPageController'

export function DashboardPageView({ controller }: { controller: DashboardPageController }) {
  return (
    <div className="w-full">
      <DashboardToolbar controller={controller} />
      <DashboardWorkspace controller={controller} />
      <DashboardDialogs controller={controller} />
    </div>
  )
}
