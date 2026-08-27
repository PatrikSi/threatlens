import { useParams } from 'react-router-dom'

import { InvestigationDetailWorkspace } from './InvestigationDetailWorkspace'
import { InvestigationListWorkspace } from './InvestigationListWorkspace'
import { useInvestigationDetail } from './useInvestigationDetail'
import { useInvestigationsPage } from './useInvestigationsPage'

export function InvestigationsPage() {
  const { investigationId } = useParams<{ investigationId: string }>()
  return investigationId
    ? <InvestigationDetailRoute investigationId={investigationId} />
    : <InvestigationListRoute />
}

function InvestigationListRoute() {
  const controller = useInvestigationsPage()
  return <InvestigationListWorkspace controller={controller} />
}

function InvestigationDetailRoute({ investigationId }: { investigationId: string }) {
  const controller = useInvestigationDetail(investigationId)
  return <InvestigationDetailWorkspace controller={controller} />
}
