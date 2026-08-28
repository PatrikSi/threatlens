import {
  OIDCClientAuthMethod,
  OIDCProviderSettings,
  OIDCProviderUpdateRequest,
  OIDCRoleMapping,
  User,
} from '../types/api'

export interface OIDCSettingsDraft {
  name: string
  enabled: boolean
  issuerUrl: string
  clientId: string
  clientSecret: string
  clearClientSecret: boolean
  clientAuthMethod: OIDCClientAuthMethod
  publicBaseUrl: string
  scopes: string
  roleClaim: string
  roleMappings: OIDCRoleMapping[]
  defaultRole: User['role']
  jitProvisioningEnabled: boolean
  autoApproveUsers: boolean
  requireVerifiedEmail: boolean
  syncRolesOnLogin: boolean
}

export type OIDCValidationIssue = {
  message: string
  fieldId: string
}

export type OIDCSettingsChange = {
  field: keyof OIDCSettingsDraft
  label: string
  previous: string
  next: string
}

export type OIDCImpactWarning = {
  level: 'warning' | 'danger'
  message: string
}

export type OIDCImpactReview = {
  changes: OIDCSettingsChange[]
  warnings: OIDCImpactWarning[]
  requiresAcknowledgement: boolean
}

const OIDC_DRAFT_FIELDS: Array<{
  field: keyof OIDCSettingsDraft
  label: string
}> = [
  { field: 'name', label: 'Display name' },
  { field: 'enabled', label: 'Provider status' },
  { field: 'issuerUrl', label: 'Issuer URL' },
  { field: 'clientId', label: 'Client ID' },
  { field: 'clientSecret', label: 'Client secret' },
  { field: 'clearClientSecret', label: 'Stored client secret' },
  { field: 'clientAuthMethod', label: 'Client authentication' },
  { field: 'publicBaseUrl', label: 'Public ThreatLens URL' },
  { field: 'scopes', label: 'Scopes' },
  { field: 'roleClaim', label: 'Role claim' },
  { field: 'roleMappings', label: 'Role mappings' },
  { field: 'defaultRole', label: 'Default role' },
  { field: 'jitProvisioningEnabled', label: 'JIT provisioning' },
  { field: 'autoApproveUsers', label: 'Automatic approval' },
  { field: 'requireVerifiedEmail', label: 'Verified email requirement' },
  { field: 'syncRolesOnLogin', label: 'Role synchronization' },
]

export const DEFAULT_OIDC_DRAFT: OIDCSettingsDraft = {
  name: 'Company SSO',
  enabled: false,
  issuerUrl: '',
  clientId: '',
  clientSecret: '',
  clearClientSecret: false,
  clientAuthMethod: 'client_secret_basic',
  publicBaseUrl: '',
  scopes: 'openid profile email',
  roleClaim: 'groups',
  roleMappings: [],
  defaultRole: 'viewer',
  jitProvisioningEnabled: false,
  autoApproveUsers: false,
  requireVerifiedEmail: true,
  syncRolesOnLogin: true,
}

export function createOIDCDraft(
  settings: OIDCProviderSettings,
): OIDCSettingsDraft {
  return {
    name: settings.name,
    enabled: settings.enabled,
    issuerUrl: settings.issuer_url,
    clientId: settings.client_id,
    clientSecret: '',
    clearClientSecret: false,
    clientAuthMethod: settings.client_auth_method,
    publicBaseUrl: settings.public_base_url || resolveBrowserOrigin(),
    scopes: settings.scopes.join(' '),
    roleClaim: settings.role_claim,
    roleMappings: settings.role_mappings.map((mapping) => ({ ...mapping })),
    defaultRole: settings.default_role,
    jitProvisioningEnabled: settings.jit_provisioning_enabled,
    autoApproveUsers: settings.auto_approve_users,
    requireVerifiedEmail: settings.require_verified_email,
    syncRolesOnLogin: settings.sync_roles_on_login,
  }
}

