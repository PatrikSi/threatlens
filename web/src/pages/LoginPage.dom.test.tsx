// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AuthProvider } from '../components/AuthContext'
import { ApiError } from '../api/client'
;(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

const loginPageDomMocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
}))

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number
    path: string
    detail: unknown
    responseBody: unknown
    code: string | null
    requestId: string | null
    retryable: boolean
    retryAfterSeconds: number | null

    constructor(
      message: string,
      status: number,
      path: string,
      detail: unknown = null,
      diagnostics: {
        responseBody?: unknown
        code?: string | null
        requestId?: string | null
        retryable?: boolean
        retryAfterSeconds?: number | null
      } = {},
    ) {
      super(message)
      this.name = 'ApiError'
      this.status = status
      this.path = path
      this.detail = detail
      this.responseBody = diagnostics.responseBody ?? detail
      this.code = diagnostics.code ?? null
      this.requestId = diagnostics.requestId ?? null
      this.retryable = diagnostics.retryable ?? false
      this.retryAfterSeconds = diagnostics.retryAfterSeconds ?? null
    }
  },
  apiFetch: loginPageDomMocks.apiFetch,
  buildApiUrl: (path: string) => `/api/v1${path}`,
}))

import {
  LoginPage,
  clearPendingOidcReturnDestination,
  readPendingOidcReturnDestination,
  resolvePostLoginDestination,
  stagePendingOidcReturnDestination,
} from './LoginPage'

let queryClient: QueryClient | null = null
let root: Root | null = null
let container: HTMLDivElement | null = null

type InitialEntry = string | { pathname: string; state?: unknown }

function renderPage(initialEntries: InitialEntry[] = ['/login']) {
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
      <MemoryRouter initialEntries={initialEntries}>
        <AuthProvider>
          <QueryClientProvider client={queryClient!}>
            <LoginPage />
            <LocationProbe />
          </QueryClientProvider>
        </AuthProvider>
      </MemoryRouter>,
    )
  })
  return container
}

function flushPromises() {
  return new Promise((resolve) => window.setTimeout(resolve, 0))
}

function credentialMutationVariables(mutationKey: readonly unknown[]) {
  return (
    queryClient
      ?.getMutationCache()
      .findAll({ mutationKey, exact: true })
      .map((mutation) => mutation.state.variables) ?? []
  )
}

function setInputValue(input: HTMLInputElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    'value',
  )
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

async function submitForm(view: HTMLElement) {
  await act(async () => {
    view
      .querySelector('form')
      ?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await flushPromises()
    await flushPromises()
  })
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
  loginPageDomMocks.apiFetch.mockReset()
  window.sessionStorage.clear()
})

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{`${location.pathname}${location.search}${location.hash}`}</output>
}

