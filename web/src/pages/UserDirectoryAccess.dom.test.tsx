// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import type { AdminUser } from '../types/api'
import { UsersPage } from './UsersPage'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<object>(), apiFetch: vi.fn() }))
vi.mock('../components/AuthContext', () => ({ useAuth: () => ({ markLoggedOut: vi.fn() }) }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: { id: 'acting-admin', role: 'admin', access: { permissions: ['write:users'], durable_permissions: ['write:users'] }, features: {} }, isError: false, isLoading: false }) }))

let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.clearAllMocks() })
const user: AdminUser = {
  id: 'private-user', email: 'private-directory-user@example.invalid', role: 'analyst', is_active: true,
  is_approved: true, approved_at: '2026-11-01T00:00:00Z', created_at: '2026-11-01T00:00:00Z',
  password_login_enabled: true, provisioning_source: 'local', authentication_methods: ['password'],
  oidc_provider_name: null, oidc_linked_at: null, oidc_last_login_at: null,
  password_managed_by: 'local', role_managed_by: 'local', mfa_enabled: true,
  mfa_confirmed_at: '2026-11-01T00:00:00Z', active_session_count: 2, security_version: 3,
}
const directory = { users: [user], total: 1, limit: 50, offset: 0, has_more: false }
function normalRead(path: string) {
  if (path.startsWith('/users/directory?')) return directory
  if (path === '/auth/security/mfa') return { local_mfa_available: true, managed_by: 'local', enabled: true, confirmed_at: '2026-11-01T00:00:00Z', recovery_codes_remaining: 8 }
  if (path === '/auth/security/sessions') return { sessions: [], active_count: 0, history_truncated: false }
  return {}
}
function search(value: string) {
  const input = view!.host.querySelector<HTMLInputElement>('#user-directory-search')!
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

describe('user-directory independent read contexts', () => {
  it('keeps saved directory labels private while preserving a hidden local password draft', async () => {
    let status = 200
    const rejected: number[] = []
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (path.startsWith('/users/directory?') && status !== 200) {
        rejected.push(status)
        return Promise.reject(new ApiError('Directory read unavailable', status, path))
      }
      return Promise.resolve(normalRead(path)) as never
    })
    view = await mountIntel(<UsersPage />, '/')
    await settle()
    act(() => intelButton(view!.host, 'Manage').click())
    const input = view.host.querySelector<HTMLInputElement>(`#user-reset-password-${user.id}`)!
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'UnsentPassword123!')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    status = 403
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['users', 'directory'] }) })
    await settle()
    expect(rejected).toContain(403)
    const unload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(true)
    expect(view.host.textContent).not.toContain(user.email)
    expect(view.host.querySelector(`#user-reset-password-${user.id}`)).toBeNull()
    status = 200
    await act(async () => { await view!.client.refetchQueries({ queryKey: ['users', 'directory'] }) })
    await settle()
    act(() => intelButton(view!.host, 'Manage').click())
    expect(view.host.querySelector<HTMLInputElement>(`#user-reset-password-${user.id}`)?.value).toBe('UnsentPassword123!')
  })

  it('keeps a cached directory search hidden after a sibling search read denial', async () => {
    let status = 200
    const rejected: number[] = []
    const pending = deferred<unknown>()
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (!path.startsWith('/users/directory?')) return Promise.resolve(normalRead(path)) as never
      if (status === 200) return Promise.resolve(directory) as never
      if (status === 403) {
        rejected.push(403)
        return Promise.reject(new ApiError('Directory read unavailable', 403, path))
      }
      return pending.promise as never
    })
    view = await mountIntel(<UsersPage />, '/')
    await settle()
    expect(view.host.textContent).toContain(user.email)
    search('private')
    await settle()
    status = 403
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['users', 'directory'] }) })
    await settle()
    expect(rejected).toContain(403)
    // Existing any-error row handling is a positive control, not a new finding.
    expect(view.host.textContent).not.toContain(user.email)
    status = 503
    // The failed directory hides its filter controls. Re-enter the default
    // cached search through the route while retaining the same QueryClient.
    await act(async () => { await view!.router.navigate('/other') })
    await act(async () => { await view!.router.navigate('/') })
    await settle()
    expect(view.host.textContent).not.toContain(user.email)
  })

  it('retires a selected directory MFA-reset target after the directory read is denied', async () => {
    let status = 200
    const rejected: number[] = []
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (path.startsWith('/users/directory?') && status !== 200) {
        rejected.push(status)
        return Promise.reject(new ApiError('Directory read unavailable', status, path))
      }
      return Promise.resolve(normalRead(path)) as never
    })
    view = await mountIntel(<UsersPage />, '/')
    await settle()
    act(() => intelButton(view!.host, 'Manage').click())
    act(() => view!.host.querySelector<HTMLButtonElement>(`[aria-label="Reset multi-factor authentication for ${user.email}"]`)!.click())
    await settle()
    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain(user.email)
    for (const next of [403, 503]) {
      status = next
      await act(async () => { await view!.client.invalidateQueries({ queryKey: ['users', 'directory'] }) })
      await settle()
      expect(rejected).toContain(next)
      expect(document.querySelector('[role="alertdialog"]')).toBeNull()
      expect(document.body.textContent).not.toContain(user.email)
    }
    expect(vi.mocked(apiFetch).mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false)
  })
})
