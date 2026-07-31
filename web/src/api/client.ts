const DEFAULT_API_BASE_URL = import.meta.env.DEV
  ? typeof window !== 'undefined'
    ? `${window.location.protocol}//${window.location.hostname}:8000/v1`
    : 'http://localhost:8000/v1'
  : '/api/v1'
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? DEFAULT_API_BASE_URL
const REQUEST_TIMEOUT_MS = Number(import.meta.env.VITE_API_TIMEOUT_MS ?? 15000)
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

  constructor(message: string, status: number, path: string, detail: unknown = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.path = path
    this.detail = detail
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
    const csrfToken = getCookieValue(CSRF_COOKIE_NAME)
    if (csrfToken && !headers.has(CSRF_HEADER_NAME)) {
      headers.set(CSRF_HEADER_NAME, csrfToken)
    }
  }

  const timeoutController = new AbortController()
  const { signal, cleanup } = composeAbortSignals(requestOptions.signal, timeoutController.signal)
  const requestTimeoutMs = timeoutMs ?? REQUEST_TIMEOUT_MS
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
    if (error instanceof DOMException && error.name === 'AbortError' && !requestOptions.signal?.aborted) {
      throw new Error(`Request timed out after ${requestTimeoutMs / 1000}s (${path})`, { cause: error })
    }
    throw error
  } finally {
    clearTimeout(timeout)
    cleanup()
  }

  if (!response.ok) {
    const raw = await response.text()
    const parsed = tryParseJson(raw)
    const message = extractErrorMessage(parsed, raw, response.status)
    throw new ApiError(message, response.status, path, parsed)
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
    throw new ApiError(`Expected JSON response from API (${path})`, response.status, path, raw)
  }
  return parsed.value as T
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

function extractErrorMessage(parsed: unknown, raw: string, statusCode: number): string {
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
  if (raw.trim()) {
    return raw
  }
  return `HTTP ${statusCode}`
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
