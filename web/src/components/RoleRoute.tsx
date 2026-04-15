import { Link, Navigate, useLocation } from 'react-router-dom'

import { ApiError } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { User } from '../types/api'
import { SessionIssueState } from './SessionIssueState'

interface RoleRouteProps {
  roles: User['role'][]
  children: React.ReactNode
}

export function RoleRoute({ roles, children }: RoleRouteProps) {
  const meQuery = useCurrentUser()
  const location = useLocation()
  const from = { pathname: location.pathname, search: location.search, hash: location.hash }

  if (meQuery.isLoading) {
    return <div className="p-6 text-sm text-slate dark:text-slate-300">Checking permissions...</div>
  }
  if (meQuery.error instanceof ApiError && meQuery.error.status === 401) {
    return <Navigate to="/login" replace state={{ authMessage: 'Session expired. Sign in again.', from }} />
  }
  if (meQuery.error instanceof ApiError && meQuery.error.status === 403) {
    return (
      <div className="mx-auto max-w-2xl rounded-2xl border border-amber-300/60 bg-white/90 p-6 shadow-sm dark:border-amber-500/30 dark:bg-[#041612]/95">
        <h2 className="font-display text-3xl text-ink dark:text-white">Access blocked</h2>
        <p className="mt-2 text-sm text-slate dark:text-slate-300">
          {meQuery.error.message || 'This account is authenticated, but it cannot access this area right now.'}
        </p>
      </div>
    )
  }
  if (meQuery.error) {
    return (
      <SessionIssueState
        title="Permission check unavailable"
        description="ThreatLens could not confirm your role because the API is unavailable or returned an unexpected error."
        errorMessage={meQuery.error instanceof Error ? meQuery.error.message : undefined}
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

  if (!roles.includes(meQuery.data.role)) {
    return (
      <div className="mx-auto max-w-2xl rounded-2xl border border-amber-300/60 bg-white/90 p-6 shadow-sm dark:border-amber-500/30 dark:bg-[#041612]/95">
        <h2 className="font-display text-3xl text-ink dark:text-white">Role required</h2>
        <p className="mt-2 text-sm text-slate dark:text-slate-300">
          This page is limited to {roles.join(' or ')} accounts. Your current role is {meQuery.data?.role ?? 'unknown'}.
        </p>
        <div className="mt-4 flex flex-wrap gap-3">
          <Link
            to="/settings"
            className="rounded border border-slate/20 px-3 py-1.5 text-sm font-semibold text-slate-700 transition hover:border-slate/30 hover:bg-slate/5 dark:border-white/10 dark:text-slate-100 dark:hover:bg-white/[0.06]"
          >
            Back to settings
          </Link>
          <Link
            to="/"
            className="rounded border border-cyan/30 bg-cyan/10 px-3 py-1.5 text-sm font-semibold text-cyan transition hover:bg-cyan/15 dark:border-cyan-500/35 dark:text-cyan-100"
          >
            Go to dashboard
          </Link>
        </div>
      </div>
    )
  }

  return <>{children}</>
}
