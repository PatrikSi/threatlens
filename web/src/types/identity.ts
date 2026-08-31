import type { EffectiveAccess } from './access'

export interface User {
  id: string
  email: string
  role: 'admin' | 'analyst' | 'viewer'
  is_active: boolean
  is_approved: boolean
  approved_at: string | null
  created_at: string
  password_login_enabled?: boolean
  provisioning_source?: 'local' | 'oidc'
}

export interface AdminUser extends User {
  password_login_enabled: boolean
  provisioning_source: 'local' | 'oidc'
  authentication_methods: Array<'password' | 'oidc'>
  oidc_provider_name: string | null
  oidc_linked_at: string | null
  oidc_last_login_at: string | null
  password_managed_by: 'local' | 'oidc'
  role_managed_by: 'local' | 'oidc'
  identity_linked?: boolean
  sso_sign_in_available?: boolean
  oidc_identity_status?:
    'not_linked' | 'linked_available' | 'linked_unavailable'
  credential_management_source?: 'local' | 'oidc'
  /** Local TOTP state for password sign-in; this does not describe OIDC assurance. */
  mfa_enabled: boolean
  mfa_confirmed_at: string | null
  /** Active opaque browser sessions tracked after the session hardening migration. */
  active_session_count: number
  /** Optimistic concurrency token for role, activation, approval, and credential changes. */
  security_version?: number
  credentials_rotated?: boolean
  revoked_api_tokens?: number
  revoked_auth_sessions?: number
}

export interface CredentialRevocationCounts {
  revoked_api_tokens?: number
  revoked_auth_sessions?: number
}

export type AdminUserUpdateResponse = AdminUser & CredentialRevocationCounts

export interface UserDirectoryResponse {
  users: AdminUser[]
  total: number
  limit: number
  offset: number
  has_more: boolean
}

export interface PasswordChangeResponse extends CredentialRevocationCounts {
  status: 'ok'
  sign_in_required?: boolean
}

export interface AppFeatures {
  ai_enabled: boolean
  ai_configured: boolean
  ai_summary_enabled: boolean
  ai_relevance_enabled: boolean
  ai_daily_brief_enabled: boolean
  ai_reporting_enabled?: boolean
}

export interface CurrentUser extends User {
  features: AppFeatures
  /** Present on servers with opaque-session recent-authentication support. */
  authentication?: CurrentAuthentication
  /** Present on servers with canonical roles, groups, and credential attenuation. */
  access?: EffectiveAccess
}

export interface CurrentAuthentication {
  credential_kind: 'opaque_session' | 'legacy_session' | 'api_token'
  session_auth_method: AuthMethod | null
  mfa_method: MFAMethod | null
  recently_authenticated: boolean
  recent_authentication_expires_at: string | null
  identity_provider_mfa_asserted: boolean
  reauthentication_endpoint: string | null
  /** Transitional server fields retained while older self-hosted releases upgrade. */
  session_id?: string | null
  recent_authentication_valid?: boolean
  security_actions_supported?: boolean
  /** Server-computed parity with sensitive mutation authentication guards. */
  sensitive_actions_ready?: boolean
  sensitive_actions_blocker?: string | null
}

export interface TokenResponse {
  token_type: 'session_cookie'
  csrf_token?: string | null
  mfa_required?: boolean | null
}

export type AuthMethod = 'local' | 'oidc'
export type MFAMethod = 'totp' | 'recovery_code' | 'external'

export interface AuthSession {
  id: string
  current: boolean
  auth_method: AuthMethod
  mfa_method: MFAMethod | null
  client_ip: string | null
  user_agent: string | null
  authenticated_at: string
  last_seen_at: string
  idle_expires_at: string
  absolute_expires_at: string
  revoked_at: string | null
  revoked_reason: string | null
}

export interface AuthSessionListResponse {
  sessions: AuthSession[]
  active_count: number
  active_truncated?: boolean
  history_truncated: boolean
}

export interface MFAStatusResponse {
  local_mfa_available: boolean
  managed_by: 'local' | 'identity_provider'
  enabled: boolean
  confirmed_at: string | null
  recovery_codes_remaining: number
}

export interface MFAEnrollmentResponse {
  secret: string
  provisioning_uri: string
}

export interface MFAEnrollmentCancelResponse {
  status: 'ok'
  cancelled: boolean
}

