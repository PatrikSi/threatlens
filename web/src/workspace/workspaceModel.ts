import type { AppFeatures } from '../types/identity'
import type {
  WorkspaceEffectiveModuleResponse,
  WorkspaceEffectiveResponse,
  WorkspaceModulePolicy,
  WorkspaceModulePreference,
  WorkspaceRegistryResponse,
  WorkspaceRole,
  WorkspaceRolePolicyResponse,
  WorkspaceRolePolicyWriteRequest,
  WorkspaceUserPreferenceResponse,
  WorkspaceUserPreferenceWriteRequest,
} from '../types/workspace'
import {
  TRUSTED_DASHBOARD_PANEL_BY_ID,
  TRUSTED_WORKSPACE_MODULE_BY_ID,
  TRUSTED_WORKSPACE_MODULES,
  isTrustedDashboardPanelId,
  isTrustedWorkspaceModuleId,
  type TrustedDashboardPanel,
  type TrustedDashboardPanelId,
  type TrustedWorkspaceModule,
  type TrustedWorkspaceModuleId,
} from './moduleRegistry'

export interface WorkspaceUserContext {
  role: WorkspaceRole
  permissions?: readonly string[]
  features: AppFeatures
  accountEligible?: boolean
}

export interface ResolvedWorkspaceModule extends TrustedWorkspaceModule {
  visible: boolean
  optional: boolean
  order: number
  mobilePriority: number
  permissionAllowed: boolean
  featureAvailable: boolean
  policyVisible: boolean
  preferenceVisible: boolean
  reasons: readonly string[]
  resolutionSource: 'server' | 'trusted-fallback' | 'local-control'
}

export interface ResolvedWorkspaceModel {
  modules: readonly ResolvedWorkspaceModule[]
  primaryNavigation: readonly ResolvedWorkspaceModule[]
  mobileNavigation: readonly ResolvedWorkspaceModule[]
  settingsNavigation: readonly ResolvedWorkspaceModule[]
  mobileSettingsNavigation: readonly ResolvedWorkspaceModule[]
  landingModuleId: TrustedWorkspaceModuleId | null
  landingPath: string
  dashboardPanelIds: readonly TrustedDashboardPanelId[]
  warnings: readonly string[]
  unknownModuleIds: readonly string[]
  unknownDashboardPanelIds: readonly string[]
}

export interface WorkspaceModulePreferenceDraft {
  visible: boolean
  order: number
}

export function effectiveWorkspaceForClientControls(
  effective: WorkspaceEffectiveResponse | undefined,
): WorkspaceEffectiveResponse | undefined {
  if (!effective) return undefined

  const modules = effective.modules.filter((module) => {
    const definition = TRUSTED_WORKSPACE_MODULE_BY_ID.get(module.id as TrustedWorkspaceModuleId)
    return definition?.policyManaged !== false
  })
  return modules.length === effective.modules.length ? effective : { ...effective, modules }
}

