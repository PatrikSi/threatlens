import type { ApiError, ApiRequestError, ApiTransportError } from './client'

export type ErrorMessageOptions = {
  retryGuidance?: string
  includeApiDetail?: boolean
  includeTechnicalDetail?: boolean
}

export function resolveApiErrorMessage(
  error: unknown,
  fallback: string,
  options: ErrorMessageOptions = {},
): string {
  const context = ensureSentence(fallback)
  if (isApiRequestError(error)) {
    return joinSentences(context, error.message)
  }
  if (isApiTransportError(error)) {
    return joinSentences(context, error.message, options.retryGuidance ?? 'Try again after connectivity is restored.')
  }

  if (isApiError(error)) {
    const detail = options.includeApiDetail === false ? '' : usefulApiDetail(error)
    const retryGuidance = resolveRetryGuidance(error, options.retryGuidance)
    const reference = error.requestId ? `Request reference: ${error.requestId}.` : ''
    return joinSentences(context, detail, retryGuidance, reference)
  }

  if (error instanceof Error && error.message.trim()) {
    return joinSentences(
      context,
      options.includeTechnicalDetail === false ? '' : error.message,
      options.retryGuidance ?? 'Try again. Check the application logs if the problem continues.',
    )
  }

  return joinSentences(context, options.retryGuidance ?? 'Try again. Check the application logs if the problem continues.')
}

function usefulApiDetail(error: ApiError): string {
  const message = error.message.trim()
  if (!message || /^HTTP \d{3}$/i.test(message)) {
    return statusGuidance(error.status)
  }
  return message
}

function resolveRetryGuidance(error: ApiError, override?: string): string {
  if (override) {
    return override
  }
  if (error.status === 429) {
    return typeof error.retryAfterSeconds === 'number'
      ? `Try again in about ${error.retryAfterSeconds} seconds.`
      : 'Wait before trying again.'
  }
  if (error.status === 401) {
    return 'Sign in again if your session has expired.'
  }
  if (error.status === 403) {
    if (/csrf/i.test(error.message)) {
      return 'Refresh the page to renew the browser session, then try again.'
    }
    if (/inactive|approval|pending/i.test(error.message)) {
      return 'Contact an administrator if this account should be active and approved.'
    }
    return 'Verify that your account has the required role and token scope.'
  }
  if (error.retryable) {
    return 'Try again. Check API and worker health if the problem continues.'
  }
  return ''
}

function statusGuidance(status: number): string {
  if (status === 400 || status === 422) return 'The API rejected one or more submitted values.'
  if (status === 401) return 'Authentication is required or is no longer valid.'
  if (status === 403) return 'The API denied this operation.'
  if (status === 404) return 'The requested resource was not found or is no longer available.'
  if (status === 409) return 'The resource changed or is already in a conflicting state.'
  if (status >= 500) return 'The API encountered an internal or dependency failure.'
  return `The API returned HTTP ${status}.`
}

function ensureSentence(value: string): string {
  const trimmed = value.trim()
  if (!trimmed) return ''
  return /[.!?]$/.test(trimmed) ? trimmed : `${trimmed}.`
}

function joinSentences(...values: string[]): string {
  const result: string[] = []
  for (const value of values) {
    const sentence = ensureSentence(value)
    if (!sentence || result.includes(sentence)) continue
    result.push(sentence)
  }
  return result.join(' ')
}

function isApiError(error: unknown): error is ApiError {
  if (!(error instanceof Error)) return false
  const candidate = error as Partial<ApiError>
  return error.name === 'ApiError' && typeof candidate.status === 'number' && typeof candidate.path === 'string'
}

function isApiTransportError(error: unknown): error is ApiTransportError {
  if (!(error instanceof Error)) return false
  const candidate = error as Partial<ApiTransportError>
  return (
    error.name === 'ApiTransportError' &&
    (candidate.kind === 'timeout' || candidate.kind === 'network') &&
    typeof candidate.path === 'string'
  )
}

function isApiRequestError(error: unknown): error is ApiRequestError {
  if (!(error instanceof Error)) return false
  const candidate = error as Partial<ApiRequestError>
  return (
    error.name === 'ApiRequestError' &&
    candidate.code === 'invalid_csrf_cookie' &&
    typeof candidate.path === 'string'
  )
}
