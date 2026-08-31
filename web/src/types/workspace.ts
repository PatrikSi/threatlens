import type { AppFeatures } from './identity'

export type WorkspaceRole = 'admin' | 'analyst' | 'viewer'
export type WorkspaceSection = 'primary' | 'settings'
export type WorkspaceMobileBehavior = 'primary' | 'secondary'
export type WorkspaceFeatureKey = keyof AppFeatures

export interface WorkspaceModuleDefinitionResponse {
  id: string
  label: string
  route: string
  section: WorkspaceSection
  parent_id: string | null
  required_permission: string | null
  required_permissions: string[]
  feature_flag: string | null
  default_optional: boolean
  default_order: number
  default_mobile_priority: number
  mobile_behavior: WorkspaceMobileBehavior
}

export interface WorkspaceDashboardPanelDefinitionResponse {
  id: string
  label: string
  required_permission: string | null
  required_permissions: string[]
  feature_flag: string | null
}

export interface WorkspaceRegistryResponse {
  modules: WorkspaceModuleDefinitionResponse[]
  dashboard_panels: WorkspaceDashboardPanelDefinitionResponse[]
}

export interface WorkspaceModulePolicy {
  module_id: string
  visible: boolean
  optional: boolean
  order: number
  mobile_priority: number
}

export interface WorkspaceRolePolicyResponse {
  role: WorkspaceRole
  landing_module_id: string
  modules: WorkspaceModulePolicy[]
  dashboard_panel_ids: string[]
  revision: number
  updated_by_user_id: string | null
  created_at: string
  updated_at: string
  unknown_module_ids: string[]
  unknown_dashboard_panel_ids: string[]
  warnings: string[]
}

export interface WorkspaceRolePolicyWriteRequest {
  expected_revision: number
  landing_module_id: string
  modules: WorkspaceModulePolicy[]
  dashboard_panel_ids: string[]
}

export interface WorkspaceRolePolicyResetRequest {
  expected_revision: number
}

export interface WorkspaceModulePreference {
  module_id: string
  visible: boolean | null
  order: number | null
}

export interface WorkspaceUserPreferenceResponse {
  user_id: string
  role: WorkspaceRole
  landing_module_id: string | null
  modules: WorkspaceModulePreference[]
  dashboard_panel_ids: string[] | null
  revision: number
  updated_by_user_id: string | null
  created_at: string | null
  updated_at: string | null
  unknown_module_ids: string[]
  unknown_dashboard_panel_ids: string[]
  warnings: string[]
}

export interface WorkspaceUserPreferenceWriteRequest {
  expected_revision: number
  landing_module_id: string | null
  modules: WorkspaceModulePreference[]
  dashboard_panel_ids: string[] | null
}

export interface WorkspaceUserPreferenceResetRequest {
  expected_revision: number
}

export interface WorkspaceEffectiveModuleResponse {
  id: string
  label: string
  route: string
  section: WorkspaceSection
  parent_id: string | null
  visible: boolean
  optional: boolean
  order: number
  mobile_priority: number
  mobile_behavior: WorkspaceMobileBehavior
  permission_allowed: boolean
  missing_permissions: string[]
  feature_available: boolean
  policy_visible: boolean
  preference_visible: boolean
  reasons: string[]
}

export interface WorkspaceEffectiveDashboardPanelResponse {
  id: string
  visible: boolean
  permission_allowed: boolean
  feature_available: boolean
  missing_permissions: string[]
  reasons: string[]
}

export interface WorkspaceEffectiveResponse {
  role: WorkspaceRole
  policy_revision: number
  preference_revision: number
  landing_module_id: string | null
  dashboard_panel_ids: string[]
  dashboard_panels: WorkspaceEffectiveDashboardPanelResponse[]
  modules: WorkspaceEffectiveModuleResponse[]
  warnings: string[]
}