export function resolveWorkspaceModel(
  effective: WorkspaceEffectiveResponse | undefined,
  serverRegistry: WorkspaceRegistryResponse | undefined,
  context: WorkspaceUserContext,
): ResolvedWorkspaceModel {
  const warnings = collectRegistryWarnings(serverRegistry)
  const roleMatches = effective === undefined || effective.role === context.role
  if (!roleMatches) {
    warnings.push(`workspace_role_mismatch:${effective.role}:${context.role}`)
  }
  warnings.push(...(effective?.warnings ?? []))
  const trustedEffective = roleMatches ? effective : undefined

  const effectiveById = uniqueEffectiveModules(trustedEffective?.modules ?? [], warnings)
  for (const module of effective?.modules ?? []) {
    if (!isTrustedWorkspaceModuleId(module.id)) {
      warnings.push(`untrusted_effective_module:${module.id}`)
    }
  }

  const resolvedById = new Map<TrustedWorkspaceModuleId, ResolvedWorkspaceModule>()
  for (const definition of TRUSTED_WORKSPACE_MODULES) {
    const serverModule = effectiveById.get(definition.id)
    if (serverModule) {
      warnings.push(...moduleContractWarnings(definition, serverModule))
    }

    const localPermissionAllowed = definition.isContainer ||
      hasRequiredPermissions(context.permissions, definition.requiredPermissions)
    const localFeatureAvailable = hasRequiredFeature(context.features, definition.featureDependency)
    const parentVisible = definition.parentId === null || resolvedById.get(definition.parentId)?.visible === true
    const resolved = resolveModule({
      definition,
      serverModule,
      effectiveAvailable: trustedEffective !== undefined,
      context,
      localPermissionAllowed,
      localFeatureAvailable,
      parentVisible,
    })
    resolvedById.set(definition.id, resolved)
  }
  hideEmptyContainers(resolvedById)

  const modules = [...resolvedById.values()]
  const primaryNavigation = modules
    .filter((module) => module.section === 'primary' && module.visible)
    .sort(byDesktopOrder)
  const mobileNavigation = [...primaryNavigation].sort(byMobileOrder)
  const settingsNavigation = modules
    .filter((module) => module.section === 'settings' && module.visible)
    .sort(byDesktopOrder)
  const mobileSettingsNavigation = modules
    .filter((module) => module.section === 'settings' && module.visible)
    .sort(byMobileOrder)
  const preferredLandingModuleId = trustedEffective?.landing_module_id ?? null
  const landing = resolveLandingModule(modules, preferredLandingModuleId)
  if (preferredLandingModuleId && landing?.id !== preferredLandingModuleId) {
    warnings.push(`landing_module_unavailable:${preferredLandingModuleId}`)
  }

  const dashboardPanelIds = resolveDashboardPanelIds(trustedEffective, context, warnings)
  const unknownModuleIds = sortedUnique([
    ...(serverRegistry?.modules ?? []).map((module) => module.id).filter((id) => !isTrustedWorkspaceModuleId(id)),
    ...(effective?.modules ?? []).map((module) => module.id).filter((id) => !isTrustedWorkspaceModuleId(id)),
  ])
  const unknownDashboardPanelIds = sortedUnique([
    ...(serverRegistry?.dashboard_panels ?? [])
      .map((panel) => panel.id)
      .filter((id) => !isTrustedDashboardPanelId(id)),
    ...(effective?.dashboard_panel_ids ?? []).filter((id) => !isTrustedDashboardPanelId(id)),
  ])

  return {
    modules,
    primaryNavigation,
    mobileNavigation,
    settingsNavigation,
    mobileSettingsNavigation,
    landingModuleId: landing?.id ?? null,
    landingPath: landing?.route ?? '/',
    dashboardPanelIds,
    warnings: sortedUnique(warnings),
    unknownModuleIds,
    unknownDashboardPanelIds,
  }
}

function hideEmptyContainers(
  resolvedById: Map<TrustedWorkspaceModuleId, ResolvedWorkspaceModule>,
) {
  for (const definition of [...TRUSTED_WORKSPACE_MODULES].reverse()) {
    if (!definition.isContainer) continue
    const hasVisibleChild = [...resolvedById.values()].some(
      (module) => module.parentId === definition.id && module.visible,
    )
    const container = resolvedById.get(definition.id)
    if (!container?.visible || hasVisibleChild) continue
    resolvedById.set(definition.id, {
      ...container,
      visible: false,
      reasons: [...new Set([...container.reasons, 'empty_container'])],
    })
  }
}

