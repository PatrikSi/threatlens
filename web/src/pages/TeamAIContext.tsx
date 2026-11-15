import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { useQueryAccessScope } from '../hooks/useQueryAccessScope'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import type { TeamAIContext as Context } from '../types/teams'
import { TEAM_BUTTON } from './teamPresentation'

const fields = [
  ['technology_stack', 'Technology stack'],
  ['priorities', 'Team priorities'],
  ['available_telemetry', 'Available telemetry'],
] as const
const inputClass =
  'mt-1 w-full rounded border border-slate/30 bg-white p-2 dark:bg-[#072019]'

function createDraft(context: Context) {
  return {
    technology_stack: context.technology_stack.join('\n'),
    priorities: context.priorities.join('\n'),
    available_telemetry: context.available_telemetry.join('\n'),
    relevance_criteria: context.relevance_criteria,
  }
}
function parseEntries(value: string) {
  return value
    .split('\n')
    .map((entry) => entry.trim())
    .filter(Boolean)
}

export function TeamAIContextTab({
  teamId,
  selected,
  writable,
  unavailable,
  indicatorSelected = false,
  detailsSelected = !selected && !indicatorSelected,
}: {
  teamId: string
  selected: boolean
  writable: boolean
  unavailable: boolean
  indicatorSelected?: boolean
  detailsSelected?: boolean
}) {
  return (
    <>
      <nav aria-label="Team configuration" className="flex flex-wrap gap-4">
        <Link
          className="font-semibold text-cyan"
          aria-current={detailsSelected ? 'page' : undefined}
          to={`/teams?team=${teamId}`}
        >
          Team details
        </Link>
        <Link
          className="font-semibold text-cyan"
          aria-current={selected ? 'page' : undefined}
          to={`/teams?team=${teamId}&panel=ai-context`}
        >
          AI context
        </Link>
        <Link
          className="font-semibold text-cyan"
          aria-current={indicatorSelected ? 'page' : undefined}
          to={`/teams?team=${teamId}&panel=indicator-suppressions`}
        >
          Indicator suppressions
        </Link>
      </nav>
      {selected && (
        <TeamAIContext
          key={teamId}
          teamId={teamId}
          writable={writable && !unavailable}
        />
      )}
    </>
  )
}

export function TeamAIContext({
  teamId,
  writable,
}: {
  teamId: string
  writable: boolean
}) {
  const access = useQueryAccessScope(['teams', 'ai-context', teamId])
  const retained = useRef<Context | undefined>(undefined)
  const query = useQuery({
    queryKey: ['teams', 'ai-context', teamId],
    ...access.queryOptions(({ signal }) =>
      apiFetch<Context>(`/teams/${teamId}/ai-context`, { signal })),
    refetchInterval: 30_000,
  })
  if (query.data) retained.current = query.data
  const context = query.data ?? (retained.current?.team_id === teamId ? retained.current : undefined)
  return (
    <section aria-label="Team AI context" className="space-y-3">
      <h2 className="text-lg font-semibold">Team AI context</h2>
      <p className="text-sm">
        Tailor assessments and hunt suggestions to this team. Shared article
        evidence remains unchanged. This context is sent to the configured AI
        provider when a team assessment is requested; do not include
        credentials.
      </p>
      {(query.isError || access.denied) && (
        <div role="alert">
          {resolveApiErrorMessage(
            query.error,
            'Team AI context could not be refreshed. Your draft is preserved.',
          )}{' '}
          <button className={TEAM_BUTTON} onClick={() => void query.refetch()}>
            Retry AI context
          </button>
        </div>
      )}
      {context ? (
        <TeamAIContextEditor
          key={teamId}
          context={context}
          hidden={!query.data}
          writable={writable && !query.isError && !access.denied}
        />
      ) : (
        !query.isError && <p role="status">Loading team AI context…</p>
      )}
    </section>
  )
}

