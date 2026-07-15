// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AuthProvider } from '../components/AuthContext'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const loginPageDomMocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
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
  apiFetch: loginPageDomMocks.apiFetch,
}))

import { LoginPage } from './LoginPage'

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
})

describe('LoginPage accessibility', () => {
  it('shows an explicit error when registration settings cannot be loaded', async () => {
    loginPageDomMocks.apiFetch.mockRejectedValue(new Error('registration settings unavailable'))

    const view = renderPage()

    await act(async () => {
      await vi.waitFor(() => {
        expect(view.querySelector('[role="alert"]')).not.toBeNull()
      })
    })

    const alert = view.querySelector('[role="alert"]')
    expect(alert?.textContent).toContain('Registration availability could not be loaded')
    expect(view.querySelector<HTMLButtonElement>('button[type="submit"]')?.textContent).toContain('Sign in')
  })

  it('connects labels to controls and supplies login autocomplete hints', async () => {
    loginPageDomMocks.apiFetch.mockResolvedValue({ allow_self_registration: false })

    const view = renderPage()

    await act(async () => {
      await flushPromises()
    })

    expect(view.querySelector('label[for="login-email"]')?.textContent).toContain('Email')
    expect(view.querySelector<HTMLInputElement>('#login-email')?.autocomplete).toBe('email')
    expect(view.querySelector('label[for="login-password"]')?.textContent).toContain('Password')
    expect(view.querySelector<HTMLInputElement>('#login-password')?.autocomplete).toBe('current-password')
  })

  it('exposes route auth messages as live alerts', async () => {
    loginPageDomMocks.apiFetch.mockResolvedValue({ allow_self_registration: false })

    const view = renderPage([
      {
        pathname: '/login',
        state: { authMessage: 'Session expired. Sign in again.' },
      },
    ])

    await act(async () => {
      await flushPromises()
    })

    const alert = view.querySelector('[role="alert"][aria-live="polite"][aria-atomic="true"]')
    expect(alert).not.toBeNull()
    expect(alert?.textContent).toContain('Session expired. Sign in again.')
  })
})
