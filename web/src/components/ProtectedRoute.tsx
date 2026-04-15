import { Navigate } from 'react-router-dom'

import { ApiError } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const meQuery = useCurrentUser()

  if (meQuery.isLoading) {
    return <div className="p-6 text-sm text-slate dark:text-slate-300">Loading session...</div>
  }
  if (meQuery.error instanceof ApiError && meQuery.error.status === 401) {
    return <Navigate to="/login" replace state={{ authMessage: 'Session expired. Sign in again.' }} />
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
  if (!meQuery.data) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}
