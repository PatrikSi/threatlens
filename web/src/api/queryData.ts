import { ApiError } from './client'

/** Keep stale data through outages, but hide it when the resource is no longer accessible. */
export function accessibleQueryData<T>(query: { data: T | undefined; error: unknown }): T | undefined {
  return query.error instanceof ApiError && [401, 403, 404].includes(query.error.status)
    ? undefined
    : query.data
}