export function createOIDCRequest(
  draft: OIDCSettingsDraft,
  expectedConfigRevision?: number,
): OIDCProviderUpdateRequest {
  const request: OIDCProviderUpdateRequest = {
    name: draft.name.trim(),
    enabled: draft.enabled,
    issuer_url: draft.issuerUrl.trim(),
    client_id: draft.clientId.trim(),
    clear_client_secret: draft.clearClientSecret,
    client_auth_method: draft.clientAuthMethod,
    public_base_url: draft.publicBaseUrl.trim().replace(/\/$/, ''),
    scopes: parseScopes(draft.scopes),
    role_claim: draft.roleClaim.trim(),
    role_mappings: draft.roleMappings.map((mapping) => ({
      claim_value: mapping.claim_value.trim(),
      role: mapping.role,
    })),
    default_role: draft.defaultRole,
    jit_provisioning_enabled: draft.jitProvisioningEnabled,
    auto_approve_users: draft.jitProvisioningEnabled && draft.autoApproveUsers,
    require_verified_email: draft.requireVerifiedEmail,
    sync_roles_on_login: draft.syncRolesOnLogin,
  }
  if (draft.clientSecret.trim()) {
    request.client_secret = draft.clientSecret
  }
  if (
    typeof expectedConfigRevision === 'number' &&
    expectedConfigRevision >= 0
  ) {
    request.expected_config_revision = expectedConfigRevision
  }
  return request
}

export function validateOIDCDraft(
  draft: OIDCSettingsDraft,
  hasStoredSecret: boolean,
): string | null {
  return validateOIDCDraftIssue(draft, hasStoredSecret)?.message ?? null
}

export function validateOIDCDraftIssue(
  draft: OIDCSettingsDraft,
  hasStoredSecret: boolean,
): OIDCValidationIssue | null {
  if (!draft.name.trim()) {
    return { message: 'Enter a provider display name.', fieldId: 'oidc-name' }
  }
  if (draft.enabled && !draft.issuerUrl.trim()) {
    return { message: 'Enter the OIDC issuer URL.', fieldId: 'oidc-issuer' }
  }
  if (draft.enabled && !draft.clientId.trim()) {
    return { message: 'Enter the OIDC client ID.', fieldId: 'oidc-client-id' }
  }
  if (draft.enabled && !draft.publicBaseUrl.trim()) {
    return {
      message: 'Enter the public ThreatLens URL.',
      fieldId: 'oidc-public-url',
    }
  }
  if (!parseScopes(draft.scopes).includes('openid')) {
    return { message: 'Scopes must include openid.', fieldId: 'oidc-scopes' }
  }
  if (!draft.roleClaim.trim()) {
    return {
      message: 'Enter the claim used for role mapping.',
      fieldId: 'oidc-role-claim',
    }
  }
  if (
    draft.enabled &&
    draft.clientAuthMethod !== 'none' &&
    !draft.clientSecret.trim() &&
    (!hasStoredSecret || draft.clearClientSecret)
  ) {
    return {
      message: 'Enter a client secret for the selected authentication method.',
      fieldId: 'oidc-client-secret',
    }
  }
  const claimValues = draft.roleMappings.map((mapping) =>
    mapping.claim_value.trim(),
  )
  const emptyMappingIndex = claimValues.findIndex((value) => !value)
  if (emptyMappingIndex >= 0) {
    return {
      message: 'Every role mapping must include a claim value.',
      fieldId: `oidc-mapping-${emptyMappingIndex}`,
    }
  }
  if (new Set(claimValues).size !== claimValues.length) {
    const duplicateIndex = claimValues.findIndex(
      (value, index) => claimValues.indexOf(value) !== index,
    )
    return {
      message: 'Role mapping claim values must be unique.',
      fieldId: `oidc-mapping-${Math.max(0, duplicateIndex)}`,
    }
  }
  return null
}

export function oidcDraftFingerprint(draft: OIDCSettingsDraft): string {
  return JSON.stringify(createOIDCRequest(draft))
}

export function diffOIDCSettings(
  previous: OIDCSettingsDraft,
  next: OIDCSettingsDraft,
): OIDCSettingsChange[] {
  return OIDC_DRAFT_FIELDS.flatMap(({ field, label }) => {
    if (oidcFieldValuesEqual(previous[field], next[field])) return []
    return [
      {
        field,
        label,
        previous: formatOIDCFieldValue(field, previous[field]),
        next: formatOIDCFieldValue(field, next[field]),
      },
    ]
  })
}

export function overlappingOIDCChanges(
  baseline: OIDCSettingsDraft,
  operatorDraft: OIDCSettingsDraft,
  serverDraft: OIDCSettingsDraft,
): OIDCSettingsChange[] {
  return OIDC_DRAFT_FIELDS.flatMap(({ field, label }) => {
    const operatorChanged = !oidcFieldValuesEqual(
      baseline[field],
      operatorDraft[field],
    )
    const serverChanged = !oidcFieldValuesEqual(
      baseline[field],
      serverDraft[field],
    )
    if (
      !operatorChanged ||
      !serverChanged ||
      oidcFieldValuesEqual(operatorDraft[field], serverDraft[field])
    ) {
      return []
    }
    return [
      {
        field,
        label,
        previous: formatOIDCFieldValue(field, serverDraft[field]),
        next: formatOIDCFieldValue(field, operatorDraft[field]),
      },
    ]
  })
}

