import { Plus, Trash2, UserMinus, UserPlus } from 'lucide-react'

import { resolveApiErrorMessage } from '../api/errors'
import type {
  AdminUser,
  IAMGroup,
  IAMGroupMember,
  IAMGroupRoleAssignment,
  IAMRole,
} from '../types/api'

interface MemberPickerProps {
  search: string
  onSearch: (value: string) => void
  selectedUserId: string
  onSelectUser: (userId: string) => void
  candidates: AdminUser[]
  loading: boolean
  error: unknown
  disabled: boolean
  onAdd: () => void
  onRetry: () => void
}

export function MemberPicker({
  search,
  onSearch,
  selectedUserId,
  onSelectUser,
  candidates,
  loading,
  error,
  disabled,
  onAdd,
  onRetry,
}: MemberPickerProps) {
  return (
    <div className="mt-3 rounded-lg border border-slate/20 p-3 dark:border-white/10">
      <label className="block text-xs font-semibold">
        Find an active user
        <input
          type="search"
          className="mt-1 min-h-11 w-full rounded border border-slate/25 bg-white px-3 py-2 text-sm font-normal dark:border-cyan-900/40 dark:bg-[#072019]"
          value={search}
          placeholder="Search by email"
          disabled={disabled}
          onChange={(event) => onSearch(event.target.value)}
        />
      </label>
      <div className="mt-2 flex flex-col gap-2 sm:flex-row">
        <select
          aria-label="User to add"
          className="min-h-11 min-w-0 flex-1 rounded border border-slate/25 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
          value={selectedUserId}
          disabled={disabled || loading}
          onChange={(event) => onSelectUser(event.target.value)}
        >
          <option value="">{loading ? 'Loading users…' : 'Select user'}</option>
          {candidates.map((user) => (
            <option key={user.id} value={user.id}>{user.email}</option>
          ))}
        </select>
        <button
          type="button"
          className="inline-flex min-h-11 items-center justify-center gap-2 rounded border border-slate/25 px-3 py-2 text-sm font-semibold disabled:opacity-60 dark:border-cyan-900/40"
          disabled={!selectedUserId || disabled}
          onClick={onAdd}
        >
          <UserPlus className="h-4 w-4" aria-hidden="true" />
          Add
        </button>
      </div>
      {error != null && (
        <div role="alert" className="mt-2 flex items-center justify-between gap-3 rounded border border-red-300/60 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-100">
          <span>{resolveApiErrorMessage(error, 'User candidates could not be loaded')}</span>
          <button type="button" className="shrink-0 font-semibold underline" onClick={onRetry} disabled={disabled}>Retry</button>
        </div>
      )}
    </div>
  )
}

interface MemberListProps {
  members: IAMGroupMember[] | undefined
  loading: boolean
  error: unknown
  mutableGroup: boolean
  disabled: boolean
  onRemove: (membershipId: string) => void
}

export function MemberList({
  members,
  loading,
  error,
  mutableGroup,
  disabled,
  onRemove,
}: MemberListProps) {
  if (loading) return <p className="px-3 py-4 text-sm text-slate dark:text-slate-300">Loading members…</p>
  if (error) return <InlineError error={error} fallback="Members could not be loaded" />
  if (!members?.length) return <p className="px-3 py-4 text-sm text-slate dark:text-slate-300">No direct members.</p>
  return (
    <ul className="divide-y divide-slate/15 dark:divide-white/10">
      {members.map((member) => (
        <li key={member.id} className="flex items-center justify-between gap-3 px-3 py-2.5">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold">{member.email}</p>
            <p className="mt-0.5 text-[11px] uppercase text-slate dark:text-slate-400">{memberOrigin(member)}</p>
          </div>
          {canRemoveMember(mutableGroup, member) && (
            <button
              type="button"
              className="inline-flex min-h-11 min-w-11 items-center justify-center rounded border border-slate/20 text-red-700 disabled:opacity-60 sm:min-h-0 sm:min-w-0 sm:px-2 sm:py-1 dark:border-white/10 dark:text-red-200"
              aria-label={`Remove ${member.email} from group`}
              disabled={disabled}
              onClick={() => onRemove(member.id)}
            >
              <UserMinus className="h-4 w-4" aria-hidden="true" />
            </button>
          )}
        </li>
      ))}
    </ul>
  )
}

interface GroupRolesSectionProps {
  group: IAMGroup
  mutableGroup: boolean
  busy: boolean
  dirty: boolean
  groupRoles: IAMGroupRoleAssignment[] | undefined
  groupRolesLoading: boolean
  groupRolesError: unknown
  roleCandidates: IAMRole[]
  selectedRoleId: string
  onSelectRole: (roleId: string) => void
  onAddRole: () => void
  onRemoveRole: (assignmentId: string) => void
}

