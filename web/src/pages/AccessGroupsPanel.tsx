import { useCallback, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  LockKeyhole,
  Plus,
  Save,
  Search,
  Trash2,
} from 'lucide-react'

import { resolveApiErrorMessage } from '../api/errors'
import { isAmbiguousMutationError } from '../api/mutationResilience'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import type {
  AdminUser,
  IAMGroup,
  IAMGroupMember,
  IAMGroupRoleAssignment,
  IAMRole,
} from '../types/api'
import type { IAMCatalog } from './accessGovernanceApi'
import {
  GroupRolesSection,
  MemberList,
  MemberPicker,
} from './AccessGroupAssignments'
import {
  addIAMGroupMember,
  addIAMGroupRole,
  createIAMGroup,
  deleteIAMGroup,
  loadIAMGroupMembers,
  loadIAMGroupRoles,
  loadIAMMemberCandidates,
  removeIAMGroupMember,
  removeIAMGroupRole,
  updateIAMGroup,
} from './accessGovernanceApi'

interface GroupDraft {
  key: string
  name: string
  description: string
}

interface GroupDeleteRequest {
  id: string
  name: string
  revision: number
  memberCount: number
  roleCount: number
}

interface MemberRemovalRequest {
  member: IAMGroupMember
  groupId: string
  groupRevision: number
}

interface RoleRemovalRequest {
  assignment: IAMGroupRoleAssignment
  groupId: string
  groupRevision: number
}

const EMPTY_GROUP: GroupDraft = { key: '', name: '', description: '' }

