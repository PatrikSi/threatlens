import { Navigate } from 'react-router-dom'

import { useCurrentUser } from '../hooks/useCurrentUser'
import { User } from '../types/api'

interface RoleRouteProps {
  roles: User['role'][]
  children: React.ReactNode
}

export function RoleRoute({ roles, children }: RoleRouteProps) {
  const meQuery = useCurrentUser()

  if (meQuery.isLoading) {
    return <div className="p-6 text-sm text-slate dark:text-slate-300">Checking permissions...</div>
  }

  if (!meQuery.data || !roles.includes(meQuery.data.role)) {
    return <Navigate to="/" replace />
  }

  return <>{children}</>
}