function resolveModule({
  definition,
  serverModule,
  effectiveAvailable,
  context,
  localPermissionAllowed,
  localFeatureAvailable,
  parentVisible,
}: {
  definition: TrustedWorkspaceModule
  serverModule: WorkspaceEffectiveModuleResponse | undefined
  effectiveAvailable: boolean
  context: WorkspaceUserContext
  localPermissionAllowed: boolean
  localFeatureAvailable: boolean
  parentVisible: boolean
}): ResolvedWorkspaceModule {
  if (!definition.policyManaged) {
    const policyVisible = definition.defaultVisibleRoles.includes(context.role)
    const visible = Boolean(
      context.accountEligible !== false &&
      policyVisible &&
      localPermissionAllowed &&
      localFeatureAvailable &&
      parentVisible,
    )
    return {
      ...definition,
      visible,
      optional: definition.defaultOptional,
      order: definition.defaultOrder,
      mobilePriority: definition.defaultMobilePriority,
      permissionAllowed: localPermissionAllowed,
      featureAvailable: localFeatureAvailable,
      policyVisible,
      preferenceVisible: true,
      reasons: visible ? [] : localResolutionReasons(context, policyVisible, localPermissionAllowed, localFeatureAvailable, parentVisible),
      resolutionSource: 'local-control',
    }
  }

  if (!serverModule) {
    const policyVisible = !effectiveAvailable && definition.defaultVisibleRoles.includes(context.role)
    const visible = Boolean(
      !effectiveAvailable &&
      context.accountEligible !== false &&
      policyVisible &&
      localPermissionAllowed &&
      localFeatureAvailable &&
      parentVisible,
    )
    return {
      ...definition,
      visible,
      optional: definition.defaultOptional,
      order: definition.defaultOrder,
      mobilePriority: definition.defaultMobilePriority,
      permissionAllowed: localPermissionAllowed,
      featureAvailable: localFeatureAvailable,
      policyVisible,
      preferenceVisible: true,
      reasons: effectiveAvailable
        ? ['server_module_missing']
        : localResolutionReasons(context, policyVisible, localPermissionAllowed, localFeatureAvailable, parentVisible),
      resolutionSource: 'trusted-fallback',
    }
  }

  const permissionAllowed = serverModule.permission_allowed && localPermissionAllowed
  const featureAvailable = serverModule.feature_available && localFeatureAvailable
  const serverVisibilityAllowed = serverModule.visible || (
    parentVisible &&
    serverModule.reasons.length > 0 &&
    serverModule.reasons.every((reason) => reason === 'parent_hidden')
  )
  const visible = Boolean(
    serverVisibilityAllowed &&
    context.accountEligible !== false &&
    permissionAllowed &&
    featureAvailable &&
    serverModule.policy_visible &&
    serverModule.preference_visible &&
    parentVisible,
  )
  const reasons = new Set(serverModule.reasons)
  if (parentVisible) reasons.delete('parent_hidden')
  if (!localPermissionAllowed) reasons.add('permission_missing')
  if (!localFeatureAvailable) reasons.add('feature_unavailable')
  if (!parentVisible) reasons.add('parent_hidden')
  if (context.accountEligible === false) reasons.add('account_ineligible')
  return {
    ...definition,
    visible,
    optional: serverModule.optional,
    order: serverModule.order,
    mobilePriority: serverModule.mobile_priority,
    permissionAllowed,
    featureAvailable,
    policyVisible: serverModule.policy_visible,
    preferenceVisible: serverModule.preference_visible,
    reasons: [...reasons],
    resolutionSource: 'server',
  }
}

function localResolutionReasons(
  context: WorkspaceUserContext,
  policyVisible: boolean,
  permissionAllowed: boolean,
  featureAvailable: boolean,
  parentVisible: boolean,
): string[] {
  const reasons: string[] = []
  if (!policyVisible) reasons.push('policy_hidden')
  if (!permissionAllowed) reasons.push('permission_missing')
  if (!featureAvailable) reasons.push('feature_unavailable')
  if (!parentVisible) reasons.push('parent_hidden')
  if (context.accountEligible === false) reasons.push('account_ineligible')
  return reasons
}

export function resolveLandingRoute(
  modules: readonly ResolvedWorkspaceModule[],
  preferredModuleId: string | null,
): string {
  return resolveLandingModule(modules, preferredModuleId)?.route ?? '/'
}

