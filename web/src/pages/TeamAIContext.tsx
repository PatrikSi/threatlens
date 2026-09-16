import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import type { TeamAIContext as Context } from '../types/teams'
import { TEAM_BUTTON } from './teamPresentation'

const fields = [
  ['technology_stack', 'Technology stack'],
  ['priorities', 'Team priorities'],
  ['available_telemetry', 'Available telemetry'],
] as const
const inputClass = 'mt-1 w-full rounded border border-slate/30 bg-white p-2 dark:bg-[#072019]'

function createDraft(context: Context) {
  return {
    technology_stack: context.technology_stack.join('\n'),
    priorities: context.priorities.join('\n'),
    available_telemetry: context.available_telemetry.join('\n'),
    relevance_criteria: context.relevance_criteria,
  }
}
function parseEntries(value: string) {
  return value.split('\n').map((entry) => entry.trim()).filter(Boolean)
}

export function TeamAIContextTab({ teamId, selected, writable, unavailable }: { teamId: string; selected: boolean; writable: boolean; unavailable: boolean }) {
  return <>
    <nav aria-label="Team configuration" className="flex flex-wrap gap-4">
      <Link className="font-semibold text-cyan" aria-current={!selected ? 'page' : undefined} to={`/teams?team=${teamId}`}>Team details</Link>
      <Link className="font-semibold text-cyan" aria-current={selected ? 'page' : undefined} to={`/teams?team=${teamId}&panel=ai-context`}>AI context</Link>
    </nav>
    {selected && <TeamAIContext key={teamId} teamId={teamId} writable={writable && !unavailable} />}
  </>
}

export function TeamAIContext({ teamId, writable }: { teamId: string; writable: boolean }) {
  const query = useQuery({
    queryKey: ['teams', 'ai-context', teamId],
    queryFn: ({ signal }) => apiFetch<Context>(`/teams/${teamId}/ai-context`, { signal }),
    refetchInterval: 30_000,
  })
  const accessLost = query.error instanceof ApiError && [401, 403, 404].includes(query.error.status)
  return (
    <section aria-label="Team AI context" className="space-y-3">
      <h2 className="text-lg font-semibold">Team AI context</h2>
      <p className="text-sm">Tailor assessments and hunt suggestions to this team. Shared article evidence remains unchanged. This context is sent to the configured AI provider when a team assessment is requested; do not include credentials.</p>
      {query.isError && <div role="alert">
        {resolveApiErrorMessage(query.error, 'Team AI context could not be refreshed. Your draft is preserved.')}{' '}
        <button className={TEAM_BUTTON} onClick={() => void query.refetch()}>Retry AI context</button>
      </div>}
      {!accessLost && query.data ? <TeamAIContextEditor key={teamId} context={query.data} writable={writable && !query.isError} /> : !query.isError && <p role="status">Loading team AI context…</p>}
    </section>
  )
}

export function TeamAIContextEditor({ context, writable }: { context: Context; writable: boolean }) {
  const client = useQueryClient()
  const [baseline, setBaseline] = useState(context)
  const [draft, setDraft] = useState(() => createDraft(context))
  const [notice, setNotice] = useState('')
  const dirty = JSON.stringify(draft) !== JSON.stringify(createDraft(baseline))
  const discard = useUnsavedChangesWarning(dirty, 'Discard unsaved team AI context?')
  const validation = fields.map(([key, label]) => {
    const entries = parseEntries(draft[key])
    return entries.length > 40 || entries.some((entry) => entry.length > 200) ? `${label} allows up to 40 entries of 200 characters each.` : ''
  }).find(Boolean)
  const save = useMutation({
    mutationFn: () => apiFetch<Context>(`/teams/${context.team_id}/ai-context`, {
      method: 'PATCH',
      body: JSON.stringify({
        expected_version: baseline.version,
        ...Object.fromEntries(fields.map(([key]) => [key, parseEntries(draft[key])])),
        relevance_criteria: draft.relevance_criteria.trim(),
      }),
    }),
    onSuccess: async (saved) => {
      await client.cancelQueries({ queryKey: ['teams', 'ai-context', context.team_id] })
      setBaseline(saved)
      setDraft(createDraft(saved))
      setNotice('Team AI context saved. Existing assessments retain their original context revision until regenerated.')
      client.setQueryData(['teams', 'ai-context', context.team_id], saved)
      void client.invalidateQueries({ queryKey: ['team-assessments'] })
    },
    onError: () => { void client.invalidateQueries({ queryKey: ['teams', 'ai-context', context.team_id] }) },
  })
  const canEdit = writable && context.can_manage
  const reload = () => discard(() => {
    setBaseline(context)
    setDraft(createDraft(context))
    setNotice('')
    save.reset()
  })
  return <div className="space-y-3">
    {discard.discardDialog}
    {context.version !== baseline.version && <p role="status">This context changed elsewhere. Your draft still uses revision {baseline.version}; reload the saved context before editing the newer revision.</p>}
    {notice && <p role="status">{notice}</p>}
    {save.isError && <p role="alert">{resolveApiErrorMessage(save.error, 'Unable to save team AI context. Your draft is preserved.')}</p>}
    {validation && <p role="alert">{validation}</p>}
    {!context.can_manage && <p className="text-sm">Team managers can update this context.</p>}
    <fieldset disabled={!canEdit || save.isPending} className="space-y-3">
      {fields.map(([key, label]) => <label key={key} className="block text-sm">
        {label}
        <textarea className={inputClass} rows={3} value={draft[key]} maxLength={8040} onChange={(event) => setDraft((current) => ({ ...current, [key]: event.target.value }))} />
        <span className="text-xs">One entry per line, up to 40 entries.</span>
      </label>)}
      <label className="block text-sm">Relevance criteria
        <textarea className={inputClass} rows={4} maxLength={4000} value={draft.relevance_criteria} onChange={(event) => setDraft((current) => ({ ...current, relevance_criteria: event.target.value }))} />
      </label>
      <button className={TEAM_BUTTON} disabled={!dirty || Boolean(validation)} onClick={() => save.mutate()}>{save.isPending ? 'Saving AI context…' : 'Save AI context'}</button>
    </fieldset>
    {(dirty || context.version !== baseline.version) && <button className={TEAM_BUTTON} disabled={save.isPending} onClick={reload}>Reload saved AI context</button>}
  </div>
}
