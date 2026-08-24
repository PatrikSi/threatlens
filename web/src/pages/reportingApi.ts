import { apiFetch, type ApiFetchOptions } from '../api/client'
import { isAmbiguousReportingMutationError } from './reportingResilience'
import {
  beginPendingReportingRequest,
  settlePendingReportingRequest,
} from './reportingRequestCoordinator'


export async function idempotentReportingFetch<Result>(
  path: string,
  scope: string,
  options: ApiFetchOptions,
  validate: (value: unknown) => Result,
): Promise<Result> {
  const key = beginPendingReportingRequest(scope)
  const headers = new Headers(options.headers)
  headers.set('Idempotency-Key', key)
  try {
    const value = await apiFetch<unknown>(path, { ...options, headers })
    const result = validate(value)
    settlePendingReportingRequest(scope, key, 'confirmed')
    return result
  } catch (error) {
    settlePendingReportingRequest(
      scope,
      key,
      isAmbiguousReportingMutationError(error) ? 'ambiguous' : 'rejected',
    )
    throw error
  }
}
