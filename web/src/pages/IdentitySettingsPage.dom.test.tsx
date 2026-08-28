// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
;(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

const identityPageMocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  locationState: null as unknown,
  currentUser: {
    data: {
      id: 'admin-1',
      email: 'admin@example.com',
      role: 'admin',
      is_active: true,
      is_approved: true,
      authentication: undefined as
        | undefined
        | {
            credential_kind: 'opaque_session'
            session_id: string
            session_auth_method: 'local' | 'oidc'
            mfa_method: 'totp' | 'external' | null
            recently_authenticated: boolean
            recent_authentication_expires_at: string | null
            identity_provider_mfa_asserted: boolean
            reauthentication_endpoint: string
            security_actions_supported: boolean
          },
    },
    isLoading: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(() => Promise.resolve()),
  },
}))

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, apiFetch: identityPageMocks.apiFetch }
})

vi.mock('../hooks/useUnsavedChangesWarning', () => ({
  useUnsavedChangesWarning: () => ({
    discardDialog: null,
    confirmDiscard: (action: () => void) => action(),
  }),
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => identityPageMocks.currentUser,
}))

vi.mock('react-router-dom', () => ({
  useLocation: () => ({
    pathname: '/settings/identity',
    search: '',
    hash: '',
    state: identityPageMocks.locationState,
    key: 'test',
  }),
}))

import { IdentitySettingsPage } from './IdentitySettingsPage'
import { ApiError } from '../api/client'

let queryClient: QueryClient | null = null
let root: Root | null = null
let container: HTMLDivElement | null = null

const providerSettings = {
  id: 'provider-1',
  configured: true,
  config_revision: 3,
  name: 'Acme SSO',
  enabled: true,
  issuer_url: 'https://idp.example.com',
  client_id: 'threatlens',
  has_client_secret: true,
  client_auth_method: 'client_secret_basic',
  public_base_url: 'https://threatlens.example.com',
  callback_url: 'https://threatlens.example.com/custom/oidc/callback',
  callback_path: '/custom/oidc/callback',
  scopes: ['openid', 'profile', 'email', 'groups'],
  role_claim: 'groups',
  role_mappings: [{ claim_value: 'soc-analysts', role: 'analyst' }],
  default_role: 'viewer',
  jit_provisioning_enabled: true,
  auto_approve_users: false,
  require_verified_email: true,
  sync_roles_on_login: true,
  created_at: '2026-07-31T10:00:00Z',
  updated_at: '2026-07-31T10:00:00Z',
}

const localSessionInventory = {
  sessions: [
    {
      id: 'session-admin-1',
      current: true,
      auth_method: 'local',
      mfa_method: 'totp',
      client_ip: '192.0.2.10',
      user_agent: 'Test browser',
      authenticated_at: '2026-08-27T10:00:00Z',
      last_seen_at: '2026-08-27T10:00:00Z',
      idle_expires_at: '2026-08-28T10:00:00Z',
      absolute_expires_at: '2026-09-03T10:00:00Z',
      revoked_at: null,
      revoked_reason: null,
    },
  ],
  active_count: 1,
  history_truncated: false,
}

const localMfaStatus = {
  local_mfa_available: true,
  managed_by: 'local',
  enabled: true,
  confirmed_at: '2026-08-27T09:00:00Z',
  recovery_codes_remaining: 8,
}

function renderPage() {
  if (!identityPageMocks.apiFetch.getMockImplementation()) {
    identityPageMocks.apiFetch.mockImplementation((path: string) =>
      Promise.resolve(
        path === '/auth/security/sessions'
          ? localSessionInventory
          : path === '/auth/security/mfa'
            ? localMfaStatus
            : providerSettings,
      ),
    )
  }
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(
      <QueryClientProvider client={queryClient!}>
        <IdentitySettingsPage />
      </QueryClientProvider>,
    )
  })
  return container
}

function flushPromises() {
  return new Promise((resolve) => window.setTimeout(resolve, 0))
}

function setInputValue(input: HTMLInputElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    'value',
  )
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