function resolveLandingModule(
  modules: readonly ResolvedWorkspaceModule[],
  preferredModuleId: string | null,
): ResolvedWorkspaceModule | undefined {
  if (preferredModuleId && isTrustedWorkspaceModuleId(preferredModuleId)) {
    const preferred = modules.find((module) => module.id === preferredModuleId)
    if (preferred?.visible && preferred.landingEligible) return preferred
  }
  return (
    modules
      .filter((module) => module.visible && module.landingEligible && module.section === 'primary')
      .sort(byDesktopOrder)[0] ??
    modules.filter((module) => module.visible && module.landingEligible).sort(byDesktopOrder)[0]
  )
}

function resolveDashboardPanelIds(
  effective: WorkspaceEffectiveResponse | undefined,
  context: WorkspaceUserContext,
  warnings: string[],
): TrustedDashboardPanelId[] {
  if (!effective) {
    return isDashboardPanelAvailable(TRUSTED_DASHBOARD_PANEL_BY_ID.get('rss')!, context) ? ['rss'] : []
  }
  const detailsById = new Map(effective.dashboard_panels.map((panel) => [panel.id, panel]))
  return effective.dashboard_panel_ids.filter((id): id is TrustedDashboardPanelId => {
    if (!isTrustedDashboardPanelId(id)) {
      warnings.push(`untrusted_effective_dashboard_panel:${id}`)
      return false
    }
    const definition = TRUSTED_DASHBOARD_PANEL_BY_ID.get(id)!
    const detail = detailsById.get(id)
    return Boolean(
      detail?.visible &&
      detail.permission_allowed &&
      detail.feature_available &&
      isDashboardPanelAvailable(definition, context),
    )
  })
}

export function isDashboardPanelAvailable(panel: TrustedDashboardPanel, context: WorkspaceUserContext): boolean {
  return (
    context.accountEligible !== false &&
    hasRequiredPermissions(context.permissions, panel.requiredPermissions) &&
    hasRequiredFeature(context.features, panel.featureDependency)
  )
}

export function hasRequiredPermissions(
  grantedPermissions: readonly string[] | undefined,
  requiredPermissions: readonly string[],
): boolean {
  if (requiredPermissions.length === 0 || grantedPermissions === undefined) return true
  const granted = new Set(grantedPermissions)
  return requiredPermissions.every((permission) => {
    if (granted.has(permission) || granted.has('*:*') || granted.has('admin:*')) return true
    const separator = permission.indexOf(':')
    if (separator <= 0) return false
    const action = permission.slice(0, separator)
    const resource = permission.slice(separator + 1)
    if (granted.has(`${action}:*`)) return true
    return action === 'read' && (granted.has(`write:${resource}`) || granted.has('write:*'))
  })
}

function hasRequiredFeature(features: AppFeatures, feature: keyof AppFeatures | null): boolean {
  return feature === null || features[feature] === true
}

