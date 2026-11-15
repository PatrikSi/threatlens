// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { AccessGovernancePage } from './AccessGovernancePage'
import { mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<object>(), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: { id: 'acting-admin', role: 'admin', access: { permissions: ['*:*'], durable_permissions: ['*:*'] } }, isError: false, isLoading: false }) }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.clearAllMocks() })

const policy = {
  state: { mode: 'audit', revision: 731, updated_at: '2026-11-01T00:00:00Z' }, labels: [],
  preflight: { current_coverage_version: 1, required_coverage_version: 1, ready_for_enforcement: true, blockers: [] },
}
function normalRead(path: string) {
  if (path.startsWith('/iam/service-accounts?')) return { items: [], total: 0 }
  if (path.startsWith('/iam/access-reviews?')) return { campaigns: [], total: 0 }
  if (path.startsWith('/iam/elevations?')) return { elevations: [], total: 0 }
  if (path.startsWith('/iam/action-approvals?')) return { approvals: [], total: 0 }
  if (path === '/iam/data-policies') return { ...policy, state: { ...policy.state, revision: 0 } }
  return []
}

describe('access governance optional read authority', () => {
  it.each([
    { area: 'data-policy', path: '/iam/data-policies', value: policy, privateValue: '731' },
    { area: 'service-accounts', path: '/iam/service-accounts?', value: { items: [], total: 731 }, privateValue: '731' },
    { area: 'access-reviews', path: '/iam/access-reviews?', value: { campaigns: [{ id: 'review', name: 'Private review campaign', status: 'open', review_due_at: '2026-12-01T00:00:00Z' }], total: 731 }, privateValue: 'Private review campaign' },
    { area: 'elevations', path: '/iam/elevations?', value: { elevations: [{ id: 'elevation', target_email: 'private-elevation@example.invalid', role_name: 'Private elevated role', status: 'pending', request_expires_at: '2026-12-01T00:00:00Z' }], total: 731 }, privateValue: 'private-elevation@example.invalid' },
    { area: 'action-approvals', path: '/iam/action-approvals?', value: { approvals: [{ id: 'approval', action_label: 'Private approval action', target_type: 'private-target', status: 'pending', expires_at: '2026-12-01T00:00:00Z' }], total: 731 }, privateValue: 'Private approval action' },
  ])('keeps denied $area overview metadata private through a later outage', async ({ area, path: target, value, privateValue }) => {
    let status = 200
    const rejected: number[] = []
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (path.startsWith(target)) {
        if (status === 200) return Promise.resolve(value) as never
        rejected.push(status)
        return Promise.reject(new ApiError('Governance inventory read unavailable', status, path))
      }
      return Promise.resolve(normalRead(path)) as never
    })
    view = await mountIntel(<AccessGovernancePage />, '/')
    await settle()
    expect(view.host.textContent).toContain(privateValue)
    for (const next of [403, 503]) {
      status = next
      await act(async () => { await view!.client.invalidateQueries({ queryKey: ['governance', area] }) })
      await settle()
      expect(rejected).toContain(next)
      expect(view.host.textContent).not.toContain(privateValue)
      expect(view.host.textContent).not.toContain('731')
      expect(view.host.textContent).toContain('Access roles')
      expect(view.host.textContent).toContain('Groups')
    }
  })
})
