import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({ apiFetch: vi.fn() }))

vi.mock('../api/client', () => ({ apiFetch: apiMocks.apiFetch }))

import { getWorkspaceRegistry } from './workspaceApi'

beforeEach(() => {
  apiMocks.apiFetch.mockReset()
})

describe('workspace API compatibility', () => {
  it('normalizes legacy singular permission fields at the API boundary', async () => {
    apiMocks.apiFetch.mockResolvedValue({
      modules: [{
        id: 'primary.dashboard', label: 'Dashboard', route: '/', section: 'primary',
        parent_id: null, required_permission: 'read:items', feature_flag: null,
        default_optional: false, default_order: 0, default_mobile_priority: 0,
        mobile_behavior: 'primary',
      }],
      dashboard_panels: [{
        id: 'rss', label: 'RSS intelligence', required_permission: 'read:items',
        feature_flag: null,
      }],
    })

    const registry = await getWorkspaceRegistry()

    expect(registry.modules[0].required_permissions).toEqual(['read:items'])
    expect(registry.dashboard_panels[0].required_permissions).toEqual(['read:items'])
  })

  it('preserves authoritative plural permission lists from current servers', async () => {
    apiMocks.apiFetch.mockResolvedValue({
      modules: [{
        id: 'primary.alerts', label: 'Alerts', route: '/alerts', section: 'primary',
        parent_id: null, required_permission: 'read:alerts',
        required_permissions: ['read:alerts', 'read:items'], feature_flag: null,
        default_optional: true, default_order: 10, default_mobile_priority: 10,
        mobile_behavior: 'primary',
      }],
      dashboard_panels: [],
    })

    const registry = await getWorkspaceRegistry()

    expect(registry.modules[0].required_permissions).toEqual(['read:alerts', 'read:items'])
  })
})