export function rebaseOIDCDraft(
  baseline: OIDCSettingsDraft,
  operatorDraft: OIDCSettingsDraft,
  serverDraft: OIDCSettingsDraft,
): OIDCSettingsDraft {
  const rebased = cloneOIDCDraft(serverDraft)
  const writable = rebased as unknown as Record<string, unknown>
  for (const { field } of OIDC_DRAFT_FIELDS) {
    if (!oidcFieldValuesEqual(baseline[field], operatorDraft[field])) {
      writable[field] = cloneOIDCFieldValue(operatorDraft[field])
    }
  }
  return rebased
}

export function buildOIDCImpactReview(
  baseline: OIDCSettingsDraft,
  draft: OIDCSettingsDraft,
): OIDCImpactReview {
  const changes = diffOIDCSettings(baseline, draft)
  const warnings: OIDCImpactWarning[] = []
  if (baseline.enabled && !draft.enabled) {
    warnings.push({
      level: 'danger',
      message:
        'Disabling SSO can lock out linked accounts that do not have a local password.',
    })
  }
  if (draft.clearClientSecret || draft.clientAuthMethod === 'none') {
    warnings.push({
      level: 'warning',
      message:
        'The stored client secret will be removed. Confidential-client sign-in will fail until a new secret is saved.',
    })
  }
  if (!draft.requireVerifiedEmail && draft.jitProvisioningEnabled) {
    warnings.push({
      level: 'warning',
      message:
        'JIT provisioning will trust well-formed email identifiers even when the provider does not verify them.',
    })
  }
  const grantsAdmin =
    draft.defaultRole === 'admin' ||
    draft.roleMappings.some((mapping) => mapping.role === 'admin')
  if (draft.jitProvisioningEnabled && draft.autoApproveUsers && grantsAdmin) {
    warnings.push({
      level: 'danger',
      message:
        'JIT provisioning and automatic approval can grant administrator access from the configured default or claim mapping.',
    })
  }
  if (
    draft.jitProvisioningEnabled &&
    draft.autoApproveUsers &&
    !draft.requireVerifiedEmail &&
    grantsAdmin
  ) {
    warnings.push({
      level: 'danger',
      message:
        'Critical combination: an unverified email claim can be auto-approved into an administrator role.',
    })
  }
  if (
    draft.syncRolesOnLogin &&
    draft.roleMappings.some((mapping) => mapping.role === 'admin')
  ) {
    warnings.push({
      level: 'warning',
      message:
        'Mapped administrator roles will be synchronized on each SSO sign-in.',
    })
  }
  return {
    changes,
    warnings,
    requiresAcknowledgement: warnings.some(
      (warning) => warning.level === 'danger',
    ),
  }
}

function parseScopes(value: string): string[] {
  return [
    ...new Set(
      value
        .split(/[\s,]+/)
        .map((scope) => scope.trim())
        .filter(Boolean),
    ),
  ]
}

function oidcFieldValuesEqual(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right)
}

function formatOIDCFieldValue(
  field: keyof OIDCSettingsDraft,
  value: unknown,
): string {
  if (field === 'clientSecret')
    return value ? 'Replacement entered' : 'No replacement entered'
  if (field === 'clearClientSecret')
    return value ? 'Remove on save' : 'Keep stored value'
  if (field === 'roleMappings') {
    const mappings = value as OIDCRoleMapping[]
    return mappings.length
      ? mappings
          .map(
            (mapping) =>
              `${mapping.claim_value || '(blank)'} -> ${mapping.role}`,
          )
          .join(', ')
      : 'No mappings'
  }
  if (typeof value === 'boolean') return value ? 'Enabled' : 'Disabled'
  return String(value || 'Not set')
}

function cloneOIDCDraft(draft: OIDCSettingsDraft): OIDCSettingsDraft {
  return {
    ...draft,
    roleMappings: draft.roleMappings.map((mapping) => ({ ...mapping })),
  }
}

function cloneOIDCFieldValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) =>
      item && typeof item === 'object' ? { ...item } : item,
    )
  }
  return value
}

function resolveBrowserOrigin(): string {
  return typeof window === 'undefined' ? '' : window.location.origin
}
