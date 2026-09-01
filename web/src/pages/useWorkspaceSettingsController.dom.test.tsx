// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  WorkspaceEffectiveResponse,
  WorkspaceRolePolicyResponse,
  WorkspaceUserPreferenceResponse,
  WorkspaceUserPreferenceWriteRequest,
} from '../types/workspace'
import { TRUSTED_WORKSPACE_MODULES } from '../workspace/moduleRegistry'
import type { WorkspaceContextValue } from '../workspace/workspaceContext'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const controllerMocks = vi.hoisted(() => ({
  getRolePolicies: vi.fn(),
  resetRolePolicy: vi.fn(),
  updateRolePolicy: vi.fn(),
  savePreferences: vi.fn(),
  resetPreferences: vi.fn(),
  refreshWorkspace: vi.fn(),
  workspace: null as unknown,
  serverPolicies: [] as unknown[],
  currentUserAccess: {
    permissions: ['write:workspace'],
  } as {
    permissions: string[]
    durable_permissions?: string[]
    elevation_ids?: string[]
  },
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({
    data: {
      id: 'user-1',
      email: 'admin@example.com',
      role: 'admin',
      is_active: true,
      is_approved: true,
      access: controllerMocks.currentUserAccess,
    },
    isLoading: false,
    isError: false,
    error: null,
  }),
}))

vi.mock('../hooks/useUnsavedChangesWarning', () => ({
  useUnsavedChangesWarning: vi.fn(),
}))

vi.mock('../workspace/useWorkspace', () => ({
  useWorkspace: () => controllerMocks.workspace,
}))

vi.mock('../workspace/workspaceApi', () => ({
  workspaceQueryKeys: {
    root: ['workspace'],
    effective: (userId: string) => ['workspace', 'effective', userId],
    preferences: (userId: string) => ['workspace', 'preferences', userId],
    rolePolicies: ['workspace', 'role-policies'],
  },
  getWorkspaceRolePolicies: controllerMocks.getRolePolicies,
  resetWorkspaceRolePolicy: controllerMocks.resetRolePolicy,
  updateWorkspaceRolePolicy: controllerMocks.updateRolePolicy,
}))

import { useWorkspaceSettingsController, type WorkspaceSettingsController } from './useWorkspaceSettingsController'

let root: Root | null = null
let container: HTMLDivElement | null = null
let queryClient: QueryClient | null = null
let latestController: WorkspaceSettingsController | null = null

beforeEach(() => {
  controllerMocks.currentUserAccess = {
    permissions: ['write:workspace'],
  }
  controllerMocks.serverPolicies = rolePolicies()
  controllerMocks.getRolePolicies.mockImplementation(async () => controllerMocks.serverPolicies)
  controllerMocks.updateRolePolicy.mockImplementation(async (role, payload) => {
    const current = (controllerMocks.serverPolicies as WorkspaceRolePolicyResponse[]).find(
      (policy) => policy.role === role,
    )!
    const updated: WorkspaceRolePolicyResponse = {
      ...current,
      ...payload,
      revision: current.revision + 1,
      updated_at: '2026-01-02T00:00:00Z',
    }
    controllerMocks.serverPolicies = (controllerMocks.serverPolicies as WorkspaceRolePolicyResponse[]).map(
      (policy) => policy.role === role ? updated : policy,
    )
    return updated
  })
  controllerMocks.resetRolePolicy.mockImplementation(async (role) => {
    const current = (controllerMocks.serverPolicies as WorkspaceRolePolicyResponse[]).find(
      (policy) => policy.role === role,
    )!
    return { ...current, revision: current.revision + 1 }
  })
  controllerMocks.savePreferences.mockImplementation(async (payload: WorkspaceUserPreferenceWriteRequest) => ({
    ...preferences(),
    landing_module_id: payload.landing_module_id,
    modules: payload.modules,
    dashboard_panel_ids: payload.dashboard_panel_ids,
    revision: payload.expected_revision + 1,
  }))
  controllerMocks.resetPreferences.mockResolvedValue({ ...preferences(), revision: 0 })
  const initialWorkspace = workspaceValue()
  controllerMocks.workspace = initialWorkspace
  controllerMocks.refreshWorkspace.mockResolvedValue({
    effective: initialWorkspace.effective,
    registry: { modules: [], dashboard_panels: [] },
    preferences: initialWorkspace.preferences,
  })
})

