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

export function createOIDCDraft(settings: OIDCProviderSettings): OIDCSettingsDraft {
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

export function createOIDCRequest(draft: OIDCSettingsDraft): OIDCProviderUpdateRequest {
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
  return request
}

export function validateOIDCDraft(draft: OIDCSettingsDraft, hasStoredSecret: boolean): string | null {
  if (!draft.name.trim()) {
    return 'Enter a provider display name.'
  }
  if (draft.enabled && !draft.issuerUrl.trim()) {
    return 'Enter the OIDC issuer URL.'
  }
  if (draft.enabled && !draft.clientId.trim()) {
    return 'Enter the OIDC client ID.'
  }
  if (draft.enabled && !draft.publicBaseUrl.trim()) {
    return 'Enter the public ThreatLens URL.'
  }
  if (!parseScopes(draft.scopes).includes('openid')) {
    return 'Scopes must include openid.'
  }
  if (!draft.roleClaim.trim()) {
    return 'Enter the claim used for role mapping.'
  }
  if (
    draft.enabled &&
    draft.clientAuthMethod !== 'none' &&
    !draft.clientSecret.trim() &&
    (!hasStoredSecret || draft.clearClientSecret)
  ) {
    return 'Enter a client secret for the selected authentication method.'
  }
  const claimValues = draft.roleMappings.map((mapping) => mapping.claim_value.trim())
  if (claimValues.some((value) => !value)) {
    return 'Every role mapping must include a claim value.'
  }
  if (new Set(claimValues).size !== claimValues.length) {
    return 'Role mapping claim values must be unique.'
  }
  return null
}

export function oidcDraftFingerprint(draft: OIDCSettingsDraft): string {
  return JSON.stringify(createOIDCRequest(draft))
}

function parseScopes(value: string): string[] {
  return [...new Set(value.split(/[\s,]+/).map((scope) => scope.trim()).filter(Boolean))]
}

function resolveBrowserOrigin(): string {
  return typeof window === 'undefined' ? '' : window.location.origin
}
