import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { captureSessionLease } from '../api/sessionLifecycle'
import { useAuth } from './AuthContext'

export function SessionQueryProvider({ children }: { children: React.ReactNode }) {
  const { sessionVersion } = useAuth()
  return <SessionCache key={sessionVersion}>{children}</SessionCache>
}

function SessionCache({ children }: { children: React.ReactNode }) {
  const [client] = useState(() => {
    const mutations = new WeakMap<object, ReturnType<typeof captureSessionLease>>()
    return new QueryClient({
      defaultOptions: { queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false } },
      mutationCache: new MutationCache({
        onMutate: (_variables, mutation) => { mutations.set(mutation, captureSessionLease()) },
        onSuccess: (_data, _variables, _context, mutation) => { mutations.get(mutation)?.assertCurrent() },
      }),
    })
  })
  useEffect(() => () => {
    // Late mutation callbacks retain only this retired client, never the next session's cache.
    void client.cancelQueries()
    client.clear()
  }, [client])
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}
