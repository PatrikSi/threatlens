import { useQueryClient, type QueryKey } from '@tanstack/react-query'

import { useQueryAccessScope } from '../hooks/useQueryAccessScope'
import type { LifecycleOverviewResponse, LifecycleRunListResponse } from '../types/lifecycle'

type ResultFactory = ReturnType<ReturnType<typeof useQueryAccessScope>['captureResult']>
export type LifecycleResultCapture = { catalog: ResultFactory; runs: ResultFactory }

export function useLifecycleReadAccess() {
  const client = useQueryClient()
  const catalog = useQueryAccessScope(['operations', 'lifecycle', 'catalog'])
  const runs = useQueryAccessScope(['operations', 'lifecycle', 'runs'])
  return {
    catalog,
    runs,
    capture: (): LifecycleResultCapture => ({ catalog: catalog.captureResult(), runs: runs.captureResult() }),
    currentCatalog: () => client.getQueryState(['operations', 'lifecycle'])?.status === 'error'
      ? undefined : catalog.currentData<LifecycleOverviewResponse>(client.getQueryData(['operations', 'lifecycle'])),
    currentRuns: (key: QueryKey) => client.getQueryState(key)?.status === 'error'
      ? undefined : runs.currentData<LifecycleRunListResponse>(client.getQueryData(key)),
  }
}
