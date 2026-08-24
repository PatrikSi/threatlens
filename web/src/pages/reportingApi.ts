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
  if (!lease.durable || !lease.shared) {
    settlePendingReportingRequest(scope, key, 'blocked')
    throw new Error(
      'ThreatLens could not safely store a shared request key. Enable local browser storage, reload this page, and retry; no request was sent.',
    )
  }
  try {
    const headers = new Headers(options.headers)
    headers.set('Idempotency-Key', key)
    const value = await apiFetch<unknown>(path, { ...options, headers })
    assertReportingRequestLeaseCurrent(lease)
    const result = validate(value)
    settlePendingReportingRequest(scope, key, 'confirmed')
    return result
  } catch (error) {
    const ambiguous = isAmbiguousReportingMutationError(error)
    const settlement = settlePendingReportingRequest(
      scope,
      key,
      ambiguous ? 'ambiguous' : 'rejected',
    )
    if (
      ambiguous
      && (!settlement.durable || !settlement.shared)
      && error instanceof Error
    ) {
      error.message = `${error.message} ThreatLens could not retain the shared request key after the failure. Keep this tab open and restore local browser storage before retrying.`
    }
    throw error
  }
}
