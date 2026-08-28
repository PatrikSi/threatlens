import { ApiError } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'

const TERMINAL_CHALLENGE_CODES = new Set([
  'mfa_challenge_missing',
  'mfa_challenge_invalid_or_expired',
  'mfa_challenge_expired',
  'mfa_challenge_security_changed',
  'mfa_challenge_attempts_exhausted',
])

export function isExpiredMfaChallenge(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status !== 401) {
    return false
  }
  if (error.code) {
    return TERMINAL_CHALLENGE_CODES.has(error.code)
  }
  const detail =
    `${error.message} ${typeof error.detail === 'string' ? error.detail : ''}`.toLowerCase()
  return /challenge.*(missing|expired|no longer)|missing or expired|start sign-in again/.test(
    detail,
  )
}

export function resolveMfaLoginError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'mfa_challenge_security_changed') {
      return resolveApiErrorMessage(
        error,
        'Account security changed during sign-in',
        {
          retryGuidance:
            'Return to password sign-in to start a new verification session.',
        },
      )
    }
    if (error.code === 'mfa_challenge_attempts_exhausted') {
      return resolveApiErrorMessage(
        error,
        'The verification session has no attempts remaining',
        {
          retryGuidance:
            'Return to password sign-in to start a new verification session.',
        },
      )
    }
    if (error.code === 'mfa_login_rate_limited') {
      return resolveApiErrorMessage(
        error,
        'MFA verification is temporarily rate limited',
      )
    }
    if (error.code === 'mfa_verification_unavailable') {
      return resolveApiErrorMessage(
        error,
        'MFA verification is temporarily unavailable',
        {
          retryGuidance:
            'Wait briefly and try again. Contact an administrator if verification remains unavailable.',
        },
      )
    }
    if (error.code === 'account_unavailable') {
      return resolveApiErrorMessage(
        error,
        'This account is not currently available',
        {
          retryGuidance:
            'Contact an administrator to verify the account status.',
        },
      )
    }
    if (error.code === 'mfa_code_invalid') {
      return resolveApiErrorMessage(
        error,
        'The verification code was not accepted',
        {
          retryGuidance:
            'Enter a current authenticator code or an unused recovery code.',
        },
      )
    }
  }
  if (isExpiredMfaChallenge(error)) {
    return resolveApiErrorMessage(error, 'The verification session expired', {
      retryGuidance:
        'Return to password sign-in to start a new verification session.',
    })
  }
  if (error instanceof ApiError && error.status === 401) {
    return resolveApiErrorMessage(
      error,
      'The verification code was not accepted',
      {
        retryGuidance:
          'Enter a current authenticator code or an unused recovery code.',
      },
    )
  }
  return resolveApiErrorMessage(
    error,
    'MFA verification could not be completed',
  )
}
