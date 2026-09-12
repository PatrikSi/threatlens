import { useContext, useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { accessibleQueryData } from '../api/queryData'
import type { AlertOccurrence } from '../types/alerts'
import type { Team } from '../types/teams'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { AlertTriageDraftContext } from './alertTriageDraftContext'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
import { formatDateTime } from '../utils/datetime'
import { AlertAssigneePicker } from './AlertTeamSelectors'
import { deadlineDraft, deadlinePayload, type AlertDeadlineDraft } from './alertTeamTriageModel'

const buttonClassName = 'min-h-10 rounded border border-slate/30 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-white/20'

export function AlertTeamTriage({ occurrence, disabled, onUpdated }: {
  occurrence: AlertOccurrence
  disabled: boolean
  onUpdated: (updates: AlertOccurrence[]) => void
}) {
  if (!occurrence.team_id) return null
  return <TeamTriageActions key={occurrence.id} occurrence={occurrence} teamId={occurrence.team_id} disabled={disabled} onUpdated={onUpdated} />
}

function TeamTriageActions({ occurrence, teamId, disabled, onUpdated }: {
  occurrence: AlertOccurrence; teamId: string; disabled: boolean; onUpdated: (updates: AlertOccurrence[]) => void
}) {
  const user = useCurrentUser()
  const client = useQueryClient()
  const teamQuery = useQuery({
    queryKey: ['teams', teamId],
    queryFn: ({ signal }) => apiFetch<Team>(`/teams/${encodeURIComponent(teamId)}`, { signal }),
    staleTime: 30_000,
    retry: false,
  })
  const team = accessibleQueryData(teamQuery)
  const [assigning, setAssigning] = useState(false)
  const [assignee, setAssignee] = useState('')
  const [assignmentVersion, setAssignmentVersion] = useState(occurrence.version)
  const [deadline, setDeadline] = useState<AlertDeadlineDraft | null>(null)
  const [feedback, setFeedback] = useState('')
  const canWrite = !disabled && !user.isError && team?.active === true && !teamQuery.isError &&
    hasRequiredPermissions(user.data?.access?.permissions ?? [], ['write:alerts', 'write:teams', 'read:items'])
  const canManage = canWrite && team?.can_manage === true
  const isOpen = occurrence.lifecycle_state !== 'closed'
  const mutation = useMutation({
    mutationKey: ['alerts', 'occurrences', 'team-triage'],
    mutationFn: (request: { action: 'assignment' | 'deadline'; body: Record<string, unknown> }) =>
      apiFetch<AlertOccurrence>(`/alerts/occurrences/${occurrence.id}/${request.action}`, {
        method: 'PATCH', body: JSON.stringify(request.body),
      }),
    onSuccess: (updated) => {
      onUpdated([updated])
      setAssigning(false)
      setDeadline(null)
      setFeedback('Team triage updated. The activity history records this change.')
    },
    onError: () => {
      setFeedback('')
      void client.invalidateQueries({ queryKey: ['alerts', 'occurrences'] })
      void client.invalidateQueries({ queryKey: ['teams', teamId] })
    },
  })
  const pending = mutation.isPending
  const setDraftDirty = useContext(AlertTriageDraftContext)
  const draftDirty = Boolean(deadline || assigning || pending)
  useEffect(() => {
    setDraftDirty?.(draftDirty)
    return () => setDraftDirty?.(false)
  }, [draftDirty, setDraftDirty])
  const startDeadline = () => { mutation.reset(); setDeadline(deadlineDraft(occurrence)); setFeedback('') }
  const conflict = mutation.error instanceof ApiError && mutation.error.status === 409

  return <section className="space-y-3 border-b border-slate/20 px-3 py-3 dark:border-white/10 sm:px-4" aria-label="Team assignment and deadline">
    <h4 className="font-semibold">Team triage · {team?.name ?? teamId.slice(0, 8)}</h4>
    <AlertTeamStatus occurrence={occurrence} currentUserId={user.data?.id} />
    {teamQuery.isError && <p role="alert" className="text-sm text-red-700 dark:text-red-300">
      {resolveApiErrorMessage(teamQuery.error, 'Current team access could not be verified')}{' '}
      <button type="button" className="underline" onClick={() => { void teamQuery.refetch() }}>Retry team access</button>
    </p>}
    {!canWrite && !teamQuery.isLoading && !teamQuery.isError && <p className="text-xs">Assignment requires current team membership and permissions to write teams and alerts and read articles.</p>}
    <TeamActionButtons occurrence={occurrence} currentUserId={user.data?.id} canWrite={canWrite && isOpen}
      canManage={canManage} pending={pending} editing={assigning || Boolean(deadline)}
      onAssignment={(action) => mutation.mutate({ action: 'assignment', body: { action, expected_version: occurrence.version } })}
      onEditAssignment={() => {
        mutation.reset(); setAssignee(occurrence.assignee_user_id ?? ''); setAssignmentVersion(occurrence.version); setAssigning(true); setFeedback('')
      }} onEditDeadline={startDeadline} />
    {assigning && <form className="space-y-2" onSubmit={(event) => {
      event.preventDefault()
      if (canManage && isOpen && !pending) mutation.mutate({ action: 'assignment', body: {
        action: 'assign', assignee_user_id: assignee || null, expected_version: assignmentVersion,
      } })
    }}>
      <AlertAssigneePicker teamId={teamId} value={assignee} onChange={setAssignee} disabled={pending || !canManage || !isOpen} />
      <p className="text-xs">The server verifies the selected analyst still has access to this evidence.</p>
      <div className="flex gap-2">
        <button type="submit" className={buttonClassName} disabled={pending || !canManage || !isOpen}>Save assignee</button>
        <button type="button" className={buttonClassName} disabled={pending} onClick={() => { setAssigning(false); mutation.reset() }}>Cancel assignment</button>
      </div>
    </form>}
    {deadline && <AlertDeadlineForm draft={deadline} pending={pending} disabled={!canManage || !isOpen}
      onChange={setDeadline} onCancel={() => { setDeadline(null); mutation.reset() }}
      onSave={(body) => mutation.mutate({ action: 'deadline', body })} />}
    {mutation.isError && <div role="alert" className="text-sm text-red-700 dark:text-red-300">
      <p>{resolveApiErrorMessage(mutation.error, 'Team triage could not be updated')}</p>
      {conflict && <p>The occurrence changed. Review refreshed details before retrying; your draft is retained.</p>}
      {conflict && <button type="button" className="mt-2 underline" onClick={() => {
        if (deadline) setDeadline(deadlineDraft(occurrence))
        if (assigning) { setAssignee(occurrence.assignee_user_id ?? ''); setAssignmentVersion(occurrence.version) }
        mutation.reset()
      }}>Discard triage draft and use displayed version {occurrence.version}</button>}
    </div>}
    {feedback && <p role="status" className="text-sm">{feedback}</p>}
  </section>
}

export function AlertTeamStatus({ occurrence, currentUserId }: { occurrence: AlertOccurrence; currentUserId?: string }) {
  if (!occurrence.team_id) return null
  const overdue = occurrence.lifecycle_state !== 'closed' && occurrence.due_at && Date.parse(occurrence.due_at) < Date.now()
  const assignee = occurrence.assignee_user_id === currentUserId ? 'you' : occurrence.assignee_user_id?.slice(0, 8)
  return <div className="mt-1 space-y-1 text-xs">
    <p>Team queue · {assignee ? `Assigned to ${assignee}` : 'Unassigned'}</p>
    {occurrence.due_at && <p>{overdue ? 'Overdue' : 'Due'} {formatDateTime(occurrence.due_at)}</p>}
    {occurrence.escalated_at && <p className="font-semibold text-amber-800 dark:text-amber-200">Escalated {formatDateTime(occurrence.escalated_at)}</p>}
  </div>
}


function TeamActionButtons({ occurrence, currentUserId, canWrite, canManage, pending, editing, onAssignment, onEditAssignment, onEditDeadline }: {
  occurrence: AlertOccurrence; currentUserId?: string; canWrite: boolean; canManage: boolean; pending: boolean; editing: boolean;
  onAssignment: (action: 'claim' | 'unclaim') => void; onEditAssignment: () => void; onEditDeadline: () => void;
}) {
  if (!canWrite) return null
  return <div className="flex flex-wrap gap-2">
    {!occurrence.assignee_user_id && <button type="button" className={buttonClassName} disabled={pending || editing}
      onClick={() => onAssignment('claim')}>Claim occurrence</button>}
    {occurrence.assignee_user_id === currentUserId && <button type="button" className={buttonClassName} disabled={pending || editing}
      onClick={() => onAssignment('unclaim')}>Unclaim occurrence</button>}
    {canManage && <>
      <button type="button" className={buttonClassName} disabled={pending || editing} onClick={onEditAssignment}>Change assignee</button>
      <button type="button" className={buttonClassName} disabled={pending || editing} onClick={onEditDeadline}>Edit deadline</button>
    </>}
  </div>
}

function AlertDeadlineForm({ draft, pending, disabled, onChange, onCancel, onSave }: {
  draft: AlertDeadlineDraft; pending: boolean; disabled: boolean; onChange: (draft: AlertDeadlineDraft) => void;
  onCancel: () => void; onSave: (body: Record<string, unknown>) => void;
}) {
  const submission = deadlinePayload(draft)
  return <form className="space-y-2" onSubmit={(event) => {
    event.preventDefault()
    if (submission.body && !disabled && !pending) onSave(submission.body)
  }}>
    <label className="block text-sm">Due time (local time; leave empty to clear)
      <input type="datetime-local" className="mt-1 block min-h-10 w-full rounded border bg-transparent px-2"
        disabled={pending || disabled} value={draft.dueAt}
        onChange={(event) => onChange({ ...draft, dueAt: event.target.value })} />
    </label>
    <label className="block text-sm">Escalate minutes after due time (optional)
      <input type="number" min={0} max={525600} className="mt-1 block min-h-10 w-full rounded border bg-transparent px-2"
        disabled={pending || disabled} value={draft.escalationMinutes}
        onChange={(event) => onChange({ ...draft, escalationMinutes: event.target.value })} />
    </label>
    <p className="text-xs">Escalation marks overdue work for team managers and records an activity event. It does not send an external notification.</p>
    {submission.error && <p role="alert" className="text-sm text-red-700 dark:text-red-300">{submission.error}</p>}
    <div className="flex gap-2">
      <button type="submit" className={buttonClassName} disabled={pending || disabled || Boolean(submission.error)}>Save deadline</button>
      <button type="button" className={buttonClassName} disabled={pending} onClick={onCancel}>Cancel deadline</button>
    </div>
  </form>
}
