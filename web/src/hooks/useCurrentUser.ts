import { useQuery } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import { useAuth } from '../components/AuthContext'
import { User } from '../types/api'

export function useCurrentUser() {
  const { token } = useAuth()

  return useQuery({
    queryKey: ['auth', 'me', token],
    enabled: Boolean(token),
    queryFn: () => apiFetch<User>('/auth/me'),
    staleTime: 60_000,
    retry: (failureCount, error) => {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        return false
      }
      return failureCount < 1
    },
  })
}
