// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiFetch } from '../api/client'
import {
  beginOIDCReauthentication,
  consumeOIDCReauthContinuation,
  normalizeOIDCReturnPath,
  readOIDCReauthNavigationState,
} from './oidcReauthentication'

vi.mock('../api/client', () => ({ apiFetch: vi.fn() }))

describe('OIDC reauthentication continuation', () => {
  beforeEach(() => {
    sessionStorage.clear()
    vi.mocked(apiFetch).mockReset()
  })

  it('stores only a bounded internal continuation after the backend starts reauthentication', async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      authorization_url: 'https://idp.example.com/authorize',
    })
    const redirect = vi.fn()

    await beginOIDCReauthentication({
      returnPath: '/settings/users?tab=security',
      purpose: 'admin_mfa_reset',
      context: { targetUserId: 'user-1', reason: 'Lost device' },
      redirect,
    })

    expect(apiFetch).toHaveBeenCalledWith('/auth/oidc/reauth', {
      method: 'POST',
    })
    expect(redirect).toHaveBeenCalledWith('https://idp.example.com/authorize')
    const consumed = consumeOIDCReauthContinuation('success')
    expect(consumed).toMatchObject({
      continuation: {
        returnPath: '/settings/users?tab=security',
        purpose: 'admin_mfa_reset',
        context: { targetUserId: 'user-1', reason: 'Lost device' },
      },
      navigationState: {
        oidcReauth: { result: 'success', purpose: 'admin_mfa_reset' },
      },
    })
    expect(consumeOIDCReauthContinuation('success')).toBeNull()
  })

  it('rejects external and protocol-relative return paths', () => {
    expect(normalizeOIDCReturnPath('https://evil.example/settings/users')).toBe(
      '/settings/account',
    )
    expect(normalizeOIDCReturnPath('//evil.example/settings/users')).toBe(
      '/settings/account',
    )
    expect(normalizeOIDCReturnPath('/settings/users')).toBe('/settings/users')
  })

  it('validates navigation state by purpose', () => {
    const state = {
      oidcReauth: {
        result: 'reauthentication_failed',
        purpose: 'oidc_provider_update',
      },
    }
    expect(
      readOIDCReauthNavigationState(state, 'oidc_provider_update'),
    ).toEqual(state.oidcReauth)
    expect(readOIDCReauthNavigationState(state, 'admin_mfa_reset')).toBeNull()
  })

  it('preserves a bounded session-revocation continuation', async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      authorization_url: 'https://idp.example.com/authorize',
    })
    const redirect = vi.fn()

    await beginOIDCReauthentication({
      returnPath: '/settings/account',
      purpose: 'session_revocation',
      context: {
        sessionAction: 'single',
        sessionId: '11111111-1111-4111-8111-111111111111',
      },
      redirect,
    })

    const consumed = consumeOIDCReauthContinuation('success')
    expect(consumed?.continuation).toMatchObject({
      returnPath: '/settings/account',
      purpose: 'session_revocation',
      context: {
        sessionAction: 'single',
        sessionId: '11111111-1111-4111-8111-111111111111',
      },
    })
    expect(
      readOIDCReauthNavigationState(
        consumed?.navigationState,
        'session_revocation',
      ),
    ).toMatchObject({ result: 'success', purpose: 'session_revocation' })
  })

  it('does not redirect when browser storage cannot preserve the privileged continuation', async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      authorization_url: 'https://idp.example.com/authorize',
    })
    const storageWrite = vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation(() => {
        throw new DOMException('Denied', 'SecurityError')
      })
    const redirect = vi.fn()

    await expect(
      beginOIDCReauthentication({
        returnPath: '/settings/identity',
        purpose: 'oidc_provider_update',
        redirect,
      }),
    ).rejects.toThrow('could not preserve the privileged action')
    expect(redirect).not.toHaveBeenCalled()
    storageWrite.mockRestore()
  })
})