export function AccessGroupsPanel({
  catalog,
  canWrite,
  canReadUsers,
  durablePermissions,
  onDirtyChange,
}: {
  catalog: IAMCatalog
  canWrite: boolean
  canReadUsers: boolean
  durablePermissions: string[]
  onDirtyChange?: (dirty: boolean) => void
}) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const {
    selectedGroupId,
    setSelectedGroupId,
    selectedGroup,
    draft,
    setDraft,
    draftRevision,
    creating,
    dirty,
    normalizedDraft,
    loadGroupDraft,
  } = useGroupDraftState(catalog.groups)
  const [deleteRequest, setDeleteRequest] = useState<GroupDeleteRequest | null>(null)
  const [memberSearch, setMemberSearch] = useState('')
  const [memberOffset, setMemberOffset] = useState(0)
  const [selectedUserId, setSelectedUserId] = useState('')
  const [selectedRoleId, setSelectedRoleId] = useState('')
  const [memberRemoval, setMemberRemoval] = useState<MemberRemovalRequest | null>(null)
  const [roleRemoval, setRoleRemoval] = useState<RoleRemovalRequest | null>(null)

  useEffect(() => {
    setMemberSearch('')
    setMemberOffset(0)
    setSelectedUserId('')
    setSelectedRoleId('')
  }, [selectedGroupId])

  useEffect(() => {
    if (!selectedGroup || memberOffset < selectedGroup.member_count) return
    setMemberOffset(
      Math.max(0, Math.floor((selectedGroup.member_count - 1) / 100) * 100),
    )
  }, [memberOffset, selectedGroup])

  const {
    canDelegateSelectedGroup,
    mutableGroup,
    canAddMember,
  } = groupMutationCapabilities({
    canWrite,
    creating,
    selectedGroup,
    roles: catalog.roles,
    durablePermissions,
  })
  const validation = groupValidation(draft, creating)
  const confirmDiscard = useUnsavedChangesWarning(
    dirty,
    'Discard the unsaved group details?',
  )

  useEffect(() => {
    onDirtyChange?.(dirty)
  }, [dirty, onDirtyChange])
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange])

  const filteredGroups = useMemo(() => {
    const needle = search.trim().toLowerCase()
    if (!needle) return catalog.groups
    return catalog.groups.filter((group) =>
      [group.name, group.key, group.description, group.external_key ?? ''].some(
        (value) => value.toLowerCase().includes(needle),
      ),
    )
  }, [catalog.groups, search])

  const membersQuery = useQuery({
    queryKey: [
      'governance',
      'iam-groups',
      selectedGroup?.id,
      'members',
      memberOffset,
    ],
    queryFn: () => loadIAMGroupMembers(selectedGroup!.id, memberOffset),
    enabled: Boolean(selectedGroup),
  })
  const groupRolesQuery = useQuery({
    queryKey: ['governance', 'iam-groups', selectedGroup?.id, 'roles'],
    queryFn: () => loadIAMGroupRoles(selectedGroup!.id),
    enabled: Boolean(selectedGroup),
  })
  const memberCandidatesQuery = useQuery({
    queryKey: ['governance', 'iam-member-candidates', memberSearch],
    queryFn: () => loadIAMMemberCandidates(memberSearch),
    enabled: Boolean(selectedGroup && canAddMember && canReadUsers && !dirty),
  })

  const refreshGroup = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['governance', 'iam-catalog'] }),
      selectedGroup
        ? queryClient.invalidateQueries({
            queryKey: ['governance', 'iam-groups', selectedGroup.id],
          })
        : Promise.resolve(),
    ])
  }

  const saveGroup = useMutation({
    mutationFn: () =>
      creating
        ? createIAMGroup(normalizedDraft)
        : updateIAMGroup(selectedGroupId as string, {
            expected_revision: draftRevision!,
            name: normalizedDraft.name,
            description: normalizedDraft.description,
          }),
    onSuccess: async (saved) => {
      await refreshGroup()
      loadGroupDraft(saved)
    },
    onError: refreshGroup,
  })
  const removeGroup = useMutation({
    mutationFn: () =>
      deleteIAMGroup(deleteRequest!.id, deleteRequest!.revision),
    onSuccess: async () => {
      setDeleteRequest(null)
      setSelectedGroupId(null)
      await queryClient.invalidateQueries({ queryKey: ['governance', 'iam-catalog'] })
    },
    onError: refreshGroup,
  })
  const addMember = useMutation({
    mutationFn: () =>
      addIAMGroupMember(selectedGroup!.id, selectedUserId, draftRevision!),
    onSuccess: async () => {
      setSelectedUserId('')
      await refreshGroup()
    },
    onError: async (error) => {
      if (isAmbiguousMutationError(error)) setSelectedUserId('')
      await refreshGroup()
    },
  })
  const removeMember = useMutation({
    mutationFn: (membershipId: string) =>
      removeIAMGroupMember(
        memberRemoval!.groupId,
        membershipId,
        memberRemoval!.groupRevision,
      ),
    onSuccess: async () => {
      setMemberRemoval(null)
      await refreshGroup()
    },
    onError: refreshGroup,
  })
  const addRole = useMutation({
    mutationFn: () => {
      const role = catalog.roles.find((candidate) => candidate.id === selectedRoleId)
      if (!role) throw new Error('Select a role before assigning it.')
      return addIAMGroupRole(selectedGroup!.id, role, draftRevision!)
    },
    onSuccess: async () => {
      setSelectedRoleId('')
      await refreshGroup()
    },
    onError: async (error) => {
      if (isAmbiguousMutationError(error)) setSelectedRoleId('')
      await refreshGroup()
    },
  })
  const removeRole = useMutation({
    mutationFn: (assignmentId: string) =>
      removeIAMGroupRole(
        roleRemoval!.groupId,
        assignmentId,
        roleRemoval!.groupRevision,
      ),
    onSuccess: async () => {
      setRoleRemoval(null)
      await refreshGroup()
    },
    onError: refreshGroup,
  })

  const busy = [
    saveGroup,
    removeGroup,
    addMember,
    removeMember,
    addRole,
    removeRole,
  ].some((mutation) => mutation.isPending)
  const mutationError =
    saveGroup.error ??
    removeGroup.error ??
    addMember.error ??
    removeMember.error ??
    addRole.error ??
    removeRole.error
  const saveOutcomeUnknown = isAmbiguousMutationError(saveGroup.error)
  const mutationOutcomeUnknown = [
    saveGroup.error,
    addMember.error,
    addRole.error,
  ].some(isAmbiguousMutationError)
  const currentMemberIds = new Set(
    (membersQuery.data ?? []).map((member) => member.user_id),
  )
  const memberCandidates = (memberCandidatesQuery.data?.users ?? []).filter(
    (user) =>
      user.is_active && user.is_approved && !currentMemberIds.has(user.id),
  )
  const assignedRoleIds = new Set(
    (groupRolesQuery.data ?? []).map((assignment) => assignment.role_id),
  )
  const roleCandidates = catalog.roles.filter(
    (role) =>
      !role.is_system &&
      rolePermissionsAreDurable(role, durablePermissions) &&
      !assignedRoleIds.has(role.id),
  )

  const chooseGroup = (groupId: string | 'new') => {
    if (busy || groupId === selectedGroupId) return
    confirmDiscard(() => {
      const nextGroup =
        groupId === 'new'
          ? null
          : catalog.groups.find((group) => group.id === groupId) ?? null
      loadGroupDraft(nextGroup ?? 'new')
      saveGroup.reset()
      removeGroup.reset()
    })
  }

  const requestGroupDelete = async () => {
    if (!selectedGroup || dirty || busy) return
    const [memberResult, roleResult] = await Promise.all([
      membersQuery.refetch(),
      groupRolesQuery.refetch(),
    ])
    removeGroup.reset()
    setDeleteRequest({
      id: selectedGroup.id,
      name: selectedGroup.name,
      revision: draftRevision!,
      memberCount: Math.max(
        selectedGroup.member_count,
        memberResult.data?.length ?? 0,
      ),
      roleCount: Math.max(
        selectedGroup.role_ids.length,
        roleResult.data?.length ?? 0,
      ),
    })
  }

  return (
    <section className="tl-surface overflow-hidden rounded-xl" aria-labelledby="groups-heading">
      <GroupsHeader canWrite={canWrite} busy={busy} onNew={() => chooseGroup('new')} />
      <div className="grid min-h-[680px] lg:grid-cols-[320px_minmax(0,1fr)]">
        <GroupsSidebar
          groups={filteredGroups}
          selectedGroupId={selectedGroupId}
          search={search}
          onSearch={setSearch}
          onChoose={chooseGroup}
        />
        <GroupWorkspace
          selectedGroupId={selectedGroupId}
          selectedGroup={selectedGroup}
          draft={draft}
          setDraft={setDraft}
          creating={creating}
          mutableGroup={mutableGroup}
          busy={busy}
          dirty={dirty}
          validation={validation}
          saving={saveGroup.isPending}
          mutationError={mutationError}
          mutationOutcomeUnknown={mutationOutcomeUnknown}
          saveBlocked={saveOutcomeUnknown}
          targetUnavailable={!creating && !selectedGroup}
          membershipDelegationBlocked={Boolean(
            selectedGroup &&
              !selectedGroup.is_system &&
              selectedGroup.source === 'local' &&
              !canDelegateSelectedGroup,
          )}
          canAddMember={canAddMember}
          onSave={() => saveGroup.mutate()}
          onDelete={() => void requestGroupDelete()}
          canReadUsers={canReadUsers}
          members={membersQuery.data}
          membersLoading={membersQuery.isLoading}
          membersError={membersQuery.error}
          memberOffset={memberOffset}
          onMemberOffsetChange={setMemberOffset}
          memberSearch={memberSearch}
          onMemberSearch={(value) => {
            setMemberSearch(value)
            setSelectedUserId('')
          }}
          selectedUserId={selectedUserId}
          onSelectUser={(userId) => {
            addMember.reset()
            setSelectedUserId(userId)
          }}
          memberCandidates={memberCandidates}
          memberCandidatesLoading={memberCandidatesQuery.isLoading}
          memberCandidatesError={memberCandidatesQuery.error}
          onRetryMemberCandidates={() => void memberCandidatesQuery.refetch()}
          onAddMember={() => addMember.mutate()}
          onRemoveMember={(membershipId) => {
            removeMember.reset()
            setMemberRemoval(
              (() => {
                const member = membersQuery.data?.find(
                  (candidate) => candidate.id === membershipId,
                )
                return member
                  ? {
                      member,
                      groupId: selectedGroup!.id,
                      groupRevision: draftRevision!,
                    }
                  : null
              })(),
            )
          }}
          groupRoles={groupRolesQuery.data}
          groupRolesLoading={groupRolesQuery.isLoading}
          groupRolesError={groupRolesQuery.error}
          roleCandidates={roleCandidates}
          selectedRoleId={selectedRoleId}
          onSelectRole={(roleId) => {
            addRole.reset()
            setSelectedRoleId(roleId)
          }}
          onAddRole={() => addRole.mutate()}
          onRemoveRole={(assignmentId) => {
            removeRole.reset()
            setRoleRemoval(
              (() => {
                const assignment = groupRolesQuery.data?.find(
                  (candidate) => candidate.id === assignmentId,
                )
                return assignment
                  ? {
                      assignment,
                      groupId: selectedGroup!.id,
                      groupRevision: draftRevision!,
                    }
                  : null
              })(),
            )
          }}
          onAcknowledgeUnknown={() => {
            if (saveOutcomeUnknown) {
              const matched = creating
                ? catalog.groups.find(
                    (group) => group.key === normalizedDraft.key,
                  )
                : selectedGroup
              if (matched) loadGroupDraft(matched)
            }
            saveGroup.reset()
            addMember.reset()
            addRole.reset()
          }}
        />
      </div>
      <ConfirmDialog
        open={deleteRequest !== null}
        title={`Delete ${deleteRequest?.name ?? 'group'}?`}
        description={`This will remove ${deleteRequest?.memberCount ?? 0} member record(s) and ${deleteRequest?.roleCount ?? 0} role grant(s). OIDC claim mappings must be detached first. This action cannot be undone.`}
        confirmLabel="Delete group"
        isConfirming={removeGroup.isPending}
        confirmDisabled={removeGroup.error != null}
        onConfirm={() => removeGroup.mutate()}
        onCancel={() => {
          setDeleteRequest(null)
          removeGroup.reset()
        }}
      >
        {removeGroup.error && (
          <p role="alert" className="rounded border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-100">
            {resolveApiErrorMessage(removeGroup.error, 'Group deletion failed')}
          </p>
        )}
      </ConfirmDialog>
      <ConfirmDialog
        open={memberRemoval !== null}
        title={`Remove ${memberRemoval?.member.email ?? 'member'}?`}
        description="This immediately removes the local group-derived access currently granted to this user. Concurrent group changes will reject the request."
        confirmLabel="Remove member"
        confirmTone="danger"
        isConfirming={removeMember.isPending}
        confirmDisabled={removeMember.error != null}
        onConfirm={() => memberRemoval && removeMember.mutate(memberRemoval.member.id)}
        onCancel={() => {
          setMemberRemoval(null)
          removeMember.reset()
        }}
      >
        {removeMember.error && (
          <p role="alert" className="rounded border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-100">
            {resolveApiErrorMessage(removeMember.error, 'Member removal failed')}
          </p>
        )}
      </ConfirmDialog>
      <ConfirmDialog
        open={roleRemoval !== null}
        title={`Remove ${roleRemoval?.assignment.role_name ?? 'role'}?`}
        description="This immediately removes this role bundle from every member of the group. Concurrent group changes will reject the request."
        confirmLabel="Remove role grant"
        confirmTone="danger"
        isConfirming={removeRole.isPending}
        confirmDisabled={removeRole.error != null}
        onConfirm={() => roleRemoval && removeRole.mutate(roleRemoval.assignment.id)}
        onCancel={() => {
          setRoleRemoval(null)
          removeRole.reset()
        }}
      >
        {removeRole.error && (
          <p role="alert" className="rounded border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-100">
            {resolveApiErrorMessage(removeRole.error, 'Role-grant removal failed')}
          </p>
        )}
      </ConfirmDialog>
      {confirmDiscard.discardDialog}
    </section>
  )
}

