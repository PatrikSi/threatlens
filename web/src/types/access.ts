export type PrincipalType = 'user' | 'service_account'

export interface EffectiveRole {
  id: string | null
  key: string
  name: string
  source: string
}

export interface EffectiveAccess {
  principal_type: PrincipalType
  principal_id: string
  legacy_role: 'admin' | 'analyst' | 'viewer' | null
  account_eligible: boolean
  credential_limited: boolean
  roles: EffectiveRole[]
  groups: string[]
  permissions: string[]
  policy_revision: number
}

export interface AccessExplanation {
  permission: string
  allowed: boolean
  grant_sources: string[]
  policy_revision: number
  reason:
    | 'permission_granted'
    | 'permission_not_granted'
    | 'credential_scope_missing'
    | 'account_ineligible'
}
