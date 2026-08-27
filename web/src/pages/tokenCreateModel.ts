import { ApiError } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import type { TokenCreateFormState } from '../hooks/useTokenCreateFormState'
import type { ApiTokenCreateRequest } from '../types/api'

export type TokenCreateValidationIssue = {
  message: string
  field: 'name' | 'expiry' | 'password' | 'code'
}

export function buildTokenCreatePayload(
  state: TokenCreateFormState,
  mfaEnabled: boolean,
  localCredentialsRequired = true,
): ApiTokenCreateRequest {
  const scopes = state.scopesText
    .split(',')
    .map((scope) => scope.trim())
    .filter(Boolean)
  return {
    name: state.name,
    expires_in_days: state.expiresInDays,
    ...(scopes.length > 0 ? { scopes } : {}),
    ...(localCredentialsRequired && state.currentPassword
      ? { current_password: state.currentPassword }
      : {}),
    ...(localCredentialsRequired && mfaEnabled && state.code.trim()
      ? { code: state.code.trim() }
      : {}),
  }
}

export function getTokenCreateValidationIssue(
  state: TokenCreateFormState,
  mfaEnabled: boolean,
  localCredentialsRequired = true,
): TokenCreateValidationIssue | null {
  if (!state.name.trim()) {
    return { message: 'Enter a token name.', field: 'name' }
  }
  if (
    !Number.isInteger(state.expiresInDays) ||
    state.expiresInDays < 1 ||
    state.expiresInDays > 3650
  ) {
    return {
      message: 'Expiry must be between 1 and 3650 days.',
      field: 'expiry',
    }
  }
  if (localCredentialsRequired && !state.currentPassword) {
    return { message: 'Enter your current password.', field: 'password' }
  }
  if (localCredentialsRequired && mfaEnabled && !state.code.trim()) {
    return {
      message: 'Enter a current authenticator or recovery code.',
      field: 'code',
    }
  }
  return null
}

export function resolveTokenCreateError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'opaque_session_required') {
      return resolveApiErrorMessage(
        error,
        'This browser session cannot create API tokens',
        {
          retryGuidance: 'Sign out, sign in again, then retry token creation.',
        },
      )
    }
    if (error.code === 'oidc_reauthentication_required') {
      return resolveApiErrorMessage(
        error,
        'Recent SSO verification is required',
        {
          retryGuidance:
            'Use Verify with SSO, then review and submit the restored token request.',
        },
      )
    }
    if (
      error.code === 'session_inactive' ||
      error.code === 'account_security_changed'
    ) {
      return resolveApiErrorMessage(
        error,
        'Your account security session changed',
        {
          retryGuidance: 'Sign out, sign in again, then retry token creation.',
        },
      )
    }
    if (error.code === 'mfa_verification_required') {
      return resolveApiErrorMessage(
        error,
        'Multi-factor verification is required',
        {
          retryGuidance:
            'Enter a current authenticator or unused recovery code.',
        },
      )
    }
    if (error.code === 'mfa_code_invalid') {
      return resolveApiErrorMessage(
        error,
        'The verification code was not accepted',
        {
          retryGuidance:
            'Enter a current authenticator or unused recovery code.',
        },
      )
    }
    if (error.code === 'mfa_verification_unavailable') {
      return resolveApiErrorMessage(error, 'MFA verification is unavailable', {
        retryGuidance:
          'Wait briefly and try again. Contact an administrator if the problem continues.',
      })
    }
  }
  return resolveApiErrorMessage(error, 'API token could not be created')
}
