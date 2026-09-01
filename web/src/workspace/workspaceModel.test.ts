import { describe, expect, it } from 'vitest'

import type { AppFeatures } from '../types/identity'
import type {
  WorkspaceEffectiveModuleResponse,
  WorkspaceEffectiveResponse,
  WorkspaceModuleDefinitionResponse,
  WorkspaceRegistryResponse,
  WorkspaceRolePolicyResponse,
  WorkspaceUserPreferenceResponse,
} from '../types/workspace'
import {
  TRUSTED_DASHBOARD_PANELS,
  TRUSTED_WORKSPACE_MODULES,
  isTopNavigationModule,
  type TrustedWorkspaceModuleId,
} from './moduleRegistry'
import {
  effectiveWorkspaceForClientControls,
  hasRequiredPermissions,
  preferenceWriteRequest,
  resolveWorkspaceModel,
  rolePolicyWriteRequest,
  workspaceWarningMessage,
} from './workspaceModel'

const FEATURES: AppFeatures = {
  ai_enabled: true,
  ai_configured: true,
  ai_summary_enabled: true,
  ai_relevance_enabled: true,
  ai_daily_brief_enabled: true,
  ai_reporting_enabled: true,
}

describe('workspace model', () => {
  it('matches backend permission implication and wildcard semantics', () => {
    expect(hasRequiredPermissions(['write:items'], ['read:items'])).toBe(true)
    expect(hasRequiredPermissions(['write:*'], ['read:items', 'write:feeds'])).toBe(true)
    expect(hasRequiredPermissions(['admin:*'], ['read:items', 'write:workspace'])).toBe(true)
    expect(hasRequiredPermissions(['read:*'], ['read:items', 'write:feeds'])).toBe(false)
  })

  it('keeps the trusted registry local, unique, and rooted in application routes', () => {
    expect(new Set(TRUSTED_WORKSPACE_MODULES.map((module) => module.id)).size).toBe(
      TRUSTED_WORKSPACE_MODULES.length,
    )
    expect(new Set(TRUSTED_DASHBOARD_PANELS.map((panel) => panel.id)).size).toBe(
      TRUSTED_DASHBOARD_PANELS.length,
    )
    expect(TRUSTED_WORKSPACE_MODULES.every((module) => module.route.startsWith('/'))).toBe(true)
    expect(TRUSTED_WORKSPACE_MODULES.every((module) => !module.route.startsWith('//'))).toBe(true)

    const moduleIndexes = new Map(
      TRUSTED_WORKSPACE_MODULES.map((module, index) => [module.id, index]),
    )
    for (const module of TRUSTED_WORKSPACE_MODULES) {
      if (module.parentId) {
        expect(moduleIndexes.get(module.parentId)!).toBeLessThan(moduleIndexes.get(module.id)!)
      }
    }
  })

  it('defines the policy-managed top navbar independently from Settings descendants', () => {
    expect(
      TRUSTED_WORKSPACE_MODULES
        .filter((module) => isTopNavigationModule(module) && module.policyManaged)
        .map((module) => module.id),
    ).toEqual([
      'primary.dashboard',
      'primary.alerts',
      'primary.investigations',
      'primary.feeds',
      'primary.stats',
      'primary.export',
      'primary.reporting',
    ])
    expect(
      TRUSTED_WORKSPACE_MODULES
        .filter((module) => isTopNavigationModule(module) && !module.policyManaged)
        .map((module) => module.id),
    ).toEqual(['primary.settings'])
    expect(
      TRUSTED_WORKSPACE_MODULES
        .filter((module) => module.section === 'settings')
        .every((module) => !isTopNavigationModule(module)),
    ).toBe(true)
  })

  it('removes local-control containers from editable effective preferences while retaining future entries', () => {
    const effective = effectiveWorkspace()
    const integrations = TRUSTED_WORKSPACE_MODULES.find((module) => module.id === 'settings.integrations')!
    effective.modules.push({
      id: integrations.id,
      label: integrations.label,
      route: integrations.route,
      section: integrations.section,
      parent_id: integrations.parentId,
      visible: true,
      optional: true,
      order: integrations.defaultOrder,
      mobile_priority: integrations.defaultMobilePriority,
      mobile_behavior: integrations.mobileBehavior,
      permission_allowed: true,
      missing_permissions: [],
      feature_available: true,
      policy_visible: true,
      preference_visible: true,
      reasons: [],
    })
    effective.modules.push({
      ...effective.modules[0],
      id: 'future.timeline',
      label: 'Timeline',
      route: '/timeline',
    })

    const normalized = effectiveWorkspaceForClientControls(effective)!
    expect(normalized.modules.map((module) => module.id)).not.toContain('settings.integrations')
    expect(normalized.modules.map((module) => module.id)).toContain('future.timeline')
    expect(effective.modules.map((module) => module.id)).toContain('settings.integrations')
  })

  it('fails closed across authorization, feature, policy, and preference constraints', () => {
    const effective = effectiveWorkspace({
      'primary.stats': { policy_visible: false, visible: false, reasons: ['policy_hidden'] },
      'primary.export': { preference_visible: false, visible: false, reasons: ['preference_hidden'] },
    })
    const model = resolveWorkspaceModel(effective, serverRegistry(), {
      role: 'admin',
      permissions: ['read:alerts', 'read:stats', 'read:reports', 'read:feeds', 'read:investigations', 'read:workspace'],
      features: { ...FEATURES, ai_enabled: false },
      accountEligible: true,
    })
    const modules = new Map(model.modules.map((module) => [module.id, module]))

    expect(modules.get('primary.alerts')).toMatchObject({ visible: false, permissionAllowed: false })
    expect(modules.get('primary.alerts')?.reasons).toContain('permission_missing')
    expect(modules.get('settings.ai')).toMatchObject({ visible: false, featureAvailable: false })
    expect(modules.get('primary.stats')).toMatchObject({ visible: false, policyVisible: false })
    expect(modules.get('primary.export')).toMatchObject({ visible: false, preferenceVisible: false })
    expect(model.primaryNavigation.map((module) => module.id)).not.toContain('primary.alerts')
  })

  it('uses trusted fallbacks without exposing role-inappropriate modules when the API is degraded', () => {
    const model = resolveWorkspaceModel(undefined, undefined, {
      role: 'analyst',
      features: FEATURES,
      accountEligible: true,
    })

    expect(model.primaryNavigation.map((module) => module.label)).toEqual([
      'Dashboard',
      'Alerts',
      'Investigations',
      'Feeds',
      'Stats',
      'Export',
      'Reporting',
      'Settings',
    ])
    expect(model.settingsNavigation.map((module) => module.id)).toContain('settings.workspace')
    expect(model.settingsNavigation.map((module) => module.id)).not.toContain('settings.identity')
    expect(model.settingsNavigation.map((module) => module.id)).not.toContain('settings.integrations.smtp')
  })

  it('uses canonical permissions for delegable authoritative server modules', () => {
    const effective = effectiveWorkspace()
    effective.role = 'analyst'
    const model = resolveWorkspaceModel(effective, serverRegistry(), {
      role: 'analyst', permissions: ['*:*'], features: FEATURES, accountEligible: true,
    })

    for (const moduleId of [
      'settings.tagging',
      'settings.users',
      'settings.audit',
      'settings.operations',
      'settings.integrations.smtp',
    ]) {
      const module = model.modules.find((entry) => entry.id === moduleId)
      expect(module).toMatchObject({ visible: true, permissionAllowed: true })
      expect(module?.reasons).not.toContain('route_role_required')
    }
  })

  it('keeps sealed administration modules out of non-Administrator navigation', () => {
    const effective = effectiveWorkspace()
    effective.role = 'viewer'
    const model = resolveWorkspaceModel(effective, serverRegistry(), {
      role: 'viewer', permissions: ['*:*'], features: FEATURES, accountEligible: true,
    })

    for (const moduleId of ['settings.ai', 'settings.identity']) {
      const module = model.modules.find((entry) => entry.id === moduleId)
      expect(module).toMatchObject({
        visible: false,
        permissionAllowed: true,
        roleAllowed: false,
      })
      expect(module?.reasons).toContain('route_role_required')
      expect(model.settingsNavigation.map((entry) => entry.id)).not.toContain(moduleId)
    }
  })

  it('lets each integration child use its own permission without inheriting the webhook permission', () => {
    const effective = effectiveWorkspace({
      'settings.integrations.webhooks': {
        visible: false,
        permission_allowed: false,
        reasons: ['permission_missing'],
      },
      'settings.integrations.smtp': {
        visible: false,
        permission_allowed: true,
        reasons: ['parent_hidden'],
      },
    })
    const model = resolveWorkspaceModel(effective, serverRegistry(), {
      role: 'admin', permissions: ['read:integrations'], features: FEATURES, accountEligible: true,
    })

    expect(model.settingsNavigation.map((module) => module.id)).toContain('settings.integrations')
    expect(model.settingsNavigation.map((module) => module.id)).toContain('settings.integrations.smtp')
    expect(model.settingsNavigation.map((module) => module.id)).not.toContain('settings.integrations.webhooks')
    expect(model.modules.find((module) => module.id === 'settings.integrations.smtp')?.reasons)
      .not.toContain('parent_hidden')
  })

  it('does not expose an empty Integrations container', () => {
    const model = resolveWorkspaceModel(effectiveWorkspace(), serverRegistry(), {
      role: 'admin', permissions: [], features: FEATURES, accountEligible: true,
    })

    expect(model.settingsNavigation.map((module) => module.id)).not.toContain('settings.integrations')
    expect(model.modules.find((module) => module.id === 'settings.integrations')?.reasons)
      .toContain('empty_container')
  })

  it('never turns unknown server IDs or routes into links and reports version skew', () => {
    const registry = serverRegistry()
    registry.modules.find((module) => module.id === 'primary.dashboard')!.route = 'https://attacker.example/'
    registry.modules.push({
      id: 'future.timeline',
      label: 'Timeline',
      route: 'javascript:alert(1)',
      section: 'primary',
      parent_id: null,
      required_permission: null,
      required_permissions: [],
      feature_flag: null,
      default_optional: true,
      default_order: 5,
      default_mobile_priority: 5,
      mobile_behavior: 'primary',
    })
    const effective = effectiveWorkspace()
    effective.modules[0].route = 'https://attacker.example/dashboard'
    effective.modules.push({
      id: 'future.timeline', label: 'Timeline', route: 'javascript:alert(1)', section: 'primary', parent_id: null,
      visible: true, optional: true, order: 5, mobile_priority: 5, mobile_behavior: 'primary',
      permission_allowed: true, missing_permissions: [], feature_available: true, policy_visible: true,
      preference_visible: true, reasons: [],
    })
    effective.dashboard_panel_ids.push('future-map')
    const model = resolveWorkspaceModel(effective, registry, {
      role: 'admin', permissions: ['*:*'], features: FEATURES, accountEligible: true,
    })

    expect(model.primaryNavigation.find((module) => module.id === 'primary.dashboard')?.route).toBe('/')
    expect(model.primaryNavigation.some((module) => module.route.includes('attacker'))).toBe(false)
    expect(model.primaryNavigation.some((module) => module.route.startsWith('javascript:'))).toBe(false)
    expect(model.unknownModuleIds).toEqual(['future.timeline'])
    expect(model.unknownDashboardPanelIds).toEqual(['future-map'])
    expect(model.warnings).toContain('server_module_route_mismatch:primary.dashboard')
    expect(model.warnings).toContain('untrusted_server_module:future.timeline')
  })

  it('falls back to the first visible trusted primary module when landing is unavailable', () => {
    const effective = effectiveWorkspace({
      'primary.dashboard': { visible: false, policy_visible: false, reasons: ['policy_hidden'] },
    })
    effective.landing_module_id = 'future.timeline'
    const model = resolveWorkspaceModel(effective, serverRegistry(), {
      role: 'admin', permissions: ['*:*'], features: FEATURES, accountEligible: true,
    })

    expect(model.landingModuleId).toBe('primary.alerts')
    expect(model.landingPath).toBe('/alerts')
    expect(model.warnings).toContain('landing_module_unavailable:future.timeline')
  })

  it('never resolves Settings or Integrations containers as landing destinations', () => {
    const effective = effectiveWorkspace()
    effective.landing_module_id = 'primary.settings'
    const model = resolveWorkspaceModel(effective, serverRegistry(), {
      role: 'admin', permissions: ['*:*'], features: FEATURES, accountEligible: true,
    })

    expect(model.landingModuleId).toBe('primary.dashboard')
    expect(model.landingPath).toBe('/')
    expect(model.warnings).toContain('landing_module_unavailable:primary.settings')
    expect(TRUSTED_WORKSPACE_MODULES.find((module) => module.id === 'primary.settings'))
      .toMatchObject({ isContainer: true, landingEligible: false, policyManaged: false })
    expect(TRUSTED_WORKSPACE_MODULES.find((module) => module.id === 'settings.integrations'))
      .toMatchObject({ isContainer: true, landingEligible: false, policyManaged: false })
  })

  it('keeps one stable settings order across breakpoints', () => {
    const effective = effectiveWorkspace({
      'settings.ai': { order: 100, mobile_priority: 1 },
      'settings.tagging': { order: 1, mobile_priority: 100 },
    })
    const model = resolveWorkspaceModel(effective, serverRegistry(), {
      role: 'admin', permissions: ['*:*'], features: FEATURES, accountEligible: true,
    })

    const desktop = model.settingsNavigation.map((module) => module.id)
    expect(desktop.indexOf('settings.tagging')).toBeLessThan(desktop.indexOf('settings.ai'))
    expect('mobileSettingsNavigation' in model).toBe(false)
  })

  it('keeps personal desktop main order separate from organization mobile priority', () => {
    const effective = effectiveWorkspace({
      'primary.dashboard': { order: 30, mobile_priority: 0 },
      'primary.alerts': { order: 0, mobile_priority: 30 },
    })
    const model = resolveWorkspaceModel(effective, serverRegistry(), {
      role: 'admin', permissions: ['*:*'], features: FEATURES, accountEligible: true,
    })

    const desktop = model.primaryNavigation.map((module) => module.id)
    const mobile = model.mobileNavigation.map((module) => module.id)
    expect(desktop.indexOf('primary.alerts')).toBeLessThan(desktop.indexOf('primary.dashboard'))
    expect(mobile.indexOf('primary.dashboard')).toBeLessThan(mobile.indexOf('primary.alerts'))
  })

  it('describes a missing registry entry without claiming an effective trusted entry is hidden', () => {
    expect(workspaceWarningMessage('server_module_missing:primary.alerts')).toContain(
      'uses the trusted local contract when an effective server entry is available',
    )
    expect(workspaceWarningMessage('server_module_missing:primary.alerts')).not.toContain(
      'hidden until the versions match',
    )
  })

  it('does not apply an effective snapshot issued for a different role', () => {
    const effective = effectiveWorkspace()
    effective.role = 'admin'
    const model = resolveWorkspaceModel(effective, serverRegistry(), {
      role: 'analyst', permissions: ['*:*'], features: FEATURES, accountEligible: true,
    })

    expect(model.modules.find((module) => module.id === 'primary.dashboard')?.resolutionSource).toBe(
      'trusted-fallback',
    )
    expect(model.settingsNavigation.map((module) => module.id)).not.toContain('settings.identity')
    expect(model.warnings).toContain('workspace_role_mismatch:admin:analyst')
  })

  it('hides an ambiguous duplicate effective module instead of choosing one response entry', () => {
    const effective = effectiveWorkspace()
    effective.modules.push({ ...effective.modules[0], visible: false, reasons: ['policy_hidden'] })
    const model = resolveWorkspaceModel(effective, serverRegistry(), {
      role: 'admin', permissions: ['*:*'], features: FEATURES, accountEligible: true,
    })

    expect(model.primaryNavigation.map((module) => module.id)).not.toContain('primary.dashboard')
    expect(model.landingPath).toBe('/alerts')
    expect(model.warnings).toContain('duplicate_effective_module:primary.dashboard')
  })

  it('round-trips future server fields while changing only trusted role policy entries', () => {
    const policy = rolePolicy()
    policy.modules.push({
      module_id: 'future.timeline', visible: true, optional: true, order: 500, mobile_priority: 500,
    })
    policy.dashboard_panel_ids.push('future-map')
    const dashboard = policy.modules.find((module) => module.module_id === 'primary.dashboard')!
    const draft = new Map([[dashboard.module_id as TrustedWorkspaceModuleId, { ...dashboard, order: 9 }]])
    const payload = rolePolicyWriteRequest(policy, draft, policy.landing_module_id, ['rss'])

    expect(payload.modules.find((module) => module.module_id === 'primary.dashboard')?.order).toBe(9)
    expect(payload.modules.find((module) => module.module_id === 'future.timeline')).toEqual(
      policy.modules.find((module) => module.module_id === 'future.timeline'),
    )
    expect(payload.dashboard_panel_ids).toEqual(['rss', 'future-map'])
  })

  it('round-trips future personal preferences without trusting them as modules', () => {
    const preferences: WorkspaceUserPreferenceResponse = {
      user_id: 'user-1', role: 'analyst', landing_module_id: null, revision: 3,
      modules: [
        { module_id: 'primary.stats', visible: false, order: 40 },
        { module_id: 'future.timeline', visible: true, order: 500 },
      ],
      dashboard_panel_ids: ['rss', 'future-map'], updated_by_user_id: 'user-1',
      created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
      unknown_module_ids: [], unknown_dashboard_panel_ids: [], warnings: [],
    }
    const payload = preferenceWriteRequest(
      preferences,
      new Map([['primary.stats', { visible: true, order: 2 }]]),
      null,
      ['rss'],
    )

    expect(payload.modules).toContainEqual({ module_id: 'future.timeline', visible: true, order: 500 })
    expect(payload.modules).toContainEqual({ module_id: 'primary.stats', visible: true, order: 2 })
    expect(payload.dashboard_panel_ids).toEqual(['rss', 'future-map'])
  })
})

