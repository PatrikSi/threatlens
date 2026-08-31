import type {
  WorkspaceEffectiveResponse,
  WorkspaceModulePolicy,
  WorkspaceRolePolicyResponse,
  WorkspaceRolePolicyWriteRequest,
  WorkspaceUserPreferenceResponse,
  WorkspaceUserPreferenceWriteRequest,
} from '../types/workspace'
import {
  TRUSTED_WORKSPACE_MODULES,
  isTrustedDashboardPanelId,
  isTrustedWorkspaceModuleId,
  type TrustedWorkspaceModule,
  type TrustedWorkspaceModuleId,
} from '../workspace/moduleRegistry'
import {
  preferenceWriteRequest,
  rolePolicyWriteRequest,
  type WorkspaceModulePreferenceDraft,
} from '../workspace/workspaceModel'

export interface RolePolicyDraft {
  landingModuleId: string
  modules: Map<TrustedWorkspaceModuleId, WorkspaceModulePolicy>
  dashboardPanelIds: string[]
}

export interface PersonalWorkspaceDraft {
  landingModuleId: string | null
  modules: Map<TrustedWorkspaceModuleId, WorkspaceModulePreferenceDraft>
  inheritDashboardPanels: boolean
  dashboardPanelIds: string[]
}

export interface RolePolicyPreview {
  primary: TrustedWorkspaceModule[]
  mobile: TrustedWorkspaceModule[]
  settings: TrustedWorkspaceModule[]
}

export function createRolePolicyDraft(policy: WorkspaceRolePolicyResponse): RolePolicyDraft {
  return {
    landingModuleId: policy.landing_module_id,
    modules: new Map(
      policy.modules
        .filter((module): module is WorkspaceModulePolicy & { module_id: TrustedWorkspaceModuleId } =>
          isTrustedWorkspaceModuleId(module.module_id),
        )
        .map((module) => [module.module_id, { ...module }]),
    ),
    dashboardPanelIds: policy.dashboard_panel_ids.filter(isTrustedDashboardPanelId),
  }
}

export function updateRolePolicyModule(
  draft: RolePolicyDraft,
  moduleId: TrustedWorkspaceModuleId,
  patch: Partial<Pick<WorkspaceModulePolicy, 'visible' | 'optional' | 'order' | 'mobile_priority'>>,
): RolePolicyDraft {
  const current = draft.modules.get(moduleId)
  if (!current) return draft
  const modules = new Map(draft.modules)
  modules.set(moduleId, { ...current, ...patch })
  return { ...draft, modules }
}

export function moveRolePolicyModule(
  draft: RolePolicyDraft,
  moduleId: TrustedWorkspaceModuleId,
  direction: -1 | 1,
): RolePolicyDraft {
  const targetId = adjacentSiblingModuleId(draft.modules, moduleId, direction)
  return targetId ? reorderRolePolicyModule(draft, moduleId, targetId) : draft
}

export function reorderRolePolicyModule(
  draft: RolePolicyDraft,
  moduleId: TrustedWorkspaceModuleId,
  targetId: TrustedWorkspaceModuleId,
): RolePolicyDraft {
  const modules = reorderSiblingModules(draft.modules, moduleId, targetId)
  return modules === draft.modules ? draft : { ...draft, modules }
}

export function buildRolePolicyPayload(
  policy: WorkspaceRolePolicyResponse,
  draft: RolePolicyDraft,
): WorkspaceRolePolicyWriteRequest {
  return rolePolicyWriteRequest(
    policy,
    draft.modules,
    draft.landingModuleId,
    draft.dashboardPanelIds,
  )
}

export function rolePolicyDraftIsDirty(policy: WorkspaceRolePolicyResponse, draft: RolePolicyDraft): boolean {
  const payload = buildRolePolicyPayload(policy, draft)
  return JSON.stringify(payload.modules) !== JSON.stringify(policy.modules) ||
    payload.landing_module_id !== policy.landing_module_id ||
    JSON.stringify(payload.dashboard_panel_ids) !== JSON.stringify(policy.dashboard_panel_ids)
}

export function createPersonalWorkspaceDraft(
  effective: WorkspaceEffectiveResponse,
  preferences: WorkspaceUserPreferenceResponse,
): PersonalWorkspaceDraft {
  const preferenceById = new Map(preferences.modules.map((module) => [module.module_id, module]))
  const modules = new Map<TrustedWorkspaceModuleId, WorkspaceModulePreferenceDraft>()
  for (const module of effective.modules) {
    if (!module.optional || !isTrustedWorkspaceModuleId(module.id)) continue
    const preference = preferenceById.get(module.id)
    modules.set(module.id, {
      visible: preference?.visible ?? module.preference_visible,
      order: preference?.order ?? module.order,
    })
  }
  return {
    landingModuleId: preferences.landing_module_id,
    modules,
    inheritDashboardPanels: preferences.dashboard_panel_ids === null,
    dashboardPanelIds: (preferences.dashboard_panel_ids ?? effective.dashboard_panel_ids).filter(
      isTrustedDashboardPanelId,
    ),
  }
}

