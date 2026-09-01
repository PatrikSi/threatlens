// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
;(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

const accountPageDomMocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  markLoggedOut: vi.fn(),
  navigate: vi.fn(),
  currentUser: {
    id: 'user-1',
    email: 'analyst@example.com',
    role: 'analyst',
    is_active: true,
    is_approved: true,
    approved_at: '2026-04-20T10:00:00Z',
    created_at: '2026-04-19T10:00:00Z',
    password_login_enabled: true,
    provisioning_source: 'local',
    features: {
      ai_enabled: false,
      ai_configured: false,
      ai_summary_enabled: false,
      ai_relevance_enabled: false,
      ai_daily_brief_enabled: false,
    },
  },
  useBlocker: vi.fn(() => ({
    state: 'unblocked' as const,
    proceed: vi.fn(),
    reset: vi.fn(),
  })),
}))

vi.mock('../api/client', () => ({
  apiFetch: accountPageDomMocks.apiFetch,
  buildApiUrl: (path: string) => `/api/v1${path}`,
}))

vi.mock('../components/AuthContext', () => ({
  useAuth: () => ({
    sessionVersion: 0,
    markAuthenticated: vi.fn(),
    markLoggedOut: accountPageDomMocks.markLoggedOut,
  }),
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({
    data: accountPageDomMocks.currentUser,
    isLoading: false,
    isError: false,
    error: null,
  }),
}))

vi.mock('react-router-dom', () => ({
  useNavigate: () => accountPageDomMocks.navigate,
  useLocation: () => ({
    pathname: '/settings/account',
    search: window.location.search,
    hash: window.location.hash,
    state: null,
    key: 'test',
  }),
  useBlocker: accountPageDomMocks.useBlocker,
}))

import { AccountPage } from './AccountPage'

let queryClient: QueryClient | null = null
let root: Root | null = null
let container: HTMLDivElement | null = null

