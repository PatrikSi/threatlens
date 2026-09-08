import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import { captureSessionLease, invalidateSession, SessionChangedError, setSessionVerificationUnavailable } from '../api/sessionLifecycle'
import { useAuth } from '../components/AuthContext'
import { CurrentUser } from '../types/api'

export function useCurrentUser() {
  const { observeAuthenticatedIdentity, sessionVersion } = useAuth()

  const query = useQuery({
    queryKey: ['auth', 'me', sessionVersion],
    queryFn: async ({ signal }) => {
      const session = captureSessionLease()
      try {
        const user = await apiFetch<CurrentUser>('/auth/me', { signal })
        session.assertCurrent()
        setSessionVerificationUnavailable(false)
        return user
      } catch (error) {
        session.assertCurrent()
        if (signal.aborted) throw error
        if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
          invalidateSession()
        }
        setSessionVerificationUnavailable(true)
        throw error
      }
    },
    staleTime: 60_000,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    refetchOnMount: 'always',
    refetchOnWindowFocus: true,
    retry: (failureCount, error) => {
      if (error instanceof SessionChangedError) return false
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        return false
      }
      return failureCount < 1
    },
  })
  const userId = query.error ? undefined : query.data?.id
  useEffect(() => {
    if (userId) observeAuthenticatedIdentity(userId)
  }, [observeAuthenticatedIdentity, userId])
  return query
}
