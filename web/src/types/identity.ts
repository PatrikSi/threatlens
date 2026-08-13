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
}

export interface TokenResponse {
  token_type: 'session_cookie'
  csrf_token: string
}

export interface UserCreateRequest {
  email: string
  password: string
  role: 'admin' | 'analyst' | 'viewer'
  is_active: boolean
  is_approved: boolean
}

export interface UserUpdateRequest {
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

export type OIDCClientAuthMethod = 'client_secret_basic' | 'client_secret_post' | 'none'

export interface OIDCRoleMapping {
  claim_value: string
  role: User['role']
}

export interface OIDCProviderSettings {
  id: string | null
  configured: boolean
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

export interface ApiTokenCreateResponse {
  token: string
  token_prefix: string
  expires_at: string | null
}
