import { describe, expect, it } from 'vitest'

import { ApiError } from '../api/client'
import { isExpiredMfaChallenge, resolveMfaLoginError } from './loginMfaModel'

function apiError(
  code: string,
  message: string,
  status = 401,
  retryAfterSeconds: number | null = null,
) {
  return new ApiError(message, status, '/auth/mfa/verify', message, {
    code,
    requestId: 'request-123',
    retryAfterSeconds,
  })
}

describe('MFA login error model', () => {
  it.each([
    'mfa_challenge_missing',
    'mfa_challenge_invalid_or_expired',
    'mfa_challenge_expired',
    'mfa_challenge_security_changed',
    'mfa_challenge_attempts_exhausted',
  ])(
    'uses terminal challenge code %s instead of parsing detail text',
    (code) => {
      expect(
        isExpiredMfaChallenge(apiError(code, 'Unrelated legacy detail')),
      ).toBe(true)
    },
  )

  it('does not classify a coded invalid MFA value as an expired challenge', () => {
    const error = apiError(
      'mfa_code_invalid',
      'Challenge expired is misleading legacy text',
    )

    expect(isExpiredMfaChallenge(error)).toBe(false)
    expect(resolveMfaLoginError(error)).toContain(
      'verification code was not accepted',
    )
  })

  it('uses retry-after guidance for MFA login throttling', () => {
    const message = resolveMfaLoginError(
      apiError('mfa_login_rate_limited', 'Too many attempts', 429, 42),
    )

    expect(message).toContain('rate limited')
    expect(message).toContain('42 seconds')
    expect(message).toContain('request-123')
  })

  it('gives a distinct recovery path when account security changed', () => {
    expect(
      resolveMfaLoginError(
        apiError('mfa_challenge_security_changed', 'Security changed'),
      ),
    ).toContain('Return to password sign-in')
  })
})
