import { useMemo, useSyncExternalStore } from 'react'
import { useQueryClient, type Query, type QueryClient, type QueryFunctionContext } from '@tanstack/react-query'

import { ApiError } from '../api/client'
import { createQueryAccessStore } from './queryAccessStore'

type ScopedRead<T> = { generation: number; value: T }

// The client is already owned by one browser session. Keeping the resource
// state with it preserves a denial through paging and component remounts, while
// a different publication team has its own explicit scope.
const scopes = new WeakMap<QueryClient, ReturnType<typeof createQueryAccessStore>>()

/** Inspect a known resource withdrawal without creating or authorizing a scope. */
export function isQueryAccessDenied(client: QueryClient, prefix: readonly (string | boolean)[]) {
  return scopes.get(client)?.denied(JSON.stringify(prefix)) ?? false
}

function resourceScope(client: QueryClient, key: string) {
  let resources = scopes.get(client)
  if (!resources) {
    resources = createQueryAccessStore()
    scopes.set(client, resources)
  }
  return resources.get(key)
}

/** Preserve outage context, but retire every cached read preceding a denial. */
export function useQueryAccessScope(prefix: readonly (string | boolean)[]) {
  const client = useQueryClient()
  const key = JSON.stringify(prefix)
  const scope = useMemo(() => resourceScope(client, key), [client, key])
  const snapshot = useSyncExternalStore(scope.subscribe, scope.snapshot, scope.snapshot)

  function currentData<T>(data: ScopedRead<T> | undefined): T | undefined {
    const current = scope.snapshot()
    return data && !current.denied && data.generation === current.generation ? data.value : undefined
  }

  // A write can update a previously verified read, but cannot establish read
  // access or release a denial. Capture before the request, never on completion.
  function captureResult() {
    const generation = scope.snapshot().generation
    return <T,>(value: T): ScopedRead<T> => ({ generation, value })
  }

  async function read<T>(request: () => Promise<T>): Promise<ScopedRead<T>> {
    const generation = scope.snapshot().generation
    try {
      const value = await request()
      scope.authorize(generation)
      return { generation, value }
    } catch (error) {
      if (error instanceof ApiError && [401, 403, 404].includes(error.status)) scope.deny(generation)
      throw error
    }
  }

  function queryOptions<T>(request: (context: QueryFunctionContext) => Promise<T>, freshStaleTime?: number) {
    return {
      queryFn: (context: QueryFunctionContext) => read(() => request(context)),
      select: (data: ScopedRead<T>) => currentData(data),
      staleTime: (query: Query<ScopedRead<T>>) => {
        // A cached success from before the denial is not fresh authorization.
        // Re-entering that key must fetch even when the client uses Infinity.
        const current = scope.snapshot()
        if (!query.state.data || current.denied || query.state.data.generation !== current.generation) return 0
        const inherited = freshStaleTime ?? client.defaultQueryOptions<ScopedRead<T>>({ queryKey: query.queryKey }).staleTime
        return typeof inherited === 'function' ? inherited(query) : inherited ?? 0
      },
    }
  }

  return { denied: snapshot.denied, generation: snapshot.generation, currentData, captureResult, read, queryOptions }
}
