// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const accountPageDomMocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  markLoggedOut: vi.fn(),
  navigate: vi.fn(),
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
    data: {
      id: 'user-1',
      email: 'analyst@example.com',
      role: 'analyst',
      is_active: true,
      is_approved: true,
      approved_at: '2026-04-20T10:00:00Z',
      created_at: '2026-04-19T10:00:00Z',
      features: {
        ai_enabled: false,
        ai_configured: false,
        ai_summary_enabled: false,
        ai_relevance_enabled: false,
        ai_daily_brief_enabled: false,
      },
    },
    isLoading: false,
    isError: false,
    error: null,
  }),
}))

vi.mock('react-router-dom', () => ({
  useNavigate: () => accountPageDomMocks.navigate,
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
  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
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
  accountPageDomMocks.apiFetch.mockReset()
  accountPageDomMocks.markLoggedOut.mockReset()
  accountPageDomMocks.navigate.mockReset()
  accountPageDomMocks.useBlocker.mockReset()
  accountPageDomMocks.useBlocker.mockImplementation(() => ({
    state: 'unblocked' as const,
    proceed: vi.fn(),
    reset: vi.fn(),
  }))
})

describe('AccountPage DOM workflows', () => {
  it('redirects to login after a successful password change', async () => {
    accountPageDomMocks.apiFetch.mockResolvedValue({})

    const view = renderPage()
    const currentPasswordInput = view.querySelector<HTMLInputElement>('#account-current-password')
    const newPasswordInput = view.querySelector<HTMLInputElement>('#account-new-password')
    const form = view.querySelector('form')

    expect(currentPasswordInput).not.toBeNull()
    expect(newPasswordInput).not.toBeNull()
    expect(form).not.toBeNull()

    act(() => {
      setInputValue(currentPasswordInput!, 'current-password')
      setInputValue(newPasswordInput!, 'new-password-123')
    })

    await act(async () => {
      form!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await flushPromises()
      await flushPromises()
    })

    expect(accountPageDomMocks.apiFetch).toHaveBeenCalledWith('/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({
        current_password: 'current-password',
        new_password: 'new-password-123',
      }),
    })

    expect(currentPasswordInput?.value).toBe('')
    expect(newPasswordInput?.value).toBe('')
    expect(accountPageDomMocks.markLoggedOut).toHaveBeenCalledTimes(1)
    expect(accountPageDomMocks.navigate).toHaveBeenCalledWith('/login', {
      replace: true,
      state: { authMessage: 'Password updated. Sign in again with your new password.' },
    })
  })

  it('announces a failed password change assertively', async () => {
    accountPageDomMocks.apiFetch.mockRejectedValue(new Error('Password change failed'))

    const view = renderPage()
    const currentPasswordInput = view.querySelector<HTMLInputElement>('#account-current-password')
    const newPasswordInput = view.querySelector<HTMLInputElement>('#account-new-password')
    const form = view.querySelector('form')

    expect(currentPasswordInput).not.toBeNull()
    expect(newPasswordInput).not.toBeNull()
    expect(form).not.toBeNull()

    act(() => {
      setInputValue(currentPasswordInput!, 'current-password')
      setInputValue(newPasswordInput!, 'new-password-123')
    })

    await act(async () => {
      form!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await flushPromises()
      await flushPromises()
    })

    const notice = view.querySelector('[role="alert"][aria-live="assertive"][aria-atomic="true"]')
    expect(notice).not.toBeNull()
    expect(notice?.textContent).toContain('Failed to change password.')
  })

  it('shows inline validation before sending a short password change', async () => {
    const view = renderPage()
    const currentPasswordInput = view.querySelector<HTMLInputElement>('#account-current-password')
    const newPasswordInput = view.querySelector<HTMLInputElement>('#account-new-password')
    const form = view.querySelector('form')

    expect(currentPasswordInput).not.toBeNull()
    expect(newPasswordInput).not.toBeNull()
    expect(form).not.toBeNull()

    act(() => {
      setInputValue(currentPasswordInput!, 'wrong-password-qa')
      setInputValue(newPasswordInput!, 'short')
    })

    await act(async () => {
      form!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await flushPromises()
    })

    const notice = view.querySelector('[role="alert"][aria-live="assertive"][aria-atomic="true"]')
    expect(notice).not.toBeNull()
    expect(notice?.textContent).toContain('New password must be at least 8 characters.')
    expect(accountPageDomMocks.apiFetch).not.toHaveBeenCalledWith('/auth/change-password', expect.anything())
  })

  it('treats an unfinished password change as unsaved work', () => {
    const view = renderPage()
    const currentPasswordInput = view.querySelector<HTMLInputElement>('#account-current-password')

    expect(currentPasswordInput).not.toBeNull()

    act(() => {
      setInputValue(currentPasswordInput!, 'current-password')
    })

    expect(accountPageDomMocks.useBlocker).toHaveBeenLastCalledWith(true)
  })

  it('shows a linked OIDC identity with a protected unlink control', async () => {
    accountPageDomMocks.apiFetch.mockImplementation((path: string) => {
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

    expect(view.textContent).toContain('Linked to Acme SSO as analyst@example.com')
    expect(view.querySelector('#account-unlink-password')).not.toBeNull()
    expect([...view.querySelectorAll('button')].some((button) => button.textContent?.includes('Unlink SSO'))).toBe(true)
  })

  it('starts OIDC account linking through the protected API', async () => {
    accountPageDomMocks.apiFetch.mockImplementation((path: string) => {
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
      return new Promise(() => {})
    })

    const view = renderPage()
    await act(async () => {
      await waitForQueriesToSettle()
    })
    const linkButton = [...view.querySelectorAll('button')].find((button) => button.textContent === 'Link SSO account')
    expect(linkButton).not.toBeUndefined()

    await act(async () => {
      linkButton!.click()
      await flushPromises()
    })

    expect(accountPageDomMocks.apiFetch).toHaveBeenCalledWith('/auth/oidc/link', { method: 'POST' })
    expect(linkButton?.textContent).toBe('Connecting...')
  })

  it('keeps unlink confirmation visible after identity status refreshes', async () => {
    let linked = true
    accountPageDomMocks.apiFetch.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === '/auth/oidc/account' && options?.method === 'DELETE') {
        linked = false
        return Promise.resolve(undefined)
      }
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
    })

    const view = renderPage()
    await act(async () => {
      await waitForQueriesToSettle()
    })
    expect(view.querySelector('#account-unlink-password')).not.toBeNull()
    act(() => {
      setInputValue(view.querySelector<HTMLInputElement>('#account-unlink-password')!, 'AnalystPass123!')
    })

    await act(async () => {
      view.querySelector<HTMLInputElement>('#account-unlink-password')?.closest('form')?.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      )
      await vi.waitFor(() => {
        expect(queryClient?.isMutating()).toBe(0)
        expect(queryClient?.isFetching()).toBe(0)
      })
      await flushPromises()
      await flushPromises()
    })

    expect(view.textContent).toContain('SSO identity unlinked.')
    expect([...view.querySelectorAll('button')].some((button) => button.textContent === 'Link SSO account')).toBe(true)
  })
})