function renderPage() {
  if (!accountPageDomMocks.apiFetch.getMockImplementation()) {
    accountPageDomMocks.apiFetch.mockResolvedValue({
      available: false,
      provider_name: null,
      linked: false,
      linked_email: null,
      linked_at: null,
      password_login_enabled: true,
    })
  }
  queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(
      <QueryClientProvider client={queryClient!}>
        <AccountPage />
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

function flushPromises() {
  return new Promise((resolve) => window.setTimeout(resolve, 0))
}

async function waitForQueriesToSettle() {
  await vi.waitFor(() => {
    expect(queryClient?.isFetching()).toBe(0)
  })
}

function resolveSecurityQuery(path: string, mfaEnabled = false) {
  if (path === '/auth/security/mfa') {
    return {
      local_mfa_available: true,
      managed_by: 'local',
      enabled: mfaEnabled,
      confirmed_at: mfaEnabled ? '2026-08-20T10:00:00Z' : null,
      recovery_codes_remaining: mfaEnabled ? 8 : 0,
    }
  }
  if (path === '/auth/security/sessions') {
    return { sessions: [], active_count: 0, history_truncated: false }
  }
  return null
}

afterEach(async () => {
  await act(async () => {
    root?.unmount()
    await flushPromises()
  })
  queryClient?.clear()
  queryClient = null
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  accountPageDomMocks.currentUser = {
    id: 'user-1',
    email: 'analyst@example.com',
    role: 'analyst',
    is_active: true,
    is_approved: true,
    approved_at: '2026-04-20T10:00:00Z',
    created_at: '2026-04-19T10:00:00Z',
    password_login_enabled: true,
    provisioning_source: 'local',
    features: {
      ai_enabled: false,
      ai_configured: false,
      ai_summary_enabled: false,
      ai_relevance_enabled: false,
      ai_daily_brief_enabled: false,
    },
  }
  accountPageDomMocks.apiFetch.mockReset()
  accountPageDomMocks.markLoggedOut.mockReset()
  accountPageDomMocks.navigate.mockReset()
  accountPageDomMocks.useBlocker.mockReset()
  accountPageDomMocks.useBlocker.mockImplementation(() => ({
    state: 'unblocked' as const,
    proceed: vi.fn(),
    reset: vi.fn(),
  }))
  window.history.replaceState(null, '', '/')
  window.sessionStorage.clear()
})

describe('AccountPage DOM workflows', () => {
  it('presents SSO-provisioned identity and password ownership without local controls', async () => {
    accountPageDomMocks.currentUser = {
      ...accountPageDomMocks.currentUser,
      password_login_enabled: false,
      provisioning_source: 'oidc',
    }
    accountPageDomMocks.apiFetch.mockResolvedValue({
      available: true,
      provider_name: 'Authentik',
      linked: true,
      linked_email: 'analyst@example.com',
      linked_at: '2026-04-19T10:00:00Z',
      password_login_enabled: false,
    })

    const view = renderPage()
    await act(async () => {
      await waitForQueriesToSettle()
    })

    expect(view.querySelectorAll('h1')).toHaveLength(1)
    expect(view.querySelector('h1')?.textContent).toBe('My account')
    expect(view.textContent).toContain(
      'Provisioning source: Single sign-on (OIDC)',
    )
    expect(view.textContent).toContain(
      'account and its sign-in identity are managed by Authentik',
    )
    expect(view.textContent).toContain(
      'Password credentials are managed by Authentik',
    )
    expect(view.querySelector('#account-current-password')).toBeNull()
    expect(view.querySelector('#account-unlink-password')).toBeNull()
  })

  it('identifies a linked local account as hybrid and retains local credential controls', async () => {
    accountPageDomMocks.apiFetch.mockResolvedValue({
      available: true,
      provider_name: 'Authentik',
      linked: true,
      linked_email: 'analyst@example.com',
      linked_at: '2026-04-19T10:00:00Z',
      password_login_enabled: true,
    })

    const view = renderPage()
    await act(async () => {
      await waitForQueriesToSettle()
    })

    expect(view.textContent).toContain('Provisioning source: Local')
    expect(view.querySelector('#account-current-password')).not.toBeNull()
    expect(
      [...view.querySelectorAll('button')].some(
        (button) => button.textContent === 'Unlink SSO',
      ),
    ).toBe(true)
  })

  it('redirects to login after a successful password change', async () => {
    accountPageDomMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/auth/change-password') {
        return Promise.resolve({
          status: 'ok',
          sign_in_required: true,
          revoked_auth_sessions: 3,
          revoked_api_tokens: 2,
        })
      }
      const securityResponse = resolveSecurityQuery(path)
      if (securityResponse) return Promise.resolve(securityResponse)
      return Promise.resolve({
        available: false,
        provider_name: null,
        linked: false,
        linked_email: null,
        linked_at: null,
        password_login_enabled: true,
      })
    })

    const view = renderPage()
    const currentPasswordInput = view.querySelector<HTMLInputElement>(
      '#account-current-password',
    )
    const newPasswordInput = view.querySelector<HTMLInputElement>(
      '#account-new-password',
    )
    const form = view.querySelector('form')

    expect(currentPasswordInput).not.toBeNull()
    expect(newPasswordInput).not.toBeNull()
    expect(form).not.toBeNull()

    act(() => {
      setInputValue(currentPasswordInput!, 'current-password')
      setInputValue(newPasswordInput!, 'new-password-123')
    })

    await act(async () => {
      form!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      )
      await flushPromises()
      await flushPromises()
    })

    expect(accountPageDomMocks.apiFetch).toHaveBeenCalledWith(
      '/auth/change-password',
      {
        method: 'POST',
        body: JSON.stringify({
          current_password: 'current-password',
          new_password: 'new-password-123',
        }),
      },
    )

    expect(currentPasswordInput?.value).toBe('')
    expect(newPasswordInput?.value).toBe('')
    expect(accountPageDomMocks.markLoggedOut).toHaveBeenCalledTimes(1)
    expect(accountPageDomMocks.navigate).toHaveBeenCalledWith('/login', {
      replace: true,
      state: {
        authMessage:
          'Password updated. 3 browser sessions revoked and 2 API tokens revoked. Sign in again with your new password.',
      },
    })
  })

  it('announces a failed password change assertively', async () => {
    accountPageDomMocks.apiFetch.mockRejectedValue(
      new Error('Password change failed'),
    )

    const view = renderPage()
    const currentPasswordInput = view.querySelector<HTMLInputElement>(
      '#account-current-password',
    )
    const newPasswordInput = view.querySelector<HTMLInputElement>(
      '#account-new-password',
    )
    const form = view.querySelector('form')

    expect(currentPasswordInput).not.toBeNull()
    expect(newPasswordInput).not.toBeNull()
    expect(form).not.toBeNull()

    act(() => {
      setInputValue(currentPasswordInput!, 'current-password')
      setInputValue(newPasswordInput!, 'new-password-123')
    })

    await act(async () => {
      form!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      )
      await flushPromises()
      await flushPromises()
    })

    const notice = view.querySelector(
      '[role="alert"][aria-live="assertive"][aria-atomic="true"]',
    )
    expect(notice).not.toBeNull()
    expect(notice?.textContent).toContain('Password could not be changed.')
    expect(notice?.textContent).toContain('Password change failed.')
  })

  it('shows inline validation before sending a short password change', async () => {
    const view = renderPage()
    const currentPasswordInput = view.querySelector<HTMLInputElement>(
      '#account-current-password',
    )
    const newPasswordInput = view.querySelector<HTMLInputElement>(
      '#account-new-password',
    )
    const form = view.querySelector('form')

    expect(currentPasswordInput).not.toBeNull()
    expect(newPasswordInput).not.toBeNull()
    expect(form).not.toBeNull()

    act(() => {
      setInputValue(currentPasswordInput!, 'wrong-password-qa')
      setInputValue(newPasswordInput!, 'short')
    })

    await act(async () => {
      form!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      )
      await flushPromises()
    })

    const notice = view.querySelector(
      '[role="alert"][aria-live="assertive"][aria-atomic="true"]',
    )
    expect(notice).not.toBeNull()
    expect(notice?.textContent).toContain(
      'New password must be at least 8 characters.',
    )
    expect(accountPageDomMocks.apiFetch).not.toHaveBeenCalledWith(
      '/auth/change-password',
      expect.anything(),
    )
  })

  it('treats an unfinished password change as unsaved work', () => {
    const view = renderPage()
    const currentPasswordInput = view.querySelector<HTMLInputElement>(
      '#account-current-password',
    )

    expect(currentPasswordInput).not.toBeNull()

    act(() => {
      setInputValue(currentPasswordInput!, 'current-password')
    })

    expect(accountPageDomMocks.useBlocker).toHaveBeenCalledWith(true)
  })

  it('shows a linked OIDC identity with a protected unlink control', async () => {
    accountPageDomMocks.apiFetch.mockImplementation((path: string) => {
      const securityResponse = resolveSecurityQuery(path)
      if (securityResponse) return Promise.resolve(securityResponse)
      if (path === '/auth/oidc/account') {
        return Promise.resolve({
          available: true,
          provider_name: 'Acme SSO',
          linked: true,
          linked_email: 'analyst@example.com',
          linked_at: '2026-07-31T10:00:00Z',
          password_login_enabled: true,
        })
      }
      return Promise.resolve({})
    })

    const view = renderPage()
    await act(async () => {
      await waitForQueriesToSettle()
    })

    expect(view.textContent).toContain(
      'Linked to Acme SSO as analyst@example.com',
    )
    expect(
      [...view.querySelectorAll('button')].some((button) =>
        button.textContent?.includes('Unlink SSO'),
      ),
    ).toBe(true)
  })

  it('opens an accessible OIDC link dialog with explicit assurance boundaries and password focus', async () => {
    accountPageDomMocks.apiFetch.mockImplementation((path: string) => {
      const securityResponse = resolveSecurityQuery(path)
      if (securityResponse) return Promise.resolve(securityResponse)
      if (path === '/auth/oidc/account') {
        return Promise.resolve({
          available: true,
          provider_name: 'Acme SSO',
          linked: false,
          linked_email: null,
          linked_at: null,
          password_login_enabled: true,
        })
      }
      return Promise.resolve({})
    })

    const view = renderPage()
    await act(async () => {
      await waitForQueriesToSettle()
    })
    const linkButton = [...view.querySelectorAll('button')].find(
      (button) => button.textContent === 'Link SSO account',
    )
    expect(linkButton).not.toBeUndefined()

    await act(async () => {
      linkButton!.focus()
      linkButton!.click()
      await waitForQueriesToSettle()
      await flushPromises()
    })

    const dialog = document.body.querySelector<HTMLElement>('[role="dialog"]')
    const passwordInput = dialog?.querySelector<HTMLInputElement>(
      '#oidc-link-current-password',
    )
    expect(dialog).not.toBeNull()
    expect(dialog?.getAttribute('aria-modal')).toBe('true')
    expect(dialog?.textContent).toContain(
      'adds Acme SSO as an identity-provider-managed sign-in path',
    )
    expect(dialog?.textContent).toContain(
      'ThreatLens local MFA is not an additional prompt',
    )
    expect(dialog?.textContent).toContain('require a fresh authentication')
    expect(passwordInput?.autocomplete).toBe('current-password')
    expect(dialog?.querySelector('#oidc-link-code')).toBeNull()
    await vi.waitFor(() => expect(document.activeElement).toBe(passwordInput))
  })

  it('sends the local password only in the OIDC link JSON body when local MFA is disabled', async () => {
    accountPageDomMocks.apiFetch.mockImplementation((path: string) => {
      const securityResponse = resolveSecurityQuery(path)
      if (securityResponse) return Promise.resolve(securityResponse)
      if (path === '/auth/oidc/account') {
        return Promise.resolve({
          available: true,
          provider_name: 'Acme SSO',
          linked: false,
          linked_email: null,
          linked_at: null,
          password_login_enabled: true,
        })
      }
      if (path === '/auth/oidc/link') return new Promise(() => {})
      return Promise.resolve({})
    })

    const view = renderPage()
    await act(async () => {
      await waitForQueriesToSettle()
    })
    const linkButton = [...view.querySelectorAll('button')].find(
      (button) => button.textContent === 'Link SSO account',
    )!
    await act(async () => {
      linkButton.click()
      await waitForQueriesToSettle()
    })
    const dialog = document.body.querySelector<HTMLElement>('[role="dialog"]')!
    act(() => {
      setInputValue(
        dialog.querySelector<HTMLInputElement>('#oidc-link-current-password')!,
        'AnalystPass123!',
      )
    })

    await act(async () => {
      dialog
        .querySelector<HTMLFormElement>('#oidc-link-form')!
        .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await flushPromises()
    })

    expect(accountPageDomMocks.apiFetch).toHaveBeenCalledWith(
      '/auth/oidc/link',
      {
        method: 'POST',
        body: JSON.stringify({ current_password: 'AnalystPass123!' }),
      },
    )
    expect(dialog.textContent).toContain('Connecting...')
  })

  it('requires a current authenticator code and includes it in the protected request body', async () => {
    accountPageDomMocks.apiFetch.mockImplementation((path: string) => {
      const securityResponse = resolveSecurityQuery(path, true)
      if (securityResponse) return Promise.resolve(securityResponse)
      if (path === '/auth/oidc/account') {
        return Promise.resolve({
          available: true,
          provider_name: 'Acme SSO',
          linked: false,
          linked_email: null,
          linked_at: null,
          password_login_enabled: true,
        })
      }
      if (path === '/auth/oidc/link') return new Promise(() => {})
      return Promise.resolve({})
    })

    const view = renderPage()
    await act(async () => {
      await waitForQueriesToSettle()
      ;[...view.querySelectorAll('button')]
        .find((button) => button.textContent === 'Link SSO account')!
        .click()
      await waitForQueriesToSettle()
    })
    const dialog = document.body.querySelector<HTMLElement>('[role="dialog"]')!
    const codeInput = dialog.querySelector<HTMLInputElement>('#oidc-link-code')!
    expect(codeInput).not.toBeNull()
    expect(codeInput.inputMode).toBe('numeric')
    expect(codeInput.autocomplete).toBe('one-time-code')
    expect(dialog.textContent).toContain(
      'Recovery codes are not accepted for account linking',
    )

    act(() => {
      setInputValue(
        dialog.querySelector<HTMLInputElement>('#oidc-link-current-password')!,
        'AnalystPass123!',
      )
      setInputValue(codeInput, '482193')
    })
    await act(async () => {
      dialog
        .querySelector<HTMLFormElement>('#oidc-link-form')!
        .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await flushPromises()
    })

    expect(accountPageDomMocks.apiFetch).toHaveBeenCalledWith(
      '/auth/oidc/link',
      {
        method: 'POST',
        body: JSON.stringify({
          current_password: 'AnalystPass123!',
          code: '482193',
        }),
      },
    )
  })

  it('reports missing and malformed step-up fields without sending a request', async () => {
    accountPageDomMocks.apiFetch.mockImplementation((path: string) => {
      const securityResponse = resolveSecurityQuery(path, true)
      if (securityResponse) return Promise.resolve(securityResponse)
      if (path === '/auth/oidc/account') {
        return Promise.resolve({
          available: true,
          provider_name: 'Acme SSO',
          linked: false,
          linked_email: null,
          linked_at: null,
          password_login_enabled: true,
        })
      }
      return Promise.resolve({})
    })

    const view = renderPage()
    await act(async () => {
      await waitForQueriesToSettle()
      ;[...view.querySelectorAll('button')]
        .find((button) => button.textContent === 'Link SSO account')!
        .click()
      await waitForQueriesToSettle()
    })
    const dialog = document.body.querySelector<HTMLElement>('[role="dialog"]')!
    const form = dialog.querySelector<HTMLFormElement>('#oidc-link-form')!

    act(() => {
      form.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      )
    })
    expect(dialog.textContent).toContain(
      'Enter your current ThreatLens password.',
    )
    expect(dialog.textContent).toContain(
      'Enter the current 6-digit code from your ThreatLens authenticator.',
    )

    act(() => {
      setInputValue(
        dialog.querySelector<HTMLInputElement>('#oidc-link-current-password')!,
        'AnalystPass123!',
      )
      setInputValue(
        dialog.querySelector<HTMLInputElement>('#oidc-link-code')!,
        '12345',
      )
      form.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      )
    })
    expect(dialog.textContent).not.toContain(
      'Enter your current ThreatLens password.',
    )
    expect(dialog.textContent).toContain(
      'Enter the current 6-digit code from your ThreatLens authenticator.',
    )
    expect(accountPageDomMocks.apiFetch).not.toHaveBeenCalledWith(
      '/auth/oidc/link',
      expect.anything(),
    )
  })

  it('keeps correctable server errors in the dialog and clears secrets and mutation state on cancel', async () => {
    accountPageDomMocks.apiFetch.mockImplementation((path: string) => {
      const securityResponse = resolveSecurityQuery(path, true)
      if (securityResponse) return Promise.resolve(securityResponse)
      if (path === '/auth/oidc/account') {
        return Promise.resolve({
          available: true,
          provider_name: 'Acme SSO',
          linked: false,
          linked_email: null,
          linked_at: null,
          password_login_enabled: true,
        })
      }
      if (path === '/auth/oidc/link') {
        return Promise.reject(
          new Error(
            'The current password or authenticator code was not accepted',
          ),
        )
      }
      return Promise.resolve({})
    })

    const view = renderPage()
    await act(async () => {
      await waitForQueriesToSettle()
    })
    const linkButton = [...view.querySelectorAll('button')].find(
      (button) => button.textContent === 'Link SSO account',
    )!
    await act(async () => {
      linkButton.focus()
      linkButton.click()
      await waitForQueriesToSettle()
    })
    let dialog = document.body.querySelector<HTMLElement>('[role="dialog"]')!
    act(() => {
      setInputValue(
        dialog.querySelector<HTMLInputElement>('#oidc-link-current-password')!,
        'WrongPass123!',
      )
      setInputValue(
        dialog.querySelector<HTMLInputElement>('#oidc-link-code')!,
        '482193',
      )
    })
    await act(async () => {
      dialog
        .querySelector<HTMLFormElement>('#oidc-link-form')!
        .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await vi.waitFor(() => expect(queryClient?.isMutating()).toBe(0))
    })

    expect(dialog.textContent).toContain(
      'SSO account linking could not be started.',
    )
    expect(dialog.textContent).toContain(
      'current password or authenticator code was not accepted',
    )
    expect(
      dialog.querySelector<HTMLInputElement>('#oidc-link-current-password')
        ?.value,
    ).toBe('WrongPass123!')
    expect(
      dialog.querySelector<HTMLInputElement>('#oidc-link-code')?.value,
    ).toBe('482193')

    await act(async () => {
      ;[...dialog.querySelectorAll('button')]
        .find((button) => button.textContent === 'Cancel')!
        .click()
      await flushPromises()
    })
    expect(document.body.querySelector('[role="dialog"]')).toBeNull()
    expect(document.activeElement).toBe(linkButton)
    expect(
      queryClient
        ?.getMutationCache()
        .findAll({ mutationKey: ['auth', 'oidc', 'link'], exact: true }),
    ).toHaveLength(0)

    await act(async () => {
      linkButton.click()
      await waitForQueriesToSettle()
    })
    dialog = document.body.querySelector<HTMLElement>('[role="dialog"]')!
    expect(
      dialog.querySelector<HTMLInputElement>('#oidc-link-current-password')
        ?.value,
    ).toBe('')
    expect(
      dialog.querySelector<HTMLInputElement>('#oidc-link-code')?.value,
    ).toBe('')
  })

  it('clears the link draft and zero-retention mutation state before provider handoff', async () => {
    window.history.replaceState(null, '', '/account')
    accountPageDomMocks.apiFetch.mockImplementation((path: string) => {
      const securityResponse = resolveSecurityQuery(path)
      if (securityResponse) return Promise.resolve(securityResponse)
      if (path === '/auth/oidc/account') {
        return Promise.resolve({
          available: true,
          provider_name: 'Acme SSO',
          linked: false,
          linked_email: null,
          linked_at: null,
          password_login_enabled: true,
        })
      }
      if (path === '/auth/oidc/link') {
        return Promise.resolve({
          authorization_url: '#fresh-provider-authentication',
        })
      }
      return Promise.resolve({})
    })

    const view = renderPage()
    await act(async () => {
      await waitForQueriesToSettle()
    })
    const linkButton = [...view.querySelectorAll('button')].find(
      (button) => button.textContent === 'Link SSO account',
    )!
    await act(async () => {
      linkButton.click()
      await waitForQueriesToSettle()
    })
    const dialog = document.body.querySelector<HTMLElement>('[role="dialog"]')!
    act(() => {
      setInputValue(
        dialog.querySelector<HTMLInputElement>('#oidc-link-current-password')!,
        'AnalystPass123!',
      )
    })

    await act(async () => {
      dialog
        .querySelector<HTMLFormElement>('#oidc-link-form')!
        .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await vi.waitFor(() =>
        expect(window.location.hash).toBe('#fresh-provider-authentication'),
      )
      await flushPromises()
    })

    expect(document.body.querySelector('[role="dialog"]')).toBeNull()
    await vi.waitFor(() => {
      expect(
        queryClient
          ?.getMutationCache()
          .findAll({ mutationKey: ['auth', 'oidc', 'link'], exact: true }),
      ).toHaveLength(0)
    })

    await act(async () => {
      linkButton.click()
      await waitForQueriesToSettle()
    })
    const reopenedDialog =
      document.body.querySelector<HTMLElement>('[role="dialog"]')!
    expect(
      reopenedDialog.querySelector<HTMLInputElement>(
        '#oidc-link-current-password',
      )?.value,
    ).toBe('')
  })

  it('requires explicit step-up confirmation before unlinking an SSO identity', async () => {
    let linked = true
    accountPageDomMocks.apiFetch.mockImplementation(
      (path: string, options?: { method?: string }) => {
        if (path === '/auth/oidc/account' && options?.method === 'DELETE') {
          linked = false
          return Promise.resolve(undefined)
        }
        const securityResponse = resolveSecurityQuery(path)
        if (securityResponse) return Promise.resolve(securityResponse)
        if (path === '/auth/oidc/account') {
          return Promise.resolve({
            available: true,
            provider_name: 'Acme SSO',
            linked,
            linked_email: linked ? 'analyst@example.com' : null,
            linked_at: linked ? '2026-07-31T10:00:00Z' : null,
            password_login_enabled: true,
          })
        }
        return Promise.resolve({})
      },
    )

    const view = renderPage()
    await act(async () => {
      await waitForQueriesToSettle()
    })
    const unlinkButton = [
      ...view.querySelectorAll<HTMLButtonElement>('button'),
    ].find((button) => button.textContent === 'Unlink SSO')!
    await act(async () => {
      unlinkButton.click()
      await waitForQueriesToSettle()
      await flushPromises()
    })
    const dialog = document.body.querySelector<HTMLElement>('[role="dialog"]')!
    const passwordInput = dialog.querySelector<HTMLInputElement>(
      '#oidc-unlink-current-password',
    )!
    expect(dialog.textContent).toContain('Review the sign-in impact')
    expect(dialog.textContent).toContain(
      'other browser sessions will be revoked',
    )
    expect(dialog.textContent).toContain('API tokens are unchanged')
    await vi.waitFor(() => expect(document.activeElement).toBe(passwordInput))
    act(() => setInputValue(passwordInput, 'AnalystPass123!'))

    await act(async () => {
      dialog
        .querySelector<HTMLFormElement>('#oidc-unlink-form')
        ?.dispatchEvent(
          new Event('submit', { bubbles: true, cancelable: true }),
        )
      await vi.waitFor(() => {
        expect(queryClient?.isMutating()).toBe(0)
        expect(queryClient?.isFetching()).toBe(0)
      })
      await flushPromises()
      await flushPromises()
    })

    expect(accountPageDomMocks.apiFetch).toHaveBeenCalledWith(
      '/auth/oidc/account',
      {
        method: 'DELETE',
        body: JSON.stringify({ current_password: 'AnalystPass123!' }),
      },
    )
    expect(view.textContent).toContain('SSO identity unlinked.')
    expect(view.textContent).toContain('other browser sessions were revoked')
    expect(
      [...view.querySelectorAll('button')].some(
        (button) => button.textContent === 'Link SSO account',
      ),
    ).toBe(true)
  })

  it('accepts the local MFA step-up contract when unlinking an SSO identity', async () => {
    accountPageDomMocks.apiFetch.mockImplementation(
      (path: string, options?: { method?: string }) => {
        if (path === '/auth/oidc/account' && options?.method === 'DELETE') {
          return Promise.resolve(undefined)
        }
        const securityResponse = resolveSecurityQuery(path, true)
        if (securityResponse) return Promise.resolve(securityResponse)
        if (path === '/auth/oidc/account') {
          return Promise.resolve({
            available: true,
            provider_name: 'Acme SSO',
            linked: true,
            linked_email: 'analyst@example.com',
            linked_at: '2026-07-31T10:00:00Z',
            password_login_enabled: true,
          })
        }
        return Promise.resolve({})
      },
    )

    const view = renderPage()
    await act(async () => await waitForQueriesToSettle())
    await act(async () => {
      ;[...view.querySelectorAll<HTMLButtonElement>('button')]
        .find((button) => button.textContent === 'Unlink SSO')
        ?.click()
      await waitForQueriesToSettle()
    })
    const dialog = document.body.querySelector<HTMLElement>('[role="dialog"]')!
    act(() => {
      setInputValue(
        dialog.querySelector<HTMLInputElement>(
          '#oidc-unlink-current-password',
        )!,
        ' Password With Spaces ',
      )
      setInputValue(
        dialog.querySelector<HTMLInputElement>('#oidc-unlink-code')!,
        ' recovery-code ',
      )
    })
    await act(async () => {
      dialog
        .querySelector<HTMLFormElement>('#oidc-unlink-form')
        ?.dispatchEvent(
          new Event('submit', { bubbles: true, cancelable: true }),
        )
      await vi.waitFor(() => expect(queryClient?.isMutating()).toBe(0))
    })

    expect(accountPageDomMocks.apiFetch).toHaveBeenCalledWith(
      '/auth/oidc/account',
      {
        method: 'DELETE',
        body: JSON.stringify({
          current_password: ' Password With Spaces ',
          code: 'recovery-code',
        }),
      },
    )
  })

  it('explains a provider reauthentication failure after an account-link callback', async () => {
    window.history.replaceState(
      null,
      '',
      '/settings/account?oidc_link=reauthentication_failed',
    )

    const view = renderPage()
    await act(async () => await waitForQueriesToSettle())

    expect(view.querySelector('[role="alert"]')?.textContent).toContain(
      'did not confirm a fresh authentication',
    )
  })

  it.each([
    ['provider_configuration_changed', 'configuration changed'],
    ['callback_rate_limited', 'Wait briefly'],
    ['reauth_session_expired', 'session changed or expired'],
    ['reauth_identity_mismatch', 'does not match'],
  ])(
    'explains the %s OIDC reauthentication callback result',
    async (result, expected) => {
      window.history.replaceState(
        null,
        '',
        `/settings/account?oidc_reauth=${result}`,
      )

      const view = renderPage()
      await act(async () => await waitForQueriesToSettle())

      expect(view.querySelector('[role="alert"]')?.textContent).toContain(
        expected,
      )
    },
  )

  it('returns a completed OIDC step-up to its bounded privileged workflow', async () => {
    window.sessionStorage.setItem(
      'threatlens.oidc_reauth.continuation.v1',
      JSON.stringify({
        returnPath: '/settings/users',
        purpose: 'admin_mfa_reset',
        context: { targetUserId: 'user-1', reason: 'Lost device' },
        createdAt: Date.now(),
      }),
    )
    window.history.replaceState(
      null,
      '',
      '/settings/account?oidc_reauth=success',
    )

    renderPage()
    await act(async () => await waitForQueriesToSettle())

    expect(accountPageDomMocks.navigate).toHaveBeenCalledWith(
      '/settings/users',
      {
        replace: true,
        state: {
          oidcReauth: {
            result: 'success',
            purpose: 'admin_mfa_reset',
            context: { targetUserId: 'user-1', reason: 'Lost device' },
          },
        },
      },
    )
    expect(window.sessionStorage.length).toBe(0)
  })
})
