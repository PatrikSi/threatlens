import { Navigate } from 'react-router-dom'

import { ApiError } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const meQuery = useCurrentUser()

  if (meQuery.isLoading) {
    return <div className="p-6 text-sm text-slate dark:text-slate-300">Loading session...</div>
  }
  if (meQuery.error instanceof ApiError && (meQuery.error.status === 401 || meQuery.error.status === 403)) {
    return <Navigate to="/login" replace />
  }
  if (!meQuery.data) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}
