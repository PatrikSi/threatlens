export type PermissionRisk = 'standard' | 'elevated' | 'critical'

export interface IAMPermission {
  id: string
  group: string
  label: string
  description: string
  risk: PermissionRisk
  delegable: boolean
}

export interface IAMRole {
  id: string
  key: string
  name: string
  description: string
  permissions: string[]
  is_system: boolean
  revision: number
  assignment_count: number
  group_count: number
  created_at: string
  updated_at: string
}

export interface IAMGroup {
  id: string
  key: string
  name: string
  description: string
  source: 'local' | 'oidc'
  external_key: string | null
  is_system: boolean
  revision: number
  member_count: number
  role_ids: string[]
  created_at: string
  updated_at: string
}

export interface IAMGroupMember {
  id: string
  user_id: string
  email: string
  source: 'local' | 'oidc'
  source_key: string
  created_at: string
}

export interface IAMGroupRoleAssignment {
  id: string
  group_id: string
  role_id: string
  role_key: string
  role_name: string
  role_revision: number
  created_at: string
}

export type DataPolicyMode = 'disabled' | 'audit' | 'enforced'

export interface DataPolicyState {
  mode: DataPolicyMode
  revision: number
  coverage_version: number
  required_coverage_version: number
  enforced_at: string | null
  enforced_by_user_id: string | null
  updated_by_user_id: string | null
  updated_at: string
}

export interface DataPolicyBlocker {
  code: string
  detail: string
  count: number | null
}

export interface DataPolicyRouteManifestEvidence {
  installed: boolean
  valid: boolean
  version: number
  digest: string
  declared_operation_count: number
  validated_operation_count: number
  request_context_operation_count: number
  governance_class_counts: Record<string, number>
}

export interface DataPolicyPreflight {
  ready_for_audit: boolean
  ready_for_enforcement: boolean
  current_coverage_version: number
  required_coverage_version: number
  blockers: DataPolicyBlocker[]
  evaluated_policy_revision: number
  full: boolean
  checked_at: string
  route_manifest: DataPolicyRouteManifestEvidence
  blocker_counts: Record<string, number>
}

export interface HandlingLabel {
  id: string
  key: string
  name: string
  description: string
  color: string
  is_unrestricted: boolean
  is_system: boolean
  is_active: boolean
  revision: number
  role_ids: string[]
  assigned_feed_count: number
  created_at: string
  updated_at: string
}

export interface DataPolicyOverview {
  state: DataPolicyState
  labels: HandlingLabel[]
  preflight: DataPolicyPreflight
}

export interface HandlingLabelMutation {
  label: HandlingLabel
  policy_revision: number
  changed: boolean
}

export interface DataPolicyModeMutation {
  state: DataPolicyState
  changed: boolean
  preflight: DataPolicyPreflight
}

export interface ServiceAccountSummary {
  id: string
  key: string
  name: string
  description: string
  is_active: boolean
  revision: number
  role_ids: string[]
  effective_permissions: string[]
  credential_count: number
  active_credential_count: number
  disabled_at: string | null
  created_at: string
  updated_at: string
}

export interface ServiceAccountList {
  items: ServiceAccountSummary[]
  total: number
  page: number
  page_size: number
}

export interface AccessReviewCampaignSummary {
  id: string
  name: string
  description: string
  review_due_at: string
  is_overdue: boolean
  item_count: number
  decided_item_count: number
  revoke_item_count: number
  apply_terminal_item_count: number
  status: 'open' | 'closed' | 'applying' | 'applied' | 'cancelled' | 'quarantined'
  revision: number
  created_at: string
  updated_at: string
}

export interface AccessReviewCampaignList {
  campaigns: AccessReviewCampaignSummary[]
  total: number
  page: number
  page_size: number
}

export interface TemporaryElevationSummary {
  id: string
  target_email: string
  role_key: string
  role_name: string
  request_reason: string
  request_expires_at: string
  status: string
  revision: number
  grant_expires_at: string | null
  created_at: string
  updated_at: string
}

export interface TemporaryElevationList {
  elevations: TemporaryElevationSummary[]
  total: number
  page: number
  page_size: number
}

export interface ActionApprovalSummary {
  id: string
  action_type: string
  action_label: string
  target_type: string
  target_id: string
  requested_by_email: string
  request_reason: string
  expires_at: string
  status: string
  revision: number
  created_at: string
  updated_at: string
}

export interface ActionApprovalList {
  approvals: ActionApprovalSummary[]
  total: number
  page: number
  page_size: number
}
