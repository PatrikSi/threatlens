import { useState } from 'react'
import type { useCurrentUser } from '../hooks/useCurrentUser'
import type { useQueryAccessScope } from '../hooks/useQueryAccessScope'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
import type { DashboardSavedViewPreview } from './dashboardSavedViews'

type ViewAccess = ReturnType<typeof useQueryAccessScope>

export function useDashboardViewIntents(access: ViewAccess) {
  const [deletion, setDeletion] = useState<Awaited<ReturnType<typeof access.read<DashboardSavedViewPreview>>> | null>(null)
  const [loading, setLoading] = useState<Awaited<ReturnType<typeof access.read<{ id: string; name: string }>>> | null>(null)
  return {
    pendingViewDelete: access.currentData(deletion ?? undefined) ?? null,
    pendingSavedViewLoad: access.currentData(loading ?? undefined) ?? null,
    setPendingViewDelete: (view: DashboardSavedViewPreview | null) => setDeletion(view ? access.captureResult()(view) : null),
    setPendingSavedViewLoad: (view: { id: string; name: string } | null) => setLoading(view ? access.captureResult()(view) : null),
    retireDeletedView: (id: string) => {
      setLoading((current) => current?.value.id === id ? null : current)
      setDeletion((current) => current?.value.id === id ? null : current)
    },
  }
}

export function dashboardItemWritesAvailable(query: ReturnType<typeof useCurrentUser>) {
  return !query.isError && !query.isLoading && hasRequiredPermissions(query.data?.access?.permissions ?? [], ['write:items'])
}

export function dashboardViewActionsAvailable(query: { data?: unknown; isError: boolean; isFetching: boolean }) {
  return Boolean(query.data) && !query.isError && !query.isFetching
}

export function readableSavedViewBaseline<T>(access: ViewAccess, generation: number, baseline: T) {
  return !access.denied && generation === access.generation ? baseline : null
}