function GroupsHeader({
  canWrite,
  busy,
  onNew,
}: {
  canWrite: boolean
  busy: boolean
  onNew: () => void
}) {
  return (
    <header className="border-b border-slate/15 px-4 py-4 dark:border-white/10 sm:px-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 id="groups-heading" className="font-display text-xl">Groups and assignments</h2>
          <p className="mt-1 text-sm text-slate dark:text-slate-300">
            Local membership is explicit. SSO-derived groups and system membership are synchronized elsewhere.
          </p>
        </div>
        {canWrite && (
          <button type="button" className="inline-flex min-h-11 items-center justify-center gap-2 rounded bg-ink px-3 py-2 text-sm font-semibold text-white sm:min-h-0 dark:bg-cyan dark:text-[#053c2e]" onClick={onNew} disabled={busy}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            New group
          </button>
        )}
      </div>
    </header>
  )
}

function GroupsSidebar({
  groups,
  selectedGroupId,
  search,
  onSearch,
  onChoose,
}: {
  groups: IAMGroup[]
  selectedGroupId: string | 'new' | null
  search: string
  onSearch: (value: string) => void
  onChoose: (groupId: string) => void
}) {
  return (
    <aside className="border-b border-slate/15 p-3 dark:border-white/10 lg:border-b-0 lg:border-r">
      <label className="relative block">
        <span className="sr-only">Search groups</span>
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate" aria-hidden="true" />
        <input type="search" className="min-h-11 w-full rounded border border-slate/25 bg-white py-2 pl-9 pr-3 text-sm dark:border-cyan-900/40 dark:bg-[#072019]" placeholder="Search groups" value={search} onChange={(event) => onSearch(event.target.value)} />
      </label>
      <ul className="mt-3 max-h-[620px] space-y-1 overflow-y-auto" aria-label="IAM groups">
        {groups.map((group) => <li key={group.id}><GroupOption group={group} selected={group.id === selectedGroupId} onChoose={onChoose} /></li>)}
        {groups.length === 0 && <li className="px-3 py-6 text-center text-sm text-slate dark:text-slate-400">No groups match this search.</li>}
      </ul>
    </aside>
  )
}

