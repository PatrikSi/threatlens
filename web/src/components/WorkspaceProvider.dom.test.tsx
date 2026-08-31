// @vitest-environment jsdom

import { QueryClient, QueryClientProvider, focusManager } from '@tanstack/react-query'
import { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  WorkspaceEffectiveResponse,
  WorkspaceRegistryResponse,
  WorkspaceUserPreferenceResponse,
  WorkspaceUserPreferenceWriteRequest,
} from '../types/workspace'
import { TRUSTED_DASHBOARD_PANELS, TRUSTED_WORKSPACE_MODULES } from '../workspace/moduleRegistry'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const providerMocks = vi.hoisted(() => ({
  getEffectiveWorkspace: vi.fn(),
  getWorkspacePreferences: vi.fn(),
  getWorkspaceRegistry: vi.fn(),
  resetWorkspacePreferences: vi.fn(),
  updateWorkspacePreferences: vi.fn(),
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({
    data: {
      id: 'user-1', email: 'admin@example.com', role: 'admin', is_active: true, is_approved: true,
      approved_at: '2026-01-01T00:00:00Z', created_at: '2026-01-01T00:00:00Z',
      features: {
        ai_enabled: true, ai_configured: true, ai_summary_enabled: true,
        ai_relevance_enabled: true, ai_daily_brief_enabled: true,
      },
      access: {
        principal_type: 'user', principal_id: 'user-1', legacy_role: 'admin', account_eligible: true,
        credential_limited: false, roles: [], groups: [], permissions: ['*:*'], policy_revision: 1,
      },
    },
    isLoading: false,
    isError: false,
    error: null,
  }),
}))

vi.mock('../workspace/workspaceApi', () => ({
  workspaceQueryKeys: {
    root: ['workspace'],
    effective: (userId: string) => ['workspace', 'effective', userId],
    registry: ['workspace', 'registry'],
    preferences: (userId: string) => ['workspace', 'preferences', userId],
  },
  getEffectiveWorkspace: providerMocks.getEffectiveWorkspace,
  getWorkspacePreferences: providerMocks.getWorkspacePreferences,
  getWorkspaceRegistry: providerMocks.getWorkspaceRegistry,
  resetWorkspacePreferences: providerMocks.resetWorkspacePreferences,
  updateWorkspacePreferences: providerMocks.updateWorkspacePreferences,
}))

import { useWorkspace } from '../workspace/useWorkspace'
import { WorkspaceProvider } from './WorkspaceProvider'

let root: Root | null = null
let container: HTMLDivElement | null = null
let queryClient: QueryClient | null = null

beforeEach(() => {
  providerMocks.getWorkspaceRegistry.mockResolvedValue(serverRegistry())
  providerMocks.getEffectiveWorkspace.mockResolvedValue(effectiveWorkspace())
  providerMocks.getWorkspacePreferences.mockResolvedValue(preferences())
  providerMocks.updateWorkspacePreferences.mockResolvedValue({ ...preferences(), revision: 2 })
  providerMocks.resetWorkspacePreferences.mockResolvedValue({ ...preferences(), revision: 0 })
})

afterEach(() => {
  act(() => root?.unmount())
  queryClient?.clear()
  root = null
  queryClient = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  focusManager.setFocused(undefined)
  Object.values(providerMocks).forEach((mock) => mock.mockReset())
})