afterEach(() => {
  act(() => root?.unmount())
  queryClient?.clear()
  root = null
  queryClient = null
  latestController = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  for (const mock of [
    controllerMocks.getRolePolicies,
    controllerMocks.resetRolePolicy,
    controllerMocks.updateRolePolicy,
    controllerMocks.savePreferences,
    controllerMocks.resetPreferences,
    controllerMocks.refreshWorkspace,
  ]) mock.mockReset()
})

describe('useWorkspaceSettingsController', () => {
  it('allows policy inspection but not editing from temporary elevation alone', async () => {
    controllerMocks.currentUserAccess = {
      permissions: ['read:workspace', 'write:workspace'],
      durable_permissions: ['read:workspace'],
      elevation_ids: ['elevation-1'],
    }

    renderController()
    await act(async () => {
      await vi.waitFor(() => {
        expect(container?.textContent).toContain('role-ready:personal-ready')
      })
    })

    expect(controller().canReadPolicies).toBe(true)
    expect(controller().canManagePolicies).toBe(false)
    expect(controllerMocks.getRolePolicies).toHaveBeenCalledTimes(1)
    expect(controller().roleDraft).not.toBeNull()
    expect(controller().personalDraft).not.toBeNull()

    const originalLanding = controller().roleDraft?.landingModuleId
    act(() => {
      controller().setRoleDraft((current) => current ? { ...current, landingModuleId: 'primary.alerts' } : current)
    })
    expect(controller().roleDraft?.landingModuleId).toBe(originalLanding)
    expect(controller().roleDirty).toBe(false)
  })

  it('keeps organization policies private without workspace read permission', async () => {
    controllerMocks.currentUserAccess = {
      permissions: ['read:items'],
      durable_permissions: ['read:items'],
    }

    renderController()
    await act(async () => {
      await vi.waitFor(() => {
        expect(container?.textContent).toContain('no-policy:no-role:personal-ready')
      })
    })

    expect(controller().canReadPolicies).toBe(false)
    expect(controller().canManagePolicies).toBe(false)
    expect(controllerMocks.getRolePolicies).not.toHaveBeenCalled()
  })

  it('preserves concurrent dirty drafts across query refreshes and unrelated mutation success', async () => {
    controllerMocks.workspace = workspaceValue(effectiveWorkspace(), {
      ...preferences(),
      modules: [{ module_id: 'primary.dashboard', visible: true, order: 0 }],
    })
    renderController()
    await waitForController()

    act(() => {
      controller().setPersonalDraft((current) => current ? { ...current, landingModuleId: 'primary.alerts' } : current)
      controller().setRoleDraft((current) => {
        if (!current) return current
        const modules = new Map(current.modules)
        const feeds = modules.get('primary.feeds')!
        modules.set('primary.feeds', { ...feeds, visible: !feeds.visible })
        return { ...current, modules }
      })
    })
    const personalLanding = controller().personalDraft?.landingModuleId
    const roleFeedsVisible = controller().roleDraft?.modules.get('primary.feeds')?.visible

    const refreshedPreferences = { ...preferences(), revision: 7 }
    const refreshedEffective = { ...effectiveWorkspace(), preference_revision: 7 }
    controllerMocks.workspace = workspaceValue(refreshedEffective, refreshedPreferences)
    const refreshedPolicies = rolePolicies().map((policy) =>
      policy.role === 'analyst' ? { ...policy, revision: 9 } : policy,
    )
    controllerMocks.serverPolicies = refreshedPolicies
    act(() => {
      queryClient?.setQueryData(['workspace', 'role-policies'], refreshedPolicies)
      rerenderController()
    })

    expect(controller().personalDraft?.landingModuleId).toBe(personalLanding)
    expect(controller().roleDraft?.modules.get('primary.feeds')?.visible).toBe(roleFeedsVisible)
    expect(controller().personalDirty).toBe(true)
    expect(controller().roleDirty).toBe(true)

    controllerMocks.updateRolePolicy.mockResolvedValueOnce({
      ...rolePolicies()[1],
      modules: [...controller().roleDraft!.modules.values()],
      revision: 3,
    })
    await act(async () => {
      controller().updateRolePolicy.mutate()
      await vi.waitFor(() => expect(controllerMocks.updateRolePolicy).toHaveBeenCalledTimes(1))
      await vi.waitFor(() => expect(controller().updateRolePolicy.isPending).toBe(false))
    })

    expect(controller().personalDraft?.landingModuleId).toBe(personalLanding)
    expect(controller().personalDirty).toBe(true)

    act(() => {
      controller().setRoleDraft((current) => current ? { ...current, landingModuleId: 'primary.alerts' } : current)
    })
    const roleLanding = controller().roleDraft?.landingModuleId
    await act(async () => {
      await controller().savePersonal()
    })

    expect(controller().roleDraft?.landingModuleId).toBe(roleLanding)
    expect(controllerMocks.savePreferences).toHaveBeenCalledWith(expect.objectContaining({
      landing_module_id: 'primary.alerts',
      modules: [],
    }))
  })

  it('keeps stale drafts on 409 and supports scoped discard-and-reload recovery', async () => {
    renderController()
    await waitForController()

    act(() => {
      controller().setPersonalDraft((current) => current ? { ...current, landingModuleId: 'primary.alerts' } : current)
      controller().setRoleDraft((current) => current ? { ...current, landingModuleId: 'primary.feeds' } : current)
    })
    controllerMocks.updateRolePolicy.mockRejectedValueOnce(apiConflict('Role policy revision changed.'))
    await act(async () => {
      controller().updateRolePolicy.mutate()
      await vi.waitFor(() => expect(controller().updateRolePolicy.isPending).toBe(false))
    })

    expect(controller().roleRevisionConflict).toBe(true)
    expect(controller().roleError).toContain('Discard this draft and reload')
    expect(controller().roleDraft?.landingModuleId).toBe('primary.feeds')

    const latestPolicies = rolePolicies().map((policy) =>
      policy.role === 'analyst'
        ? { ...policy, landing_module_id: 'primary.reporting', revision: 8 }
        : policy,
    )
    controllerMocks.serverPolicies = latestPolicies
    await act(async () => {
      await controller().discardAndReloadRole()
    })

    expect(controller().roleRevisionConflict).toBe(false)
    expect(controller().roleDraft?.landingModuleId).toBe('primary.reporting')
    expect(controller().personalDraft?.landingModuleId).toBe('primary.alerts')

    controllerMocks.savePreferences.mockRejectedValueOnce(apiConflict('Preference revision changed.'))
    await act(async () => {
      await controller().savePersonal()
    })
    expect(controller().personalRevisionConflict).toBe(true)
    expect(controller().personalDraft?.landingModuleId).toBe('primary.alerts')

    const latestPreferences = { ...preferences(), landing_module_id: 'primary.export', revision: 6 }
    queryClient?.setQueryData(['workspace', 'effective', 'user-1'], {
      ...effectiveWorkspace(),
      preference_revision: 6,
    })
    queryClient?.setQueryData(['workspace', 'preferences', 'user-1'], latestPreferences)
    controllerMocks.refreshWorkspace.mockResolvedValueOnce({
      effective: { ...effectiveWorkspace(), preference_revision: 6 },
      registry: { modules: [], dashboard_panels: [] },
      preferences: latestPreferences,
    })
    await act(async () => {
      await controller().discardAndReloadPersonal()
    })

    expect(controller().personalRevisionConflict).toBe(false)
    expect(controller().personalDraft?.landingModuleId).toBe('primary.export')
  })

  it('closes reset confirmation state and exposes errors when resets fail', async () => {
    renderController()
    await waitForController()
    controllerMocks.resetPreferences.mockRejectedValueOnce(new Error('Personal reset failed.'))
    controllerMocks.resetRolePolicy.mockRejectedValueOnce(new Error('Role reset failed.'))

    act(() => {
      controller().setResetPersonalRequested(true)
      controller().setResetRoleRequested(true)
    })
    await act(async () => {
      await controller().resetPersonal()
      controller().resetRolePolicy.mutate()
      await vi.waitFor(() => expect(controller().resetRolePolicy.isPending).toBe(false))
    })

    expect(controller().resetPersonalRequested).toBe(false)
    expect(controller().personalError).toContain('Personal reset failed')
    expect(controller().resetRoleRequested).toBe(false)
    expect(controller().roleError).toContain('Role reset failed')
  })

  it('turns refresh rejection into visible editor errors', async () => {
    controllerMocks.refreshWorkspace.mockRejectedValueOnce(new Error('Workspace refresh failed.'))
    renderController()
    await waitForController()

    await act(async () => {
      await controller().refresh()
    })

    expect(controller().personalError).toContain('Workspace refresh failed')
    expect(controller().roleError).toContain('Workspace refresh failed')
  })
})

