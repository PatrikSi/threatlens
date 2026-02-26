import { useQuery } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { useAuth } from '../components/AuthContext'
import { User } from '../types/api'

export function useCurrentUser() {
  const { token } = useAuth()

  return useQuery({
    queryKey: ['auth', 'me', token],
    enabled: Boolean(token),
    queryFn: () => apiFetch<User>('/auth/me'),
    staleTime: 60_000,
  })
}
