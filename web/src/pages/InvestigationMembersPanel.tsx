import { FormEvent, useEffect, useState } from 'react'

import { resolveApiErrorMessage } from '../api/errors'
import type { InvestigationMember, InvestigationMemberRole } from '../types/investigations'
import { formatDateTime } from '../utils/datetime'
import { isFinalInvestigationOwner } from './investigationPageModel'
import { InvestigationConfirmDialog, InvestigationInlineMessage } from './InvestigationShared'
import type { InvestigationDetailController } from './useInvestigationDetail'

const MEMBER_ROLES: ReadonlyArray<{
  value: InvestigationMemberRole
  label: string
}> = [
  { value: 'owner', label: 'Owner' },
  { value: 'editor', label: 'Editor' },
  { value: 'viewer', label: 'Viewer' },
]

export function InvestigationMembersPanel({
  controller,
}: {
  controller: InvestigationDetailController
}) {
  const detail = controller.detailQuery.data
  const [selectedUserId, setSelectedUserId] = useState('')
  const [newMemberRole, setNewMemberRole] = useState<InvestigationMemberRole>('viewer')
  const [pendingRemoval, setPendingRemoval] = useState<InvestigationMember | null>(null)
  const [pendingRoleChange, setPendingRoleChange] = useState<{
    member: InvestigationMember
    role: InvestigationMemberRole
  } | null>(null)
  if (!detail || !controller.access) return null

  const candidateResponse = controller.memberCandidatesQuery.data
  const candidatePages = Math.max(
    1,
    Math.ceil((candidateResponse?.total ?? 0) / (candidateResponse?.page_size ?? 20)),
  )
  const removalError =
    pendingRemoval &&
    controller.mutation.isError &&
    controller.mutation.variables?.kind === 'remove-member' &&
    controller.mutation.variables.userId === pendingRemoval.user_id
      ? resolveApiErrorMessage(controller.mutation.error, 'Member could not be removed', {
          retryGuidance: 'Review the member list and try again.',
        })
      : null
  const roleChangeError =
    pendingRoleChange &&
    controller.mutation.isError &&
    controller.mutation.variables?.kind === 'update-member' &&
    controller.mutation.variables.userId === pendingRoleChange.member.user_id
      ? resolveApiErrorMessage(controller.mutation.error, 'Member role could not be changed', {
          retryGuidance: 'Review the member list and try again.',
        })
      : null

  const addMember = (event: FormEvent) => {
    event.preventDefault()
    if (!selectedUserId) return
    controller.mutation.mutate(
      { kind: 'add-member', userId: selectedUserId, role: newMemberRole },
      { onSuccess: () => setSelectedUserId('') },
    )
  }

  return (
    <section aria-labelledby="investigation-members-heading" className="min-w-0">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 id="investigation-members-heading" className="text-base font-semibold">
            Members ({detail.member_count})
          </h2>
          <p className="mt-0.5 text-sm text-slate dark:text-slate-300">
            Owners govern access; editors can work the investigation; viewers can follow it.
          </p>
        </div>
      </div>

      {controller.access.canManageMembers && (
        <form
          className="mt-4 border-y border-slate/15 py-3 dark:border-white/10"
          onSubmit={addMember}
        >
          <div className="grid min-w-0 gap-2 md:grid-cols-[minmax(0,1fr)_180px_auto] md:items-end">
            <div className="min-w-0">
              <label htmlFor="investigation-member-search" className="text-sm font-semibold">
                Find an active account
              </label>
              <input
                id="investigation-member-search"
                type="search"
                maxLength={255}
                className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                value={controller.memberSearch}
                onChange={(event) => controller.setMemberSearch(event.target.value)}
                placeholder="Search by email"
              />
            </div>
            <div>
              <label htmlFor="investigation-new-member-role" className="text-sm font-semibold">
                Investigation role
              </label>
              <select
                id="investigation-new-member-role"
                className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={newMemberRole}
                onChange={(event) =>
                  setNewMemberRole(event.target.value as InvestigationMemberRole)
                }
              >
                {MEMBER_ROLES.map((role) => (
                  <option key={role.value} value={role.value}>
                    {role.label}
                  </option>
                ))}
              </select>
            </div>
            <button
              type="submit"
              className="min-h-11 rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e]"
              disabled={!selectedUserId || controller.mutation.isPending}
            >
              Add member
            </button>
          </div>

          <CandidateResults
            controller={controller}
            selectedUserId={selectedUserId}
            onSelect={setSelectedUserId}
          />

          {candidatePages > 1 && (
            <div className="mt-2 flex items-center justify-between gap-2 text-sm">
              <button
                type="button"
                className="min-h-11 rounded border border-slate/20 px-3 py-2 font-semibold disabled:opacity-50 md:min-h-0 md:py-1 dark:border-white/10"
                disabled={controller.memberPage <= 1 || controller.memberCandidatesQuery.isFetching}
                onClick={() => controller.setMemberPage((page) => page - 1)}
              >
                Previous accounts
              </button>
              <span>
                Page {controller.memberPage} of {candidatePages}
              </span>
              <button
                type="button"
                className="min-h-11 rounded border border-slate/20 px-3 py-2 font-semibold disabled:opacity-50 md:min-h-0 md:py-1 dark:border-white/10"
                disabled={
                  controller.memberPage >= candidatePages ||
                  controller.memberCandidatesQuery.isFetching
                }
                onClick={() => controller.setMemberPage((page) => page + 1)}
              >
                Next accounts
              </button>
            </div>
          )}
        </form>
      )}

      {!controller.access.canManageMembers && detail.status !== 'archived' && (
        <InvestigationInlineMessage tone="info">
          Only an investigation owner can add, remove, or change members.
        </InvestigationInlineMessage>
      )}

      <MembersList
        members={detail.members}
        currentUserId={controller.currentUserQuery.data?.id}
        canManage={controller.access.canManageMembers}
        pending={controller.mutation.isPending}
        onRoleChange={(member, role) => {
          if (isAccessReducingRoleChange(member.role, role)) {
            setPendingRoleChange({ member, role })
            return
          }
          controller.mutation.mutate({
            kind: 'update-member',
            userId: member.user_id,
            role,
          })
        }}
        onRemove={setPendingRemoval}
      />

      <InvestigationConfirmDialog
        open={Boolean(pendingRoleChange)}
        title="Reduce investigation access?"
        description={
          pendingRoleChange
            ? `Change ${pendingRoleChange.member.email} from ${roleLabel(pendingRoleChange.member.role)} to ${roleLabel(pendingRoleChange.role)}? They will immediately lose the permissions provided by their current role.`
            : undefined
        }
        confirmLabel="Change member role"
        isConfirming={controller.mutation.isPending}
        error={roleChangeError}
        onCancel={() => setPendingRoleChange(null)}
        onConfirm={() => {
          if (!pendingRoleChange) return
          controller.mutation.mutate(
            {
              kind: 'update-member',
              userId: pendingRoleChange.member.user_id,
              role: pendingRoleChange.role,
            },
            { onSuccess: () => setPendingRoleChange(null) },
          )
        }}
      />

      <InvestigationConfirmDialog
        open={Boolean(pendingRemoval)}
        title="Remove investigation member?"
        description={
          pendingRemoval
            ? `Remove ${pendingRemoval.email} from this investigation? Their account and other access will not be changed.`
            : undefined
        }
        confirmLabel="Remove member"
        isConfirming={controller.mutation.isPending}
        error={removalError}
        onCancel={() => setPendingRemoval(null)}
        onConfirm={() => {
          if (!pendingRemoval) return
          controller.mutation.mutate(
            { kind: 'remove-member', userId: pendingRemoval.user_id },
            { onSuccess: () => setPendingRemoval(null) },
          )
        }}
      />
    </section>
  )
}

