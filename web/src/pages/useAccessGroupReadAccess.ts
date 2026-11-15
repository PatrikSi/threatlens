import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useQueryAccessScope } from '../hooks/useQueryAccessScope'
import type { IAMGroupMember, IAMGroupRoleAssignment } from '../types/api'
import { loadIAMGroupMembers, loadIAMGroupRoles, loadIAMMemberCandidates } from './accessGovernanceApi'

type MemberRemoval = { member: IAMGroupMember; groupId: string; groupRevision: number }
type RoleRemoval = { assignment: IAMGroupRoleAssignment; groupId: string; groupRevision: number }

export function useAccessGroupReadAccess(groupId: string, offset: number, search: string, canFindUsers: boolean) {
  const client = useQueryClient()
  const membersAccess = useQueryAccessScope(['governance', 'iam-groups', groupId, 'members'])
  const rolesAccess = useQueryAccessScope(['governance', 'iam-groups', groupId, 'roles'])
  const candidatesAccess = useQueryAccessScope(['users', 'directory'])
  const membersKey = ['governance', 'iam-groups', groupId || undefined, 'members', offset]
  const rolesKey = ['governance', 'iam-groups', groupId || undefined, 'roles']
  const candidatesKey = ['governance', 'iam-member-candidates', search]
  const membersRead = membersAccess.queryOptions(() => loadIAMGroupMembers(groupId, offset))
  const rolesRead = rolesAccess.queryOptions(() => loadIAMGroupRoles(groupId))
  const candidatesRead = candidatesAccess.queryOptions(() => loadIAMMemberCandidates(search))
  const membersQuery = useQuery({ queryKey: membersKey, ...membersRead, enabled: Boolean(groupId) })
  const groupRolesQuery = useQuery({ queryKey: rolesKey, ...rolesRead, enabled: Boolean(groupId) })
  const memberCandidatesQuery = useQuery({ queryKey: candidatesKey, ...candidatesRead, enabled: Boolean(groupId) && canFindUsers })
  const [memberIntent, setMemberIntent] = useState<Awaited<ReturnType<typeof membersAccess.read<MemberRemoval>>> | null>(null)
  const [roleIntent, setRoleIntent] = useState<Awaited<ReturnType<typeof rolesAccess.read<RoleRemoval>>> | null>(null)
  const [candidate, setCandidate] = useState<Awaited<ReturnType<typeof candidatesAccess.read<{ id: string; groupId: string }>>> | null>(null)
  useEffect(() => { setCandidate(null) }, [groupId])

  function currentMembers() {
    const state = client.getQueryState<Awaited<ReturnType<typeof membersRead.queryFn>>>(membersKey)
    return state?.status === 'success' && state.fetchStatus === 'idle' ? membersAccess.currentData(state.data) : undefined
  }
  function currentRoles() {
    const state = client.getQueryState<Awaited<ReturnType<typeof rolesRead.queryFn>>>(rolesKey)
    return state?.status === 'success' && state.fetchStatus === 'idle' ? rolesAccess.currentData(state.data) : undefined
  }
  function currentCandidate(id: string) {
    const selected = candidatesAccess.currentData(candidate ?? undefined)
    if (selected?.id !== id || selected.groupId !== groupId) return undefined
    const state = client.getQueryState<Awaited<ReturnType<typeof candidatesRead.queryFn>>>(candidatesKey)
    if (state?.status !== 'success' || state.fetchStatus !== 'idle') return undefined
    return candidatesAccess.currentData(state.data)?.users.find((user) => user.id === id && user.is_active && user.is_approved)
  }
  const selectedCandidate = candidatesAccess.currentData(candidate ?? undefined)
  function currentMemberRemoval() {
    const intent = membersAccess.currentData(memberIntent ?? undefined)
    return intent?.groupId === groupId ? intent : null
  }
  function currentRoleRemoval() {
    const intent = rolesAccess.currentData(roleIntent ?? undefined)
    return intent?.groupId === groupId ? intent : null
  }
  return {
    membersQuery: assignmentReadPresentation(membersQuery),
    groupRolesQuery: assignmentReadPresentation(groupRolesQuery),
    memberCandidatesQuery, currentMembers, currentRoles, currentCandidate,
    currentMemberRemoval, currentRoleRemoval,
    selectedUserId: selectedCandidate?.groupId === groupId ? selectedCandidate.id : '',
    setSelectedUserId: (id: string) => setCandidate(id ? candidatesAccess.captureResult()({ id, groupId }) : null),
    memberRemoval: currentMemberRemoval(),
    roleRemoval: currentRoleRemoval(),
    setMemberRemoval: (intent: MemberRemoval | null) => setMemberIntent(intent ? membersAccess.captureResult()(intent) : null),
    setRoleRemoval: (intent: RoleRemoval | null) => setRoleIntent(intent ? rolesAccess.captureResult()(intent) : null),
  }
}

function assignmentReadPresentation<T extends {
  data: unknown; isLoading: boolean; isFetching: boolean; isSuccess: boolean; error: unknown
}>(query: T) {
  const awaitingRead = query.data === undefined && query.isFetching
  const needsVerification = query.data === undefined && query.isSuccess && !query.isFetching
  return {
    ...query,
    isLoading: query.isLoading || awaitingRead,
    error: query.error ?? (needsVerification ? new Error('Verify current assignment access before showing saved members or grants.') : null),
  }
}
