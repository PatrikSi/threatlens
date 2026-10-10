// @vitest-environment jsdom

import { MutationObserver, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { act, useLayoutEffect } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { createMemoryRouter, Outlet, RouterProvider, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { captureSessionLease, invalidateSession, SessionChangedError } from '../api/sessionLifecycle'
import { AuthProvider, useAuth } from '../components/AuthContext'
import { ProtectedRoute } from '../components/ProtectedRoute'
import { SessionQueryProvider } from '../components/SessionQueryProvider'
import { useCurrentUser } from '../hooks/useCurrentUser'
import type { CurrentUser } from '../types/api'
import { LoginPage } from './LoginPage'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let root: Root | null = null
let router: ReturnType<typeof createMemoryRouter> | null = null
let container: HTMLDivElement | null = null
const clients = new Set<QueryClient>()

afterEach(async () => {
  await act(async () => {
    root?.unmount()
    await Promise.resolve()
  })
  router?.dispose()
  clients.forEach((client) => client.clear())
  clients.clear()
  root = null
  router = null
  container?.remove()
  container = null
  invalidateSession()
  window.localStorage.clear()
  window.sessionStorage.clear()
  vi.unstubAllGlobals()
})

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

function abortAware<T>(promise: Promise<T>, signal?: AbortSignal | null): Promise<T> {
  if (!signal) return promise
  if (signal.aborted) return Promise.reject(signal.reason)
  return new Promise((resolve, reject) => {
    const abort = () => reject(signal.reason ?? new DOMException('Aborted', 'AbortError'))
    signal.addEventListener('abort', abort, { once: true })
    promise.then(
      (value) => { signal.removeEventListener('abort', abort); resolve(value) },
      (error) => { signal.removeEventListener('abort', abort); reject(error) },
    )
  })
}

function jsonResponse(data: unknown) {
  return new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

function currentUser(id: string): CurrentUser {
  return {
    id, email: `${id}@example.com`, role: 'analyst', is_active: true, is_approved: true,
    approved_at: '2026-01-01T00:00:00Z', created_at: '2026-01-01T00:00:00Z',
    features: {
      ai_enabled: false, ai_configured: false, ai_summary_enabled: false,
      ai_relevance_enabled: false, ai_daily_brief_enabled: false,
    },
  }
}

function setInputValue(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

async function mountLogin(credentials = { email: 'next-user@example.com', password: 'isolated-password' }) {
  window.localStorage.clear()
  window.sessionStorage.clear()
  invalidateSession()
  const loginResponse = deferred<unknown>()
  const userResponse = deferred<CurrentUser>()
  const requests: string[] = []
  let activeClient!: QueryClient
  const loginMounts: Array<{ client: QueryClient; sessionVersion: number; credentialFormVisible: boolean }> = []
  const protectedCommits: Array<{ client: QueryClient; identity: string | undefined; privateData: unknown }> = []

  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, options?: RequestInit) => {
    const path = new URL(String(input), 'http://threatlens.local').pathname.replace(/^\/(?:api\/)?v1/, '')
    requests.push(path)
    if (path === '/auth/login' && options?.method === 'POST') {
      expect(JSON.parse(String(options.body))).toEqual(credentials)
      return abortAware(loginResponse.promise, options.signal).then(jsonResponse)
    }
    if (path === '/auth/me') {
      // Every observer gets its own response body, even when requests share the same controlled reply.
      return abortAware(userResponse.promise, options?.signal).then(jsonResponse)
    }
    if (path === '/auth/registration-settings') return Promise.resolve(jsonResponse({ allow_self_registration: false }))
    if (path === '/auth/oidc/settings') return Promise.resolve(jsonResponse({ enabled: false, provider_name: null }))
    throw new Error(`Unexpected isolated login request: ${path}`)
  }))

  function SessionContents() {
    activeClient = useQueryClient()
    clients.add(activeClient)
    return <Outlet />
  }
  function Providers() {
    return <AuthProvider><SessionQueryProvider><SessionContents /></SessionQueryProvider></AuthProvider>
  }
  function ObservedLogin() {
    const client = useQueryClient()
    const { sessionVersion } = useAuth()
    useLayoutEffect(() => {
      loginMounts.push({
        client, sessionVersion,
        credentialFormVisible: Boolean(container?.querySelector('#login-email') && container.querySelector('#login-password')),
      })
    }, [client, sessionVersion])
    return <LoginPage />
  }
  function ProtectedDestination() {
    const client = useQueryClient()
    const user = useCurrentUser()
    const location = useLocation()
    const privateData = client.getQueryData(['private-work'])
    useLayoutEffect(() => {
      protectedCommits.push({ client, identity: user.data?.id, privateData })
    }, [client, user.data?.id, privateData])
    return <p>Verified destination {location.pathname}: {user.data?.id}</p>
  }

  router = createMemoryRouter([
    {
      element: <Providers />,
      children: [
        { path: '/login', element: <ObservedLogin /> },
        { path: '/start', element: <ProtectedRoute><ProtectedDestination /></ProtectedRoute> },
      ],
    },
  ], { initialEntries: ['/login'] })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root?.render(<RouterProvider router={router!} />)
    await Promise.resolve()
  })
  await act(async () => {
    await vi.waitFor(() => expect(container?.querySelector('#login-email')).not.toBeNull())
  })
  const originalClient = activeClient
  originalClient.setQueryData(['auth', 'me', 0], currentUser('previous-user'))
  originalClient.setQueryData(['private-work'], 'Previous session private work')

  async function submitCredentials(values = credentials) {
    act(() => {
      setInputValue(container!.querySelector<HTMLInputElement>('#login-email')!, values.email)
      setInputValue(container!.querySelector<HTMLInputElement>('#login-password')!, values.password)
    })
    const form = container!.querySelector('form')!
    const submitted = vi.fn()
    form.addEventListener('submit', submitted, { once: true })
    await act(async () => {
      container!.querySelector<HTMLButtonElement>('button[type="submit"]')!.click()
      await Promise.resolve()
    })
    return {
      constraintAdmitted: form.checkValidity(),
      submitEvents: submitted.mock.calls.length,
      loginRequests: requests.filter((path) => path === '/auth/login').length,
    }
  }
  async function completeAuthentication() {
    await act(async () => {
      await vi.waitFor(() => expect(requests.filter((path) => path === '/auth/login')).toHaveLength(1))
      loginResponse.resolve({ token_type: 'session_cookie' })
      await Promise.resolve()
    })
    await act(async () => {
      await vi.waitFor(() => expect(router?.state.location.pathname).toBe('/start'))
      await vi.waitFor(() => expect(requests).toContain('/auth/me'))
    })
  }
  async function authenticate() {
    expect(await submitCredentials()).toEqual({ constraintAdmitted: true, submitEvents: 1, loginRequests: 1 })
    await completeAuthentication()
  }
  async function verifyNewIdentity() {
    await act(async () => {
      userResponse.resolve(currentUser('next-user'))
      await vi.waitFor(() => expect(container?.textContent).toContain('Verified destination /start: next-user'))
    })
  }
  return { originalClient, loginMounts, protectedCommits, authenticate, submitCredentials, completeAuthentication, verifyNewIdentity, currentClient: () => activeClient }
}

