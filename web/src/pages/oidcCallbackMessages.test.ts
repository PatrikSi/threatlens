import { describe, expect, it } from 'vitest'

import {
  resolveOIDCLinkNotice,
  resolveOIDCLoginError,
  resolveOIDCReauthNotice,
  resolveOIDCReauthStartError,
} from './oidcCallbackMessages'

describe('OIDC callback messages', () => {
  it.each([
    ['provider_configuration_changed', 'configuration changed'],
    ['callback_rate_limited', 'Wait briefly'],
  ])('provides actionable login guidance for %s', (code, expected) => {
    expect(resolveOIDCLoginError(code)).toContain(expected)
  })

  it.each([
    ['provider_configuration_changed', 'Start the link again'],
    ['callback_rate_limited', 'Wait briefly'],
  ])('provides actionable link guidance for %s', (code, expected) => {
    expect(resolveOIDCLinkNotice(code)).toMatchObject({ error: true })
    expect(resolveOIDCLinkNotice(code).message).toContain(expected)
  })

  it.each([
    ['success', 'verification completed', false],
    ['provider_configuration_changed', 'configuration changed', true],
    ['callback_rate_limited', 'Wait briefly', true],
    ['provider_rejected', 'Start verification again', true],
    ['missing_code', 'authorization code', true],
    ['invalid_state', 'expired', true],
    ['reauthentication_failed', 'recent sign-in', true],
    ['reauth_session_expired', 'session changed or expired', true],
    ['reauth_identity_mismatch', 'does not match', true],
    ['authentication_failed', 'could not validate', true],
  ])('maps the OIDC reauthentication result %s', (code, expected, error) => {
    expect(resolveOIDCReauthNotice(code)).toEqual({
      message: expect.stringContaining(expected),
      error,
    })
  })

  it.each([
    ['browser_session_required', 'authenticated browser session'],
    ['opaque_session_required', 'legacy session'],
    ['oidc_session_required', 'not authenticated with SSO'],
    ['session_inactive', 'no longer active'],
    ['oidc_provider_unavailable', 'No enabled identity provider'],
    ['oidc_reauthentication_start_failed', 'test the saved provider'],
  ])('maps the OIDC reauthentication start error %s', (code, expected) => {
    const error = Object.assign(new Error('backend detail'), {
      name: 'ApiError',
      code,
      requestId: 'reauth-start-123',
    })

    expect(resolveOIDCReauthStartError(error)).toContain(expected)
    expect(resolveOIDCReauthStartError(error)).toContain('reauth-start-123')
  })
})