function GroupOption({
  group,
  selected,
  onChoose,
}: {
  group: IAMGroup
  selected: boolean
  onChoose: (groupId: string) => void
}) {
  return (
    <button
      type="button"
      aria-current={selected ? 'true' : undefined}
      className={`w-full rounded-lg border px-3 py-2.5 text-left ${selected ? 'border-cyan/50 bg-cyan/10' : 'border-transparent hover:border-slate/20 hover:bg-slate/5 dark:hover:border-white/10 dark:hover:bg-white/[0.04]'}`}
      onClick={() => onChoose(group.id)}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="font-semibold text-ink dark:text-white">{group.name}</span>
        {(group.is_system || group.source === 'oidc') && <LockKeyhole className="mt-0.5 h-4 w-4 shrink-0 text-slate" aria-label="Externally managed group" />}
      </div>
      <p className="mt-0.5 font-mono text-xs text-slate dark:text-slate-400">{group.key}</p>
      <p className="mt-1 text-xs text-slate dark:text-slate-400">{group.member_count} members · {group.role_ids.length} roles · {group.source.toUpperCase()}</p>
    </button>
  )
}

interface GroupWorkspaceProps {
  selectedGroupId: string | 'new' | null
  selectedGroup: IAMGroup | null
  draft: GroupDraft
  setDraft: Dispatch<SetStateAction<GroupDraft>>
  creating: boolean
  mutableGroup: boolean
  busy: boolean
  dirty: boolean
  validation: string | null
  saving: boolean
  mutationError: unknown
  mutationOutcomeUnknown: boolean
  saveBlocked: boolean
  targetUnavailable: boolean
  membershipDelegationBlocked: boolean
  canAddMember: boolean
  onSave: () => void
  onDelete: () => void
  canReadUsers: boolean
  members: IAMGroupMember[] | undefined
  membersLoading: boolean
  membersError: unknown
  memberOffset: number
  onMemberOffsetChange: (offset: number) => void
  memberSearch: string
  onMemberSearch: (value: string) => void
  selectedUserId: string
  onSelectUser: (userId: string) => void
  memberCandidates: AdminUser[]
  memberCandidatesLoading: boolean
  memberCandidatesError: unknown
  onRetryMemberCandidates: () => void
  onAddMember: () => void
  onRemoveMember: (membershipId: string) => void
  groupRoles: IAMGroupRoleAssignment[] | undefined
  groupRolesLoading: boolean
  groupRolesError: unknown
  roleCandidates: IAMRole[]
  selectedRoleId: string
  onSelectRole: (roleId: string) => void
  onAddRole: () => void
  onRemoveRole: (assignmentId: string) => void
  onAcknowledgeUnknown: () => void
}

