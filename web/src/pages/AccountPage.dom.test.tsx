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
}))

vi.mock('../api/client', () => ({
  apiFetch: accountPageDomMocks.apiFetch,
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
}))

import { AccountPage } from './AccountPage'

let queryClient: QueryClient | null = null
let root: Root | null = null
let container: HTMLDivElement | null = null

function renderPage() {
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
})
