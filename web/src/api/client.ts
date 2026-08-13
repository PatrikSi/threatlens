const DEFAULT_API_BASE_URL = import.meta.env.DEV
  ? typeof window !== 'undefined'
    ? `${window.location.protocol}//${window.location.hostname}:8000/v1`
    : 'http://localhost:8000/v1'
  : '/api/v1'
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? DEFAULT_API_BASE_URL
const DEFAULT_REQUEST_TIMEOUT_MS = 15000
const REQUEST_TIMEOUT_MS = normalizeTimeoutMs(import.meta.env.VITE_API_TIMEOUT_MS, DEFAULT_REQUEST_TIMEOUT_MS)
const CSRF_COOKIE_NAME = import.meta.env.VITE_CSRF_COOKIE_NAME ?? 'threatlens_csrf'
const CSRF_HEADER_NAME = (import.meta.env.VITE_CSRF_HEADER_NAME ?? 'x-csrf-token').toLowerCase()
const UNSAFE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

type ApiFetchOptions = RequestInit & {
  timeoutMs?: number
}

export class ApiError extends Error {
  status: number
  path: string
  detail: unknown
  responseBody: unknown
  code: string | null
  requestId: string | null
  retryable: boolean
  retryAfterSeconds: number | null

  constructor(
    message: string,
    status: number,
    path: string,
    detail: unknown = null,
    diagnostics: {
      responseBody?: unknown
      code?: string | null
      requestId?: string | null
      retryable?: boolean
      retryAfterSeconds?: number | null
    } = {},
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.path = path
    this.detail = detail
    this.responseBody = diagnostics.responseBody ?? detail
    this.code = diagnostics.code ?? null
    this.requestId = diagnostics.requestId ?? null
    this.retryable = diagnostics.retryable ?? false
    this.retryAfterSeconds = diagnostics.retryAfterSeconds ?? null
  }
}

export class ApiTransportError extends Error {
  path: string
  kind: 'timeout' | 'network'
  retryable = true

  constructor(message: string, path: string, kind: 'timeout' | 'network', cause?: unknown) {
    super(message, cause === undefined ? undefined : { cause })
    this.name = 'ApiTransportError'
    this.path = path
    this.kind = kind
  }
}

export class ApiRequestError extends Error {
  path: string
  code: 'invalid_csrf_cookie'
  retryable = false

  constructor(message: string, path: string, code: 'invalid_csrf_cookie', cause?: unknown) {
    super(message, cause === undefined ? undefined : { cause })
    this.name = 'ApiRequestError'
    this.path = path
    this.code = code
  }
}

