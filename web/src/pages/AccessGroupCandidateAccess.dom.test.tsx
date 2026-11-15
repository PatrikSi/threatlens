// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { AccessGroupsPanel } from './AccessGroupsPanel'
import type { IAMCatalog } from './accessGovernanceApi'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<object>(), apiFetch: vi.fn() }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.clearAllMocks() })
const catalog: IAMCatalog = {
  permissions: [],
  roles: [{ id: 'role', key: 'private-role', name: 'Private assigned role', description: '', permissions: [], is_system: false, revision: 2, assignment_count: 1, group_count: 1, created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z' }],
  groups: [{ id: 'group', key: 'response', name: 'Response group', description: '', source: 'local', external_key: null, is_system: false, revision: 2, member_count: 1, role_ids: ['role'], created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z' }],
}
const user = { id: 'candidate', email: 'private-candidate@example.invalid', is_active: true, is_approved: true }
const member = { id: 'member', user_id: 'member-user', email: 'private-member@example.invalid', source: 'local', source_key: '', created_at: '2026-11-01T00:00:00Z' }
const role = { id: 'grant', group_id: 'group', role_id: 'role', role_key: 'private-role', role_name: 'Private assigned role', role_revision: 2, created_at: '2026-11-01T00:00:00Z' }
const candidates = { users: [user], total: 1, limit: 20, offset: 0, has_more: false }
function panel() { return <AccessGroupsPanel catalog={catalog} canWrite canReadUsers durablePermissions={['*:*']} /> }
function search(value: string) {
  const input = view!.host.querySelector<HTMLInputElement>('input[placeholder="Search by email"]')!
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
function normalRead(path: string) {
  if (path.startsWith('/users/directory?')) return candidates
  if (path.includes('/role-assignments')) return [role]
  if (path.includes('/members?')) return [member]
  return []
}

describe('access group independently protected reads', () => {
  it('keeps denied member candidates private through a later directory outage', async () => {
    let status = 200
    const rejected: number[] = []
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (path.startsWith('/users/directory?') && status !== 200) {
        rejected.push(status)
        return Promise.reject(new ApiError('Directory read unavailable', status, path))
      }
      return Promise.resolve(normalRead(path)) as never
    })
    view = await mountIntel(panel(), '/')
    await settle()
    expect(view.host.textContent).toContain(user.email)
    for (const next of [403, 503]) {
      status = next
      await act(async () => { await view!.client.invalidateQueries({ queryKey: ['governance', 'iam-member-candidates'] }) })
      await settle()
      expect(rejected).toContain(next)
      expect(view.host.textContent).not.toContain(user.email)
      expect(view.host.textContent).toContain(member.email)
    }
  })

  it('keeps a cached candidate search private after a sibling search denial', async () => {
    let status = 200
    const pending = deferred<unknown>()
    const rejected: number[] = []
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (!path.startsWith('/users/directory?')) return Promise.resolve(normalRead(path)) as never
      if (status === 200) return Promise.resolve(candidates) as never
      if (status === 403) {
        rejected.push(403)
        return Promise.reject(new ApiError('Directory read unavailable', 403, path))
      }
      return pending.promise as never
    })
    view = await mountIntel(panel(), '/')
    await settle()
    expect(view.host.textContent).toContain(user.email)
    search('private')
    await settle()
    status = 403
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['governance', 'iam-member-candidates'] }) })
    await settle()
    expect(rejected).toContain(403)
    status = 503
    search('')
    await settle()
    expect(view.host.textContent).not.toContain(user.email)
  })

  it('pauses adding a selected member during an ordinary candidate outage', async () => {
    let unavailable = false
    vi.mocked(apiFetch).mockImplementation((path) => path.startsWith('/users/directory?') && unavailable
      ? Promise.reject(new ApiError('Directory temporarily unavailable', 503, path))
      : Promise.resolve(normalRead(path)) as never)
    view = await mountIntel(panel(), '/')
    await settle()
    const select = view.host.querySelector<HTMLSelectElement>('[aria-label="User to add"]')!
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(select, user.id)
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(intelButton(view.host, 'Add').disabled).toBe(false)
    unavailable = true
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['governance', 'iam-member-candidates'] }) })
    await settle()
    expect(view.host.textContent).toContain(user.email)
    const add = intelButton(view.host, 'Add')
    expect(add.disabled).toBe(true)
    act(() => add.click())
    expect(vi.mocked(apiFetch).mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false)
  })

  it.each([
    { family: 'members', path: '/members?', label: `Remove ${member.email} from group`, privateValue: member.email },
    { family: 'roles', path: '/role-assignments', label: `Remove ${role.role_name} role from group`, privateValue: role.role_name },
  ])('retires selected $family removal after its independent read is denied', async ({ family, path: target, label, privateValue }) => {
    let status = 200
    const rejected: number[] = []
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (path.includes(target) && status !== 200) {
        rejected.push(status)
        return Promise.reject(new ApiError('Group assignment read unavailable', status, path))
      }
      return Promise.resolve(normalRead(path)) as never
    })
    view = await mountIntel(panel(), '/')
    await settle()
    act(() => view!.host.querySelector<HTMLButtonElement>(`[aria-label="${label}"]`)!.click())
    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain(privateValue)
    for (const next of [403, 503]) {
      status = next
      await act(async () => { await view!.client.invalidateQueries({ queryKey: ['governance', 'iam-groups', 'group', family] }) })
      await settle()
      expect(rejected).toContain(next)
      expect(document.querySelector('[role="alertdialog"]')).toBeNull()
    }
    expect(vi.mocked(apiFetch).mock.calls.some(([, options]) => options?.method === 'DELETE')).toBe(false)
  })
})
