// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
;(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

const securityMocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  markLoggedOut: vi.fn(),
  navigate: vi.fn(),
  currentUserRefetch: vi.fn().mockResolvedValue({}),
  locationState: null as unknown,
  authMethod: 'local' as 'local' | 'oidc',
}))

vi.mock('../api/client', () => ({ apiFetch: securityMocks.apiFetch }))
vi.mock('../components/AuthContext', () => ({
  useAuth: () => ({ markLoggedOut: securityMocks.markLoggedOut }),
}))
vi.mock('../hooks/useUnsavedChangesWarning', () => ({
  useUnsavedChangesWarning: () => ({ discardDialog: null }),
}))
vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({
    data: {
      id: 'user-1',
      authentication: {
        session_auth_method: securityMocks.authMethod,
        recently_authenticated: false,
      },
    },
    refetch: securityMocks.currentUserRefetch,
  }),
}))
vi.mock('react-router-dom', () => ({
  useNavigate: () => securityMocks.navigate,
  useLocation: () => ({
    pathname: '/settings/account',
    search: '',
    state: securityMocks.locationState,
  }),
}))

import { AccountSecuritySection } from './AccountSecuritySection'

let queryClient: QueryClient | null = null
let root: Root | null = null
let container: HTMLDivElement | null = null

const currentSession = {
  id: '11111111-1111-4111-8111-111111111111',
  current: true,
  auth_method: 'local',
  mfa_method: 'totp',
  client_ip: '192.0.2.10',
  user_agent: 'Mozilla/5.0 (Windows NT 10.0) Chrome/130.0',
  authenticated_at: '2026-08-27T08:00:00Z',
  last_seen_at: '2026-08-27T09:00:00Z',
  idle_expires_at: '2026-08-28T09:00:00Z',
  absolute_expires_at: '2026-09-03T08:00:00Z',
  revoked_at: null,
  revoked_reason: null,
} as const

function defaultApi(path: string) {
  if (path === '/auth/security/mfa') {
    return Promise.resolve({
      local_mfa_available: true,
      managed_by: 'local',
      enabled: false,
      confirmed_at: null,
      recovery_codes_remaining: 0,
    })
  }
  if (path === '/auth/security/sessions') {
    return Promise.resolve({
      sessions: [currentSession],
      active_count: 1,
      history_truncated: false,
    })
  }
  return Promise.resolve({})
}

function renderSection() {
  if (!securityMocks.apiFetch.getMockImplementation())
    securityMocks.apiFetch.mockImplementation(defaultApi)
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(
      <QueryClientProvider client={queryClient!}>
        <AccountSecuritySection />
      </QueryClientProvider>,
    )
  })
  return container
}

function setInputValue(input: HTMLInputElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    'value',
  )
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

async function settle() {
  await vi.waitFor(() => {
    expect(queryClient?.isFetching()).toBe(0)
    expect(queryClient?.isMutating()).toBe(0)
  })
}

function mutationResponseData(): string {
  return JSON.stringify(
    queryClient
      ?.getMutationCache()
      .getAll()
      .map((mutation) => mutation.state.data) ?? [],
  )
}

afterEach(async () => {
  await act(async () => root?.unmount())
  queryClient?.clear()
  container?.remove()
  document.body.innerHTML = ''
  queryClient = null
  root = null
  container = null
  securityMocks.apiFetch.mockReset()
  securityMocks.markLoggedOut.mockReset()
  securityMocks.navigate.mockReset()
  securityMocks.currentUserRefetch.mockReset()
  securityMocks.currentUserRefetch.mockResolvedValue({})
  securityMocks.locationState = null
  securityMocks.authMethod = 'local'
  Reflect.deleteProperty(navigator, 'clipboard')
})

