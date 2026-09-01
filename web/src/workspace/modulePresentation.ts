import type {
  TrustedWorkspaceModule,
  TrustedWorkspaceModuleId,
} from './moduleRegistry'

export type SettingsPresentationGroupId =
  | 'personal'
  | 'organization'
  | 'automation'
  | 'system'

export interface SettingsPresentationGroup {
  id: SettingsPresentationGroupId
  label: string
}

export interface SettingsModulePresentation {
  groupId: SettingsPresentationGroupId
  label: string
}

export type WorkspaceNavigationGroupId =
  | 'main'
  | 'settings.personal'
  | 'settings.organization'
  | 'settings.automation'
  | 'settings.integrations'
  | 'settings.system'

export interface WorkspaceNavigationGroupPresentation {
  id: WorkspaceNavigationGroupId
  label: string
}

export const SETTINGS_PRESENTATION_GROUPS: readonly SettingsPresentationGroup[] = [
  { id: 'personal', label: 'Personal' },
  { id: 'organization', label: 'Organization' },
  { id: 'automation', label: 'Automation' },
  { id: 'system', label: 'System' },
]

export const WORKSPACE_NAVIGATION_GROUPS: readonly WorkspaceNavigationGroupPresentation[] = [
  { id: 'main', label: 'Main navigation' },
  { id: 'settings.personal', label: 'Personal settings' },
  { id: 'settings.organization', label: 'Organization settings' },
  { id: 'settings.automation', label: 'Automation settings' },
  { id: 'settings.integrations', label: 'Integration settings' },
  { id: 'settings.system', label: 'System settings' },
]

const SETTINGS_MODULE_PRESENTATION: Partial<
  Record<TrustedWorkspaceModuleId, SettingsModulePresentation>
> = {
  'settings.account': { groupId: 'personal', label: 'My account' },
  'settings.tokens': { groupId: 'personal', label: 'API tokens' },
  'settings.workspace': { groupId: 'personal', label: 'Navigation' },
  'settings.users': { groupId: 'organization', label: 'Users' },
  'settings.access': { groupId: 'organization', label: 'Access control' },
  'settings.identity': { groupId: 'organization', label: 'Single sign-on' },
  'settings.audit': { groupId: 'organization', label: 'Audit log' },
  'settings.ai': { groupId: 'automation', label: 'AI automation' },
  'settings.tagging': { groupId: 'automation', label: 'Content tagging' },
  'settings.integrations': { groupId: 'automation', label: 'Integrations' },
  'settings.integrations.webhooks': {
    groupId: 'automation',
    label: 'My webhooks',
  },
  'settings.integrations.smtp': {
    groupId: 'automation',
    label: 'Email delivery',
  },
  'settings.operations': { groupId: 'system', label: 'System health' },
}

export function settingsModulePresentation(
  moduleId: TrustedWorkspaceModuleId,
  fallbackLabel: string,
): SettingsModulePresentation {
  return (
    SETTINGS_MODULE_PRESENTATION[moduleId] ?? {
      groupId: 'organization',
      label: fallbackLabel,
    }
  )
}

export function workspaceModuleDisplayLabel(module: TrustedWorkspaceModule): string {
  if (module.section !== 'settings') return module.label
  return settingsModulePresentation(module.id, module.label).label
}

export function workspaceNavigationGroupPresentation(
  module: Pick<TrustedWorkspaceModule, 'id' | 'parentId' | 'section' | 'label'>,
): WorkspaceNavigationGroupPresentation {
  if (module.section === 'primary') return WORKSPACE_NAVIGATION_GROUPS[0]
  if (module.parentId === 'settings.integrations') {
    return WORKSPACE_NAVIGATION_GROUPS.find((group) => group.id === 'settings.integrations')!
  }

  const settingsGroup = settingsModulePresentation(module.id, module.label).groupId
  return WORKSPACE_NAVIGATION_GROUPS.find(
    (group) => group.id === `settings.${settingsGroup}`,
  )!
}

export function workspaceNavigationGroupOrder(module: TrustedWorkspaceModule): number {
  const groupId = workspaceNavigationGroupPresentation(module).id
  return WORKSPACE_NAVIGATION_GROUPS.findIndex((group) => group.id === groupId)
}

export function formatSettingsRoleLabel(role: string): string {
  if (role === 'admin') return 'Administrator'
  if (role === 'analyst') return 'Analyst'
  if (role === 'viewer') return 'Viewer'

  return role
    .replaceAll(/[_-]+/g, ' ')
    .replace(/\b\w/g, (character) => character.toUpperCase())
}
