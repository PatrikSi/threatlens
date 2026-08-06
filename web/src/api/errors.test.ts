import { describe, expect, it } from 'vitest'

import { ApiError, ApiTransportError } from './client'
import { resolveApiErrorMessage } from './errors'

describe('resolveApiErrorMessage', () => {
  it('combines operation context, backend detail, retry guidance, and request reference', () => {
    const error = new ApiError('OIDC discovery endpoint did not respond.', 503, '/auth/oidc/test', null, {
      code: 'service_unavailable',
      requestId: 'request-503',
      retryable: true,
    })

    expect(resolveApiErrorMessage(error, 'Identity provider test failed')).toBe(
      'Identity provider test failed. OIDC discovery endpoint did not respond. ' +
        'Try again. Check API and worker health if the problem continues. Request reference: request-503.',
    )
  })

  it('reports rate-limit retry timing', () => {
    const error = new ApiError('Too many requests.', 429, '/auth/login', null, {
      retryable: true,
      retryAfterSeconds: 45,
    })

    expect(resolveApiErrorMessage(error, 'Sign in failed')).toContain('Try again in about 45 seconds.')
  })

  it('can suppress API detail for enumeration-sensitive authentication failures', () => {
    const error = new ApiError('Sensitive authentication detail.', 401, '/auth/login', null, {
      requestId: 'login-request',
    })

    const message = resolveApiErrorMessage(error, 'Sign in failed', {
      includeApiDetail: false,
      retryGuidance: 'Check your credentials and try again.',
    })

    expect(message).toContain('Check your credentials and try again.')
    expect(message).toContain('Request reference: login-request.')
    expect(message).not.toContain('Sensitive authentication detail')
  })

  it('gives accurate recovery guidance for CSRF failures', () => {
    const error = new ApiError('Missing or invalid CSRF token', 403, '/auth/logout')

    const message = resolveApiErrorMessage(error, 'Logout could not be completed')

    expect(message).toContain('Refresh the page to renew the browser session')
    expect(message).not.toContain('required role')
  })

  it('describes network failures without exposing browser implementation messages', () => {
    const error = new ApiTransportError(
      'ThreatLens could not reach the API. Check the network connection and API container health.',
      '/feeds',
      'network',
      new TypeError('Failed to fetch'),
    )

    const message = resolveApiErrorMessage(error, 'Feeds could not be loaded')

    expect(message).toContain('Check the network connection and API container health.')
    expect(message).not.toContain('Failed to fetch')
  })
})
