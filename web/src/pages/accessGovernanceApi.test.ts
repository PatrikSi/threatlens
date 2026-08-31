import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  apiFetch: vi.fn(),
}))

import { apiFetch, ApiTransportError } from '../api/client'
import {
  addIAMGroupMember,
  addIAMGroupRole,
  createHandlingLabel,
  createIAMGroup,
  createIAMRole,
  deleteIAMGroup,
  deleteIAMRole,
  loadAccessReviews,
  loadActionApprovals,
  loadDataPolicyOverview,
  loadIAMCatalog,
  loadIAMGroupMembers,
  loadIAMGroupRoles,
  loadIAMMemberCandidates,
  loadServiceAccounts,
  loadTemporaryElevations,
  removeIAMGroupMember,
  removeIAMGroupRole,
  replaceHandlingLabelRoles,
  setHandlingLabelStatus,
  updateDataPolicyMode,
  updateHandlingLabel,
  updateIAMGroup,
  updateIAMRole,
} from './accessGovernanceApi'
import { resetPendingReportingKeys } from './reportingRequestCoordinator'

afterEach(() => {
  vi.mocked(apiFetch).mockReset()
  resetPendingReportingKeys()
})

describe('access governance API contract', () => {
  it('loads the IAM catalog and optional governance queues from their exact routes', async () => {
    vi.mocked(apiFetch).mockResolvedValue([])

    await loadIAMCatalog()
    await loadDataPolicyOverview()
    await loadServiceAccounts()
    await loadAccessReviews()
    await loadTemporaryElevations()
    await loadActionApprovals()

    expect(vi.mocked(apiFetch).mock.calls.map(([path]) => path)).toEqual([
      '/iam/permissions',
      '/iam/roles',
      '/iam/groups',
      '/iam/data-policies',
      '/iam/service-accounts?page=1&page_size=100',
      '/iam/access-reviews?page=1&page_size=100',
      '/iam/elevations?page=1&page_size=100',
      '/iam/action-approvals?page=1&page_size=100',
    ])
  })

  it('uses the canonical role, group, membership, and assignment paths', async () => {
    vi.mocked(apiFetch).mockResolvedValue({})
    const role = {
      id: 'role-1',
      key: 'incident-responder',
      name: 'Incident responder',
      description: 'Respond to incidents',
      permissions: ['read:items'],
      is_system: false,
      revision: 4,
      assignment_count: 0,
      group_count: 0,
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-01T00:00:00Z',
    }

    await createIAMRole({
      key: role.key,
      name: role.name,
      description: role.description,
      permissions: role.permissions,
    })
    await updateIAMRole(role.id, {
      expected_revision: role.revision,
      name: 'Senior responder',
      description: role.description,
      permissions: ['read:items', 'write:items'],
    })
    await deleteIAMRole(role.id, role.revision)
    await createIAMGroup({
      key: 'tier-two',
      name: 'Tier two',
      description: 'Escalated responders',
    })
    await updateIAMGroup('group-1', {
      expected_revision: 7,
      name: 'Tier 2 responders',
      description: 'Escalated responders',
    })
    await deleteIAMGroup('group-1', 7)
    await loadIAMGroupMembers('group-1')
    await addIAMGroupMember('group-1', 'user-1', 7)
    await removeIAMGroupMember('group-1', 'membership-1', 8)
    await loadIAMGroupRoles('group-1')
    await addIAMGroupRole('group-1', role, 9)
    await removeIAMGroupRole('group-1', 'assignment-1', 10)

    expect(vi.mocked(apiFetch).mock.calls.map(([path, options]) => [
      path,
      options?.method ?? 'GET',
    ])).toEqual([
      ['/iam/roles', 'POST'],
      ['/iam/roles/role-1', 'PATCH'],
      ['/iam/roles/role-1?expected_revision=4', 'DELETE'],
      ['/iam/groups', 'POST'],
      ['/iam/groups/group-1', 'PATCH'],
      ['/iam/groups/group-1?expected_revision=7', 'DELETE'],
      ['/iam/groups/group-1/members?limit=100&offset=0', 'GET'],
      ['/iam/groups/group-1/members', 'POST'],
      ['/iam/groups/group-1/members/membership-1?expected_group_revision=8', 'DELETE'],
      ['/iam/groups/group-1/role-assignments', 'GET'],
      ['/iam/groups/group-1/role-assignments', 'POST'],
      ['/iam/groups/group-1/role-assignments/assignment-1?expected_group_revision=10', 'DELETE'],
    ])
    expect(requestBody(1)).toEqual({
      expected_revision: 4,
      name: 'Senior responder',
      description: 'Respond to incidents',
      permissions: ['read:items', 'write:items'],
    })
    expect(requestBody(7)).toEqual({
      user_id: 'user-1',
      expected_group_revision: 7,
    })
    expect(requestBody(10)).toEqual({
      role_id: 'role-1',
      expected_group_revision: 9,
      expected_role_revision: 4,
    })
  })

  it('normalizes member-directory searches without changing the endpoint contract', async () => {
    vi.mocked(apiFetch).mockResolvedValue({})

    await loadIAMMemberCandidates('  analyst+east@example.com  ')
    await loadIAMMemberCandidates('   ')

    expect(apiFetch).toHaveBeenNthCalledWith(
      1,
      '/users/directory?limit=20&offset=0&q=analyst%2Beast%40example.com',
    )
    expect(apiFetch).toHaveBeenNthCalledWith(
      2,
      '/users/directory?limit=20&offset=0',
    )
  })

  it('sends every data-policy mutation to its exact route with a fresh idempotency key', async () => {
    vi.mocked(apiFetch).mockResolvedValue({})

    await createHandlingLabel({
      expected_policy_revision: 8,
      key: 'restricted',
      name: 'Restricted',
      description: 'Restricted intelligence',
      color: '#B91C1C',
      role_ids: ['role-1'],
    })
    await updateHandlingLabel('label-1', {
      expected_revision: 2,
      name: 'Restricted data',
      description: 'Restricted intelligence',
      color: '#DC2626',
    })
    await replaceHandlingLabelRoles('label-1', 3, ['role-1', 'role-2'])
    await setHandlingLabelStatus('label-1', 4, false)
    await updateDataPolicyMode({
      expected_revision: 9,
      mode: 'enforced',
      reason: 'Coverage preflight completed',
    })

    expect(vi.mocked(apiFetch).mock.calls.map(([path, options]) => [
      path,
      options?.method,
    ])).toEqual([
      ['/iam/data-policies/labels', 'POST'],
      ['/iam/data-policies/labels/label-1', 'PATCH'],
      ['/iam/data-policies/labels/label-1/role-grants', 'PUT'],
      ['/iam/data-policies/labels/label-1/status', 'PUT'],
      ['/iam/data-policies/mode', 'PUT'],
    ])
    expect(requestBody(0)).toEqual({
      expected_policy_revision: 8,
      key: 'restricted',
      name: 'Restricted',
      description: 'Restricted intelligence',
      color: '#B91C1C',
      role_ids: ['role-1'],
    })
    expect(requestBody(2)).toEqual({
      expected_revision: 3,
      role_ids: ['role-1', 'role-2'],
    })
    expect(requestBody(3)).toEqual({ expected_revision: 4, active: false })
    expect(requestBody(4)).toEqual({
      expected_revision: 9,
      mode: 'enforced',
      reason: 'Coverage preflight completed',
    })

    const keys = vi.mocked(apiFetch).mock.calls.map(([, options]) =>
      new Headers(options?.headers).get('Idempotency-Key'),
    )
    expect(keys.every((key) => Boolean(key))).toBe(true)
    expect(new Set(keys).size).toBe(keys.length)
  })

  it('reuses the retained key after an ambiguous data-policy response', async () => {
    vi.mocked(apiFetch)
      .mockRejectedValueOnce(
        new ApiTransportError(
          'The request timed out.',
          '/iam/data-policies/labels/label-1',
          'timeout',
        ),
      )
      .mockResolvedValueOnce({
        label: { id: 'label-1' },
        policy_revision: 10,
        changed: true,
      })

    const mutation = () =>
      updateHandlingLabel('label-1', {
        expected_revision: 3,
        name: 'Restricted',
        description: 'Restricted intelligence',
        color: '#B91C1C',
      })

    await expect(mutation()).rejects.toBeInstanceOf(ApiTransportError)
    await expect(mutation()).resolves.toMatchObject({ changed: true })

    const keys = vi.mocked(apiFetch).mock.calls.map(([, options]) =>
      new Headers(options?.headers).get('Idempotency-Key'),
    )
    expect(keys).toHaveLength(2)
    expect(keys[0]).toBe(keys[1])
  })
})

function requestBody(callIndex: number): unknown {
  const body = vi.mocked(apiFetch).mock.calls[callIndex]?.[1]?.body
  expect(typeof body).toBe('string')
  return JSON.parse(body as string)
}