export function updatePersonalModule(
  draft: PersonalWorkspaceDraft,
  moduleId: TrustedWorkspaceModuleId,
  patch: Partial<WorkspaceModulePreferenceDraft>,
): PersonalWorkspaceDraft {
  const current = draft.modules.get(moduleId)
  if (!current) return draft
  const modules = new Map(draft.modules)
  modules.set(moduleId, { ...current, ...patch })
  return { ...draft, modules }
}

export function movePersonalModule(
  draft: PersonalWorkspaceDraft,
  moduleId: TrustedWorkspaceModuleId,
  direction: -1 | 1,
): PersonalWorkspaceDraft {
  const targetId = adjacentSiblingModuleId(draft.modules, moduleId, direction)
  return targetId ? reorderPersonalModule(draft, moduleId, targetId) : draft
}

export function reorderPersonalModule(
  draft: PersonalWorkspaceDraft,
  moduleId: TrustedWorkspaceModuleId,
  targetId: TrustedWorkspaceModuleId,
): PersonalWorkspaceDraft {
  const modules = reorderSiblingModules(draft.modules, moduleId, targetId)
  return modules === draft.modules ? draft : { ...draft, modules }
}

export function buildPersonalPreferencePayload(
  preferences: WorkspaceUserPreferenceResponse,
  draft: PersonalWorkspaceDraft,
): WorkspaceUserPreferenceWriteRequest {
  return preferenceWriteRequest(
    preferences,
    draft.modules,
    draft.landingModuleId,
    draft.inheritDashboardPanels ? null : draft.dashboardPanelIds,
  )
}

export function personalDraftIsDirty(
  effective: WorkspaceEffectiveResponse,
  preferences: WorkspaceUserPreferenceResponse,
  draft: PersonalWorkspaceDraft,
): boolean {
  const initial = createPersonalWorkspaceDraft(effective, preferences)
  return JSON.stringify(serializePersonalDraft(initial)) !== JSON.stringify(serializePersonalDraft(draft))
}

function serializePersonalDraft(draft: PersonalWorkspaceDraft) {
  return {
    landingModuleId: draft.landingModuleId,
    modules: [...draft.modules.entries()].sort(([left], [right]) => left.localeCompare(right)),
    inheritDashboardPanels: draft.inheritDashboardPanels,
    dashboardPanelIds: draft.dashboardPanelIds,
  }
}

export function personalLandingOptions(
  effective: WorkspaceEffectiveResponse,
  draft: PersonalWorkspaceDraft,
): TrustedWorkspaceModule[] {
  const effectiveById = new Map(effective.modules.map((module) => [module.id, module]))
  const visibleById = new Map<TrustedWorkspaceModuleId, boolean>()
  const options: TrustedWorkspaceModule[] = []
  for (const definition of TRUSTED_WORKSPACE_MODULES) {
    const parentVisible = definition.parentId === null || visibleById.get(definition.parentId) === true
    if (!definition.policyManaged) {
      visibleById.set(definition.id, definition.isContainer && parentVisible)
      continue
    }
    const module = effectiveById.get(definition.id)
    if (!module) continue
    const preference = draft.modules.get(definition.id)
    const visible = Boolean(
      module.policy_visible &&
      module.permission_allowed &&
      module.feature_available &&
      (preference?.visible ?? module.preference_visible) &&
      parentVisible,
    )
    visibleById.set(definition.id, visible)
    if (visible) options.push(definition)
  }
  return options.sort((left, right) => {
    const leftOrder = draft.modules.get(left.id)?.order ?? effectiveById.get(left.id)?.order ?? left.defaultOrder
    const rightOrder = draft.modules.get(right.id)?.order ?? effectiveById.get(right.id)?.order ?? right.defaultOrder
    return leftOrder - rightOrder || left.id.localeCompare(right.id)
  })
}