function collectRegistryWarnings(serverRegistry: WorkspaceRegistryResponse | undefined): string[] {
  if (!serverRegistry) return []
  const warnings: string[] = []
  for (const id of duplicateIds(serverRegistry.modules.map((module) => module.id))) {
    warnings.push(`duplicate_server_module:${id}`)
  }
  for (const id of duplicateIds(serverRegistry.dashboard_panels.map((panel) => panel.id))) {
    warnings.push(`duplicate_server_dashboard_panel:${id}`)
  }
  const serverIds = new Set(serverRegistry.modules.map((module) => module.id))
  for (const module of serverRegistry.modules) {
    const trusted = TRUSTED_WORKSPACE_MODULE_BY_ID.get(module.id as TrustedWorkspaceModuleId)
    if (!trusted) {
      warnings.push(`untrusted_server_module:${module.id}`)
      continue
    }
    warnings.push(...moduleContractWarnings(trusted, module))
  }
  for (const trusted of TRUSTED_WORKSPACE_MODULES) {
    if (trusted.policyManaged && !serverIds.has(trusted.id)) {
      warnings.push(`server_module_missing:${trusted.id}`)
    }
  }
  for (const panel of serverRegistry.dashboard_panels) {
    if (!isTrustedDashboardPanelId(panel.id)) {
      warnings.push(`untrusted_server_dashboard_panel:${panel.id}`)
      continue
    }
    const trusted = TRUSTED_DASHBOARD_PANEL_BY_ID.get(panel.id)!
    if (panel.label !== trusted.label) {
      warnings.push(`server_dashboard_panel_label_mismatch:${panel.id}`)
    }
    if (!sameStrings(panel.required_permissions, trusted.requiredPermissions)) {
      warnings.push(`server_dashboard_panel_permissions_mismatch:${panel.id}`)
    }
    if (panel.feature_flag !== trusted.serverFeatureFlag) {
      warnings.push(`server_dashboard_panel_feature_mismatch:${panel.id}`)
    }
  }
  const serverPanelIds = new Set(serverRegistry.dashboard_panels.map((panel) => panel.id))
  for (const trusted of TRUSTED_DASHBOARD_PANEL_BY_ID.values()) {
    if (!serverPanelIds.has(trusted.id)) {
      warnings.push(`server_dashboard_panel_missing:${trusted.id}`)
    }
  }
  return warnings
}

function moduleContractWarnings(
  trusted: TrustedWorkspaceModule,
  server: Pick<WorkspaceEffectiveModuleResponse, 'id' | 'label' | 'route' | 'section' | 'parent_id' | 'mobile_behavior'> &
    Partial<{ required_permissions: string[]; feature_flag: string | null }>,
): string[] {
  const warnings: string[] = []
  if (server.label !== trusted.label) warnings.push(`server_module_label_mismatch:${trusted.id}`)
  if (server.route !== trusted.route) warnings.push(`server_module_route_mismatch:${trusted.id}`)
  if (server.section !== trusted.section) warnings.push(`server_module_section_mismatch:${trusted.id}`)
  if (server.parent_id !== trusted.parentId) warnings.push(`server_module_parent_mismatch:${trusted.id}`)
  if (server.mobile_behavior !== trusted.mobileBehavior) warnings.push(`server_module_mobile_mismatch:${trusted.id}`)
  if (server.required_permissions && !sameStrings(server.required_permissions, trusted.requiredPermissions)) {
    warnings.push(`server_module_permissions_mismatch:${trusted.id}`)
  }
  if ('feature_flag' in server && server.feature_flag !== trusted.serverFeatureFlag) {
    warnings.push(`server_module_feature_mismatch:${trusted.id}`)
  }
  return warnings
}

function sameStrings(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index])
}

function byDesktopOrder(left: ResolvedWorkspaceModule, right: ResolvedWorkspaceModule): number {
  return left.order - right.order || left.id.localeCompare(right.id)
}

function byMobileOrder(left: ResolvedWorkspaceModule, right: ResolvedWorkspaceModule): number {
  if (left.mobileBehavior !== right.mobileBehavior) return left.mobileBehavior === 'primary' ? -1 : 1
  return left.mobilePriority - right.mobilePriority || byDesktopOrder(left, right)
}

function sortedUnique(values: readonly string[]): string[] {
  return [...new Set(values)].sort()
}

function duplicateIds(ids: readonly string[]): string[] {
  const seen = new Set<string>()
  const duplicates = new Set<string>()
  for (const id of ids) {
    if (seen.has(id)) duplicates.add(id)
    seen.add(id)
  }
  return [...duplicates].sort()
}

function uniqueEffectiveModules(
  modules: readonly WorkspaceEffectiveModuleResponse[],
  warnings: string[],
): Map<string, WorkspaceEffectiveModuleResponse> {
  const duplicates = new Set(duplicateIds(modules.map((module) => module.id)))
  for (const id of duplicates) warnings.push(`duplicate_effective_module:${id}`)
  return new Map(
    modules
      .filter((module) => !duplicates.has(module.id))
      .map((module) => [module.id, module]),
  )
}

