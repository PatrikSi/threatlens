import { describe, expect, it } from 'vitest'

import type {
  WorkspaceEffectiveResponse,
  WorkspaceRolePolicyResponse,
  WorkspaceUserPreferenceResponse,
} from '../types/workspace'
import { TRUSTED_WORKSPACE_MODULES } from '../workspace/moduleRegistry'
import { resolveWorkspaceModel } from '../workspace/workspaceModel'
import {
  buildPersonalPreferencePayload,
  buildRolePolicyPayload,
  createPersonalWorkspaceDraft,
  createRolePolicyDraft,
  movePersonalModule,
  moveRolePolicyModule,
  personalLandingOptions,
  personalNavigationPreview,
  reorderPersonalModule,
  reorderRolePolicyModule,
  rolePolicyPreview,
  rolePolicyDraftValidation,
  updatePersonalModule,
  updateRolePolicyModule,
} from './workspaceSettingsModel'

describe('workspace settings model', () => {
  it('moves personal modules only among navigation siblings', () => {
    const draft = {
      landingModuleId: null,
      modules: new Map([
        ['primary.alerts' as const, { visible: true, order: 10 }],
        ['primary.feeds' as const, { visible: true, order: 20 }],
        ['settings.tokens' as const, { visible: true, order: 5 }],
        ['settings.integrations.webhooks' as const, { visible: true, order: 10 }],
        ['settings.integrations.smtp' as const, { visible: true, order: 20 }],
      ]),
      inheritDashboardPanels: true,
      dashboardPanelIds: ['rss'],
    }
    const moved = movePersonalModule(draft, 'primary.feeds', -1)

    expect(moved.modules.get('primary.feeds')?.order).toBe(10)
    expect(moved.modules.get('primary.alerts')?.order).toBe(20)
    expect(moved.modules.get('settings.tokens')?.order).toBe(5)

    const movedIntegration = movePersonalModule(moved, 'settings.integrations.smtp', -1)
    expect(movedIntegration.modules.get('settings.integrations.smtp')?.order).toBe(10)
    expect(movedIntegration.modules.get('settings.integrations.webhooks')?.order).toBe(20)
    expect(movedIntegration.modules.get('settings.tokens')?.order).toBe(5)
  })

  it('drops personal modules into a sibling position without crossing navigation groups', () => {
    const draft = {
      landingModuleId: null,
      modules: new Map([
        ['primary.alerts' as const, { visible: true, order: 10 }],
        ['primary.feeds' as const, { visible: true, order: 20 }],
        ['settings.tokens' as const, { visible: true, order: 5 }],
        ['settings.users' as const, { visible: true, order: 10 }],
        ['settings.ai' as const, { visible: true, order: 20 }],
      ]),
      inheritDashboardPanels: true,
      dashboardPanelIds: ['rss'],
    }

    const moved = reorderPersonalModule(draft, 'primary.alerts', 'primary.feeds')
    expect(moved.modules.get('primary.feeds')?.order).toBe(10)
    expect(moved.modules.get('primary.alerts')?.order).toBe(20)

    const crossGroup = reorderPersonalModule(moved, 'primary.alerts', 'settings.tokens')
    expect(crossGroup).toBe(moved)
    const crossSettingsGroup = reorderPersonalModule(moved, 'settings.users', 'settings.ai')
    expect(crossSettingsGroup).toBe(moved)

    const tied = {
      ...draft,
      modules: new Map([
        ['primary.alerts' as const, { visible: true, order: 10 }],
        ['primary.feeds' as const, { visible: true, order: 10 }],
      ]),
    }
    const untied = reorderPersonalModule(tied, 'primary.alerts', 'primary.feeds')
    expect(untied.modules.get('primary.feeds')?.order).toBe(10)
    expect(untied.modules.get('primary.alerts')?.order).toBe(20)
  })

  it('reorders role desktop positions without changing mobile priority or another section', () => {
    const draft = createRolePolicyDraft(rolePolicy())
    const feedsMobilePriority = draft.modules.get('primary.feeds')?.mobile_priority
    const tokensOrder = draft.modules.get('settings.tokens')?.order

    const moved = reorderRolePolicyModule(draft, 'primary.feeds', 'primary.dashboard')
    expect(moved.modules.get('primary.feeds')?.order).toBe(0)
    expect(moved.modules.get('primary.dashboard')?.order).toBe(10)
    expect(moved.modules.get('primary.feeds')?.mobile_priority).toBe(feedsMobilePriority)
    expect(moved.modules.get('settings.tokens')?.order).toBe(tokensOrder)

    const movedAgain = moveRolePolicyModule(moved, 'primary.feeds', 1)
    expect(movedAgain.modules.get('primary.dashboard')?.order).toBe(0)
    expect(movedAgain.modules.get('primary.feeds')?.order).toBe(10)

    const crossGroup = reorderRolePolicyModule(movedAgain, 'primary.feeds', 'settings.tokens')
    expect(crossGroup).toBe(movedAgain)
    const crossSettingsGroup = reorderRolePolicyModule(
      movedAgain,
      'settings.users',
      'settings.ai',
    )
    expect(crossSettingsGroup).toBe(movedAgain)
  })

  it('keeps trusted navigation containers outside role-policy visibility edits', () => {
    const policy = rolePolicy()
    const draft = createRolePolicyDraft(policy)
    const unchanged = updateRolePolicyModule(draft, 'primary.settings', { visible: false })
    const payload = buildRolePolicyPayload(policy, unchanged)

    expect(unchanged.modules.has('primary.settings')).toBe(false)
    expect(unchanged.modules.has('settings.integrations')).toBe(false)
    expect(payload.modules.find((module) => module.module_id === 'primary.settings')).toEqual(
      policy.modules.find((module) => module.module_id === 'primary.settings'),
    )
    expect(payload.modules.find((module) => module.module_id === 'settings.integrations')).toEqual(
      policy.modules.find((module) => module.module_id === 'settings.integrations'),
    )
    expect(rolePolicyDraftValidation(unchanged)).toBe('')
  })

  it('preserves Settings sidebar defaults when a top-navigation role default changes', () => {
    const policy = rolePolicy()
    const originalSettingsModules = policy.modules.filter((module) =>
      module.module_id.startsWith('settings.'),
    )
    const draft = updateRolePolicyModule(
      createRolePolicyDraft(policy),
      'primary.feeds',
      { visible: false, order: 5 },
    )

    const payload = buildRolePolicyPayload(policy, draft)

    expect(payload.modules.find((module) => module.module_id === 'primary.feeds')).toMatchObject({
      visible: false,
      order: 5,
    })
    expect(
      payload.modules.filter((module) => module.module_id.startsWith('settings.')),
    ).toEqual(originalSettingsModules)
  })

  it('preserves an unchanged future landing ID but never offers a local-only control as policy data', () => {
    const futureLanding = createRolePolicyDraft(rolePolicy())
    futureLanding.landingModuleId = 'future.timeline'

    expect(rolePolicyDraftValidation(futureLanding, 'future.timeline')).toBe('')
    expect(rolePolicyDraftValidation(futureLanding, 'another.future.module')).toContain(
      'Choose a landing module',
    )

    const localControlLanding = createRolePolicyDraft(rolePolicy())
    localControlLanding.landingModuleId = 'settings.workspace'
    expect(rolePolicyDraftValidation(localControlLanding)).toContain('Choose a landing module')
  })

  it('previews mobile priority independently while keeping primary mobile modules first', () => {
    let draft = createRolePolicyDraft(rolePolicy())
    draft = updateRolePolicyModule(draft, 'primary.dashboard', { mobile_priority: 100 })
    draft = updateRolePolicyModule(draft, 'primary.feeds', { mobile_priority: 0 })
    draft = updateRolePolicyModule(draft, 'primary.stats', { mobile_priority: 0 })
    const preview = rolePolicyPreview(draft)

    expect(preview.primary[0].id).toBe('primary.dashboard')
    expect(preview.mobile[0].id).toBe('primary.feeds')
    expect(preview.mobile.findIndex((module) => module.id === 'primary.stats')).toBeGreaterThan(
      preview.mobile.findIndex((module) => module.id === 'primary.dashboard'),
    )
  })

  it('previews settings in visible hierarchy groups and omits empty containers', () => {
    let draft = createRolePolicyDraft(rolePolicy())
    draft = updateRolePolicyModule(draft, 'settings.integrations.webhooks', {
      visible: false,
    })
    draft = updateRolePolicyModule(draft, 'settings.integrations.smtp', {
      visible: false,
    })
    draft = updateRolePolicyModule(draft, 'settings.operations', { order: 0 })
    const preview = rolePolicyPreview(draft)

    expect(preview.settings.map((module) => module.id)).not.toContain('settings.integrations')
    expect(preview.settings.findIndex((module) => module.id === 'settings.users')).toBeLessThan(
      preview.settings.findIndex((module) => module.id === 'settings.ai'),
    )
    expect(preview.settings.findIndex((module) => module.id === 'settings.ai')).toBeLessThan(
      preview.settings.findIndex((module) => module.id === 'settings.operations'),
    )
  })

  it('hydrates optional visibility, order, landing, and inherited dashboard state', () => {
    const effective = effectiveWorkspace()
    const preferences: WorkspaceUserPreferenceResponse = {
      user_id: 'user-1', role: 'analyst', landing_module_id: 'primary.feeds', revision: 4,
      modules: [{ module_id: 'primary.feeds', visible: false, order: 2 }],
      dashboard_panel_ids: null, updated_by_user_id: 'user-1',
      created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-02T00:00:00Z',
      unknown_module_ids: [], unknown_dashboard_panel_ids: [], warnings: [],
    }
    const draft = createPersonalWorkspaceDraft(effective, preferences)

    expect(draft.modules.get('primary.feeds')).toEqual({ visible: false, order: 2 })
    expect(draft.landingModuleId).toBe('primary.feeds')
    expect(draft.inheritDashboardPanels).toBe(true)
    expect(draft.dashboardPanelIds).toEqual(['rss'])
  })

  it('preserves Settings sidebar preferences when a personal top-navigation item changes', () => {
    const effective = effectiveWorkspace()
    const originalPreferences: WorkspaceUserPreferenceResponse = {
      ...preferences(),
      modules: [
        { module_id: 'primary.feeds', visible: true, order: 30 },
        { module_id: 'settings.tokens', visible: false, order: 91 },
        { module_id: 'settings.integrations.webhooks', visible: true, order: 92 },
      ],
    }
    const draft = updatePersonalModule(
      createPersonalWorkspaceDraft(effective, originalPreferences),
      'primary.feeds',
      { visible: false, order: 5 },
    )

    const payload = buildPersonalPreferencePayload(originalPreferences, draft)

    expect(payload.modules).toContainEqual({
      module_id: 'primary.feeds',
      visible: false,
      order: 5,
    })
    expect(payload.modules).toContainEqual({
      module_id: 'settings.tokens',
      visible: false,
      order: 91,
    })
    expect(payload.modules).toContainEqual({
      module_id: 'settings.integrations.webhooks',
      visible: true,
      order: 92,
    })
  })

  it('offers settings children whose trusted local containers are not server-managed', () => {
    const effective = effectiveWorkspace()
    const draft = createPersonalWorkspaceDraft(effective, {
      ...preferences(),
      modules: [],
    })

    const options = personalLandingOptions(effective, draft).map((module) => module.id)

    expect(options).toContain('settings.account')
    expect(options).toContain('settings.integrations.webhooks')
    expect(options).not.toContain('primary.settings')
    expect(options).not.toContain('settings.integrations')
    expect(
      personalLandingOptions(effective, draft).every((module) => module.landingEligible),
    ).toBe(true)
  })

  it('previews only top-navigation destinations and identifies fixed structure', () => {
    const effective = effectiveWorkspace()
    const draft = createPersonalWorkspaceDraft(effective, preferences())
    const model = resolveWorkspaceModel(effective, undefined, {
      role: 'admin',
      permissions: ['*:*'],
      features: {
        ai_enabled: true,
        ai_configured: true,
        ai_summary_enabled: true,
        ai_relevance_enabled: true,
        ai_daily_brief_enabled: true,
        ai_reporting_enabled: true,
      },
      accountEligible: true,
    })
    const preview = personalNavigationPreview(model.modules, draft)
    const fixedIds = preview.filter((item) => item.fixed).map((item) => item.module.id)

    expect(fixedIds).toEqual(['primary.dashboard', 'primary.settings'])
    expect(preview.map((item) => item.module.id)).toEqual([
      'primary.dashboard',
      'primary.alerts',
      'primary.investigations',
      'primary.feeds',
      'primary.stats',
      'primary.export',
      'primary.reporting',
      'primary.settings',
    ])
    expect(preview.every((item) => item.module.section === 'primary')).toBe(true)
  })

  it('rejects a role policy with no first-use dashboard panels', () => {
    const draft = createRolePolicyDraft(rolePolicy())
    draft.dashboardPanelIds = []

    expect(rolePolicyDraftValidation(draft)).toContain('at least one first-use dashboard panel')
  })
})