export function buildApiUrl(path: string): string {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${API_BASE_URL}${normalizedPath}`
}

export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}, auth = true): Promise<T> {
  const { timeoutMs, ...requestOptions } = options
  const headers = new Headers(requestOptions.headers)
  const hasBody = requestOptions.body !== undefined && requestOptions.body !== null
  const method = (requestOptions.method ?? 'GET').toUpperCase()
  const bodyIsFormData = typeof FormData !== 'undefined' && requestOptions.body instanceof FormData
  const bodyIsBlob = typeof Blob !== 'undefined' && requestOptions.body instanceof Blob
  if (hasBody && !headers.has('Content-Type') && !bodyIsFormData && !bodyIsBlob) {
    headers.set('Content-Type', 'application/json')
  }
  if (!headers.has('Accept')) {
    headers.set('Accept', 'application/json')
  }
  if (auth && UNSAFE_METHODS.has(method)) {
    let csrfToken: string | null
    try {
      csrfToken = getCookieValue(CSRF_COOKIE_NAME)
    } catch (error) {
      throw new ApiRequestError(
        'The browser security token is malformed. Refresh the page to renew the session.',
        path,
        'invalid_csrf_cookie',
        error,
      )
    }
    if (csrfToken && !headers.has(CSRF_HEADER_NAME)) {
      headers.set(CSRF_HEADER_NAME, csrfToken)
    }
  }

  const timeoutController = new AbortController()
  const { signal, cleanup } = composeAbortSignals(requestOptions.signal, timeoutController.signal)
  const requestTimeoutMs = normalizeTimeoutMs(timeoutMs, REQUEST_TIMEOUT_MS)
  const timeout = setTimeout(() => timeoutController.abort(), requestTimeoutMs)

  let response: Response
  try {
    response = await fetch(buildApiUrl(path), {
      ...requestOptions,
      headers,
      credentials: 'include',
      signal,
    })
  } catch (error) {
    if (isAbortError(error) && !requestOptions.signal?.aborted) {
      throw new ApiTransportError(
        `The ThreatLens API did not respond within ${formatTimeoutSeconds(requestTimeoutMs)}.`,
        path,
        'timeout',
        error,
      )
    }
    if (requestOptions.signal?.aborted) {
      throw error
    }
    throw new ApiTransportError(
      'ThreatLens could not reach the API. Check the network connection and API container health.',
      path,
      'network',
      error,
    )
  } finally {
    clearTimeout(timeout)
    cleanup()
  }

  if (!response.ok) {
    const raw = await response.text()
    const parsed = tryParseJson(raw)
    const problem = extractProblemDetails(parsed)
    const message =
      problem.message ??
      extractErrorMessage(parsed, raw, response.status, response.statusText, response.headers.get('content-type'))
    throw new ApiError(message, response.status, path, extractResponseDetail(parsed, raw), {
      responseBody: parsed ?? raw,
      code: problem.code,
      requestId: problem.requestId ?? response.headers.get('x-request-id'),
      retryable: problem.retryable ?? isRetryableStatus(response.status),
      retryAfterSeconds: parseRetryAfterSeconds(response.headers.get('retry-after')),
    })
  }

  if (response.status === 204) {
    return undefined as T
  }

  const raw = await response.text()
  if (!raw.trim()) {
    return undefined as T
  }

  const parsed = tryParseJsonResult(raw)
  if (!parsed.ok) {
    throw new ApiError('The API returned an unreadable response instead of JSON.', response.status, path, raw, {
      responseBody: raw,
      code: 'invalid_response',
      requestId: response.headers.get('x-request-id'),
    })
  }
  return parsed.value as T
}

function extractProblemDetails(parsed: unknown): {
  code: string | null
  message: string | null
  requestId: string | null
  retryable: boolean | null
} {
  if (!parsed || typeof parsed !== 'object' || !('error' in parsed)) {
    return { code: null, message: null, requestId: null, retryable: null }
  }
  const error = (parsed as { error?: unknown }).error
  if (!error || typeof error !== 'object') {
    return { code: null, message: null, requestId: null, retryable: null }
  }
  const record = error as { code?: unknown; message?: unknown; request_id?: unknown; retryable?: unknown }
  return {
    code: typeof record.code === 'string' ? record.code : null,
    message: typeof record.message === 'string' && record.message.trim() ? record.message.trim() : null,
    requestId: typeof record.request_id === 'string' && record.request_id.trim() ? record.request_id.trim() : null,
    retryable: typeof record.retryable === 'boolean' ? record.retryable : null,
  }
}

function extractResponseDetail(parsed: unknown, raw: string): unknown {
  if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
    return (parsed as { detail?: unknown }).detail ?? null
  }
  return parsed ?? (raw.trim() ? raw : null)
}

function parseRetryAfterSeconds(value: string | null): number | null {
  if (!value) {
    return null
  }
  const seconds = Number(value)
  if (Number.isFinite(seconds) && seconds >= 0) {
    return Math.ceil(seconds)
  }
  const retryAt = Date.parse(value)
  return Number.isNaN(retryAt) ? null : Math.max(0, Math.ceil((retryAt - Date.now()) / 1000))
}

function isRetryableStatus(status: number): boolean {
  return [408, 425, 429, 500, 502, 503, 504].includes(status)
}

function formatTimeoutSeconds(milliseconds: number): string {
  const seconds = milliseconds / 1000
  return `${Number.isInteger(seconds) ? seconds : seconds.toFixed(1)} seconds`
}

function composeAbortSignals(primary: AbortSignal | null | undefined, secondary: AbortSignal) {
  if (!primary) {
    return { signal: secondary, cleanup: () => {} }
  }

  if (primary.aborted) {
    return { signal: primary, cleanup: () => {} }
  }

  if (secondary.aborted) {
    return { signal: secondary, cleanup: () => {} }
  }

  const controller = new AbortController()
  const abort = (source: AbortSignal) => {
    if (controller.signal.aborted) {
      return
    }
    controller.abort(readAbortReason(source))
  }
  const onPrimaryAbort = () => abort(primary)
  const onSecondaryAbort = () => abort(secondary)

  primary.addEventListener('abort', onPrimaryAbort)
  secondary.addEventListener('abort', onSecondaryAbort)

  return {
    signal: controller.signal,
    cleanup: () => {
      primary.removeEventListener('abort', onPrimaryAbort)
      secondary.removeEventListener('abort', onSecondaryAbort)
    },
  }
}

function readAbortReason(signal: AbortSignal): unknown {
  return 'reason' in signal ? signal.reason : undefined
}

function tryParseJson(value: string): unknown {
  if (!value.trim()) {
    return null
  }
  try {
    return JSON.parse(value) as unknown
  } catch {
    return null
  }
}

function tryParseJsonResult(value: string): { ok: true; value: unknown } | { ok: false } {
  try {
    return { ok: true, value: JSON.parse(value) as unknown }
  } catch {
    return { ok: false }
  }
}

function extractErrorMessage(
  parsed: unknown,
  raw: string,
  statusCode: number,
  statusText: string,
  contentType: string | null,
): string {
  if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
    const detail = (parsed as { detail?: unknown }).detail
    if (typeof detail === 'string' && detail.trim()) {
      return detail
    }
    if (Array.isArray(detail)) {
      const messages = detail.map(formatValidationIssue).filter((message) => message.length > 0)
      if (messages.length) {
        return messages.join('; ')
      }
    }
  }
  if (parsed !== null && raw.trim()) {
    return `The API returned HTTP ${statusCode}${statusText ? ` ${statusText}` : ''} with an unsupported error payload.`
  }
  if (raw.trim() && contentType?.toLowerCase().startsWith('text/plain')) {
    return truncateSingleLine(raw, 500)
  }
  if (raw.trim()) {
    return `The API returned HTTP ${statusCode}${statusText ? ` ${statusText}` : ''} with a non-JSON response.`
  }
  return `HTTP ${statusCode}${statusText ? ` ${statusText}` : ''}`
}

function normalizeTimeoutMs(value: unknown, fallback: number): number {
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError'
}

function truncateSingleLine(value: string, maxLength: number): string {
  const normalized = value.replace(/\s+/g, ' ').trim()
  return normalized.length <= maxLength ? normalized : `${normalized.slice(0, maxLength - 3)}...`
}

function formatValidationIssue(issue: unknown): string {
  if (typeof issue === 'string') {
    return issue.trim()
  }

  if (!issue || typeof issue !== 'object') {
    return ''
  }

  const record = issue as { loc?: unknown; msg?: unknown }
  const message = typeof record.msg === 'string' ? record.msg.trim() : ''
  if (!message) {
    return ''
  }

  if (!Array.isArray(record.loc)) {
    return message
  }

  const location = record.loc
    .filter((part): part is string | number => typeof part === 'string' || typeof part === 'number')
    .join('.')
  return location ? `${location}: ${message}` : message
}

function getCookieValue(name: string): string | null {
  if (typeof document === 'undefined') {
    return null
  }

  const prefix = `${name}=`
  const parts = document.cookie.split(';')
  for (const part of parts) {
    const trimmed = part.trim()
    if (!trimmed.startsWith(prefix)) {
      continue
    }
    return decodeURIComponent(trimmed.slice(prefix.length))
  }
  return null
}
