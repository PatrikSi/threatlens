import { Navigate, useLocation } from 'react-router-dom'

import { ApiError } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { SessionIssueState } from './SessionIssueState'

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const meQuery = useCurrentUser()
  const location = useLocation()
  const from = { pathname: location.pathname, search: location.search, hash: location.hash }
  const returnState = isWorkspaceEntry(location.pathname) ? {} : { from }

  if (meQuery.isLoading) {
    return <div className="p-6 text-sm text-slate dark:text-slate-300">Loading session...</div>
  }
  if (meQuery.error instanceof ApiError && meQuery.error.status === 401) {
    return (
      <Navigate
        to="/login"
        replace
        state={{ authMessage: 'Session expired. Sign in again.', ...returnState }}
      />
    )
  }
  if (meQuery.error instanceof ApiError && meQuery.error.status === 403) {
    return (
      <div className="flex min-h-screen items-center justify-center px-4">
        <div className="tl-surface w-full max-w-lg rounded-2xl p-6 shadow-sm">
          <h2 className="font-display text-3xl text-ink dark:text-white">Access blocked</h2>
          <div className="mt-3 rounded-lg border border-amber-300/60 bg-amber-50/80 px-3 py-2 text-sm text-amber-900 dark:border-amber-800/40 dark:bg-amber-950/30 dark:text-amber-100">
            <p>{resolveApiErrorMessage(meQuery.error, 'This account cannot access ThreatLens')}</p>
            <p className="mt-2">Contact an administrator if this account should be active and approved.</p>
          </div>
        </div>
      </div>
    )
  }
  if (meQuery.error) {
    return (
      <SessionIssueState
        title="Session check unavailable"
        description="ThreatLens could not verify your session because the API is unavailable or returned an unexpected error."
        errorMessage={resolveApiErrorMessage(meQuery.error, 'Session verification failed')}
        actionLabel="Retry session check"
        onAction={() => void meQuery.refetch()}
        secondaryLinkLabel="Go to login"
        secondaryLinkTo="/login"
        fullscreen
      />
    )
  }
  if (!meQuery.data) {
    return <Navigate to="/login" replace state={{ authMessage: 'Sign in to continue.', ...returnState }} />
  }

  return <>{children}</>
}

function isWorkspaceEntry(pathname: string) {
  return pathname === '/' || pathname === '/start'
}
