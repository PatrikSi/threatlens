// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { AccessGroupsPanel } from './AccessGroupsPanel'
import type { IAMCatalog } from './accessGovernanceApi'
import { deferred, intelButton, mountIntel } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })
const catalog: IAMCatalog = {
  permissions: [], roles: [], groups: [{ id: 'group', key: 'response', name: 'Response group', description: '',
    source: 'local', external_key: null, is_system: false, revision: 2, member_count: 101, role_ids: [],
    created_at: '2026-11-15T09:00:00Z', updated_at: '2026-11-15T09:00:00Z' }],
}
const member = { id: 'member', user_id: 'member-user', email: 'private-member@example.test', source: 'local', source_key: '', created_at: '2026-11-15T09:00:00Z' }

it('does not describe a quarantined cached group-member page as empty while its fresh read is pending', async () => {
  const fresh = deferred<unknown>()
  let firstPageReads = 0
  vi.mocked(apiFetch).mockImplementation((path) => {
    if (!path.includes('/members?')) return Promise.resolve([])
    if (new URL(path, 'https://local.test').searchParams.get('offset') === '100') {
      return Promise.reject(new ApiError('Group member access unavailable', 403, path))
    }
    firstPageReads += 1
    return firstPageReads === 1 ? Promise.resolve([member]) : fresh.promise
  })
  view = await mountIntel(<AccessGroupsPanel catalog={catalog} canWrite canReadUsers={false} durablePermissions={['*:*']} />)
  const membersPanel = () => view!.host.querySelector<HTMLElement>('section[aria-labelledby="group-members-heading"]')!
  await vi.waitFor(() => expect(membersPanel().textContent).toContain(member.email))
  act(() => intelButton(membersPanel(), 'Next').click())
  await vi.waitFor(() => expect((view!.client.getQueryState(['governance', 'iam-groups', 'group', 'members', 100])?.error as ApiError)?.status).toBe(403))
  await vi.waitFor(() => expect(membersPanel().textContent).toContain('Group member access unavailable'))
  act(() => intelButton(membersPanel(), 'Previous').click())
  await vi.waitFor(() => expect(firstPageReads).toBe(2))
  await vi.waitFor(() => expect(view!.client.getQueryState(['governance', 'iam-groups', 'group', 'members', 0])?.fetchStatus).toBe('fetching'))
  await vi.waitFor(() => expect(intelButton(membersPanel(), 'Previous').disabled).toBe(true))
  expect(membersPanel().textContent).not.toContain(member.email)
  expect(membersPanel().textContent).not.toContain('No direct members.')
  await act(async () => fresh.resolve([]))
  await vi.waitFor(() => expect(membersPanel().textContent).toContain('No direct members.'))
  expect(vi.mocked(apiFetch).mock.calls.some(([, init]) => init?.method)).toBe(false)
})
