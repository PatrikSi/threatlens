import { Navigate, useLocation } from 'react-router-dom'

import { ApiError } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { SessionIssueState } from './SessionIssueState'

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const meQuery = useCurrentUser()
  const location = useLocation()
  const from = { pathname: location.pathname, search: location.search, hash: location.hash }

  if (meQuery.isLoading) {
    return <div className="p-6 text-sm text-slate dark:text-slate-300">Loading session...</div>
  }
  if (meQuery.error instanceof ApiError && meQuery.error.status === 401) {
    return <Navigate to="/login" replace state={{ authMessage: 'Session expired. Sign in again.', from }} />
  }
  if (meQuery.error instanceof ApiError && meQuery.error.status === 403) {
    return (
      <div className="flex min-h-screen items-center justify-center px-4">
        <div className="w-full max-w-lg rounded-2xl border border-amber-300/60 bg-white/90 p-6 shadow-sm dark:border-amber-500/30 dark:bg-[#041612]/95">
          <h2 className="font-display text-3xl text-ink dark:text-white">Access blocked</h2>
          <p className="mt-2 text-sm text-slate dark:text-slate-300">
            {meQuery.error.message || 'This account is authenticated, but it cannot access ThreatLens right now.'}
          </p>
          <p className="mt-3 text-sm text-slate dark:text-slate-300">
            Contact an administrator if this account should be active and approved.
          </p>
        </div>
      </div>
    )
  }
  if (meQuery.error) {
    return (
      <SessionIssueState
        title="Session check unavailable"
        description="ThreatLens could not verify your session because the API is unavailable or returned an unexpected error."
        errorMessage={meQuery.error instanceof Error ? meQuery.error.message : undefined}
        actionLabel="Retry session check"
        onAction={() => void meQuery.refetch()}
        secondaryLinkLabel="Go to login"
        secondaryLinkTo="/login"
        fullscreen
      />
    )
  }
  if (!meQuery.data) {
    return <Navigate to="/login" replace state={{ authMessage: 'Sign in to continue.', from }} />
  }

  return <>{children}</>
}