describe('AccountSecuritySection', () => {
  it('disables MFA and session mutations when a refresh leaves cached security data stale', async () => {
    let refreshFails = false
    securityMocks.apiFetch.mockImplementation((path: string) => {
      if (refreshFails)
        return Promise.reject(new Error('security backend unavailable'))
      if (path === '/auth/security/mfa') {
        return Promise.resolve({
          local_mfa_available: true,
          managed_by: 'local',
          enabled: true,
          confirmed_at: '2026-08-27T08:00:00Z',
          recovery_codes_remaining: 8,
        })
      }
      if (path === '/auth/security/sessions') {
        return Promise.resolve({
          sessions: [
            currentSession,
            {
              ...currentSession,
              id: '22222222-2222-4222-8222-222222222222',
              current: false,
              client_ip: '192.0.2.20',
            },
          ],
          active_count: 2,
          history_truncated: false,
          active_truncated: false,
        })
      }
      return Promise.resolve({})
    })
    const view = renderSection()
    await act(async () => {
      await settle()
    })
    refreshFails = true

    const refresh = Array.from(
      view.querySelectorAll<HTMLButtonElement>('button'),
    ).find((button) => button.textContent === 'Refresh security status')
    await act(async () => {
      refresh?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 25))
    })
    expect(queryClient?.isFetching()).toBe(0)

    expect(view.textContent).toContain(
      'Security actions are disabled until the current MFA status can be loaded.',
    )
    expect(view.textContent).toContain(
      'Session actions are disabled until the current session list can be loaded.',
    )
    for (const label of [
      'Generate new recovery codes',
      'Disable MFA',
      'Revoke all other sessions',
    ]) {
      const button = Array.from(
        view.querySelectorAll<HTMLButtonElement>('button'),
      ).find((candidate) => candidate.textContent === label)
      expect(button?.disabled).toBe(true)
    }
    expect(
      Array.from(
        view.querySelectorAll<HTMLButtonElement>(
          'button[aria-label^="Revoke "]',
        ),
      ).every((button) => button.disabled),
    ).toBe(true)
  })

  it('explains identity-provider ownership without exposing local MFA controls', async () => {
    securityMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/auth/security/mfa') {
        return Promise.resolve({
          local_mfa_available: false,
          managed_by: 'identity_provider',
          enabled: false,
          confirmed_at: null,
          recovery_codes_remaining: 0,
        })
      }
      return defaultApi(path)
    })
    const view = renderSection()
    await act(async () => await settle())

    expect(view.textContent).toContain('Managed by your identity provider')
    expect(view.textContent).toContain('does not add a second local MFA prompt')
    expect(view.querySelector('#mfa-enrollment-password')).toBeNull()
  })

  it('preserves the password draft when enrollment fails', async () => {
    securityMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/auth/security/mfa/enroll')
        return Promise.reject(new Error('Current password was not accepted'))
      return defaultApi(path)
    })
    const view = renderSection()
    await act(async () => await settle())
    const password = view.querySelector<HTMLInputElement>(
      '#mfa-enrollment-password',
    )!
    act(() => setInputValue(password, 'keep-this-draft'))
    await act(async () => {
      password
        .closest('form')
        ?.dispatchEvent(
          new Event('submit', { bubbles: true, cancelable: true }),
        )
      await settle()
    })

    expect(password.value).toBe('keep-this-draft')
    expect(view.querySelector('[role="alert"]')?.textContent).toContain(
      'enrollment could not be started',
    )
  })

  it('removes a dismissed enrollment secret from component and mutation state', async () => {
    securityMocks.apiFetch.mockImplementation(
      (path: string, options?: { method?: string }) => {
        if (path === '/auth/security/mfa/enroll') {
          return Promise.resolve({
            secret: 'DISMISSED-TOTP-SECRET',
            provisioning_uri: 'otpauth://totp/ThreatLens:dismissed',
          })
        }
        if (
          path === '/auth/security/mfa/enrollment' &&
          options?.method === 'DELETE'
        ) {
          return Promise.resolve({ status: 'ok', cancelled: true })
        }
        return defaultApi(path)
      },
    )
    const view = renderSection()
    await act(async () => await settle())
    act(() =>
      setInputValue(
        view.querySelector<HTMLInputElement>('#mfa-enrollment-password')!,
        'current-password',
      ),
    )
    await act(async () => {
      view
        .querySelector<HTMLInputElement>('#mfa-enrollment-password')
        ?.closest('form')
        ?.dispatchEvent(
          new Event('submit', { bubbles: true, cancelable: true }),
        )
      await settle()
    })
    expect(view.textContent).toContain('DISMISSED-TOTP-SECRET')
    expect(mutationResponseData()).toContain('DISMISSED-TOTP-SECRET')

    await act(async () => {
      Array.from(view.querySelectorAll<HTMLButtonElement>('button'))
        .find((button) => button.textContent?.trim() === 'Cancel setup')
        ?.click()
      await settle()
    })

    expect(securityMocks.apiFetch).toHaveBeenCalledWith(
      '/auth/security/mfa/enrollment',
      {
        method: 'DELETE',
      },
    )
    expect(view.textContent).not.toContain('DISMISSED-TOTP-SECRET')
    expect(mutationResponseData()).not.toContain('DISMISSED-TOTP-SECRET')
  })

  it('keeps enrollment details available when server-side cancellation fails', async () => {
    securityMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/auth/security/mfa/enroll') {
        return Promise.resolve({
          secret: 'RETRYABLE-TOTP-SECRET',
          provisioning_uri: 'otpauth://totp/ThreatLens:retry',
        })
      }
      if (path === '/auth/security/mfa/enrollment')
        return Promise.reject(new Error('Cancellation service unavailable'))
      return defaultApi(path)
    })
    const view = renderSection()
    await act(async () => await settle())
    act(() =>
      setInputValue(
        view.querySelector<HTMLInputElement>('#mfa-enrollment-password')!,
        'current-password',
      ),
    )
    await act(async () => {
      view
        .querySelector<HTMLInputElement>('#mfa-enrollment-password')
        ?.closest('form')
        ?.dispatchEvent(
          new Event('submit', { bubbles: true, cancelable: true }),
        )
      await settle()
    })

    await act(async () => {
      Array.from(view.querySelectorAll<HTMLButtonElement>('button'))
        .find((button) => button.textContent?.trim() === 'Cancel setup')
        ?.click()
      await settle()
    })

    expect(view.textContent).toContain('RETRYABLE-TOTP-SECRET')
    expect(view.querySelector('[role="alert"]')?.textContent).toContain(
      'could not be cancelled',
    )
  })

  it('completes enrollment and requires recovery-code acknowledgement', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    securityMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/auth/security/mfa/enroll') {
        return Promise.resolve({
          secret: 'JBSWY3DPEHPK3PXP',
          provisioning_uri: 'otpauth://totp/ThreatLens:test',
        })
      }
      if (path === '/auth/security/mfa/confirm') {
        return Promise.resolve({
          recovery_codes: ['ONE-CODE', 'TWO-CODE'],
          generated_at: '2026-08-27T09:00:00Z',
        })
      }
      return defaultApi(path)
    })
    const view = renderSection()
    await act(async () => await settle())
    act(() =>
      setInputValue(
        view.querySelector<HTMLInputElement>('#mfa-enrollment-password')!,
        'current-password',
      ),
    )
    await act(async () => {
      view
        .querySelector<HTMLInputElement>('#mfa-enrollment-password')
        ?.closest('form')
        ?.dispatchEvent(
          new Event('submit', { bubbles: true, cancelable: true }),
        )
      await settle()
    })
    expect(view.textContent).toContain('JBSWY3DPEHPK3PXP')

    act(() =>
      setInputValue(
        view.querySelector<HTMLInputElement>('#mfa-confirmation-code')!,
        '123456',
      ),
    )
    await act(async () => {
      view
        .querySelector<HTMLInputElement>('#mfa-confirmation-code')
        ?.closest('form')
        ?.dispatchEvent(
          new Event('submit', { bubbles: true, cancelable: true }),
        )
      await settle()
    })

    const dialog = document.body.querySelector('[role="dialog"]')
    expect(dialog?.textContent).toContain('Store your recovery codes')
    expect(dialog?.textContent).toContain('ONE-CODE')
    expect(
      dialog?.querySelector<HTMLButtonElement>(
        'button[aria-label="Recovery codes must be acknowledged"]',
      )?.disabled,
    ).toBe(true)
    expect(mutationResponseData()).not.toContain('JBSWY3DPEHPK3PXP')
    expect(mutationResponseData()).toContain('ONE-CODE')

    await act(async () => {
      Array.from(dialog?.querySelectorAll<HTMLButtonElement>('button') ?? [])
        .find((button) => button.textContent?.trim() === 'Copy all codes')
        ?.click()
      await Promise.resolve()
    })
    expect(writeText).toHaveBeenCalledWith('ONE-CODE\nTWO-CODE')
    expect(
      dialog?.querySelector('[aria-live="polite"]')?.textContent,
    ).toContain('All recovery codes copied')

    act(() => {
      Array.from(dialog?.querySelectorAll<HTMLButtonElement>('button') ?? [])
        .find((button) => button.textContent?.trim() === 'I stored these codes')
        ?.click()
    })

    expect(document.body.textContent).not.toContain('ONE-CODE')
    expect(mutationResponseData()).not.toContain('ONE-CODE')
    expect(mutationResponseData()).not.toContain('TWO-CODE')
  })

  it('removes regenerated recovery codes from mutation state after acknowledgement', async () => {
    securityMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/auth/security/mfa') {
        return Promise.resolve({
          local_mfa_available: true,
          managed_by: 'local',
          enabled: true,
          confirmed_at: '2026-08-27T09:00:00Z',
          recovery_codes_remaining: 2,
        })
      }
      if (path === '/auth/security/mfa/recovery-codes') {
        return Promise.resolve({
          recovery_codes: ['REGENERATED-ONE', 'REGENERATED-TWO'],
          generated_at: '2026-08-27T10:00:00Z',
        })
      }
      return defaultApi(path)
    })
    const view = renderSection()
    await act(async () => await settle())
    act(() => {
      Array.from(view.querySelectorAll<HTMLButtonElement>('button'))
        .find(
          (button) =>
            button.textContent?.trim() === 'Generate new recovery codes',
        )
        ?.click()
    })
    expect(
      document.body.querySelector('label[for="mfa-sensitive-code"]')
        ?.textContent,
    ).toBe('6-digit authenticator code')
    act(() => {
      setInputValue(
        document.querySelector<HTMLInputElement>('#mfa-sensitive-password')!,
        'current-password',
      )
      setInputValue(
        document.querySelector<HTMLInputElement>('#mfa-sensitive-code')!,
        '123456',
      )
    })
    await act(async () => {
      Array.from(
        document.querySelectorAll<HTMLButtonElement>(
          '[role="alertdialog"] button',
        ),
      )
        .find((button) => button.textContent?.trim() === 'Generate new codes')
        ?.click()
      await settle()
    })

    const dialog = document.body.querySelector('[role="dialog"]')
    expect(dialog?.textContent).toContain('REGENERATED-ONE')
    expect(mutationResponseData()).toContain('REGENERATED-ONE')
    act(() => {
      Array.from(dialog?.querySelectorAll<HTMLButtonElement>('button') ?? [])
        .find((button) => button.textContent?.trim() === 'I stored these codes')
        ?.click()
    })

    expect(document.body.textContent).not.toContain('REGENERATED-ONE')
    expect(mutationResponseData()).not.toContain('REGENERATED-ONE')
    expect(mutationResponseData()).not.toContain('REGENERATED-TWO')
  })

  it('shows current-session consequences and signs out after revocation', async () => {
    securityMocks.apiFetch.mockImplementation(
      (path: string, options?: { method?: string }) => {
        if (
          path === `/auth/security/sessions/${currentSession.id}` &&
          options?.method === 'DELETE'
        ) {
          return Promise.resolve({
            status: 'ok',
            revoked: true,
            current_session_revoked: true,
          })
        }
        return defaultApi(path)
      },
    )
    const view = renderSection()
    await act(async () => await settle())
    const revoke = view.querySelector<HTMLButtonElement>(
      'button[aria-label*="current session"]',
    )!
    act(() => revoke.click())
    expect(
      document.body.querySelector('[role="alertdialog"]')?.textContent,
    ).toContain('sign this browser out immediately')

    const confirm = [
      ...document.body.querySelectorAll<HTMLButtonElement>(
        '[role="alertdialog"] button',
      ),
    ].find((button) => button.textContent === 'Revoke and sign out')!
    await act(async () => {
      confirm.click()
      await settle()
    })

    expect(securityMocks.markLoggedOut).toHaveBeenCalledTimes(1)
    expect(securityMocks.navigate).toHaveBeenCalledWith(
      '/login',
      expect.objectContaining({ replace: true }),
    )
  })

  it('states the selected-session-only impact when revoking one of three browser sessions', async () => {
    const otherSessions = [1, 2].map((index) => ({
      ...currentSession,
      id: `22222222-2222-4222-8222-22222222222${index}`,
      current: false,
      user_agent: `Other browser ${index}`,
      mfa_method: null,
    }))
    securityMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/auth/security/sessions') {
        return Promise.resolve({
          sessions: [currentSession, ...otherSessions],
          active_count: 3,
          history_truncated: false,
        })
      }
      return defaultApi(path)
    })
    const view = renderSection()
    await act(async () => await settle())

    const revoke = [
      ...view.querySelectorAll<HTMLButtonElement>(
        'button[aria-label^="Revoke "]',
      ),
    ].find(
      (button) =>
        !button.getAttribute('aria-label')?.includes('current session'),
    )
    act(() => revoke?.click())
    const dialog = document.body.querySelector('[role="alertdialog"]')
    expect(dialog?.textContent).toContain(
      'Only the selected browser session will be signed out',
    )
    expect(dialog?.textContent).toContain(
      'other sessions, and API tokens remain active',
    )
    expect(dialog?.textContent).toContain('Revoke browser access')
  })

  it('verifies a stale local session and requires revocation confirmation again', async () => {
    const otherSession = {
      ...currentSession,
      id: '22222222-2222-4222-8222-222222222222',
      current: false,
      user_agent: 'Suspicious browser',
      mfa_method: null,
    }
    let revokeAttempts = 0
    securityMocks.apiFetch.mockImplementation(
      (path: string, options?: { method?: string; body?: string }) => {
        if (path === '/auth/security/sessions') {
          return Promise.resolve({
            sessions: [currentSession, otherSession],
            active_count: 2,
            active_truncated: false,
            history_truncated: false,
          })
        }
        if (
          path === `/auth/security/sessions/${otherSession.id}` &&
          options?.method === 'DELETE'
        ) {
          revokeAttempts += 1
          if (revokeAttempts === 1) {
            return Promise.reject(
              Object.assign(new Error('Recent local authentication is required.'), {
                name: 'ApiError',
                status: 403,
                code: 'local_reauthentication_required',
              }),
            )
          }
          return Promise.resolve({
            status: 'ok',
            revoked: true,
            current_session_revoked: false,
            revoked_count: 1,
          })
        }
        if (path === '/auth/security/reauthenticate') {
          return Promise.resolve({
            status: 'ok',
            session_id: currentSession.id,
            authenticated_at: '2026-08-27T10:00:00Z',
            valid_until: '2026-08-27T10:10:00Z',
          })
        }
        return defaultApi(path)
      },
    )
    const view = renderSection()
    await act(async () => await settle())
    const revoke = Array.from(
      view.querySelectorAll<HTMLButtonElement>('button[aria-label^="Revoke "]'),
    ).find(
      (button) => !button.getAttribute('aria-label')?.includes('current session'),
    )!
    act(() => revoke.click())
    const confirm = Array.from(
      document.querySelectorAll<HTMLButtonElement>('[role="alertdialog"] button'),
    ).find((button) => button.textContent === 'Revoke browser access')!
    await act(async () => {
      confirm.click()
      await settle()
    })

    expect(document.body.textContent).toContain('Recent local verification required')
    expect(confirm.disabled).toBe(true)
    await act(async () => {
      confirm.click()
      await settle()
    })
    expect(revokeAttempts).toBe(1)
    const password = document.querySelector<HTMLInputElement>(
      '#session-reauth-password',
    )!
    act(() => setInputValue(password, 'correct horse battery staple'))
    await act(async () => {
      Array.from(document.querySelectorAll<HTMLButtonElement>('button'))
        .find((button) => button.textContent === 'Verify this session')
        ?.click()
      await settle()
    })

    expect(securityMocks.apiFetch).toHaveBeenCalledWith(
      '/auth/security/reauthenticate',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          current_password: 'correct horse battery staple',
        }),
      }),
    )
    expect(document.body.textContent).toContain(
      'Review the revocation details and confirm the action again',
    )
    expect(revokeAttempts).toBe(1)
    expect(confirm.disabled).toBe(false)

    await act(async () => {
      confirm.click()
      await settle()
    })
    expect(revokeAttempts).toBe(2)
  })

  it('restores an exact revocation for explicit confirmation after SSO verification', async () => {
    const otherSession = {
      ...currentSession,
      id: '33333333-3333-4333-8333-333333333333',
      current: false,
      auth_method: 'oidc',
      user_agent: 'Firefox/130.0 Linux',
      mfa_method: 'external',
    }
    securityMocks.authMethod = 'oidc'
    securityMocks.locationState = {
      oidcReauth: {
        result: 'success',
        purpose: 'session_revocation',
        context: {
          sessionAction: 'single',
          sessionId: otherSession.id,
        },
      },
    }
    securityMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/auth/security/sessions') {
        return Promise.resolve({
          sessions: [currentSession, otherSession],
          active_count: 2,
          active_truncated: false,
          history_truncated: false,
        })
      }
      return defaultApi(path)
    })

    renderSection()
    await act(async () => await settle())

    const dialog = document.body.querySelector('[role="alertdialog"]')
    expect(dialog?.textContent).toContain('Revoke browser session?')
    expect(dialog?.textContent).toContain('Firefox on Linux')
    expect(document.body.textContent).toContain(
      'Review the revocation details and confirm the action again',
    )
    expect(securityMocks.navigate).toHaveBeenCalledWith(
      '/settings/account',
      { replace: true, state: null },
    )
  })

  it('announces failed SSO verification as an error and blocks repeated revocation', async () => {
    const otherSession = {
      ...currentSession,
      id: '44444444-4444-4444-8444-444444444444',
      current: false,
      auth_method: 'oidc',
      user_agent: 'Edge/130.0 Windows',
      mfa_method: 'external',
    }
    securityMocks.authMethod = 'oidc'
    securityMocks.locationState = {
      oidcReauth: {
        result: 'reauthentication_failed',
        purpose: 'session_revocation',
        context: {
          sessionAction: 'single',
          sessionId: otherSession.id,
        },
      },
    }
    securityMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/auth/security/sessions') {
        return Promise.resolve({
          sessions: [currentSession, otherSession],
          active_count: 2,
          active_truncated: false,
          history_truncated: false,
        })
      }
      return defaultApi(path)
    })

    renderSection()
    await act(async () => await settle())

    const notice = Array.from(
      document.querySelectorAll<HTMLElement>('[role="alert"]'),
    ).find((candidate) =>
      candidate.textContent?.includes(
        'The identity provider did not prove a recent sign-in',
      ),
    )
    const confirm = Array.from(
      document.querySelectorAll<HTMLButtonElement>('[role="alertdialog"] button'),
    ).find((button) => button.textContent === 'Revoke browser access')
    expect(notice).toBeDefined()
    expect(notice?.getAttribute('aria-live')).toBe('assertive')
    expect(notice?.className).toContain('text-red-700')
    expect(notice?.className).not.toContain('text-green-800')
    expect(confirm?.disabled).toBe(true)
  })

  it('shows idle, maximum, and effective session expiry with truncated-active guidance', async () => {
    securityMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/auth/security/sessions') {
        return Promise.resolve({
          sessions: [currentSession],
          active_count: 201,
          active_truncated: true,
          history_truncated: true,
        })
      }
      return defaultApi(path)
    })

    const view = renderSection()
    await act(async () => await settle())

    expect(view.textContent).toContain('Effective expiry')
    expect(view.textContent).toContain('Idle expiry')
    expect(view.textContent).toContain('Maximum expiry')
    expect(view.textContent).toContain(
      'Some active sessions are omitted by the API limit',
    )
    expect(view.textContent).toContain('200 most recent session records')
    expect(
      [...view.querySelectorAll('button')].some(
        (button) => button.textContent === 'Revoke all other sessions',
      ),
    ).toBe(true)
  })
})