export function rolePolicyPreview(draft: RolePolicyDraft): RolePolicyPreview {
  const visibleById = new Map<TrustedWorkspaceModuleId, boolean>()
  const visible: Array<{
    definition: TrustedWorkspaceModule
    order: number
    mobilePriority: number
  }> = []
  for (const definition of TRUSTED_WORKSPACE_MODULES) {
    const policy = draft.modules.get(definition.id)
    const policyVisible = definition.policyManaged ? policy?.visible === true : true
    const parentVisible = definition.parentId === null || visibleById.get(definition.parentId) === true
    const isVisible = policyVisible && parentVisible
    visibleById.set(definition.id, isVisible)
    if (isVisible) {
      visible.push({
        definition,
        order: policy?.order ?? definition.defaultOrder,
        mobilePriority: policy?.mobile_priority ?? definition.defaultMobilePriority,
      })
    }
  }
  const sorted = [...visible].sort(
    (left, right) => left.order - right.order || left.definition.id.localeCompare(right.definition.id),
  )
  const mobile = visible
    .filter(({ definition }) => definition.section === 'primary')
    .sort((left, right) => {
      if (left.definition.mobileBehavior !== right.definition.mobileBehavior) {
        return left.definition.mobileBehavior === 'primary' ? -1 : 1
      }
      return left.mobilePriority - right.mobilePriority ||
        left.order - right.order ||
        left.definition.id.localeCompare(right.definition.id)
    })
    .map(({ definition }) => definition)
  return {
    primary: sorted.filter(({ definition }) => definition.section === 'primary').map(({ definition }) => definition),
    mobile,
    settings: sorted.filter(({ definition }) => definition.section === 'settings').map(({ definition }) => definition),
  }
}

export function rolePolicyDraftValidation(
  draft: RolePolicyDraft,
  preservedLandingModuleId?: string,
): string {
  const preview = rolePolicyPreview(draft)
  const available = [...preview.primary, ...preview.settings].filter((module) => module.policyManaged)
  if (available.length === 0) {
    return 'At least one trusted module must remain visible so the role has a landing destination.'
  }
  if (draft.dashboardPanelIds.length === 0) {
    return 'Choose at least one first-use dashboard panel.'
  }
  if (
    !isTrustedWorkspaceModuleId(draft.landingModuleId) &&
    draft.landingModuleId === preservedLandingModuleId
  ) {
    return ''
  }
  if (!available.some((module) => module.id === draft.landingModuleId)) {
    return 'Choose a landing module that remains visible with all of its parent navigation modules.'
  }
  return ''
}

function adjacentSiblingModuleId<T extends { order: number }>(
  modules: Map<TrustedWorkspaceModuleId, T>,
  moduleId: TrustedWorkspaceModuleId,
  direction: -1 | 1,
): TrustedWorkspaceModuleId | null {
  const ordered = orderedSiblingModules(modules, moduleId)
  const index = ordered.findIndex(([id]) => id === moduleId)
  const targetIndex = index + direction
  return index >= 0 && targetIndex >= 0 && targetIndex < ordered.length
    ? ordered[targetIndex][0]
    : null
}

function reorderSiblingModules<T extends { order: number }>(
  modules: Map<TrustedWorkspaceModuleId, T>,
  moduleId: TrustedWorkspaceModuleId,
  targetId: TrustedWorkspaceModuleId,
): Map<TrustedWorkspaceModuleId, T> {
  if (moduleId === targetId) return modules
  const sourceDefinition = TRUSTED_WORKSPACE_MODULES.find((module) => module.id === moduleId)
  const targetDefinition = TRUSTED_WORKSPACE_MODULES.find((module) => module.id === targetId)
  if (!sourceDefinition || !targetDefinition || sourceDefinition.parentId !== targetDefinition.parentId) {
    return modules
  }

  const ordered = orderedSiblingModules(modules, moduleId)
  const sourceIndex = ordered.findIndex(([id]) => id === moduleId)
  const targetIndex = ordered.findIndex(([id]) => id === targetId)
  if (sourceIndex < 0 || targetIndex < 0) return modules

  const orderValues = ordered.map(([, value]) => value.order)
  const assignedOrderValues = orderValues.every(
    (value, index) => index === 0 || value > orderValues[index - 1]!,
  )
    ? orderValues
    : orderValues.map((_, index) => (orderValues[0] ?? 0) + index * 10)
  const [moved] = ordered.splice(sourceIndex, 1)
  ordered.splice(targetIndex, 0, moved)

  const next = new Map(modules)
  ordered.forEach(([id, value], index) => {
    next.set(id, { ...value, order: assignedOrderValues[index] })
  })
  return next
}

function orderedSiblingModules<T extends { order: number }>(
  modules: Map<TrustedWorkspaceModuleId, T>,
  moduleId: TrustedWorkspaceModuleId,
): Array<[TrustedWorkspaceModuleId, T]> {
  const definition = TRUSTED_WORKSPACE_MODULES.find((module) => module.id === moduleId)
  if (!definition) return []
  return [...modules.entries()]
    .filter(([id]) => TRUSTED_WORKSPACE_MODULES.find((module) => module.id === id)?.parentId === definition.parentId)
    .sort(([leftId, left], [rightId, right]) => left.order - right.order || leftId.localeCompare(rightId))
}

export function toggleStringValue(values: readonly string[], value: string, selected: boolean): string[] {
  if (selected) return values.includes(value) ? [...values] : [...values, value]
  return values.filter((entry) => entry !== value)
}