function renderController() {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  rerenderController()
}

function rerenderController() {
  act(() => {
    root?.render(
      <QueryClientProvider client={queryClient!}>
        <ControllerProbe />
      </QueryClientProvider>,
    )
  })
}

function ControllerProbe() {
  latestController = useWorkspaceSettingsController()
  return (
    <p>
      {latestController.hasUnsavedChanges ? 'dirty' : 'clean'}:
      {latestController.selectedPolicy?.revision ?? 'no-policy'}:
      {latestController.roleDraft ? 'role-ready' : 'no-role'}:
      {latestController.personalDraft ? 'personal-ready' : 'no-personal'}
    </p>
  )
}

async function waitForController() {
  await act(async () => {
    await vi.waitFor(() => {
      expect(controllerMocks.getRolePolicies).toHaveBeenCalled()
      expect(controller().selectedPolicy).toBeDefined()
      expect(container?.textContent).toContain('role-ready:personal-ready')
    })
  })
}

function controller(): WorkspaceSettingsController {
  if (!latestController) throw new Error('Controller has not rendered.')
  return latestController
}

function workspaceValue(
  effective = effectiveWorkspace(),
  userPreferences = preferences(),
): WorkspaceContextValue {
  return {
    model: { warnings: [] } as unknown as WorkspaceContextValue['model'],
    userContext: {
      role: 'admin',
      permissions: ['write:workspace'],
      features: {
        ai_enabled: true,
        ai_configured: true,
        ai_summary_enabled: true,
        ai_relevance_enabled: true,
        ai_daily_brief_enabled: true,
      },
      accountEligible: true,
    },
    effective,
    registry: undefined,
    preferences: userPreferences,
    isLoading: false,
    isRefreshing: false,
    isDegraded: false,
    error: null,
    preferenceError: null,
    isSavingPreferences: false,
    isResettingPreferences: false,
    refresh: controllerMocks.refreshWorkspace,
    savePreferences: controllerMocks.savePreferences,
    resetPreferences: controllerMocks.resetPreferences,
  }
}