function effectiveWorkspace(
  overrides: Partial<Record<TrustedWorkspaceModuleId, Partial<WorkspaceEffectiveModuleResponse>>> = {},
): WorkspaceEffectiveResponse {
  return {
    role: 'admin',
    policy_revision: 1,
    preference_revision: 0,
    landing_module_id: 'primary.dashboard',
    dashboard_panel_ids: ['rss'],
    dashboard_panels: [{
      id: 'rss', visible: true, permission_allowed: true, feature_available: true,
      missing_permissions: [], reasons: [],
    }],
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
      ...overrides[module.id],
    })),
    warnings: [],
  }
}

function serverRegistry(): WorkspaceRegistryResponse {
  return {
    modules: TRUSTED_WORKSPACE_MODULES.filter((module) => module.policyManaged).map<WorkspaceModuleDefinitionResponse>((module) => ({
      id: module.id,
      label: module.label,
      route: module.route,
      section: module.section,
      parent_id: module.parentId,
      required_permission: module.requiredPermissions[0] ?? null,
      required_permissions: [...module.requiredPermissions],
      feature_flag: module.serverFeatureFlag,
      default_optional: module.defaultOptional,
      default_order: module.defaultOrder,
      default_mobile_priority: module.defaultMobilePriority,
      mobile_behavior: module.mobileBehavior,
    })),
    dashboard_panels: TRUSTED_DASHBOARD_PANELS.map((panel) => ({
      id: panel.id,
      label: panel.label,
      required_permission: panel.requiredPermissions[0] ?? null,
      required_permissions: [...panel.requiredPermissions],
      feature_flag: panel.serverFeatureFlag,
    })),
  }
}

function rolePolicy(): WorkspaceRolePolicyResponse {
  return {
    role: 'analyst', landing_module_id: 'primary.dashboard', revision: 2,
    modules: TRUSTED_WORKSPACE_MODULES.filter((module) => module.policyManaged).map((module) => ({
      module_id: module.id,
      visible: module.defaultVisibleRoles.includes('analyst'),
      optional: module.defaultOptional,
      order: module.defaultOrder,
      mobile_priority: module.defaultMobilePriority,
    })),
    dashboard_panel_ids: ['rss'], updated_by_user_id: null,
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    unknown_module_ids: [], unknown_dashboard_panel_ids: [], warnings: [],
  }
}
