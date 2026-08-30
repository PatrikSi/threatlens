import { describe, expect, it } from 'vitest'

import type {
  WorkspaceEffectiveResponse,
  WorkspaceRolePolicyResponse,
  WorkspaceUserPreferenceResponse,
} from '../types/workspace'
import { TRUSTED_WORKSPACE_MODULES } from '../workspace/moduleRegistry'
import {
  createPersonalWorkspaceDraft,
  createRolePolicyDraft,
  movePersonalModule,
  personalLandingOptions,
  rolePolicyPreview,
  rolePolicyDraftValidation,
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

    expect(moved.modules.get('primary.feeds')?.order).toBe(0)
    expect(moved.modules.get('primary.alerts')?.order).toBe(10)
    expect(moved.modules.get('settings.tokens')?.order).toBe(5)

    const movedIntegration = movePersonalModule(moved, 'settings.integrations.smtp', -1)
    expect(movedIntegration.modules.get('settings.integrations.smtp')?.order).toBe(0)
    expect(movedIntegration.modules.get('settings.integrations.webhooks')?.order).toBe(10)
    expect(movedIntegration.modules.get('settings.tokens')?.order).toBe(5)
  })

  it('keeps trusted navigation containers outside role-policy visibility edits', () => {
    const draft = createRolePolicyDraft(rolePolicy())
    const unchanged = updateRolePolicyModule(draft, 'primary.settings', { visible: false })
    unchanged.landingModuleId = 'settings.account'

    expect(unchanged.modules.has('primary.settings')).toBe(false)
    expect(rolePolicyDraftValidation(unchanged)).toBe('')
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

  it('offers settings children whose trusted local containers are not server-managed', () => {
    const effective = effectiveWorkspace()
    const draft = createPersonalWorkspaceDraft(effective, {
      ...preferences(),
      modules: [],
    })

    const options = personalLandingOptions(effective, draft).map((module) => module.id)

    expect(options).toContain('settings.account')
    expect(options).toContain('settings.integrations.webhooks')
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
    modules: TRUSTED_WORKSPACE_MODULES.filter((module) => module.policyManaged).map((module) => ({
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