function preferences(): WorkspaceUserPreferenceResponse {
  return {
    user_id: 'user-1', role: 'analyst', landing_module_id: null, revision: 4,
    modules: [], dashboard_panel_ids: null, updated_by_user_id: null,
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    unknown_module_ids: [], unknown_dashboard_panel_ids: [], warnings: [],
  }
}

function rolePolicy(): WorkspaceRolePolicyResponse {
  return {
    role: 'analyst', landing_module_id: 'primary.dashboard', revision: 2,
    modules: TRUSTED_WORKSPACE_MODULES.filter(
      (module) => module.policyManaged || module.isContainer,
    ).map((module) => ({
      module_id: module.id, visible: true, optional: module.defaultOptional,
      order: module.defaultOrder, mobile_priority: module.defaultMobilePriority,
    })),
    dashboard_panel_ids: ['rss'], updated_by_user_id: null,
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    unknown_module_ids: [], unknown_dashboard_panel_ids: [], warnings: [],
  }
}

function effectiveWorkspace(): WorkspaceEffectiveResponse {
  return {
    role: 'analyst', policy_revision: 2, preference_revision: 4,
    landing_module_id: 'primary.dashboard', dashboard_panel_ids: ['rss'],
    dashboard_panels: [{
      id: 'rss', visible: true, permission_allowed: true, feature_available: true,
      missing_permissions: [], reasons: [],
    }],
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