export interface MFARecoveryCodesResponse {
  recovery_codes: string[]
  generated_at: string
}

export interface SessionRevocationResponse {
  status: 'ok'
  revoked: boolean
  current_session_revoked: boolean
  revoked_session_count?: number
  other_sessions_revoked?: number
  auth_generation_rotated?: boolean
}

export interface SessionBulkRevocationResponse {
  status: 'ok'
  revoked_count: number
}

export interface MFADisableResponse {
  status: 'ok'
  disabled: boolean
  revoked_sessions: number
}

export interface AdminMFAResetResponse {
  status: 'ok'
  disabled: boolean
  revoked_api_tokens: number
  revoked_auth_sessions: number
}

export interface RecentAuthenticationRequest {
  current_password: string
  code?: string
}

export interface RecentAuthenticationResponse {
  verification_method: 'password' | 'password_totp'
  session_id: string
  authenticated_at: string
  valid_until: string
  status?: 'ok'
  auth_method?: 'local'
  session_rotated?: boolean
}

export interface UserCreateRequest {
  email: string
  password: string
  role: 'admin' | 'analyst' | 'viewer'
  is_active: boolean
  is_approved: boolean
}

export interface UserUpdateRequest {
  expected_security_version?: number
  email?: string
  password?: string
  role?: 'admin' | 'analyst' | 'viewer'
  is_active?: boolean
  is_approved?: boolean
}

export interface RegistrationSettingsResponse {
  allow_self_registration: boolean
  ai_enabled: boolean
}

export interface OIDCPublicSettings {
  enabled: boolean
  provider_name: string | null
}

export interface OIDCStartResponse {
  authorization_url: string
}

export interface OIDCLinkStartRequest {
  current_password: string
  code?: string
}

export type OIDCUnlinkRequest = OIDCLinkStartRequest

export interface OIDCUnlinkResponse extends CredentialRevocationCounts {
  status?: 'ok'
}

export type OIDCClientAuthMethod =
  'client_secret_basic' | 'client_secret_post' | 'none'

export interface OIDCRoleMapping {
  claim_value: string
  role: User['role']
}

export interface OIDCProviderSettings {
  id: string | null
  configured: boolean
  config_revision: number
  name: string
  enabled: boolean
  issuer_url: string
  client_id: string
  has_client_secret: boolean
  client_auth_method: OIDCClientAuthMethod
  public_base_url: string
  callback_url: string
  callback_path: string
  scopes: string[]
  role_claim: string
  role_mappings: OIDCRoleMapping[]
  default_role: User['role']
  jit_provisioning_enabled: boolean
  auto_approve_users: boolean
  require_verified_email: boolean
  sync_roles_on_login: boolean
  created_at: string | null
  updated_at: string | null
}

export interface OIDCProviderUpdateRequest {
  name: string
  enabled: boolean
  issuer_url: string
  client_id: string
  client_secret?: string
  clear_client_secret: boolean
  client_auth_method: OIDCClientAuthMethod
  public_base_url: string
  scopes: string[]
  role_claim: string
  role_mappings: OIDCRoleMapping[]
  default_role: User['role']
  jit_provisioning_enabled: boolean
  auto_approve_users: boolean
  require_verified_email: boolean
  sync_roles_on_login: boolean
  expected_config_revision?: number
}

export interface OIDCProviderTestResponse {
  status: 'ok'
  issuer: string
  authorization_endpoint: string
  token_endpoint: string
  jwks_key_count: number
}

export interface OIDCAccountStatus {
  available: boolean
  provider_name: string | null
  linked: boolean
  linked_email: string | null
  linked_at: string | null
  password_login_enabled: boolean
}

export interface ApiToken {
  id: string
  user_id: string
  name: string
  token_prefix: string
  scopes: string[]
  last_used_at: string | null
  expires_at: string | null
  revoked_at: string | null
  created_at: string
}

export interface ApiTokenListResponse {
  tokens: ApiToken[]
  total: number
  unscoped_total?: number
  page: number
  page_size: number
}

export interface ApiTokenCreateResponse {
  token: string
  token_prefix: string
  expires_at: string | null
}

export interface ApiTokenCreateRequest {
  name: string
  expires_in_days: number
  scopes?: string[]
  current_password?: string
  code?: string
}