describe('Login navigation with the production session boundary', () => {
  it.each(['reviéw@example.com', 'review@éxample.com', '🔒@example.com'])(
    'submits the backend-valid Unicode identity %s through the real credential form',
    async (email) => {
      const view = await mountLogin({ email, password: 'isolated-password' })
      expect(await view.submitCredentials()).toEqual({ constraintAdmitted: true, submitEvents: 1, loginRequests: 1 })
      await view.completeAuthentication()
      expect(view.currentClient()).not.toBe(view.originalClient)
      await view.verifyNewIdentity()
      expect(router?.state.location.pathname).toBe('/start')
      expect(view.currentClient().getQueryData(['private-work'])).toBeUndefined()
    },
  )

  it.each(['email', 'password'] as const)('keeps an empty %s from submitting credentials', async (field) => {
    const view = await mountLogin()
    const credentials = { email: 'next-user@example.com', password: 'isolated-password', [field]: '' }
    expect(await view.submitCredentials(credentials)).toEqual({ constraintAdmitted: false, submitEvents: 0, loginRequests: 0 })
    expect(router?.state.location.pathname).toBe('/login')
    expect(view.currentClient()).toBe(view.originalClient)
  })

  it('does not commit a fresh credential form after successful authentication before the destination renders', async () => {
    const view = await mountLogin()
    expect(view.loginMounts.map((mount) => mount.sessionVersion)).toEqual([0])
    await view.authenticate()
    expect(container?.textContent).toContain('Loading session')
    expect(view.currentClient()).not.toBe(view.originalClient)
    await view.verifyNewIdentity()

    // Commit history catches a brief remount that a final-DOM assertion would miss.
    const authenticatedCredentialMounts = view.loginMounts.filter(
      (mount) => mount.client !== view.originalClient && mount.sessionVersion > 0 && mount.credentialFormVisible,
    )
    expect(authenticatedCredentialMounts.map((mount) => mount.sessionVersion)).toEqual([])
  })

  it('retires the old cache and lease before any verified destination commit', async () => {
    const view = await mountLogin()
    const oldLease = captureSessionLease()
    const lateReply = deferred<string>()
    const lateMutation = new MutationObserver(view.originalClient, {
      mutationFn: () => lateReply.promise,
      onSuccess: (value) => view.originalClient.setQueryData(['late-private-work'], value),
    })
    const pendingMutation = lateMutation.mutate()
    const rejected = expect(pendingMutation).rejects.toBeInstanceOf(SessionChangedError)
    await act(async () => { await Promise.resolve() })
    await view.authenticate()

    expect(() => oldLease.assertCurrent()).toThrow(SessionChangedError)
    expect(view.currentClient().getQueryData(['private-work'])).toBeUndefined()
    expect(view.originalClient.getQueryData(['private-work'])).toBeUndefined()
    await act(async () => { lateReply.resolve('Late previous session work'); await rejected })
    await view.verifyNewIdentity()
    expect(view.protectedCommits.length).toBeGreaterThan(0)
    expect(view.protectedCommits.some((commit) => commit.client === view.originalClient)).toBe(false)
    expect(view.protectedCommits.some((commit) => commit.identity === 'previous-user' || commit.privateData !== undefined)).toBe(false)
    expect(view.originalClient.getQueryData(['late-private-work'])).toBeUndefined()
    expect(view.currentClient().getQueryData(['late-private-work'])).toBeUndefined()
  })
})