describe('LoginPage accessibility', () => {
  it('uses the workspace start route after ordinary local sign-in', async () => {
    loginPageDomMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/auth/login') return Promise.resolve({ token_type: 'session_cookie' })
      if (path === '/auth/oidc/settings') return Promise.resolve({ enabled: false, provider_name: null })
      return Promise.resolve({ allow_self_registration: false })
    })
    const view = renderPage()
    await act(async () => await flushPromises())

    act(() => {
      setInputValue(view.querySelector<HTMLInputElement>('#login-email')!, 'analyst@example.com')
      setInputValue(view.querySelector<HTMLInputElement>('#login-password')!, 'local-password')
    })
    await submitForm(view)

    expect(view.querySelector('[data-testid="location"]')?.textContent).toBe('/start')
  })

  it('preserves an explicit protected deep link after local sign-in', async () => {
    loginPageDomMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/auth/login') return Promise.resolve({ token_type: 'session_cookie' })
      if (path === '/auth/oidc/settings') return Promise.resolve({ enabled: false, provider_name: null })
      return Promise.resolve({ allow_self_registration: false })
    })
    const view = renderPage([{
      pathname: '/login',
      state: { from: { pathname: '/alerts', search: '?severity=high', hash: '#match-1' } },
    }])
    await act(async () => await flushPromises())

    act(() => {
      setInputValue(view.querySelector<HTMLInputElement>('#login-email')!, 'analyst@example.com')
      setInputValue(view.querySelector<HTMLInputElement>('#login-password')!, 'local-password')
    })
    await submitForm(view)

    expect(view.querySelector('[data-testid="location"]')?.textContent).toBe('/alerts?severity=high#match-1')
  })

  it('treats root and unsafe return state as workspace entry instead of external navigation', () => {
    expect(resolvePostLoginDestination({ from: { pathname: '/' } })).toBe('/start')
    expect(resolvePostLoginDestination({ from: { pathname: '//attacker.example' } })).toBe('/start')
    expect(resolvePostLoginDestination({ from: { pathname: '/feeds', search: 'bad' } })).toBe('/feeds')
  })

  it('stores a bounded one-time OIDC deep-link return without storing ordinary landing intent', () => {
    stagePendingOidcReturnDestination({
      from: { pathname: '/investigations/case-1', search: '?tab=evidence', hash: '#ioc' },
    })

    expect(readPendingOidcReturnDestination()).toBe('/investigations/case-1?tab=evidence#ioc')
    expect(readPendingOidcReturnDestination()).toBe('/investigations/case-1?tab=evidence#ioc')
    clearPendingOidcReturnDestination()
    expect(readPendingOidcReturnDestination()).toBeNull()

    stagePendingOidcReturnDestination({ from: { pathname: '/' } })
    expect(window.sessionStorage.length).toBe(0)
  })

  it('discards expired or external OIDC return state', () => {
    window.sessionStorage.setItem(
      'threatlens.auth.oidc-return.v1',
      JSON.stringify({ destination: '/alerts', createdAt: Date.now() - 11 * 60 * 1000 }),
    )
    expect(readPendingOidcReturnDestination()).toBeNull()

    window.sessionStorage.setItem(
      'threatlens.auth.oidc-return.v1',
      JSON.stringify({ destination: '//attacker.example', createdAt: Date.now() }),
    )
    expect(readPendingOidcReturnDestination()).toBeNull()
    clearPendingOidcReturnDestination()
    expect(window.sessionStorage.length).toBe(0)
  })

  it('shows an explicit error when registration settings cannot be loaded', async () => {
    loginPageDomMocks.apiFetch.mockRejectedValue(
      new Error('registration settings unavailable'),
    )

    const view = renderPage()

    await act(async () => {
      await vi.waitFor(() => {
        expect(view.querySelector('[role="alert"]')).not.toBeNull()
      })
    })

    const alert = view.querySelector('[role="alert"]')
    expect(alert?.textContent).toContain(
      'Registration availability could not be loaded',
    )
    expect(
      view.querySelector<HTMLButtonElement>('button[type="submit"]')
        ?.textContent,
    ).toContain('Sign in')
  })

  it('connects labels to controls and supplies login autocomplete hints', async () => {
    loginPageDomMocks.apiFetch.mockResolvedValue({
      allow_self_registration: false,
    })

    const view = renderPage()

    await act(async () => {
      await flushPromises()
    })

    expect(
      view.querySelector('label[for="login-email"]')?.textContent,
    ).toContain('Email')
    expect(
      view.querySelector<HTMLInputElement>('#login-email')?.autocomplete,
    ).toBe('email')
    expect(
      view.querySelector('label[for="login-password"]')?.textContent,
    ).toContain('Password')
    expect(
      view.querySelector<HTMLInputElement>('#login-password')?.autocomplete,
    ).toBe('current-password')
  })

  it('exposes route auth messages as live alerts', async () => {
    loginPageDomMocks.apiFetch.mockResolvedValue({
      allow_self_registration: false,
    })

    const view = renderPage([
      {
        pathname: '/login',
        state: { authMessage: 'Session expired. Sign in again.' },
      },
    ])

    await act(async () => {
      await flushPromises()
    })

    const alert = view.querySelector(
      '[role="alert"][aria-live="polite"][aria-atomic="true"]',
    )
    expect(alert).not.toBeNull()
    expect(alert?.textContent).toContain('Session expired. Sign in again.')
  })

  it('offers configured OIDC sign-in alongside the local form', async () => {
    loginPageDomMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/auth/oidc/settings') {
        return Promise.resolve({ enabled: true, provider_name: 'Acme SSO' })
      }
      return Promise.resolve({ allow_self_registration: false })
    })

    const view = renderPage()
    await act(async () => {
      await vi.waitFor(() => {
        expect(
          [...view.querySelectorAll('button')].some((button) =>
            button.textContent?.includes('Continue with Acme SSO'),
          ),
        ).toBe(true)
      })
    })

    expect(
      [...view.querySelectorAll('button')].some((button) =>
        button.textContent?.includes('Continue with Acme SSO'),
      ),
    ).toBe(true)
    expect(view.querySelector('#login-email')).not.toBeNull()
    expect(view.querySelector('#login-password')).not.toBeNull()
  })

  it('distinguishes an OIDC settings failure, includes its request reference, and retries', async () => {
    let oidcAttempts = 0
    loginPageDomMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/auth/oidc/settings') {
        oidcAttempts += 1
        if (oidcAttempts === 1) {
          return Promise.reject(
            new ApiError(
              'OIDC discovery failed',
              503,
              path,
              'OIDC discovery failed',
              {
                requestId: 'request-oidc-123',
                retryable: true,
              },
            ),
          )
        }
        return Promise.resolve({ enabled: false, provider_name: null })
      }
      return Promise.resolve({ allow_self_registration: false })
    })

    const view = renderPage()
    await act(async () => {
      await vi.waitFor(() => {
        expect(view.querySelector('[role="alert"]')?.textContent).toContain(
          'request-oidc-123',
        )
      })
    })

    const retry = [...view.querySelectorAll<HTMLButtonElement>('button')].find(
      (button) => button.textContent?.includes('Retry SSO check'),
    )
    expect(retry).not.toBeUndefined()
    await act(async () => {
      retry?.click()
      await vi.waitFor(() =>
        expect(view.textContent).toContain('Single sign-on is disabled'),
      )
    })
    expect(oidcAttempts).toBe(2)
  })

  it('shows a useful OIDC account-linking error', async () => {
    loginPageDomMocks.apiFetch.mockResolvedValue({
      allow_self_registration: false,
    })

    const view = renderPage(['/login?oidc_error=email_link_required'])
    await act(async () => {
      await flushPromises()
    })

    expect(view.querySelector('[role="alert"]')?.textContent).toContain(
      'Sign in locally',
    )
  })

  it.each([
    ['reauthentication_failed', 'complete any requested verification'],
    [
      'role_sync_blocked',
      'transfer investigation ownership or preserve another active admin',
    ],
    ['provider_configuration_changed', 'configuration changed'],
    ['callback_rate_limited', 'Wait briefly'],
  ])(
    'shows actionable guidance for the %s OIDC callback failure',
    async (errorCode, message) => {
      loginPageDomMocks.apiFetch.mockResolvedValue({
        allow_self_registration: false,
      })

      const view = renderPage([`/login?oidc_error=${errorCode}`])
      await act(async () => await flushPromises())

      expect(view.querySelector('[role="alert"]')?.textContent).toContain(
        message,
      )
    },
  )

  it.each([
    ['email_required', 'did not supply an email address'],
    ['invalid_email', 'supplied an invalid email address'],
    ['verified_email_required', 'did not supply a verified email address'],
  ])('distinguishes the %s OIDC claim failure', async (errorCode, message) => {
    loginPageDomMocks.apiFetch.mockResolvedValue({
      allow_self_registration: false,
    })

    const view = renderPage([`/login?oidc_error=${errorCode}`])
    await act(async () => {
      await flushPromises()
    })

    expect(view.querySelector('[role="alert"]')?.textContent).toContain(message)
  })

  it('shows a useful OIDC provider availability error', async () => {
    loginPageDomMocks.apiFetch.mockResolvedValue({
      allow_self_registration: false,
    })

    const view = renderPage(['/login?oidc_error=provider_unavailable'])
    await act(async () => {
      await flushPromises()
    })

    expect(view.querySelector('[role="alert"]')?.textContent).toContain(
      'temporarily unavailable',
    )
  })

  it('moves local sign-in into a separate MFA verification step', async () => {
    loginPageDomMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/auth/login')
        return Promise.resolve({
          token_type: 'session_cookie',
          mfa_required: true,
        })
      if (path === '/auth/oidc/settings')
        return Promise.resolve({ enabled: false, provider_name: null })
      return Promise.resolve({ allow_self_registration: false })
    })
    const view = renderPage()
    await act(async () => await flushPromises())

    act(() => {
      setInputValue(
        view.querySelector<HTMLInputElement>('#login-email')!,
        'analyst@example.com',
      )
      setInputValue(
        view.querySelector<HTMLInputElement>('#login-password')!,
        'local-password',
      )
    })
    await submitForm(view)

    expect(loginPageDomMocks.apiFetch).toHaveBeenCalledWith(
      '/auth/login',
      expect.objectContaining({ method: 'POST' }),
      false,
    )
    expect(view.querySelector('#login-password')).toBeNull()
    expect(
      view.querySelector<HTMLInputElement>('#login-mfa-code')?.autocomplete,
    ).toBe('one-time-code')
    expect(view.textContent).toContain('Verify Sign-In')
    expect(view.textContent).toContain('Back to password sign-in')
    await vi.waitFor(() => {
      expect(credentialMutationVariables(['auth', 'login'])).toHaveLength(0)
    })
  })

  it('submits recovery codes through the MFA verification endpoint', async () => {
    let challenged = false
    loginPageDomMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/auth/login') {
        challenged = true
        return Promise.resolve({
          token_type: 'session_cookie',
          mfa_required: true,
        })
      }
      if (path === '/auth/mfa/verify')
        return Promise.resolve({
          token_type: 'session_cookie',
          csrf_token: 'csrf',
        })
      if (path === '/auth/oidc/settings')
        return Promise.resolve({ enabled: false, provider_name: null })
      return Promise.resolve({ allow_self_registration: false })
    })
    const view = renderPage()
    await act(async () => await flushPromises())
    act(() => {
      setInputValue(
        view.querySelector<HTMLInputElement>('#login-email')!,
        'analyst@example.com',
      )
      setInputValue(
        view.querySelector<HTMLInputElement>('#login-password')!,
        'local-password',
      )
    })
    await submitForm(view)
    expect(challenged).toBe(true)

    act(() =>
      setInputValue(
        view.querySelector<HTMLInputElement>('#login-mfa-code')!,
        'RECOVERY-ONE',
      ),
    )
    await submitForm(view)

    expect(loginPageDomMocks.apiFetch).toHaveBeenCalledWith(
      '/auth/mfa/verify',
      { method: 'POST', body: JSON.stringify({ code: 'RECOVERY-ONE' }) },
      false,
    )
    expect(view.querySelector<HTMLInputElement>('#login-mfa-code')?.value).toBe(
      '',
    )
    await vi.waitFor(() => {
      expect(
        credentialMutationVariables(['auth', 'mfa', 'verify']),
      ).toHaveLength(0)
    })
  })

  it('preserves an invalid code and explains an expired challenge', async () => {
    loginPageDomMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/auth/login')
        return Promise.resolve({
          token_type: 'session_cookie',
          mfa_required: true,
        })
      if (path === '/auth/mfa/verify') {
        return Promise.reject(
          new ApiError(
            'The MFA sign-in challenge is missing or expired. Start sign-in again.',
            401,
            path,
            'The MFA sign-in challenge is missing or expired. Start sign-in again.',
            { code: 'mfa_challenge_expired' },
          ),
        )
      }
      if (path === '/auth/oidc/settings')
        return Promise.resolve({ enabled: false, provider_name: null })
      return Promise.resolve({ allow_self_registration: false })
    })
    const view = renderPage()
    await act(async () => await flushPromises())
    act(() => {
      setInputValue(
        view.querySelector<HTMLInputElement>('#login-email')!,
        'analyst@example.com',
      )
      setInputValue(
        view.querySelector<HTMLInputElement>('#login-password')!,
        'local-password',
      )
    })
    await submitForm(view)
    act(() =>
      setInputValue(
        view.querySelector<HTMLInputElement>('#login-mfa-code')!,
        '123456',
      ),
    )
    await submitForm(view)

    expect(view.querySelector<HTMLInputElement>('#login-mfa-code')?.value).toBe(
      '123456',
    )
    await vi.waitFor(() => {
      expect(
        credentialMutationVariables(['auth', 'mfa', 'verify']),
      ).toHaveLength(0)
    })
    expect(view.querySelector('[role="alert"]')?.textContent).toContain(
      'verification session expired',
    )
    expect(view.textContent).toContain('password was not retained')

    act(() => {
      Array.from(view.querySelectorAll('button'))
        .find((button) => button.textContent?.includes('Back to password'))
        ?.click()
    })
    await vi.waitFor(() => {
      expect(
        credentialMutationVariables(['auth', 'mfa', 'verify']),
      ).toHaveLength(0)
    })
    expect(view.querySelector('#login-mfa-code')).toBeNull()
  })

  it('retains a rejected password in the controlled field but immediately purges mutation variables', async () => {
    loginPageDomMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/auth/login') {
        return Promise.reject(
          new ApiError('Invalid credentials', 401, path, 'Invalid credentials'),
        )
      }
      if (path === '/auth/registration-settings')
        return Promise.resolve({ allow_self_registration: true })
      return Promise.resolve({ enabled: false, provider_name: null })
    })
    const view = renderPage()
    await act(async () => await flushPromises())
    act(() => {
      setInputValue(
        view.querySelector<HTMLInputElement>('#login-email')!,
        'analyst@example.com',
      )
      setInputValue(
        view.querySelector<HTMLInputElement>('#login-password')!,
        'RetryPass123!',
      )
    })
    await submitForm(view)

    expect(view.querySelector<HTMLInputElement>('#login-password')?.value).toBe(
      'RetryPass123!',
    )
    await vi.waitFor(() => {
      expect(credentialMutationVariables(['auth', 'login'])).toHaveLength(0)
    })

    act(() => {
      Array.from(view.querySelectorAll('button'))
        .find((button) => button.textContent?.trim() === 'Register')
        ?.click()
    })
    await vi.waitFor(() => {
      expect(credentialMutationVariables(['auth', 'login'])).toHaveLength(0)
    })
    expect(view.querySelector<HTMLInputElement>('#login-password')?.value).toBe(
      '',
    )
  })

  it('purges registration credentials after account creation', async () => {
    loginPageDomMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/auth/registration-settings')
        return Promise.resolve({ allow_self_registration: true })
      if (path === '/auth/oidc/settings')
        return Promise.resolve({ enabled: false, provider_name: null })
      if (path === '/auth/register') return Promise.resolve({ id: 'new-user' })
      return Promise.resolve({})
    })
    const view = renderPage()
    await act(async () => await flushPromises())
    await vi.waitFor(() => {
      expect(
        Array.from(view.querySelectorAll('button')).some(
          (button) => button.textContent?.trim() === 'Register',
        ),
      ).toBe(true)
    })
    await act(async () => {
      Array.from(view.querySelectorAll('button'))
        .find((button) => button.textContent?.trim() === 'Register')
        ?.click()
      await flushPromises()
    })
    await vi.waitFor(() =>
      expect(view.querySelector('#login-confirm-password')).not.toBeNull(),
    )
    act(() => {
      setInputValue(
        view.querySelector<HTMLInputElement>('#login-email')!,
        'new@example.com',
      )
      setInputValue(
        view.querySelector<HTMLInputElement>('#login-password')!,
        'TemporaryPass123!',
      )
      setInputValue(
        view.querySelector<HTMLInputElement>('#login-confirm-password')!,
        'TemporaryPass123!',
      )
    })
    await submitForm(view)

    await act(async () => {
      await vi.waitFor(() => {
        expect(credentialMutationVariables(['auth', 'register'])).toHaveLength(
          0,
        )
      })
    })
    expect(view.querySelector<HTMLInputElement>('#login-password')?.value).toBe(
      '',
    )
  })
})
