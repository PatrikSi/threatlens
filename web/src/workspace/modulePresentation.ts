import type { TrustedWorkspaceModuleId } from './moduleRegistry'

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

export const SETTINGS_PRESENTATION_GROUPS: readonly SettingsPresentationGroup[] = [
  { id: 'personal', label: 'Personal' },
  { id: 'organization', label: 'Organization' },
  { id: 'automation', label: 'Automation' },
  { id: 'system', label: 'System' },
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

export function formatSettingsRoleLabel(role: string): string {
  if (role === 'admin') return 'Administrator'
  if (role === 'analyst') return 'Analyst'
  if (role === 'viewer') return 'Viewer'

  return role
    .replaceAll(/[_-]+/g, ' ')
    .replace(/\b\w/g, (character) => character.toUpperCase())
}