function CandidateResults({
  controller,
  selectedUserId,
  onSelect,
}: {
  controller: InvestigationDetailController
  selectedUserId: string
  onSelect: (userId: string) => void
}) {
  const query = controller.memberCandidatesQuery
  const candidates = controller.availableMemberCandidates

  useEffect(() => {
    if (selectedUserId && !candidates.some((candidate) => candidate.id === selectedUserId))
      onSelect('')
  }, [candidates, onSelect, selectedUserId])

  if (query.isLoading)
    return (
      <p role="status" className="mt-3 text-sm text-slate dark:text-slate-300">
        Loading member candidates...
      </p>
    )
  if (query.isError && !query.data)
    return (
      <p role="alert" className="mt-3 text-sm text-red-700 dark:text-red-200">
        {resolveApiErrorMessage(query.error, 'Member candidates could not be loaded')}
      </p>
    )
  return (
    <div className="mt-3">
      {query.isFetching && query.data && (
        <p role="status" className="mb-2 text-xs text-slate dark:text-slate-400">
          Updating account candidates...
        </p>
      )}
      {query.isError && query.data && (
        <p role="alert" className="mb-2 text-sm text-amber-800 dark:text-amber-200">
          {resolveApiErrorMessage(query.error, 'Member candidates could not be refreshed')} Last
          loaded candidates remain visible.
        </p>
      )}
      {candidates.length > 0 ? (
        <fieldset className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
          <legend className="sr-only">Select an account to add</legend>
          {candidates.map((candidate) => (
            <label
              key={candidate.id}
              className={`flex min-h-11 min-w-0 cursor-pointer items-center gap-2 rounded border px-2 py-2 text-sm ${selectedUserId === candidate.id ? 'border-cyan bg-cyan/10' : 'border-slate/20 dark:border-white/10'}`}
            >
              <input
                type="radio"
                name="investigation-member-candidate"
                className="accent-cyan"
                value={candidate.id}
                checked={selectedUserId === candidate.id}
                onChange={() => onSelect(candidate.id)}
              />
              <span className="min-w-0">
                <span className="block truncate font-semibold">{candidate.email}</span>
                <span className="block text-xs capitalize text-slate dark:text-slate-400">
                  ThreatLens {candidate.account_role}
                </span>
              </span>
            </label>
          ))}
        </fieldset>
      ) : (
        <p className="text-sm text-slate dark:text-slate-300">
          {(query.data?.users.length ?? 0) > 0
            ? 'All accounts on this page are already members. Continue to another page or refine the search.'
            : controller.memberSearch.trim()
              ? 'No active, approved accounts match this search.'
              : 'No additional active, approved accounts are available.'}
        </p>
      )}
    </div>
  )
}

