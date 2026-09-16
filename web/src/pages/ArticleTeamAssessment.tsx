import { useEffect, useState } from 'react'
import { skipToken, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { ApiError, apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { accessibleQueryData } from '../api/queryData'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { createBeforeUnloadHandler } from '../hooks/useUnsavedChangesWarning'
import type { TeamAssessmentResponse } from '../types/articleIntelligence'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
import { AssessmentStatus } from './AssessmentStatus'
import { AssessmentTeamPicker } from './AssessmentTeamPicker'
import { AssessmentResults, type HuntReviewDrafts } from './AssessmentResults'
import { TEAM_BUTTON } from './teamPresentation'

function denied(error: unknown) { return error instanceof ApiError && [401, 403, 404].includes(error.status) }

export function ArticleTeamAssessment({ itemId }: { itemId: string }) {
  const user = useCurrentUser()
  const identity = accessibleQueryData(user)
  const permissions = identity?.access?.permissions ?? []
  if (identity?.features.ai_enabled === undefined || !hasRequiredPermissions(permissions, ['read:teams', 'read:items'])) return null
  return <ArticleTeamAssessmentContent itemId={itemId} permissions={permissions} sessionUnavailable={user.isError} />
}

function ArticleTeamAssessmentContent({ itemId, permissions, sessionUnavailable }: { itemId: string; permissions: string[]; sessionUnavailable: boolean }) {
  const [params, setParams] = useSearchParams()
  const teamId = params.get('assessment_team') ?? ''
  return <section aria-label="Team assessment" className="tl-surface-muted mt-3 space-y-3 rounded p-3">
    <h3 className="font-semibold">Team assessment and hunt suggestions</h3>
    <p className="text-sm">Assess this article against a team's saved technology, priorities and telemetry. Suggestions need analyst review and never execute external searches.</p>
    {sessionUnavailable && <p role="status">Session verification is temporarily unavailable. Review notes are preserved; protected actions resume after verification recovers.</p>}
    <AssessmentTeamPicker value={teamId} onChange={(value) => setParams((current) => {
      const next = new URLSearchParams(current)
      if (value) next.set('assessment_team', value)
      else next.delete('assessment_team')
      return next
    })} />
    {teamId && <AssessmentWorkspace key={`${itemId}-${teamId}`} itemId={itemId} teamId={teamId} canWrite={!sessionUnavailable && hasRequiredPermissions(permissions, ['write:teams'])} canCreate={hasRequiredPermissions(permissions, ['write:investigations'])} />}
  </section>
}

type Action = { kind: 'generate' } | { kind: 'review'; huntId: string; status: 'accepted' | 'rejected'; note: string; version: number } | { kind: 'investigation'; huntId: string }

export function AssessmentWorkspace({ itemId, teamId, canWrite, canCreate }: { itemId: string; teamId: string; canWrite: boolean; canCreate: boolean }) {
  const client = useQueryClient()
  const draftKey = ['team-assessment-drafts', itemId, teamId]
  const { data: drafts = {} } = useQuery<HuntReviewDrafts>({ queryKey: draftKey, queryFn: skipToken, initialData: {}, gcTime: Infinity })
  const setDrafts = (update: HuntReviewDrafts | ((current: HuntReviewDrafts) => HuntReviewDrafts)) => client.setQueryData<HuntReviewDrafts>(draftKey, (current) => typeof update === 'function' ? update(current ?? {}) : update)
  const [notice, setNotice] = useState('')
  const queryKey = ['team-assessments', itemId, teamId]
  const path = `/items/${encodeURIComponent(itemId)}/team-assessment`
  const query = useQuery({
    queryKey,
    queryFn: ({ signal }) => apiFetch<TeamAssessmentResponse>(`${path}?${new URLSearchParams({ team_id: teamId })}`, { signal }),
    refetchInterval: (state) => ['queued', 'running'].includes(state.state.data?.assessment?.status ?? '') ? 3000 : 30_000,
  })
  const data = accessibleQueryData(query)
  const assessment = data?.assessment
  const dirty = Object.keys(drafts).length > 0
  const [confirm, setConfirm] = useState<'generate' | 'reload' | null>(null)
  useEffect(() => {
    if (!dirty) return
    const handler = createBeforeUnloadHandler('You have unsaved hunt review notes.')
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])
  const mutation = useMutation({
    mutationFn: (action: Action) => {
      const expectedVersion = action.kind === 'review' ? action.version : assessment?.version ?? 0
      const suffix = action.kind === 'generate' ? '' : `/hunts/${encodeURIComponent(action.huntId)}${action.kind === 'investigation' ? '/investigation' : ''}`
      return apiFetch<TeamAssessmentResponse>(`${path}${suffix}`, {
        method: action.kind === 'review' ? 'PATCH' : 'POST',
        body: JSON.stringify({ team_id: teamId, expected_version: expectedVersion, ...(action.kind === 'review' ? { status: action.status, note: action.note.trim() } : {}) }),
      })
    },
    onSuccess: async (saved, action) => {
      await client.cancelQueries({ queryKey })
      client.setQueryData(queryKey, saved)
      if (action.kind === 'review') setDrafts((current) => { const next = { ...current }; delete next[action.huntId]; return next })
      if (action.kind === 'generate') setDrafts({})
      if (action.kind === 'investigation') void client.invalidateQueries({ queryKey: ['investigations'] })
      setNotice(action.kind === 'generate' ? 'Team assessment queued.' : action.kind === 'review' ? 'Hunt review saved.' : 'Team investigation created and linked.')
    },
    onError: () => { void client.invalidateQueries({ queryKey }) },
  })
  const busy = ['queued', 'running'].includes(assessment?.status ?? '')
  const hidden = denied(mutation.error)
  const readOnly = !canWrite || !data?.can_generate || query.isError || Boolean(assessment?.stale) || busy
  return <div className="space-y-3">
    <ConfirmDialog open={confirm !== null} title="Discard unsaved hunt review notes?" description="Your notes have not been submitted. Continuing replaces them with the saved review state." confirmLabel="Discard changes" onCancel={() => setConfirm(null)} onConfirm={() => {
      if (confirm === 'generate') mutation.mutate({ kind: 'generate' })
      else { setDrafts({}); mutation.reset(); setNotice('Saved reviews reloaded.') }
      setConfirm(null)
    }} />
    <Link className="text-sm font-semibold text-cyan underline" to={`/teams?team=${encodeURIComponent(teamId)}&panel=ai-context`}>View team AI context</Link>
    {query.isLoading && <p role="status">Loading team assessment…</p>}
    {query.isError && <p role="alert">{resolveApiErrorMessage(query.error, 'Team assessment could not be refreshed.')} <button className={TEAM_BUTTON} onClick={() => { mutation.reset(); void query.refetch() }}>Retry assessment</button></p>}
    {mutation.isError && <p role="alert">{resolveApiErrorMessage(mutation.error, 'The assessment action failed. Review notes are preserved.')} <button className={TEAM_BUTTON} onClick={() => { mutation.reset(); void query.refetch() }}>Refresh assessment</button></p>}
    {notice && <p role="status">{notice}</p>}
    {data && !hidden && <>
      <AssessmentStatus data={data} canWrite={canWrite} />
      <button className={TEAM_BUTTON}
        disabled={!data.can_generate || !data.ai_enabled || !data.configured || !canWrite || query.isError || busy || mutation.isPending}
        onClick={() => dirty ? setConfirm('generate') : mutation.mutate({ kind: 'generate' })}>
        {mutation.isPending && mutation.variables?.kind === 'generate' ? 'Queueing assessment…'
          : assessment?.status === 'error' ? 'Retry team assessment'
            : assessment ? 'Regenerate team assessment' : 'Generate team assessment'}
      </button>
      {assessment && <AssessmentResults assessment={assessment} drafts={drafts} readOnly={readOnly} pending={mutation.isPending} canCreate={canCreate}
        onNoteChange={(hunt, note) => setDrafts((current) => ({ ...current, [hunt.id]: { note, version: current[hunt.id]?.version ?? assessment.version } }))}
        onReview={(hunt, status) => mutation.mutate({ kind: 'review', huntId: hunt.id, status, note: drafts[hunt.id]?.note ?? hunt.review_note ?? '', version: drafts[hunt.id]?.version ?? assessment.version })}
        onCreate={(hunt) => mutation.mutate({ kind: 'investigation', huntId: hunt.id })}
        onReload={() => setConfirm('reload')} />}

    </>}
  </div>
}