function preferences(): WorkspaceUserPreferenceResponse {
  return {
    user_id: 'user-1',
    role: 'admin',
    landing_module_id: null,
    modules: [],
    dashboard_panel_ids: null,
    revision: 1,
    updated_by_user_id: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    unknown_module_ids: [],
    unknown_dashboard_panel_ids: [],
    warnings: [],
  }
}

function effectiveWorkspace(): WorkspaceEffectiveResponse {
  return {
    role: 'admin',
    policy_revision: 2,
    preference_revision: 1,
    landing_module_id: 'primary.dashboard',
    dashboard_panel_ids: ['rss'],
    dashboard_panels: [],
    modules: TRUSTED_WORKSPACE_MODULES.filter((module) => module.policyManaged).map((module) => ({
      id: module.id,
      label: module.label,
      route: module.route,
      section: module.section,
      parent_id: module.parentId,
      visible: true,
      optional: module.defaultOptional,
      order: module.defaultOrder,
      mobile_priority: module.defaultMobilePriority,
      mobile_behavior: module.mobileBehavior,
      permission_allowed: true,
      missing_permissions: [],
      feature_available: true,
      policy_visible: true,
      preference_visible: true,
      reasons: [],
    })),
    warnings: [],
  }
}

function rolePolicies(): WorkspaceRolePolicyResponse[] {
  return (['admin', 'analyst', 'viewer'] as const).map((role) => ({
    role,
    landing_module_id: 'primary.dashboard',
    revision: 2,
    modules: TRUSTED_WORKSPACE_MODULES.filter((module) => module.policyManaged).map((module) => ({
      module_id: module.id,
      visible: module.defaultVisibleRoles.includes(role),
      optional: module.defaultOptional,
      order: module.defaultOrder,
      mobile_priority: module.defaultMobilePriority,
    })),
    dashboard_panel_ids: ['rss'],
    updated_by_user_id: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    unknown_module_ids: [],
    unknown_dashboard_panel_ids: [],
    warnings: [],
  }))
}

function apiConflict(message: string): Error {
  return Object.assign(new Error(message), {
    name: 'ApiError',
    status: 409,
    path: '/workspace',
    retryable: false,
  })
}