function GroupWorkspace(props: GroupWorkspaceProps) {
  if (props.selectedGroupId === null) {
    return <div className="min-w-0 p-4 text-sm text-slate sm:p-5 dark:text-slate-300">Select a group to inspect membership and effective role grants.</div>
  }
  return (
    <div className="min-w-0 p-4 sm:p-5">
      <GroupDetailsEditor {...props} />
      {props.selectedGroup && (
        <div className="mt-5 grid gap-5 xl:grid-cols-2">
          <GroupMembersSection {...props} group={props.selectedGroup} />
          <GroupRolesSection {...props} group={props.selectedGroup} />
        </div>
      )}
      {props.mutationError != null && (
        <div role="alert" className="mt-5 rounded border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-100">
          <p className="flex items-start gap-2"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />{resolveApiErrorMessage(props.mutationError, 'Group mutation failed')}</p>
          {props.mutationOutcomeUnknown && (
            <>
              <p className="mt-2">The outcome is unknown. Group details and assignments were refreshed; review them before attempting another write.</p>
              <button type="button" className="mt-2 font-semibold underline" onClick={props.onAcknowledgeUnknown}>Use refreshed state</button>
            </>
          )}
        </div>
      )}
    </div>
  )
}

function GroupDetailsEditor({
  selectedGroup,
  draft,
  setDraft,
  creating,
  mutableGroup,
  busy,
  dirty,
  validation,
  saving,
  saveBlocked,
  onSave,
  onDelete,
  targetUnavailable,
}: GroupWorkspaceProps) {
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault()
        if (mutableGroup && dirty && !validation && !saveBlocked) onSave()
      }}
    >
      <fieldset disabled={!mutableGroup || busy}>
        <div className="grid gap-4 md:grid-cols-2">
          <label className="text-sm font-semibold">
            Group name
            <input
              className="mt-1 min-h-11 w-full rounded border border-slate/25 bg-white px-3 py-2 font-normal dark:border-cyan-900/40 dark:bg-[#072019]"
              value={draft.name}
              onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))}
            />
          </label>
          <label className="text-sm font-semibold">
            Stable key
            <input
              className="mt-1 min-h-11 w-full rounded border border-slate/25 bg-white px-3 py-2 font-mono font-normal dark:border-cyan-900/40 dark:bg-[#072019]"
              value={draft.key}
              readOnly={!creating}
              onChange={(event) => setDraft((current) => ({ ...current, key: event.target.value }))}
            />
          </label>
          <label className="text-sm font-semibold md:col-span-2">
            Description
            <textarea
              className="mt-1 min-h-24 w-full rounded border border-slate/25 bg-white px-3 py-2 font-normal dark:border-cyan-900/40 dark:bg-[#072019]"
              value={draft.description}
              onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))}
            />
          </label>
        </div>
      </fieldset>
      {selectedGroup && !mutableGroup && <ReadOnlyGroupNotice group={selectedGroup} />}
      {targetUnavailable && (
        <p role="alert" className="mt-3 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
          This group changed or was removed while you were editing. Copy any draft details you need, then discard this draft and reload the catalog.
        </p>
      )}
      {dirty && !creating && <p className="mt-3 text-xs text-amber-800 dark:text-amber-200">Save or discard group-detail changes before editing members or role grants.</p>}
      {validation && mutableGroup && <p role="alert" className="mt-4 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">{validation}</p>}
      <div className="mt-4 flex flex-col-reverse gap-2 border-b border-slate/15 pb-5 sm:flex-row sm:justify-between dark:border-white/10">
        <div>
          {selectedGroup && mutableGroup && (
            <button type="button" className="tl-button-danger inline-flex min-h-11 items-center justify-center gap-2 rounded px-3 py-2 text-sm font-semibold sm:min-h-0" onClick={onDelete} disabled={busy || dirty}>
              <Trash2 className="h-4 w-4" aria-hidden="true" />
              Delete group
            </button>
          )}
        </div>
        <button
          type="submit"
          className="inline-flex min-h-11 items-center justify-center gap-2 rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-60 sm:min-h-0 dark:bg-cyan dark:text-[#053c2e]"
          disabled={!mutableGroup || !dirty || Boolean(validation) || busy || saveBlocked}
        >
          <Save className="h-4 w-4" aria-hidden="true" />
          {saving ? 'Saving…' : creating ? 'Create group' : 'Save revision'}
        </button>
      </div>
    </form>
  )
}

