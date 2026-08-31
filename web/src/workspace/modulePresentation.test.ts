import { describe, expect, it } from 'vitest'

import { TRUSTED_WORKSPACE_MODULE_BY_ID, type TrustedWorkspaceModuleId } from './moduleRegistry'
import {
  SETTINGS_PRESENTATION_GROUPS,
  formatSettingsRoleLabel,
  settingsModulePresentation,
} from './modulePresentation'

describe('settings module presentation', () => {
  it('uses the enterprise settings groups in a stable order', () => {
    expect(SETTINGS_PRESENTATION_GROUPS).toEqual([
      { id: 'personal', label: 'Personal' },
      { id: 'organization', label: 'Organization' },
      { id: 'automation', label: 'Automation' },
      { id: 'system', label: 'System' },
    ])
  })

  it.each([
    ['settings.account', 'Account', 'personal', 'My account'],
    ['settings.tokens', 'API Tokens', 'personal', 'API tokens'],
    ['settings.workspace', 'Workspace', 'personal', 'Navigation'],
    ['settings.users', 'Users', 'organization', 'Users'],
    ['settings.access', 'Access', 'organization', 'Access control'],
    ['settings.identity', 'Identity', 'organization', 'Single sign-on'],
    ['settings.audit', 'Audit Logs', 'organization', 'Audit log'],
    ['settings.ai', 'AI', 'automation', 'AI automation'],
    ['settings.tagging', 'Tagging', 'automation', 'Content tagging'],
    ['settings.integrations', 'Integrations', 'automation', 'Integrations'],
    ['settings.integrations.webhooks', 'Webhooks', 'automation', 'My webhooks'],
    ['settings.integrations.smtp', 'SMTP', 'automation', 'Email delivery'],
    ['settings.operations', 'Operations', 'system', 'System health'],
  ] as const)(
    'maps %s from %s to %s / %s without changing its canonical definition',
    (moduleId, canonicalLabel, groupId, presentationLabel) => {
      const canonicalModule = TRUSTED_WORKSPACE_MODULE_BY_ID.get(moduleId)

      expect(canonicalModule?.label).toBe(canonicalLabel)
      expect(settingsModulePresentation(moduleId, canonicalLabel)).toEqual({
        groupId,
        label: presentationLabel,
      })
      expect(TRUSTED_WORKSPACE_MODULE_BY_ID.get(moduleId)?.label).toBe(canonicalLabel)
    },
  )

  it('falls back safely for a future settings module', () => {
    expect(
      settingsModulePresentation(
        'settings.future' as TrustedWorkspaceModuleId,
        'Future controls',
      ),
    ).toEqual({ groupId: 'organization', label: 'Future controls' })
  })
})

describe('settings role labels', () => {
  it.each([
    ['admin', 'Administrator'],
    ['analyst', 'Analyst'],
    ['viewer', 'Viewer'],
    ['security_admin', 'Security Admin'],
    ['incident-responder', 'Incident Responder'],
  ])('humanizes %s as %s', (role, label) => {
    expect(formatSettingsRoleLabel(role)).toBe(label)
  })
})