function MembersList({
  members,
  currentUserId,
  canManage,
  pending,
  onRoleChange,
  onRemove,
}: {
  members: InvestigationMember[]
  currentUserId: string | undefined
  canManage: boolean
  pending: boolean
  onRoleChange: (member: InvestigationMember, role: InvestigationMemberRole) => void
  onRemove: (member: InvestigationMember) => void
}) {
  return (
    <div className="mt-4 divide-y divide-slate/15 border-y border-slate/15 dark:divide-white/10 dark:border-white/10">
      {members.map((member) => {
        const finalOwner = isFinalInvestigationOwner(members, member.user_id)
        const finalOwnerDescriptionId = `investigation-final-owner-${member.user_id}`
        return (
          <article
            key={member.user_id}
            className="grid min-w-0 gap-2 py-3 md:grid-cols-[minmax(0,1fr)_150px_130px] md:items-center"
          >
            <div className="min-w-0">
              <p className="break-all font-semibold">
                {member.email}
                {member.user_id === currentUserId ? ' (you)' : ''}
              </p>
              <p className="mt-0.5 text-xs text-slate dark:text-slate-400">
                Added <time dateTime={member.created_at}>{formatDateTime(member.created_at)}</time>
              </p>
              {canManage && finalOwner && (
                <p
                  id={finalOwnerDescriptionId}
                  className="mt-1 text-xs font-medium text-amber-800 dark:text-amber-200"
                >
                  This is the final owner. Add another owner before changing or removing this member.
                </p>
              )}
            </div>
            {canManage ? (
              <div>
                <label htmlFor={`investigation-member-role-${member.user_id}`} className="sr-only">
                  Role for {member.email}
                </label>
                <select
                  id={`investigation-member-role-${member.user_id}`}
                  className="min-h-11 w-full rounded border border-slate/30 bg-white px-2 py-2 text-sm disabled:opacity-60 md:min-h-0 md:py-1.5 dark:border-cyan-900/40 dark:bg-[#072019]"
                  value={member.role}
                  disabled={pending || finalOwner}
                  aria-describedby={finalOwner ? finalOwnerDescriptionId : undefined}
                  onChange={(event) =>
                    onRoleChange(member, event.target.value as InvestigationMemberRole)
                  }
                >
                  {MEMBER_ROLES.map((role) => (
                    <option key={role.value} value={role.value}>
                      {role.label}
                    </option>
                  ))}
                </select>
              </div>
            ) : (
              <span className="text-sm capitalize">{member.role}</span>
            )}
            {canManage ? (
              <button
                type="button"
                className="min-h-11 rounded border border-slate/20 px-3 py-2 text-sm font-semibold text-red-700 disabled:opacity-50 md:min-h-0 md:py-1.5 dark:border-white/10 dark:text-red-300"
                disabled={pending || finalOwner}
                aria-describedby={finalOwner ? finalOwnerDescriptionId : undefined}
                aria-label={
                  finalOwner
                    ? `${member.email} is the final investigation owner`
                    : `Remove ${member.email} from investigation`
                }
                onClick={() => onRemove(member)}
              >
                {finalOwner ? 'Final owner' : 'Remove'}
              </button>
            ) : (
              <span className="text-xs text-slate dark:text-slate-400">Read-only</span>
            )}
          </article>
        )
      })}
    </div>
  )
}

function isAccessReducingRoleChange(
  currentRole: InvestigationMemberRole,
  nextRole: InvestigationMemberRole,
): boolean {
  const rank: Record<InvestigationMemberRole, number> = {
    viewer: 1,
    editor: 2,
    owner: 3,
  }
  return rank[nextRole] < rank[currentRole]
}

function roleLabel(role: InvestigationMemberRole): string {
  return MEMBER_ROLES.find((candidate) => candidate.value === role)?.label ?? role
}