function ReadOnlyGroupNotice({ group }: { group: IAMGroup }) {
  return <div className="mt-4 flex items-start gap-2 rounded border border-slate/20 bg-slate/5 px-3 py-2 text-sm dark:border-white/10 dark:bg-white/[0.04]"><LockKeyhole className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />{readOnlyGroupReason(group)}</div>
}

function readOnlyGroupReason(group: IAMGroup): string {
  if (group.is_system) {
    return 'System group membership is derived automatically and cannot be edited.'
  }
  if (group.source === 'oidc') {
    return 'This group is controlled by its identity-provider mapping.'
  }
  return 'Your current access is read-only.'
}

function GroupMembersSection({
  group,
  mutableGroup,
  canAddMember,
  membershipDelegationBlocked,
  canReadUsers,
  busy,
  dirty,
  members,
  membersLoading,
  membersError,
  memberOffset,
  onMemberOffsetChange,
  memberSearch,
  onMemberSearch,
  selectedUserId,
  onSelectUser,
  memberCandidates,
  memberCandidatesLoading,
  memberCandidatesError,
  onRetryMemberCandidates,
  onAddMember,
  onRemoveMember,
}: GroupWorkspaceProps & { group: IAMGroup }) {
  return (
    <section aria-labelledby="group-members-heading">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h3 id="group-members-heading" className="font-semibold">Members</h3>
          <p className="mt-1 text-xs text-slate dark:text-slate-400">Direct and provider-derived membership origins remain visible.</p>
        </div>
        <span className="text-xs font-semibold text-slate dark:text-slate-300">{group.member_count} total</span>
      </div>
      {canAddMember && canReadUsers && (
        <MemberPicker
          search={memberSearch}
          onSearch={onMemberSearch}
          selectedUserId={selectedUserId}
          onSelectUser={onSelectUser}
          candidates={memberCandidates}
          loading={memberCandidatesLoading}
          error={memberCandidatesError}
          disabled={busy || dirty}
          onAdd={onAddMember}
          onRetry={onRetryMemberCandidates}
        />
      )}
      {mutableGroup && !canReadUsers && <p className="mt-3 rounded border border-slate/20 px-3 py-2 text-xs text-slate dark:border-white/10 dark:text-slate-300">User-directory permission is required to add members. Existing membership remains visible.</p>}
      {membershipDelegationBlocked && (
        <p className="mt-3 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
          Adding a member would delegate permissions outside your durable authority. You can still remove members or role grants to reduce access.
        </p>
      )}
      <div className="mt-3 max-h-72 overflow-y-auto rounded border border-slate/20 dark:border-white/10">
        <MemberList
          members={members}
          loading={membersLoading}
          error={membersError}
          mutableGroup={mutableGroup}
          disabled={busy || dirty}
          onRemove={onRemoveMember}
        />
      </div>
      {group.member_count > 100 && (
        <div className="mt-2 flex items-center justify-between gap-3 text-xs text-slate dark:text-slate-300">
          <span>
            Showing {members?.length ? memberOffset + 1 : 0}–{Math.min(memberOffset + (members?.length ?? 0), group.member_count)} of {group.member_count}
          </span>
          <div className="flex gap-2">
            <button type="button" className="rounded border border-slate/20 px-2 py-1 font-semibold disabled:opacity-50 dark:border-white/10" disabled={memberOffset === 0 || busy} onClick={() => onMemberOffsetChange(Math.max(0, memberOffset - 100))}>
              Previous
            </button>
            <button
              type="button"
              className="rounded border border-slate/20 px-2 py-1 font-semibold disabled:opacity-50 dark:border-white/10"
              disabled={memberOffset + (members?.length ?? 0) >= group.member_count || busy}
              onClick={() => onMemberOffsetChange(memberOffset + 100)}
            >
              Next
            </button>
          </div>
        </div>
      )}
    </section>
  )
}

