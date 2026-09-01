import { Link, Navigate, useLocation } from 'react-router-dom'

import { ApiError } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { useCurrentUser } from '../hooks/useCurrentUser'
import type { User } from '../types/identity'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
import { SessionIssueState } from './SessionIssueState'

interface PermissionRouteProps {
  permissions: readonly string[]
  roles?: readonly User['role'][]
  children: React.ReactNode
}

export function PermissionRoute({ permissions, roles, children }: PermissionRouteProps) {
  const meQuery = useCurrentUser()
  const location = useLocation()
  const from = { pathname: location.pathname, search: location.search, hash: location.hash }
  const settingsActionLabel = location.pathname.startsWith('/settings')
    ? 'Back to settings'
    : 'Open settings'

  if (meQuery.isLoading) {
    return <div className="p-6 text-sm text-slate dark:text-slate-300">Checking permissions...</div>
  }
  if (meQuery.error instanceof ApiError && meQuery.error.status === 401) {
    return <Navigate to="/login" replace state={{ authMessage: 'Session expired. Sign in again.', from }} />
  }
  if (meQuery.error instanceof ApiError && meQuery.error.status === 403) {
    return (
      <div className="tl-surface mx-auto max-w-2xl rounded-2xl p-6 shadow-sm">
        <h2 className="font-display text-3xl text-ink dark:text-white">Access blocked</h2>
        <p className="mt-3 rounded-lg border border-amber-300/60 bg-amber-50/80 px-3 py-2 text-sm text-amber-900 dark:border-amber-800/40 dark:bg-amber-950/30 dark:text-amber-100">
          {resolveApiErrorMessage(meQuery.error, 'This account cannot access this area')}
        </p>
      </div>
    )
  }
  if (meQuery.error) {
    return (
      <SessionIssueState
        title="Permission check unavailable"
        description="ThreatLens could not confirm your permissions because the API is unavailable or returned an unexpected error."
        errorMessage={resolveApiErrorMessage(meQuery.error, 'Permission verification failed')}
        actionLabel="Retry permission check"
        onAction={() => void meQuery.refetch()}
        secondaryLinkLabel="Go to dashboard"
        secondaryLinkTo="/"
      />
    )
  }
  if (!meQuery.data) {
    return <Navigate to="/login" replace state={{ authMessage: 'Sign in to continue.', from }} />
  }

  const roleAllowed = roles === undefined || roles.includes(meQuery.data.role)
  const permissionsAllowed = hasRequiredPermissions(
    meQuery.data.access?.permissions ?? [],
    permissions,
  )
  if (!roleAllowed || !permissionsAllowed) {
    const roleRequirement = roles?.map(formatRoleLabel).join(' or ')
    return (
      <div className="tl-surface mx-auto max-w-2xl rounded-2xl p-6 shadow-sm">
        <h2 className="font-display text-3xl text-ink dark:text-white">
          {roleAllowed ? 'Permission required' : 'Base role required'}
        </h2>
        <p className="mt-3 rounded-lg border border-amber-300/60 bg-amber-50/80 px-3 py-2 text-sm text-amber-900 dark:border-amber-800/40 dark:bg-amber-950/30 dark:text-amber-100">
          {roleAllowed
            ? 'Your account does not currently have permission to open this area. Contact an administrator if you need access.'
            : `This area requires the ${roleRequirement} base role in addition to its permissions. Additive custom roles do not unlock sealed administrative controls.`}
        </p>
        <div className="mt-4 flex flex-wrap gap-3">
          <Link
            to="/settings"
            className="rounded border border-slate/20 px-3 py-1.5 text-sm font-semibold text-slate-700 transition hover:border-slate/30 hover:bg-slate/5 dark:border-white/10 dark:text-slate-100 dark:hover:bg-white/[0.06]"
          >
            {settingsActionLabel}
          </Link>
          <Link
            to="/start"
            className="rounded border border-cyan/30 bg-cyan/10 px-3 py-1.5 text-sm font-semibold text-cyan transition hover:bg-cyan/15 dark:border-cyan-500/35 dark:text-cyan-100"
          >
            Open workspace start
          </Link>
        </div>
      </div>
    )
  }

  return <>{children}</>
}

function formatRoleLabel(role: User['role']) {
  if (role === 'admin') return 'Administrator'
  if (role === 'analyst') return 'Analyst'
  return 'Viewer'
}