describe('WorkspaceProvider', () => {
  it('loads typed workspace state and refreshes effective state after a preference save', async () => {
    const view = renderProvider()
    await act(async () => {
      await vi.waitFor(() => expect(view.textContent).toContain('ready:Dashboard,Alerts'))
    })

    const saveButton = view.querySelector<HTMLButtonElement>('button')!
    await act(async () => {
      saveButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await vi.waitFor(() => expect(providerMocks.updateWorkspacePreferences).toHaveBeenCalledTimes(1))
      await vi.waitFor(() => expect(view.textContent).toContain('preference:2'))
    })

    expect(providerMocks.updateWorkspacePreferences).toHaveBeenCalledWith(
      expect.objectContaining({ expected_revision: 1, landing_module_id: 'primary.alerts' }),
    )
    expect(providerMocks.getEffectiveWorkspace.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('keeps trusted role defaults usable when effective workspace loading fails', async () => {
    providerMocks.getEffectiveWorkspace.mockRejectedValue(new Error('workspace unavailable'))
    const view = renderProvider()

    await act(async () => {
      await vi.waitFor(() => expect(view.textContent).toContain('degraded:Dashboard,Alerts'))
    })

    expect(view.textContent).not.toContain('javascript:')
    expect(view.textContent).toContain('preference:1')
  })

  it('polls effective policy in the foreground and safely keeps stale navigation after a failed focus refresh', async () => {
    const view = renderProvider()
    await act(async () => {
      await vi.waitFor(() => expect(view.textContent).toContain('ready:Dashboard,Alerts'))
    })
    const initialCalls = providerMocks.getEffectiveWorkspace.mock.calls.length
    const effectiveQuery = queryClient?.getQueryCache().find({
      queryKey: ['workspace', 'effective', 'user-1'],
      exact: true,
    })
    const effectiveOptions = effectiveQuery?.options as {
      refetchInterval?: number
      refetchIntervalInBackground?: boolean
    }
    expect(effectiveOptions.refetchInterval).toBe(60_000)
    expect(effectiveOptions.refetchIntervalInBackground).toBe(false)

    providerMocks.getEffectiveWorkspace.mockRejectedValueOnce(new Error('refresh unavailable'))
    focusManager.setFocused(false)
    await act(async () => {
      focusManager.setFocused(true)
      await vi.waitFor(() => {
        expect(providerMocks.getEffectiveWorkspace.mock.calls.length).toBeGreaterThan(initialCalls)
      })
      await vi.waitFor(() => expect(view.textContent).toContain('degraded:Dashboard,Alerts'))
    })

    expect(view.textContent).toContain('preference:1')
  })
})

function renderProvider() {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(
      <QueryClientProvider client={queryClient!}>
        <WorkspaceProvider><WorkspaceProbe /></WorkspaceProvider>
      </QueryClientProvider>,
    )
  })
  return container
}

function WorkspaceProbe() {
  const workspace = useWorkspace()
  const [saveError, setSaveError] = useState('')
  const payload: WorkspaceUserPreferenceWriteRequest = {
    expected_revision: workspace.preferences?.revision ?? 0,
    landing_module_id: 'primary.alerts',
    modules: [{ module_id: 'primary.alerts', visible: true, order: 1 }],
    dashboard_panel_ids: null,
  }
  return (
    <div>
      <p>{workspace.isDegraded ? 'degraded' : workspace.isLoading ? 'loading' : 'ready'}:{workspace.model.primaryNavigation.map((module) => module.label).slice(0, 2).join(',')}</p>
      <p>preference:{workspace.preferences?.revision ?? 'none'}</p>
      <button type="button" onClick={() => void workspace.savePreferences(payload).catch((error) => setSaveError(String(error)))}>Save</button>
      {saveError && <p>{saveError}</p>}
    </div>
  )
}

function preferences(): WorkspaceUserPreferenceResponse {
  return {
    user_id: 'user-1', role: 'admin', landing_module_id: null, modules: [],
    dashboard_panel_ids: null, revision: 1, updated_by_user_id: null,
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    unknown_module_ids: [], unknown_dashboard_panel_ids: [], warnings: [],
  }
}

function effectiveWorkspace(): WorkspaceEffectiveResponse {
  return {
    role: 'admin', policy_revision: 1, preference_revision: 1,
    landing_module_id: 'primary.dashboard', dashboard_panel_ids: ['rss'],
    dashboard_panels: [{ id: 'rss', visible: true, permission_allowed: true, feature_available: true, missing_permissions: [], reasons: [] }],
    modules: TRUSTED_WORKSPACE_MODULES.filter((module) => module.policyManaged).map((module) => ({
      id: module.id, label: module.label, route: module.route, section: module.section,
      parent_id: module.parentId, visible: true, optional: module.defaultOptional,
      order: module.defaultOrder, mobile_priority: module.defaultMobilePriority,
      mobile_behavior: module.mobileBehavior, permission_allowed: true, missing_permissions: [],
      feature_available: true, policy_visible: true, preference_visible: true, reasons: [],
    })),
    warnings: [],
  }
}

function serverRegistry(): WorkspaceRegistryResponse {
  return {
    modules: TRUSTED_WORKSPACE_MODULES.filter((module) => module.policyManaged).map((module) => ({
      id: module.id, label: module.label, route: module.route, section: module.section,
      parent_id: module.parentId, required_permission: module.requiredPermissions[0] ?? null,
      required_permissions: [...module.requiredPermissions], feature_flag: module.serverFeatureFlag,
      default_optional: module.defaultOptional, default_order: module.defaultOrder,
      default_mobile_priority: module.defaultMobilePriority, mobile_behavior: module.mobileBehavior,
    })),
    dashboard_panels: TRUSTED_DASHBOARD_PANELS.map((panel) => ({
      id: panel.id, label: panel.label, required_permission: panel.requiredPermissions[0] ?? null,
      required_permissions: [...panel.requiredPermissions], feature_flag: panel.serverFeatureFlag,
    })),
  }
}