function useGroupDraftState(groups: IAMGroup[]) {
  const initialGroup = groups[0] ?? null
  const [selectedGroupId, setSelectedGroupId] = useState<
    string | 'new' | null
  >(initialGroup?.id ?? null)
  const selectedGroup =
    selectedGroupId === 'new'
      ? null
      : groups.find((group) => group.id === selectedGroupId) ?? null
  const [draft, setDraft] = useState<GroupDraft>(() =>
    initialGroup ? groupDraft(initialGroup) : EMPTY_GROUP,
  )
  const [baselineDraft, setBaselineDraft] = useState<GroupDraft>(() =>
    initialGroup ? groupDraft(initialGroup) : EMPTY_GROUP,
  )
  const [draftRevision, setDraftRevision] = useState<number | null>(
    initialGroup?.revision ?? null,
  )
  const creating = selectedGroupId === 'new'
  const normalizedDraft = normalizeGroupDraft(draft)
  const dirty =
    JSON.stringify(normalizedDraft) !==
    JSON.stringify(normalizeGroupDraft(baselineDraft))
  const loadGroupDraft = useCallback((group: IAMGroup | 'new') => {
    const nextGroup = group === 'new' ? null : group
    const nextDraft = nextGroup ? groupDraft(nextGroup) : EMPTY_GROUP
    setSelectedGroupId(nextGroup?.id ?? 'new')
    setDraft(nextDraft)
    setBaselineDraft(nextDraft)
    setDraftRevision(nextGroup?.revision ?? null)
  }, [])

  useEffect(() => {
    if (selectedGroupId === 'new') {
      if (!dirty) loadGroupDraft('new')
      return
    }
    const current = groups.find((group) => group.id === selectedGroupId)
    if (current) {
      if (!dirty) loadGroupDraft(current)
      return
    }
    if (dirty) return
    const fallback = groups[0] ?? null
    if (fallback) {
      loadGroupDraft(fallback)
      return
    }
    setSelectedGroupId(null)
    setDraft(EMPTY_GROUP)
    setBaselineDraft(EMPTY_GROUP)
    setDraftRevision(null)
  }, [dirty, groups, loadGroupDraft, selectedGroupId])

  return {
    selectedGroupId,
    setSelectedGroupId,
    selectedGroup,
    draft,
    setDraft,
    draftRevision,
    creating,
    dirty,
    normalizedDraft,
    loadGroupDraft,
  }
}

