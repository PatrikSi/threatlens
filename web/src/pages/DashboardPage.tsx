import { DashboardPageView } from './DashboardPageView'
import { useDashboardPageController } from './useDashboardPageController'

export function DashboardPage() {
  const controller = useDashboardPageController()
  return <DashboardPageView controller={controller} />
}
