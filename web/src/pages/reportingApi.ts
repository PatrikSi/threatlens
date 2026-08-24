import { apiFetch, type ApiFetchOptions } from '../api/client'
import { isAmbiguousReportingMutationError } from './reportingResilience'
import {
  assertReportingRequestLeaseCurrent,
  beginPendingReportingRequestLease,
  settlePendingReportingRequest,
} from './reportingRequestCoordinator'


export async function idempotentReportingFetch<Result>(
  path: string,
  scope: string,
  options: ApiFetchOptions,
  validate: (value: unknown) => Result,
): Promise<Result> {
  const lease = await beginPendingReportingRequestLease(scope)
  assertReportingRequestLeaseCurrent(lease)
  const { key } = lease
  try {
    const headers = new Headers(options.headers)
    headers.set('Idempotency-Key', key)
    const value = await apiFetch<unknown>(path, { ...options, headers })
    assertReportingRequestLeaseCurrent(lease)
    const result = validate(value)
    settlePendingReportingRequest(scope, key, 'confirmed')
    return result
  } catch (error) {
    assertReportingRequestLeaseCurrent(lease)
    const ambiguous = isAmbiguousReportingMutationError(error)
    const settlement = settlePendingReportingRequest(
      scope,
      key,
      ambiguous ? 'ambiguous' : 'rejected',
    )
    if (
      ambiguous
      && !settlement.durable
      && error instanceof Error
    ) {
      error.message = `${error.message} Browser storage is unavailable, so keep this tab open before retrying; reloading could create a duplicate request.`
    }
    throw error
  }
}