export function GroupRolesSection({
  group,
  mutableGroup,
  busy,
  dirty,
  groupRoles,
  groupRolesLoading,
  groupRolesError,
  roleCandidates,
  selectedRoleId,
  onSelectRole,
  onAddRole,
  onRemoveRole,
}: GroupRolesSectionProps) {
  return (
    <section aria-labelledby="group-roles-heading">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h3 id="group-roles-heading" className="font-semibold">Role grants</h3>
          <p className="mt-1 text-xs text-slate dark:text-slate-400">Only custom, delegable role bundles can be granted through groups.</p>
        </div>
        <span className="text-xs font-semibold text-slate dark:text-slate-300">{groupRoles?.length ?? group.role_ids.length}</span>
      </div>
      {mutableGroup && (
        <RolePicker
          roles={roleCandidates}
          selectedRoleId={selectedRoleId}
          onSelectRole={onSelectRole}
          disabled={busy || dirty}
          onAdd={onAddRole}
        />
      )}
      <div className="mt-3 max-h-72 overflow-y-auto rounded border border-slate/20 dark:border-white/10">
        <RoleGrantList
          assignments={groupRoles}
          loading={groupRolesLoading}
          error={groupRolesError}
          mutableGroup={mutableGroup}
          disabled={busy || dirty}
          onRemove={onRemoveRole}
        />
      </div>
    </section>
  )
}

interface RolePickerProps {
  roles: IAMRole[]
  selectedRoleId: string
  onSelectRole: (roleId: string) => void
  disabled: boolean
  onAdd: () => void
}

function RolePicker({
  roles,
  selectedRoleId,
  onSelectRole,
  disabled,
  onAdd,
}: RolePickerProps) {
  return (
    <div className="mt-3 flex flex-col gap-2 rounded-lg border border-slate/20 p-3 sm:flex-row dark:border-white/10">
      <select
        aria-label="Role to grant"
        className="min-h-11 min-w-0 flex-1 rounded border border-slate/25 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
        value={selectedRoleId}
        disabled={disabled}
        onChange={(event) => onSelectRole(event.target.value)}
      >
        <option value="">Select custom role</option>
        {roles.map((role) => (
          <option key={role.id} value={role.id}>{role.name} ({role.key})</option>
        ))}
      </select>
      <button
        type="button"
        className="inline-flex min-h-11 items-center justify-center gap-2 rounded border border-slate/25 px-3 py-2 text-sm font-semibold disabled:opacity-60 dark:border-cyan-900/40"
        disabled={!selectedRoleId || disabled}
        onClick={onAdd}
      >
        <Plus className="h-4 w-4" aria-hidden="true" />
        Grant
      </button>
    </div>
  )
}

interface RoleGrantListProps {
  assignments: IAMGroupRoleAssignment[] | undefined
  loading: boolean
  error: unknown
  mutableGroup: boolean
  disabled: boolean
  onRemove: (assignmentId: string) => void
}

function RoleGrantList({
  assignments,
  loading,
  error,
  mutableGroup,
  disabled,
  onRemove,
}: RoleGrantListProps) {
  if (loading) return <p className="px-3 py-4 text-sm text-slate dark:text-slate-300">Loading role grants…</p>
  if (error) return <InlineError error={error} fallback="Role grants could not be loaded" />
  if (!assignments?.length) return <p className="px-3 py-4 text-sm text-slate dark:text-slate-300">No role grants.</p>
  return (
    <ul className="divide-y divide-slate/15 dark:divide-white/10">
      {assignments.map((assignment) => (
        <li key={assignment.id} className="flex items-center justify-between gap-3 px-3 py-2.5">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold">{assignment.role_name}</p>
            <p className="mt-0.5 font-mono text-[11px] text-slate dark:text-slate-400">{assignment.role_key} · revision {assignment.role_revision}</p>
          </div>
          {mutableGroup && (
            <button
              type="button"
              className="inline-flex min-h-11 min-w-11 items-center justify-center rounded border border-slate/20 text-red-700 disabled:opacity-60 sm:min-h-0 sm:min-w-0 sm:px-2 sm:py-1 dark:border-white/10 dark:text-red-200"
              aria-label={`Remove ${assignment.role_name} role from group`}
              disabled={disabled}
              onClick={() => onRemove(assignment.id)}
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
            </button>
          )}
        </li>
      ))}
    </ul>
  )
}

function InlineError({ error, fallback }: { error: unknown; fallback: string }) {
  return (
    <p role="alert" className="px-3 py-4 text-sm text-red-700 dark:text-red-200">
      {resolveApiErrorMessage(error, fallback)}
    </p>
  )
}

function memberOrigin(member: IAMGroupMember): string {
  if (member.source === 'oidc') return 'SSO derived'
  if (member.source_key === '__derived_all_users__') return 'System derived'
  return 'Local assignment'
}

function canRemoveMember(mutableGroup: boolean, member: IAMGroupMember): boolean {
  return (
    mutableGroup &&
    member.source === 'local' &&
    member.source_key !== '__derived_all_users__'
  )
}
