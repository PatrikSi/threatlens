import { apiFetch } from '../api/client'
import type {
  ActionApprovalList,
  AccessReviewCampaignList,
  DataPolicyMode,
  DataPolicyModeMutation,
  DataPolicyOverview,
  HandlingLabelMutation,
  IAMGroup,
  IAMGroupMember,
  IAMGroupRoleAssignment,
  IAMPermission,
  IAMRole,
  ServiceAccountList,
  TemporaryElevationList,
  UserDirectoryResponse,
} from '../types/api'
import { idempotentReportingFetch } from './reportingApi'

export interface IAMCatalog {
  permissions: IAMPermission[]
  roles: IAMRole[]
  groups: IAMGroup[]
}

export function loadIAMCatalog(): Promise<IAMCatalog> {
  return Promise.all([
    apiFetch<IAMPermission[]>('/iam/permissions'),
    apiFetch<IAMRole[]>('/iam/roles'),
    apiFetch<IAMGroup[]>('/iam/groups'),
  ]).then(([permissions, roles, groups]) => ({ permissions, roles, groups }))
}

export function createIAMRole(payload: {
  key: string
  name: string
  description: string
  permissions: string[]
}): Promise<IAMRole> {
  return apiFetch<IAMRole>('/iam/roles', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateIAMRole(
  roleId: string,
  payload: {
    expected_revision: number
    name: string
    description: string
    permissions: string[]
  },
): Promise<IAMRole> {
  return apiFetch<IAMRole>(`/iam/roles/${roleId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function deleteIAMRole(
  roleId: string,
  expectedRevision: number,
): Promise<void> {
  return apiFetch<void>(
    `/iam/roles/${roleId}?expected_revision=${expectedRevision}`,
    { method: 'DELETE' },
  )
}

export function createIAMGroup(payload: {
  key: string
  name: string
  description: string
}): Promise<IAMGroup> {
  return apiFetch<IAMGroup>('/iam/groups', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateIAMGroup(
  groupId: string,
  payload: { expected_revision: number; name: string; description: string },
): Promise<IAMGroup> {
  return apiFetch<IAMGroup>(`/iam/groups/${groupId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function deleteIAMGroup(
  groupId: string,
  expectedRevision: number,
): Promise<void> {
  return apiFetch<void>(
    `/iam/groups/${groupId}?expected_revision=${expectedRevision}`,
    { method: 'DELETE' },
  )
}

export function loadIAMGroupMembers(
  groupId: string,
  offset = 0,
): Promise<IAMGroupMember[]> {
  return apiFetch<IAMGroupMember[]>(
    `/iam/groups/${groupId}/members?limit=100&offset=${offset}`,
  )
}

export function addIAMGroupMember(
  groupId: string,
  userId: string,
  expectedGroupRevision: number,
): Promise<IAMGroupMember> {
  return apiFetch<IAMGroupMember>(`/iam/groups/${groupId}/members`, {
    method: 'POST',
    body: JSON.stringify({
      user_id: userId,
      expected_group_revision: expectedGroupRevision,
    }),
  })
}

export function removeIAMGroupMember(
  groupId: string,
  membershipId: string,
  expectedGroupRevision: number,
): Promise<void> {
  return apiFetch<void>(
    `/iam/groups/${groupId}/members/${membershipId}?expected_group_revision=${expectedGroupRevision}`,
    { method: 'DELETE' },
  )
}

export function loadIAMGroupRoles(
  groupId: string,
): Promise<IAMGroupRoleAssignment[]> {
  return apiFetch<IAMGroupRoleAssignment[]>(
    `/iam/groups/${groupId}/role-assignments`,
  )
}

export function addIAMGroupRole(
  groupId: string,
  role: IAMRole,
  expectedGroupRevision: number,
): Promise<IAMGroup> {
  return apiFetch<IAMGroup>(`/iam/groups/${groupId}/role-assignments`, {
    method: 'POST',
    body: JSON.stringify({
      role_id: role.id,
      expected_group_revision: expectedGroupRevision,
      expected_role_revision: role.revision,
    }),
  })
}

export function removeIAMGroupRole(
  groupId: string,
  assignmentId: string,
  expectedGroupRevision: number,
): Promise<void> {
  return apiFetch<void>(
    `/iam/groups/${groupId}/role-assignments/${assignmentId}?expected_group_revision=${expectedGroupRevision}`,
    { method: 'DELETE' },
  )
}

export function loadIAMMemberCandidates(
  query: string,
): Promise<UserDirectoryResponse> {
  const params = new URLSearchParams({ limit: '20', offset: '0' })
  if (query.trim()) params.set('q', query.trim())
  return apiFetch<UserDirectoryResponse>(`/users/directory?${params.toString()}`)
}

export function loadDataPolicyOverview(): Promise<DataPolicyOverview> {
  return apiFetch<DataPolicyOverview>('/iam/data-policies')
}

export function createHandlingLabel(payload: {
  expected_policy_revision: number
  key: string
  name: string
  description: string
  color: string
  role_ids: string[]
}): Promise<HandlingLabelMutation> {
  return governedMutation<HandlingLabelMutation>('/iam/data-policies/labels', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateHandlingLabel(
  labelId: string,
  payload: {
    expected_revision: number
    name: string
    description: string
    color: string
  },
): Promise<HandlingLabelMutation> {
  return governedMutation<HandlingLabelMutation>(
    `/iam/data-policies/labels/${labelId}`,
    { method: 'PATCH', body: JSON.stringify(payload) },
  )
}

export function replaceHandlingLabelRoles(
  labelId: string,
  expectedRevision: number,
  roleIds: string[],
): Promise<HandlingLabelMutation> {
  return governedMutation<HandlingLabelMutation>(
    `/iam/data-policies/labels/${labelId}/role-grants`,
    {
      method: 'PUT',
      body: JSON.stringify({
        expected_revision: expectedRevision,
        role_ids: roleIds,
      }),
    },
  )
}

export function setHandlingLabelStatus(
  labelId: string,
  expectedRevision: number,
  active: boolean,
): Promise<HandlingLabelMutation> {
  return governedMutation<HandlingLabelMutation>(
    `/iam/data-policies/labels/${labelId}/status`,
    {
      method: 'PUT',
      body: JSON.stringify({ expected_revision: expectedRevision, active }),
    },
  )
}

export function updateDataPolicyMode(payload: {
  expected_revision: number
  mode: DataPolicyMode
  reason: string
}): Promise<DataPolicyModeMutation> {
  return governedMutation<DataPolicyModeMutation>('/iam/data-policies/mode', {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function loadServiceAccounts(): Promise<ServiceAccountList> {
  return apiFetch<ServiceAccountList>('/iam/service-accounts?page=1&page_size=100')
}

export function loadAccessReviews(): Promise<AccessReviewCampaignList> {
  return apiFetch<AccessReviewCampaignList>('/iam/access-reviews?page=1&page_size=100')
}

export function loadTemporaryElevations(): Promise<TemporaryElevationList> {
  return apiFetch<TemporaryElevationList>(
    '/iam/elevations?page=1&page_size=100',
  )
}

export function loadActionApprovals(): Promise<ActionApprovalList> {
  return apiFetch<ActionApprovalList>('/iam/action-approvals?page=1&page_size=100')
}

function governedMutation<T>(path: string, options: RequestInit): Promise<T> {
  const scope = JSON.stringify([
    'data-policy',
    options.method ?? 'POST',
    path,
    typeof options.body === 'string' ? options.body : '',
  ])
  return idempotentReportingFetch<T>(
    path,
    scope,
    options,
    (value) => value as T,
  )
}