function groupDraft(group: IAMGroup): GroupDraft {
  return { key: group.key, name: group.name, description: group.description }
}

function normalizeGroupDraft(draft: GroupDraft): GroupDraft {
  return {
    key: draft.key.trim().toLowerCase(),
    name: draft.name.trim(),
    description: draft.description.trim(),
  }
}

function groupValidation(draft: GroupDraft, creating: boolean): string | null {
  if (!draft.name.trim()) return 'Group name is required.'
  if (creating && !/^[a-z][a-z0-9-]{1,62}[a-z0-9]$/.test(draft.key.trim().toLowerCase())) {
    return 'Group key must be 3–64 lowercase letters, numbers, or hyphens and start with a letter.'
  }
  return null
}

function rolePermissionsAreDurable(
  role: IAMRole,
  durablePermissions: string[],
): boolean {
  return role.permissions.every((permission) =>
    durablePermissions.includes('*') || durablePermissions.includes(permission),
  )
}

function groupPermissionsAreDurable(
  group: IAMGroup,
  roles: IAMRole[],
  durablePermissions: string[],
): boolean {
  const groupRoles = roles.filter((role) => group.role_ids.includes(role.id))
  return groupRoles.every((role) =>
    rolePermissionsAreDurable(role, durablePermissions),
  )
}

function groupMutationCapabilities({
  canWrite,
  creating,
  selectedGroup,
  roles,
  durablePermissions,
}: {
  canWrite: boolean
  creating: boolean
  selectedGroup: IAMGroup | null
  roles: IAMRole[]
  durablePermissions: string[]
}) {
  const canDelegateSelectedGroup = selectedGroup
    ? groupPermissionsAreDurable(selectedGroup, roles, durablePermissions)
    : false
  const mutableGroup =
    canWrite &&
    (creating ||
      Boolean(
        selectedGroup &&
          !selectedGroup.is_system &&
          selectedGroup.source === 'local',
      ))
  return {
    canDelegateSelectedGroup,
    mutableGroup,
    canAddMember: mutableGroup && canDelegateSelectedGroup,
  }
}
