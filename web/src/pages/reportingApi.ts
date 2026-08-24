import { apiFetch, type ApiFetchOptions } from '../api/client'
import { isAmbiguousReportingMutationError } from './reportingResilience'
import {
  clearPendingReportingKey,
  getOrCreatePendingReportingKey,
} from './reportingRequestCoordinator'


export async function idempotentReportingFetch<Result>(
  path: string,
  scope: string,
  options: ApiFetchOptions,
  validate: (value: unknown) => Result,
): Promise<Result> {
  const key = getOrCreatePendingReportingKey(scope)
  const headers = new Headers(options.headers)
  headers.set('Idempotency-Key', key)
  try {
    const value = await apiFetch<unknown>(path, { ...options, headers })
    const result = validate(value)
    clearPendingReportingKey(scope, key)
    return result
  } catch (error) {
    if (!isAmbiguousReportingMutationError(error)) {
      clearPendingReportingKey(scope, key)
    }
    throw error
  }
}
