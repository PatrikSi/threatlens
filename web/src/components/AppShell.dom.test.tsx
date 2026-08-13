// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ThemeProvider } from './ThemeContext'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const appShellDomMocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  markLoggedOut: vi.fn(),
}))

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number
    path: string
    detail: unknown

    constructor(message: string, status: number, path: string, detail: unknown = null) {
      super(message)
      this.name = 'ApiError'
      this.status = status
      this.path = path
      this.detail = detail
    }
  },
  apiFetch: appShellDomMocks.apiFetch,
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
    refetch: vi.fn(),
  }),
}))

vi.mock('./AuthContext', () => ({
  useAuth: () => ({
    sessionVersion: 0,
    markAuthenticated: vi.fn(),
    markLoggedOut: appShellDomMocks.markLoggedOut,
  }),
}))

import { ApiError } from '../api/client'
import { AppShell } from './AppShell'

let queryClient: QueryClient | null = null
let root: Root | null = null
let container: HTMLDivElement | null = null

function renderShell() {
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
      <MemoryRouter initialEntries={['/']}>
        <ThemeProvider>
          <QueryClientProvider client={queryClient!}>
            <Routes>
              <Route path="/" element={<AppShell />}>
                <Route index element={<div>Dashboard body</div>} />
              </Route>
              <Route path="/login" element={<div>Login page</div>} />
            </Routes>
          </QueryClientProvider>
        </ThemeProvider>
      </MemoryRouter>,
    )
  })
  return container
}

function flushPromises() {
  return new Promise((resolve) => window.setTimeout(resolve, 0))
}

async function clickLogout(view: HTMLDivElement) {
  const logoutButton = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Logout')
  expect(logoutButton).not.toBeNull()

  await act(async () => {
    logoutButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
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
  appShellDomMocks.apiFetch.mockReset()
  appShellDomMocks.markLoggedOut.mockReset()
  window.localStorage.clear()
  document.documentElement.className = ''
  document.documentElement.removeAttribute('data-color-mode')
})

describe('AppShell logout', () => {
  it('opens mobile navigation as a vertical list', () => {
    const view = renderShell()
    const menuButton = view.querySelector<HTMLButtonElement>('[aria-controls="mobile-primary-navigation"]')

    expect(menuButton).not.toBeNull()
    expect(view.querySelector('#mobile-primary-navigation')).toBeNull()

    act(() => {
      menuButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const mobileNavigation = view.querySelector('#mobile-primary-navigation nav')
    expect(mobileNavigation).not.toBeNull()
    expect(mobileNavigation?.className).toContain('divide-y')
    expect(mobileNavigation?.className).not.toContain('grid-cols-2')
    expect(menuButton?.getAttribute('aria-expanded')).toBe('true')
    expect(mobileNavigation?.textContent).toContain('Export')
  })

  it('shows a subtle app version in the footer', () => {
    const view = renderShell()

    expect(view.textContent).toContain('v1.0.0')
  })

  it('does not mark the browser logged out when the logout request fails over the network', async () => {
    appShellDomMocks.apiFetch.mockRejectedValue(new Error('Failed to fetch'))

    const view = renderShell()
    await clickLogout(view)

    expect(appShellDomMocks.markLoggedOut).not.toHaveBeenCalled()
    expect(view.textContent).toContain('Logout could not be completed.')
    expect(view.textContent).not.toContain('Failed to fetch')
    expect(view.textContent).toContain('Dashboard body')
  })

  it('does not mark the browser logged out when the logout request fails with a 5xx response', async () => {
    appShellDomMocks.apiFetch.mockRejectedValue(new ApiError('HTTP 503', 503, '/auth/logout'))

    const view = renderShell()
    await clickLogout(view)

    expect(appShellDomMocks.markLoggedOut).not.toHaveBeenCalled()
    expect(view.textContent).toContain('Logout could not be completed. The API encountered an internal or dependency failure.')
    expect(view.textContent).toContain('Dashboard body')
  })

  it('does not mark the browser logged out when logout fails CSRF validation', async () => {
    appShellDomMocks.apiFetch.mockRejectedValue(new ApiError('Missing or invalid CSRF token', 403, '/auth/logout'))

    const view = renderShell()
    await clickLogout(view)

    expect(appShellDomMocks.markLoggedOut).not.toHaveBeenCalled()
    expect(view.textContent).toContain('Logout could not be completed. Missing or invalid CSRF token.')
    expect(view.textContent).toContain('Refresh the page to renew the browser session')
    expect(view.textContent).toContain('Dashboard body')
  })
})