export function rolePolicyWriteRequest(
  policy: WorkspaceRolePolicyResponse,
  trustedDraft: ReadonlyMap<TrustedWorkspaceModuleId, WorkspaceModulePolicy>,
  landingModuleId: string,
  dashboardPanelIds: readonly string[],
): WorkspaceRolePolicyWriteRequest {
  return {
    expected_revision: policy.revision,
    landing_module_id: landingModuleId,
    modules: policy.modules.map((module) => {
      if (!isTrustedWorkspaceModuleId(module.module_id)) return module
      return trustedDraft.get(module.module_id) ?? module
    }),
    dashboard_panel_ids: preserveUntrustedIds(policy.dashboard_panel_ids, dashboardPanelIds, isTrustedDashboardPanelId),
  }
}

export function preferenceWriteRequest(
  preferences: WorkspaceUserPreferenceResponse,
  trustedDraft: ReadonlyMap<TrustedWorkspaceModuleId, WorkspaceModulePreferenceDraft>,
  landingModuleId: string | null,
  dashboardPanelIds: readonly string[] | null,
): WorkspaceUserPreferenceWriteRequest {
  const preserved = preferences.modules.filter((module) => !isTrustedWorkspaceModuleId(module.module_id))
  const trusted: WorkspaceModulePreference[] = [...trustedDraft.entries()].map(([moduleId, preference]) => ({
    module_id: moduleId,
    visible: preference.visible,
    order: normalizeOrder(preference.order),
  }))
  return {
    expected_revision: preferences.revision,
    landing_module_id: landingModuleId,
    modules: [...preserved, ...trusted],
    dashboard_panel_ids:
      dashboardPanelIds === null
        ? null
        : preserveUntrustedIds(
            preferences.dashboard_panel_ids ?? [],
            dashboardPanelIds,
            isTrustedDashboardPanelId,
          ),
  }
}

function preserveUntrustedIds(
  original: readonly string[],
  trustedDraft: readonly string[],
  isTrusted: (value: string) => boolean,
): string[] {
  return [...trustedDraft.filter(isTrusted), ...original.filter((value) => !isTrusted(value))]
}

function normalizeOrder(value: number): number {
  if (!Number.isFinite(value)) return 0
  return Math.min(10_000, Math.max(0, Math.round(value)))
}

export function workspaceWarningMessage(warning: string): string {
  const [code, ...details] = warning.split(':')
  const subject = details.join(':') || 'unknown entry'
  if (code === 'workspace_role_mismatch') {
    return 'The workspace snapshot role differs from the current session role. Trusted defaults are active; reload before making changes.'
  }
  if (code.startsWith('duplicate_')) {
    return `${subject} appears more than once in the server workspace response. The ambiguous entry is not used.`
  }
  if (code.startsWith('untrusted_')) {
    return `${subject} is present on the server but is not trusted by this frontend release. It remains stored but cannot create a route or link.`
  }
  if (code === 'server_module_missing' || code === 'server_dashboard_panel_missing') {
    return `${subject} is available in this frontend but missing from the server registry. ThreatLens uses the trusted local contract when an effective server entry is available; otherwise the entry remains unavailable.`
  }
  if (code.includes('mismatch')) {
    return `${subject} differs between the frontend and server registries. ThreatLens is using the local trusted definition.`
  }
  if (code === 'landing_module_unavailable' || code === 'unknown_landing_module') {
    return `${subject} cannot be used as a landing module in this frontend. A trusted visible fallback is used instead.`
  }
  if (code.startsWith('unknown_') || code.startsWith('invalid_')) {
    return `${subject} is retained for compatibility but cannot be edited by this frontend release.`
  }
  return warning.replaceAll('_', ' ')
}