afterEach(async () => {
  await act(async () => {
    root?.unmount()
    await flushPromises()
  })
  queryClient?.clear()
  root = null
  queryClient = null
  container?.remove()
  container = null
  identityPageMocks.apiFetch.mockReset()
  identityPageMocks.locationState = null
  identityPageMocks.currentUser.data.authentication = undefined
  identityPageMocks.currentUser.refetch.mockClear()
  localSessionInventory.sessions[0].auth_method = 'local'
})

describe('IdentitySettingsPage', () => {
  it('renders provider connection, callback, provisioning, and role mapping controls', async () => {
    const view = renderPage()
    await act(async () => {
      await flushPromises()
      await flushPromises()
    })
    await vi.waitFor(() => {
      expect(view.querySelector<HTMLInputElement>('#oidc-name')?.value).toBe(
        'Acme SSO',
      )
    })

    expect(view.querySelector<HTMLInputElement>('#oidc-issuer')?.value).toBe(
      'https://idp.example.com',
    )
    expect(view.querySelector<HTMLInputElement>('#oidc-mapping-0')?.value).toBe(
      'soc-analysts',
    )
    expect(view.querySelector('label[for="oidc-mapping-0"]')?.textContent).toBe(
      'Exact claim value',
    )
    expect(
      view.querySelector('label[for="oidc-mapping-role-0"]')?.textContent,
    ).toBe('ThreatLens role')
    expect(
      view.querySelector(
        'button[aria-label="Remove role mapping soc-analysts"]',
      ),
    ).not.toBeNull()
    expect(view.textContent).toContain(
      'https://threatlens.example.com/custom/oidc/callback',
    )
    expect(view.textContent).toContain('JIT provisioning')
    expect(view.textContent).toContain('Sync roles on sign-in')
    expect(view.textContent).toContain('Require verified email')

    act(() => {
      setInputValue(
        view.querySelector<HTMLInputElement>('#oidc-public-url')!,
        'https://new.example.com/',
      )
    })
    expect(view.textContent).toContain(
      'https://new.example.com/custom/oidc/callback',
    )
  })

  it('warns when either side of the OIDC flow uses plaintext HTTP', async () => {
    const view = renderPage()
    await act(async () => {
      await flushPromises()
      await flushPromises()
    })
    await vi.waitFor(() => {
      expect(view.querySelector<HTMLInputElement>('#oidc-issuer')?.value).toBe(
        'https://idp.example.com',
      )
    })

    act(() => {
      setInputValue(
        view.querySelector<HTMLInputElement>('#oidc-issuer')!,
        'http://idp.internal',
      )
    })

    expect(view.querySelector('[role="alert"]')?.textContent).toContain(
      'HTTP can expose authorization codes',
    )
  })

  it('warns when JIT provisioning accepts unverified internal email identifiers', async () => {
    const view = renderPage()
    await act(async () => {
      await flushPromises()
      await flushPromises()
    })
    await vi.waitFor(() => {
      expect(view.textContent).toContain('Require verified email')
    })

    const toggle = [...view.querySelectorAll('label')].find((label) =>
      label.textContent?.includes('Require verified email'),
    )
    act(() => {
      toggle?.querySelector<HTMLInputElement>('input')?.click()
    })

    expect(view.querySelector('[role="alert"]')?.textContent).toContain(
      '.local',
    )
    expect(view.querySelector('[role="alert"]')?.textContent).toContain(
      'malformed',
    )
  })

  it('focuses and identifies the exact invalid field when saving', async () => {
    const view = renderPage()
    await act(async () => {
      await flushPromises()
      await flushPromises()
    })
    await vi.waitFor(() =>
      expect(view.querySelector<HTMLInputElement>('#oidc-issuer')?.value).toBe(
        'https://idp.example.com',
      ),
    )
    const requestAnimationFrame = vi
      .spyOn(window, 'requestAnimationFrame')
      .mockImplementation((callback) => {
        callback(0)
        return 1
      })

    const issuer = view.querySelector<HTMLInputElement>('#oidc-issuer')!
    act(() => setInputValue(issuer, ''))
    act(() => {
      ;[...view.querySelectorAll<HTMLButtonElement>('button')]
        .find((button) => button.textContent === 'Review changes')
        ?.click()
    })

    expect(document.activeElement).toBe(issuer)
    expect(issuer.getAttribute('aria-invalid')).toBe('true')
    expect(issuer.getAttribute('aria-errormessage')).toBe('oidc-form-error')
    expect(view.querySelector('#oidc-form-error')?.textContent).toContain(
      'Enter the OIDC issuer URL',
    )
    requestAnimationFrame.mockRestore()
  })

  it('shows provider loading failures separately and offers a retry', async () => {
    identityPageMocks.apiFetch.mockRejectedValue(
      new Error('identity database unavailable'),
    )
    const view = renderPage()
    await act(async () => {
      await vi.waitFor(() =>
        expect(view.querySelector('[role="alert"]')).not.toBeNull(),
      )
    })

    expect(view.textContent).toContain('Identity settings could not be loaded')
    expect(view.textContent).toContain('identity database unavailable')
    expect(view.querySelector('#oidc-name')).toBeNull()
    const retry = [...view.querySelectorAll<HTMLButtonElement>('button')].find(
      (button) => button.textContent === 'Retry identity settings',
    )
    act(() => retry?.click())
    expect(
      identityPageMocks.apiFetch.mock.calls.filter(
        ([path]) => path === '/auth/oidc/provider',
      ),
    ).toHaveLength(2)
  })

  it('labels cached provider settings as stale and blocks editing until refresh succeeds', async () => {
    let failProviderRefresh = false
    identityPageMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/auth/security/sessions') return Promise.resolve(localSessionInventory)
      if (path === '/auth/security/mfa') return Promise.resolve(localMfaStatus)
      if (path === '/auth/oidc/provider' && failProviderRefresh) {
        return Promise.reject(new Error('provider refresh timed out'))
      }
      return Promise.resolve(providerSettings)
    })
    const view = renderPage()
    await act(async () => {
      await flushPromises()
      await flushPromises()
    })
    await vi.waitFor(() =>
      expect(view.querySelector<HTMLInputElement>('#oidc-name')?.value).toBe('Acme SSO'),
    )

    failProviderRefresh = true
    await act(async () => {
      await queryClient?.refetchQueries({ queryKey: ['auth', 'oidc', 'provider'] })
      await flushPromises()
    })

    expect(view.textContent).toContain('provider refresh timed out')
    expect(view.textContent).toContain('Last-known settings remain visible')
    expect(view.textContent).toContain('Editing and saving are disabled until a refresh succeeds')
    expect(view.querySelector<HTMLInputElement>('#oidc-name')?.matches(':disabled')).toBe(true)
    const review = Array.from(view.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent === 'Review changes',
    )
    expect(review?.disabled).toBe(true)

    failProviderRefresh = false
    const retry = Array.from(view.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent === 'Retry identity settings',
    )
    await act(async () => {
      retry?.click()
      await flushPromises()
      await flushPromises()
    })
    await vi.waitFor(() =>
      expect(view.querySelector<HTMLInputElement>('#oidc-name')?.matches(':disabled')).toBe(false),
    )
    expect(view.textContent).not.toContain('Last-known settings remain visible')
  })

  it('blocks a two-admin conflict until the operator explicitly rebases the draft', async () => {
    const updateBodies: Array<Record<string, unknown>> = []
    let updateAttempts = 0
    identityPageMocks.apiFetch.mockImplementation(
      (path: string, options?: { method?: string; body?: string }) => {
        if (path === '/auth/security/sessions')
          return Promise.resolve(localSessionInventory)
        if (path === '/auth/oidc/provider' && options?.method === 'PUT') {
          updateAttempts += 1
          updateBodies.push(
            JSON.parse(options.body ?? '{}') as Record<string, unknown>,
          )
          if (updateAttempts === 1) {
            return Promise.reject(
              new ApiError(
                'The provider configuration changed after it was loaded.',
                409,
                path,
                'The provider configuration changed after it was loaded.',
                {
                  code: 'oidc_provider_revision_conflict',
                  requestId: 'oidc-conflict-123',
                },
              ),
            )
          }
          return Promise.resolve({
            ...providerSettings,
            name: 'Operator draft',
            config_revision: 5,
            updated_at: '2026-08-27T12:00:00Z',
          })
        }
        if (path === '/auth/oidc/provider') {
          return Promise.resolve(
            updateAttempts > 0
              ? {
                  ...providerSettings,
                  name: 'Server-side update',
                  config_revision: 4,
                  updated_at: '2026-08-27T11:00:00Z',
                }
              : providerSettings,
          )
        }
        return Promise.resolve({})
      },
    )

    const view = renderPage()
    await act(async () => {
      await flushPromises()
      await flushPromises()
    })
    await vi.waitFor(() =>
      expect(view.querySelector<HTMLInputElement>('#oidc-name')?.value).toBe(
        'Acme SSO',
      ),
    )
    act(() =>
      setInputValue(
        view.querySelector<HTMLInputElement>('#oidc-name')!,
        'Operator draft',
      ),
    )

    await act(async () => {
      ;[...view.querySelectorAll<HTMLButtonElement>('button')]
        .find((button) => button.textContent === 'Review changes')
        ?.click()
      await flushPromises()
      ;[...document.body.querySelectorAll<HTMLButtonElement>('button')]
        .find((button) => button.textContent === 'Apply identity changes')
        ?.click()
      await vi.waitFor(() => expect(updateAttempts).toBe(1))
      await flushPromises()
    })
    await vi.waitFor(() => {
      expect(view.textContent).toContain('Settings changed on the server')
      expect(view.textContent).toContain('Display name')
      expect(view.textContent).toContain('Server-side update')
      expect(view.textContent).toContain('your draft has Operator draft')
      expect(view.textContent).toContain('oidc-conflict-123')
      expect(view.querySelector<HTMLInputElement>('#oidc-name')?.value).toBe(
        'Operator draft',
      )
      expect(queryClient?.isFetching()).toBe(0)
    })
    expect(updateBodies[0].expected_config_revision).toBe(3)
    expect(
      [...view.querySelectorAll<HTMLButtonElement>('button')].find(
        (button) => button.textContent === 'Review changes',
      )?.disabled,
    ).toBe(true)

    act(() => {
      ;[...view.querySelectorAll<HTMLButtonElement>('button')]
        .find((button) => button.textContent === 'Rebase my draft')
        ?.click()
    })

    await act(async () => {
      ;[...view.querySelectorAll<HTMLButtonElement>('button')]
        .find((button) => button.textContent === 'Review changes')
        ?.click()
      await flushPromises()
      ;[...document.body.querySelectorAll<HTMLButtonElement>('button')]
        .find((button) => button.textContent === 'Apply identity changes')
        ?.click()
      await vi.waitFor(() => expect(updateAttempts).toBe(2))
      await flushPromises()
    })

    expect(updateBodies[1].expected_config_revision).toBe(4)
    await vi.waitFor(() =>
      expect(view.textContent).toContain('Identity provider settings saved'),
    )
    expect(view.textContent).not.toContain('Settings changed on the server')
  })

  it('closes an open impact review when a background refresh detects a newer provider revision', async () => {
    const view = renderPage()
    await act(async () => {
      await flushPromises()
      await flushPromises()
    })
    act(() =>
      setInputValue(
        view.querySelector<HTMLInputElement>('#oidc-name')!,
        'Operator draft',
      ),
    )
    act(() => {
      ;[...view.querySelectorAll<HTMLButtonElement>('button')]
        .find((button) => button.textContent === 'Review changes')
        ?.click()
    })
    expect(document.body.querySelector('[role="alertdialog"]')).not.toBeNull()

    act(() => {
      queryClient?.setQueryData(['auth', 'oidc', 'provider'], {
        ...providerSettings,
        config_revision: 4,
        name: 'Server-side update',
        updated_at: '2026-08-27T20:10:00Z',
      })
    })
    await act(async () => await flushPromises())

    expect(document.body.querySelector('[role="alertdialog"]')).toBeNull()
    expect(view.textContent).toContain('Settings changed on the server')
    expect(
      [...view.querySelectorAll<HTMLButtonElement>('button')].find(
        (button) => button.textContent === 'Review changes',
      )?.disabled,
    ).toBe(true)
  })

  it('closes impact review and requires verification again when recent auth expires before save', async () => {
    identityPageMocks.apiFetch.mockImplementation(
      (path: string, options?: { method?: string }) => {
        if (path === '/auth/security/sessions')
          return Promise.resolve(localSessionInventory)
        if (path === '/auth/security/mfa')
          return Promise.resolve(localMfaStatus)
        if (path === '/auth/oidc/provider' && options?.method === 'PUT') {
          return Promise.reject(
            new ApiError(
              'Recent local authentication is required.',
              403,
              path,
              'Recent local authentication is required.',
              {
                code: 'local_reauthentication_required',
                requestId: 'oidc-recent-auth-123',
              },
            ),
          )
        }
        return Promise.resolve(providerSettings)
      },
    )
    const view = renderPage()
    await act(async () => {
      await flushPromises()
      await flushPromises()
    })
    act(() =>
      setInputValue(
        view.querySelector<HTMLInputElement>('#oidc-name')!,
        'Renamed provider',
      ),
    )
    act(() => {
      ;[...view.querySelectorAll<HTMLButtonElement>('button')]
        .find((button) => button.textContent === 'Review changes')
        ?.click()
    })
    await act(async () => {
      ;[...document.body.querySelectorAll<HTMLButtonElement>('button')]
        .find((button) => button.textContent === 'Apply identity changes')
        ?.click()
      await flushPromises()
      await flushPromises()
    })

    expect(document.body.textContent).toContain(
      'Administrator verification expired before the provider change could be saved',
    )
    expect(document.body.textContent).toContain('oidc-recent-auth-123')
    expect(document.body.querySelector('[role="alertdialog"]')).toBeNull()
    expect(identityPageMocks.currentUser.refetch).toHaveBeenCalled()
  })

  it('requires explicit acknowledgement for dangerous automatic administrator provisioning', async () => {
    const view = renderPage()
    await act(async () => {
      await flushPromises()
      await flushPromises()
    })
    await vi.waitFor(() =>
      expect(view.querySelector<HTMLInputElement>('#oidc-name')?.value).toBe(
        'Acme SSO',
      ),
    )

    act(() => {
      setSelectValue(
        view.querySelector<HTMLSelectElement>('#oidc-default-role')!,
        'admin',
      )
      const autoApprove = [...view.querySelectorAll('label')]
        .find((label) => label.textContent?.includes('Auto-approve JIT users'))
        ?.querySelector<HTMLInputElement>('input')
      const verifiedEmail = [...view.querySelectorAll('label')]
        .find((label) => label.textContent?.includes('Require verified email'))
        ?.querySelector<HTMLInputElement>('input')
      autoApprove?.click()
      verifiedEmail?.click()
    })
    act(() => {
      ;[...view.querySelectorAll<HTMLButtonElement>('button')]
        .find((button) => button.textContent === 'Review changes')
        ?.click()
    })

    const dialog = document.body.querySelector('[role="alertdialog"]')
    expect(dialog?.textContent).toContain('Critical combination')
    const apply = [
      ...(dialog?.querySelectorAll<HTMLButtonElement>('button') ?? []),
    ].find((button) => button.textContent === 'Apply identity changes')
    expect(apply?.disabled).toBe(true)
    act(() =>
      dialog
        ?.querySelector<HTMLInputElement>('input[type="checkbox"]')
        ?.click(),
    )
    expect(apply?.disabled).toBe(false)
  })

  it('chooses provider verification from the current OIDC session and unlocks only after callback success', async () => {
    localSessionInventory.sessions[0].auth_method = 'oidc'
    let view = renderPage()
    await act(async () => {
      await flushPromises()
      await flushPromises()
    })
    await vi.waitFor(() =>
      expect(view.textContent).toContain('SSO verification required'),
    )
    expect(
      view.querySelector<HTMLInputElement>('#oidc-name')?.matches(':disabled'),
    ).toBe(true)
    expect(view.textContent).toContain('Verify with SSO')

    await act(async () => root?.unmount())
    queryClient?.clear()
    container?.remove()
    root = null
    queryClient = null
    container = null
    identityPageMocks.apiFetch.mockReset()
    identityPageMocks.locationState = {
      oidcReauth: { result: 'success', purpose: 'oidc_provider_update' },
    }
    view = renderPage()
    await act(async () => {
      await flushPromises()
      await flushPromises()
    })
    await vi.waitFor(() =>
      expect(view.textContent).toContain('SSO verification complete'),
    )
    expect(
      view.querySelector<HTMLInputElement>('#oidc-name')?.matches(':disabled'),
    ).toBe(false)
  })

  it('requires identity-provider MFA assurance even when the OIDC session is recent', async () => {
    identityPageMocks.currentUser.data.authentication = {
      credential_kind: 'opaque_session',
      session_id: 'session-admin-1',
      session_auth_method: 'oidc',
      mfa_method: null,
      recently_authenticated: true,
      recent_authentication_expires_at: '2026-08-27T20:10:00Z',
      identity_provider_mfa_asserted: false,
      reauthentication_endpoint: '/auth/oidc/reauthenticate',
      security_actions_supported: true,
    }
    const view = renderPage()
    await act(async () => {
      await flushPromises()
      await flushPromises()
    })

    await vi.waitFor(() =>
      expect(view.textContent).toContain('Identity-provider MFA is required for provider changes'),
    )
    expect(view.textContent).toContain('Complete MFA at the identity provider')
    expect(view.textContent).toContain('Verify with SSO')
    expect(view.querySelector<HTMLInputElement>('#oidc-name')?.matches(':disabled')).toBe(true)
    expect(
      Array.from(view.querySelectorAll<HTMLButtonElement>('button')).find(
        (button) => button.textContent === 'Review changes',
      )?.disabled,
    ).toBe(true)
  })

  it('uses the server recent-auth contract for local provider changes', async () => {
    identityPageMocks.currentUser.data.authentication = {
      credential_kind: 'opaque_session',
      session_id: 'session-admin-1',
      session_auth_method: 'local',
      mfa_method: 'totp',
      recently_authenticated: false,
      recent_authentication_expires_at: null,
      identity_provider_mfa_asserted: false,
      reauthentication_endpoint: '/auth/security/reauthenticate',
      security_actions_supported: true,
    }
    identityPageMocks.apiFetch.mockImplementation(
      (path: string, options?: { body?: string }) => {
        if (path === '/auth/security/sessions')
          return Promise.resolve(localSessionInventory)
        if (path === '/auth/security/mfa')
          return Promise.resolve(localMfaStatus)
        if (path === '/auth/security/reauthenticate') {
          identityPageMocks.currentUser.data.authentication!.recently_authenticated = true
          return Promise.resolve({
            status: 'ok',
            auth_method: 'local',
            verification_method: 'password_totp',
            session_id: 'session-admin-2',
            authenticated_at: '2026-08-27T20:00:00Z',
            valid_until: '2026-08-27T20:10:00Z',
            session_rotated: true,
            submittedBody: options?.body,
          })
        }
        return Promise.resolve(providerSettings)
      },
    )

    const view = renderPage()
    await act(async () => {
      await flushPromises()
      await flushPromises()
    })
    await vi.waitFor(() =>
      expect(view.textContent).toContain('Recent local verification required'),
    )
    expect(
      view.querySelector<HTMLInputElement>('#oidc-name')?.matches(':disabled'),
    ).toBe(true)
    const verificationInputs = view.querySelectorAll<HTMLInputElement>(
      'input[autocomplete="current-password"], input[autocomplete="one-time-code"]',
    )
    act(() => {
      setInputValue(verificationInputs[0], 'correct horse battery staple')
      setInputValue(verificationInputs[1], '123456')
    })
    await act(async () => {
      ;[...view.querySelectorAll<HTMLButtonElement>('button')]
        .find((button) => button.textContent === 'Verify local session')
        ?.click()
      await flushPromises()
      await flushPromises()
    })

    expect(identityPageMocks.apiFetch).toHaveBeenCalledWith(
      '/auth/security/reauthenticate',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          current_password: 'correct horse battery staple',
          code: '123456',
        }),
      }),
    )
    await vi.waitFor(() =>
      expect(view.textContent).toContain('recently verified local sign-in'),
    )
    expect(
      view.querySelector<HTMLInputElement>('#oidc-name')?.matches(':disabled'),
    ).toBe(false)
  })
})

function setSelectValue(select: HTMLSelectElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(
    window.HTMLSelectElement.prototype,
    'value',
  )
  descriptor?.set?.call(select, value)
  select.dispatchEvent(new Event('change', { bubbles: true }))
}
