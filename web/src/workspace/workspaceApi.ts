import { apiFetch } from '../api/client'
import type {
  WorkspaceDashboardPanelDefinitionResponse,
  WorkspaceEffectiveResponse,
  WorkspaceModuleDefinitionResponse,
  WorkspaceRegistryResponse,
  WorkspaceRole,
  WorkspaceRolePolicyResetRequest,
  WorkspaceRolePolicyResponse,
  WorkspaceRolePolicyWriteRequest,
  WorkspaceUserPreferenceResetRequest,
  WorkspaceUserPreferenceResponse,
  WorkspaceUserPreferenceWriteRequest,
} from '../types/workspace'

type WorkspaceModuleDefinitionWireResponse = Omit<WorkspaceModuleDefinitionResponse, 'required_permissions'> & {
  required_permissions?: string[]
}

type WorkspaceDashboardPanelDefinitionWireResponse = Omit<WorkspaceDashboardPanelDefinitionResponse, 'required_permissions'> & {
  required_permissions?: string[]
}

interface WorkspaceRegistryWireResponse {
  modules: WorkspaceModuleDefinitionWireResponse[]
  dashboard_panels: WorkspaceDashboardPanelDefinitionWireResponse[]
}

export const workspaceQueryKeys = {
  root: ['workspace'] as const,
  effective: (userId: string) => ['workspace', 'effective', userId] as const,
  registry: ['workspace', 'registry'] as const,
  preferences: (userId: string) => ['workspace', 'preferences', userId] as const,
  rolePolicies: ['workspace', 'role-policies'] as const,
}

export async function getWorkspaceRegistry(): Promise<WorkspaceRegistryResponse> {
  const response = await apiFetch<WorkspaceRegistryWireResponse>('/workspace/modules')
  return {
    modules: response.modules.map((module) => ({
      ...module,
      required_permissions: normalizeRequiredPermissions(module),
    })),
    dashboard_panels: response.dashboard_panels.map((panel) => ({
      ...panel,
      required_permissions: normalizeRequiredPermissions(panel),
    })),
  }
}

function normalizeRequiredPermissions(
  definition: { required_permissions?: string[]; required_permission: string | null },
): string[] {
  return definition.required_permissions ?? (definition.required_permission ? [definition.required_permission] : [])
}

export function getEffectiveWorkspace() {
  return apiFetch<WorkspaceEffectiveResponse>('/workspace/effective')
}

export function getWorkspacePreferences() {
  return apiFetch<WorkspaceUserPreferenceResponse>('/workspace/preferences')
}

export function updateWorkspacePreferences(payload: WorkspaceUserPreferenceWriteRequest) {
  return apiFetch<WorkspaceUserPreferenceResponse>('/workspace/preferences', {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function resetWorkspacePreferences(payload: WorkspaceUserPreferenceResetRequest) {
  return apiFetch<WorkspaceUserPreferenceResponse>('/workspace/preferences/reset', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getWorkspaceRolePolicies() {
  return apiFetch<WorkspaceRolePolicyResponse[]>('/workspace/role-policies')
}

export function updateWorkspaceRolePolicy(role: WorkspaceRole, payload: WorkspaceRolePolicyWriteRequest) {
  return apiFetch<WorkspaceRolePolicyResponse>(`/workspace/role-policies/${role}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function resetWorkspaceRolePolicy(role: WorkspaceRole, payload: WorkspaceRolePolicyResetRequest) {
  return apiFetch<WorkspaceRolePolicyResponse>(`/workspace/role-policies/${role}/reset`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
