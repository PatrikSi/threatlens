import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, ApiTransportError, apiFetch } from './client'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('apiFetch', () => {
  it('returns undefined for empty successful responses', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response('', { status: 200 }))),
    )

    await expect(apiFetch('/empty')).resolves.toBeUndefined()
  })

  it('throws for non-JSON successful API responses', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response('accepted', { status: 202, headers: { 'content-type': 'text/plain' } }))),
    )

    await expect(apiFetch('/accepted')).rejects.toMatchObject({
      status: 202,
      path: '/accepted',
      detail: 'accepted',
    })
  })

  it('preserves JSON null successful responses', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response('null', { status: 200, headers: { 'content-type': 'application/json' } }))),
    )

    await expect(apiFetch<null>('/nullable')).resolves.toBeNull()
  })

  it('formats FastAPI validation issue arrays into readable messages', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              detail: [
                {
                  type: 'extra_forbidden',
                  loc: ['body', 'query_json', 'windows', 1, 'rss_filters', 'time_range'],
                  msg: 'Extra inputs are not permitted',
                },
              ],
            }),
            { status: 422, headers: { 'content-type': 'application/json' } },
          ),
        ),
      ),
    )

    await expect(apiFetch('/views/view-1', { method: 'PATCH', body: '{}' })).rejects.toMatchObject({
      status: 422,
      message: 'body.query_json.windows.1.rss_filters.time_range: Extra inputs are not permitted',
    })
  })

  it('preserves structured diagnostics and the actual response detail', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              detail: 'OIDC discovery failed for the configured issuer.',
              error: {
                code: 'service_unavailable',
                message: 'OIDC discovery failed for the configured issuer.',
                request_id: 'request-123',
                status: 503,
                retryable: true,
              },
            }),
            { status: 503, headers: { 'content-type': 'application/json', 'retry-after': '17' } },
          ),
        ),
      ),
    )

    const error = await apiFetch('/auth/oidc/test').catch((caught) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      status: 503,
      detail: 'OIDC discovery failed for the configured issuer.',
      code: 'service_unavailable',
      requestId: 'request-123',
      retryable: true,
      retryAfterSeconds: 17,
    })
  })

  it('does not expose proxy HTML as an API error message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          new Response('<html><body>proxy implementation detail</body></html>', {
            status: 502,
            statusText: 'Bad Gateway',
            headers: { 'content-type': 'text/html' },
          }),
        ),
      ),
    )

    const error = await apiFetch('/feeds').catch((caught) => caught)

    expect(error).toMatchObject({
      status: 502,
      message: 'The API returned HTTP 502 Bad Gateway with a non-JSON response.',
    })
    expect((error as ApiError).message).not.toContain('proxy implementation detail')
  })

  it('supports HTTP-date Retry-After headers', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-06T12:00:00Z'))
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ detail: 'Rate limit reached.' }), {
            status: 429,
            headers: { 'content-type': 'application/json', 'retry-after': 'Thu, 06 Aug 2026 12:00:30 GMT' },
          }),
        ),
      ),
    )

    await expect(apiFetch('/limited')).rejects.toMatchObject({ retryAfterSeconds: 30 })
  })

  it('turns connection failures into actionable transport errors', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))))

    await expect(apiFetch('/health')).rejects.toMatchObject({
      name: 'ApiTransportError',
      kind: 'network',
      path: '/health',
      retryable: true,
      message: 'ThreatLens could not reach the API. Check the network connection and API container health.',
    } satisfies Partial<ApiTransportError>)
  })

  it('distinguishes request timeouts from other transport failures', async () => {
    vi.useFakeTimers()
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, options: RequestInit) =>
        new Promise((_resolve, reject) => {
          options.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
        }),
      ),
    )

    const request = apiFetch('/slow', { timeoutMs: 1500 })
    const rejection = expect(request).rejects.toMatchObject({
      name: 'ApiTransportError',
      kind: 'timeout',
      message: 'The ThreatLens API did not respond within 1.5 seconds.',
    } satisfies Partial<ApiTransportError>)
    await vi.advanceTimersByTimeAsync(1500)

    await rejection
  })

  it('falls back to the default timeout when an invalid timeout is supplied', async () => {
    vi.useFakeTimers()
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, options: RequestInit) =>
        new Promise((_resolve, reject) => {
          options.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
        }),
      ),
    )

    const request = apiFetch('/slow', { timeoutMs: -1 })
    const rejection = expect(request).rejects.toMatchObject({
      kind: 'timeout',
      message: 'The ThreatLens API did not respond within 15 seconds.',
    } satisfies Partial<ApiTransportError>)
    await vi.advanceTimersByTimeAsync(14999)
    await vi.advanceTimersByTimeAsync(1)

    await rejection
  })

  it('formats string detail arrays into readable messages', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ detail: ['first problem', 'second problem'] }), {
            status: 422,
            headers: { 'content-type': 'application/json' },
          }),
        ),
      ),
    )

    await expect(apiFetch('/mixed-error')).rejects.toMatchObject({
      status: 422,
      message: 'first problem; second problem',
    })
  })
})
