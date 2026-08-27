import { describe, expect, it } from 'vitest'

import {
  resolveCurrentSession,
  resolvePrivilegedSessionState,
} from './authSessionModel'

describe('resolveCurrentSession', () => {
  it('degrades safely when the session inventory is absent or malformed', () => {
    expect(resolveCurrentSession(undefined)).toBeNull()
    expect(resolveCurrentSession({ sessions: undefined } as never)).toBeNull()
  })

  it('ignores revoked current-session records', () => {
    expect(
      resolveCurrentSession({
        sessions: [
          {
            id: 'session-1',
            current: true,
            revoked_at: '2026-08-27T19:00:00Z',
          },
        ],
      } as never),
    ).toBeNull()
  })

  it('uses authoritative recent-authentication state when the server exposes it', () => {
    expect(
      resolvePrivilegedSessionState(
        {
          credential_kind: 'opaque_session',
          session_id: 'session-1',
          session_auth_method: 'local',
          mfa_method: 'totp',
          recently_authenticated: false,
          recent_authentication_expires_at: null,
          identity_provider_mfa_asserted: false,
          reauthentication_endpoint: '/auth/security/reauthenticate',
          security_actions_supported: true,
        },
        undefined,
      ),
    ).toMatchObject({
      tracked: true,
      authMethod: 'local',
      recentAuthenticationValid: false,
      modernContract: true,
    })
  })

  it('prefers the finalized recent-auth field while accepting the transitional field', () => {
    const base = {
      credential_kind: 'opaque_session',
      session_auth_method: 'oidc',
      mfa_method: 'external',
      recent_authentication_expires_at: '2026-08-27T20:10:00Z',
      identity_provider_mfa_asserted: true,
      reauthentication_endpoint: '/auth/oidc/reauth',
    } as const
    expect(
      resolvePrivilegedSessionState(
        {
          ...base,
          recently_authenticated: false,
          recent_authentication_valid: true,
        },
        undefined,
      ).recentAuthenticationValid,
    ).toBe(false)
    expect(
      resolvePrivilegedSessionState(
        { ...base, recent_authentication_valid: true } as never,
        undefined,
      ).recentAuthenticationValid,
    ).toBe(true)
  })

  it('keeps the legacy current-session fallback for older servers', () => {
    const sessions = {
      sessions: [
        {
          id: 'session-1',
          current: true,
          auth_method: 'oidc',
          revoked_at: null,
        },
      ],
    } as never
    expect(
      resolvePrivilegedSessionState(undefined, sessions, false),
    ).toMatchObject({ recentAuthenticationValid: false })
    expect(
      resolvePrivilegedSessionState(undefined, sessions, true),
    ).toMatchObject({ recentAuthenticationValid: true })
  })
})