export function TeamAIContextEditor({
  context,
  writable,
  hidden = false,
}: {
  context: Context
  writable: boolean
  hidden?: boolean
}) {
  const client = useQueryClient()
  const access = useQueryAccessScope(['teams', 'ai-context', context.team_id])
  const [baseline, setBaseline] = useState(context)
  const [baselineGeneration, setBaselineGeneration] = useState(access.generation)
  const [draft, setDraft] = useState(() => createDraft(context))
  const [notice, setNotice] = useState('')
  const dirty = JSON.stringify(draft) !== JSON.stringify(createDraft(baseline))
  const cleanRecovery = !dirty && baselineGeneration !== access.generation
  useEffect(() => {
    if (hidden || !cleanRecovery) return
    const current = access.currentData<Context>(client.getQueryData(['teams', 'ai-context', context.team_id]))
    if (!current) return
    setBaseline(current)
    setDraft(createDraft(current))
    setBaselineGeneration(access.captureResult()(true).generation)
    setNotice('')
  }, [access, cleanRecovery, client, context.team_id, hidden])
  const discard = useUnsavedChangesWarning(
    dirty,
    'Discard unsaved team AI context?',
  )
  const validation = fields
    .map(([key, label]) => {
      const entries = parseEntries(draft[key])
      return entries.length > 40 || entries.some((entry) => entry.length > 200)
        ? `${label} allows up to 40 entries of 200 characters each.`
        : ''
    })
    .find(Boolean)
  const save = useMutation({
    mutationFn: () => {
      if (!writable || hidden || !context.can_manage || access.denied || client.getQueryState(['teams', 'ai-context', context.team_id])?.status === 'error') {
        throw new Error('Refresh the current AI context before changing it. Your draft is preserved.')
      }
      const capture = access.captureResult()
      return apiFetch<Context>(`/teams/${context.team_id}/ai-context`, {
        method: 'PATCH',
        body: JSON.stringify({
          expected_version: baseline.version,
          ...Object.fromEntries(
            fields.map(([key]) => [key, parseEntries(draft[key])]),
          ),
          relevance_criteria: draft.relevance_criteria.trim(),
        }),
      }).then(capture)
    },
    onSuccess: async (result) => {
      const saved = access.currentData(result)
      if (!saved) return
      await client.cancelQueries({
        queryKey: ['teams', 'ai-context', context.team_id],
      })
      if (!access.currentData(result)) return
      setBaseline(saved)
      setBaselineGeneration(result.generation)
      setDraft(createDraft(saved))
      setNotice(
        'Team AI context saved. Existing assessments retain their original context revision until regenerated.',
      )
      if (client.getQueryState(['teams', 'ai-context', context.team_id])?.status !== 'error') client.setQueryData(['teams', 'ai-context', context.team_id], result)
      void client.invalidateQueries({ queryKey: ['team-assessments'] })
    },
    onError: () => {
      void client.invalidateQueries({
        queryKey: ['teams', 'ai-context', context.team_id],
      })
    },
  })
  const canEdit = writable && context.can_manage && !access.denied && !hidden
  const reload = () =>
    discard(() => {
      const admission = access.captureResult()(true)
      const current = access.currentData<Context>(client.getQueryData(['teams', 'ai-context', context.team_id]))
        ?? (admission.generation === 0 ? context : undefined)
      if (!current || !access.currentData(admission)) return
      setBaseline(current)
      setDraft(createDraft(current))
      setBaselineGeneration(admission.generation)
      setNotice('')
      save.reset()
    })
  if (hidden || cleanRecovery) return <>{discard.discardDialog}</>
  return (
    <div className="space-y-3">
      {discard.discardDialog}
      {context.version !== baseline.version && (
        <p role="status">
          This context changed elsewhere. Your draft still uses revision{' '}
          {baseline.version}; reload the saved context before editing the newer
          revision.
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {save.isError && (
        <p role="alert">
          {resolveApiErrorMessage(
            save.error,
            'Unable to save team AI context. Your draft is preserved.',
          )}
        </p>
      )}
      {validation && <p role="alert">{validation}</p>}
      {!context.can_manage && (
        <p className="text-sm">Team managers can update this context.</p>
      )}
      <fieldset disabled={!canEdit || save.isPending} className="space-y-3">
        {fields.map(([key, label]) => (
          <label key={key} className="block text-sm">
            {label}
            <textarea
              className={inputClass}
              rows={3}
              value={draft[key]}
              maxLength={8040}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  [key]: event.target.value,
                }))
              }
            />
            <span className="text-xs">
              One entry per line, up to 40 entries.
            </span>
          </label>
        ))}
        <label className="block text-sm">
          Relevance criteria
          <textarea
            className={inputClass}
            rows={4}
            maxLength={4000}
            value={draft.relevance_criteria}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                relevance_criteria: event.target.value,
              }))
            }
          />
        </label>
        <button
          className={TEAM_BUTTON}
          disabled={!dirty || Boolean(validation)}
          onClick={() => save.mutate()}
        >
          {save.isPending ? 'Saving AI context…' : 'Save AI context'}
        </button>
      </fieldset>
      {(dirty || context.version !== baseline.version) && (
        <button
          className={TEAM_BUTTON}
          disabled={save.isPending}
          onClick={reload}
        >
          Reload saved AI context
        </button>
      )}
    </div>
  )
}
