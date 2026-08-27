// @vitest-environment jsdom

import { describe, expect, it, vi } from 'vitest'

import type { AuthSession } from '../types/identity'
import {
  describeSessionClient,
  downloadRecoveryCodes,
  effectiveSessionExpiry,
  formatAuthMethod,
  formatSessionRevocationResult,
  sessionStatus,
} from './accountSecurityModel'

const session: AuthSession = {
  id: 'session-id',
  current: true,
  auth_method: 'local',
  mfa_method: 'recovery_code',
  client_ip: '192.0.2.10',
  user_agent: 'Mozilla/5.0 (X11; Linux x86_64) Firefox/141.0',
  authenticated_at: '2026-08-27T08:00:00Z',
  last_seen_at: '2026-08-27T09:00:00Z',
  idle_expires_at: '2026-08-28T09:00:00Z',
  absolute_expires_at: '2026-09-03T08:00:00Z',
  revoked_at: null,
  revoked_reason: null,
}

describe('account security model', () => {
  it('summarizes browser, platform, authentication, and state', () => {
    const now = Date.parse('2026-08-27T12:00:00Z')
    expect(describeSessionClient(session)).toBe('Firefox on Linux')
    expect(formatAuthMethod(session)).toBe('Password + recovery code')
    expect(sessionStatus(session, now)).toBe('current')
    expect(sessionStatus({ ...session, current: false }, now)).toBe('active')
    expect(
      sessionStatus(
        { ...session, idle_expires_at: '2026-08-27T11:59:59Z' },
        now,
      ),
    ).toBe('expired')
    expect(
      sessionStatus({ ...session, revoked_at: '2026-08-27T10:00:00Z' }, now),
    ).toBe('revoked')
  })

  it('downloads recovery codes without embedding them in a URL', () => {
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    })
    const createObjectURL = vi
      .spyOn(URL, 'createObjectURL')
      .mockReturnValue('blob:recovery')
    const revokeObjectURL = vi
      .spyOn(URL, 'revokeObjectURL')
      .mockImplementation(() => undefined)
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => undefined)

    downloadRecoveryCodes(['ONE-CODE'], '2026-08-27T09:00:00Z')

    expect(createObjectURL).toHaveBeenCalledOnce()
    expect(click).toHaveBeenCalledOnce()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:recovery')
    createObjectURL.mockRestore()
    revokeObjectURL.mockRestore()
    click.mockRestore()
  })

  it('uses the earlier idle or maximum deadline as the effective expiry', () => {
    expect(effectiveSessionExpiry(session)).toBe('2026-08-28T09:00:00Z')
    expect(
      effectiveSessionExpiry({
        ...session,
        idle_expires_at: '2026-09-10T09:00:00Z',
      }),
    ).toBe('2026-09-03T08:00:00Z')
    expect(
      effectiveSessionExpiry({
        ...session,
        idle_expires_at: 'invalid',
      }),
    ).toBe('2026-09-03T08:00:00Z')
  })

  it('describes selected-session revocation without claiming sibling logout', () => {
    expect(
      formatSessionRevocationResult({
        status: 'ok',
        revoked: true,
        current_session_revoked: false,
        revoked_session_count: 1,
        other_sessions_revoked: 0,
        auth_generation_rotated: false,
      }),
    ).toContain('Only the selected browser session')
    expect(
      formatSessionRevocationResult({
        status: 'ok',
        revoked: true,
        current_session_revoked: false,
        other_sessions_revoked: 2,
        auth_generation_rotated: true,
      }),
    ).toContain('other signed-in browsers must sign in again')
  })
})
