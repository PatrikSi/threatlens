import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { accessibleQueryData } from '../api/queryData'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { AssessmentWorkspace } from '../pages/ArticleTeamAssessment'
import { HUNT_DRAFT_PREFIX, huntDraftEntries } from '../pages/huntReviewDrafts'
import { TEAM_BUTTON } from '../pages/teamPresentation'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
import { ConfirmDialog, DialogSurface } from './ConfirmDialog'
import { useHuntDraftScopes } from '../hooks/useHuntDraftCount'

export function HuntDraftRecovery() {
  const client = useQueryClient()
  const scopes = useHuntDraftScopes()
  const count = scopes.reduce((total, entry) => total + entry.count, 0)
  const user = useCurrentUser()
  const identity = accessibleQueryData(user)
  const permissions = identity?.access?.permissions ?? []
  const canRead = hasRequiredPermissions(permissions, ['read:items', 'read:teams'])
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState<{ itemId: string; teamId: string } | null>(null)
  const [discard, setDiscard] = useState(false)
  useEffect(() => {
    // Verified permission loss removes retained content; a temporary verification
    // outage only disables writes and keeps the session's unsaved work.
    if (identity && !user.isError && !canRead) client.removeQueries({ queryKey: [HUNT_DRAFT_PREFIX] })
  }, [identity, user.isError, canRead, client])
  if (!canRead || !count && !open) return null
  return <>
    <div className="tl-surface-muted flex flex-wrap items-center gap-3 px-3 py-2 text-sm" role="region" aria-label="Unsaved hunt reviews">
      <p>{count} unsaved hunt review {count === 1 ? 'note' : 'notes'}. Kept until you refresh, close the tab or sign out.</p>
      <button type="button" className={TEAM_BUTTON} onClick={() => setOpen(true)}>Resume hunt reviews</button>
    </div>
    <DialogSurface open={open} title="Unsaved hunt reviews" panelClassName="max-w-4xl" describeBody={false}
      description="Review or discard notes retained in this signed-in session. Access is checked again before notes are shown."
      onClose={() => { setOpen(false); setSelected(null) }}>
      {user.isError && <p role="status">Session verification is unavailable. Editing is disabled until verification recovers.</p>}
      <ul className="space-y-2">
        {scopes.map(({ key, count: scopeCount }) => <li key={JSON.stringify(key)}>
          <button type="button" className={TEAM_BUTTON} aria-pressed={selected?.itemId === key[1] && selected?.teamId === key[2]}
            onClick={() => setSelected({ itemId: String(key[1]), teamId: String(key[2]) })}>
            Article {String(key[1])} · Team {String(key[2])} · {scopeCount} unsaved
          </button>
        </li>)}
      </ul>
      {!count && <p role="status">No unsaved hunt review notes remain.</p>}
      {selected && <AssessmentWorkspace key={`${selected.itemId}-${selected.teamId}`} {...selected} verifyAccess
        canWrite={!user.isError && hasRequiredPermissions(permissions, ['write:teams'])}
        canCreate={!user.isError && hasRequiredPermissions(permissions, ['write:investigations'])} />}
      {count > 0 && <button type="button" className={TEAM_BUTTON} onClick={() => setDiscard(true)}>Discard all hunt review drafts</button>}
    </DialogSurface>
    <ConfirmDialog open={discard} title="Discard all unsaved hunt review notes?" description="This removes the unsaved notes for every article and team in this session. Saved reviews are unchanged."
      confirmLabel="Discard all drafts" onCancel={() => setDiscard(false)} onConfirm={() => {
        for (const [key] of huntDraftEntries(client)) client.setQueryData(key, {})
        setDiscard(false)
      }} />
  </>
}
