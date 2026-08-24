import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import { useAuth } from '../components/AuthContext'
import { CurrentUser } from '../types/api'

export function useCurrentUser() {
  const { observeAuthenticatedIdentity, sessionVersion } = useAuth()

  const query = useQuery({
    queryKey: ['auth', 'me', sessionVersion],
    queryFn: () => apiFetch<CurrentUser>('/auth/me'),
    staleTime: 60_000,
    retry: (failureCount, error) => {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        return false
      }
      return failureCount < 1
    },
  })
  const userId = query.data?.id
  useEffect(() => {
    if (userId) observeAuthenticatedIdentity(userId)
  }, [observeAuthenticatedIdentity, userId])
  return query
}
