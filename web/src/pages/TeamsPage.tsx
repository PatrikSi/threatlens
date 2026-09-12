import { TEAM_BUTTON } from './teamPresentation'
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'

import { ApiError, apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { useCurrentUser } from '../hooks/useCurrentUser'
import type { SavedView } from '../types/savedViews'
import type { Team, TeamPage } from '../types/teams'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
import { TeamCreateForm } from './TeamCreateForm'
import { TeamListPanel } from './TeamListPanel'
import { TeamMembersPanel } from './TeamMembersPanel'
import { TeamSettingsEditor, type TeamGroupChoice } from './TeamSettingsEditor'

function accessLost(error: unknown) {
  return error instanceof ApiError && [401, 403, 404].includes(error.status)
}

export function TeamsPage() {
  const user = useCurrentUser()
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const [created, setCreated] = useState<string | null>(null)
  const permissions = user.data?.access?.permissions ?? []
  const canAdminister =
    !user.isError && hasRequiredPermissions(permissions, ['write:iam'])
  const canInspectAdmin =
    !user.isError && hasRequiredPermissions(permissions, ['read:iam'])
  const adminMode = canInspectAdmin && params.get('mode') === 'admin'
  const selected = params.get('team') ?? ''
  const requestedPage = Number(params.get('page'))
  const page =
    Number.isSafeInteger(requestedPage) && requestedPage > 0 && requestedPage <= 1_000_000 ? requestedPage : 1
  const creating = canAdminister && params.get('create') === '1'
  const list = useQuery({
    queryKey: ['teams', 'list', adminMode, page],
    queryFn: () =>
      apiFetch<TeamPage>(
        `/teams${adminMode ? '/admin' : ''}?page=${page}&page_size=25`,
      ),
    refetchInterval: 30_000,
  })
  const detail = useQuery({
    queryKey: ['teams', 'detail', selected, adminMode],
    queryFn: () =>
      apiFetch<Team>(`/teams/${adminMode ? 'admin/' : ''}${selected}`),
    enabled: Boolean(selected) && !creating,
    refetchInterval: 30_000,
  })
  const groups = useQuery({
    queryKey: ['teams', 'group-choices'],
    queryFn: () => apiFetch<TeamGroupChoice[]>('/iam/groups'),
    enabled: canAdminister,
  })
  useEffect(() => {
    if (!created) return
    setParams({ mode: 'admin', team: created })
    setCreated(null)
    void queryClient.invalidateQueries({ queryKey: ['teams'] })
  }, [created, queryClient, setParams])
  const team = detail.data
  const changePage = (next: number) =>
    setParams((current) => {
      const updated = new URLSearchParams(current)
      updated.set('page', String(next))
      updated.delete('team')
      return updated
    })
  const selectedError = detail.error
  const hidden = accessLost(selectedError) || accessLost(user.error)
  const updateTeam = (saved: Team) =>
    queryClient.setQueryData(['teams', 'detail', saved.id, adminMode], saved)

  return (
    <main className="space-y-4">
      <header className="tl-surface rounded-xl p-4">
        <h1 className="font-display text-2xl">Team workspaces</h1>
        <p className="mt-1 text-sm text-slate dark:text-slate-300">
          Share monitoring views, triage queues and investigations through
          current IAM group membership.
        </p>
        <nav aria-label="Team views" className="mt-3 flex flex-wrap gap-3">
          <Link to="/teams" className="font-semibold text-cyan">
            My teams
          </Link>
          {canInspectAdmin && (
            <Link to="/teams?mode=admin" className="font-semibold text-cyan">
              Team administration
            </Link>
          )}
          {canAdminister && (
            <Link
              to="/teams?mode=admin&create=1"
              className="font-semibold text-cyan"
            >
              Create team
            </Link>
          )}
        </nav>
      </header>
      {groups.isError && canAdminister && (
        <div role="alert" className="tl-surface rounded-xl p-4">
          {resolveApiErrorMessage(
            groups.error,
            'IAM groups could not be loaded.',
          )}{' '}
          <button className={TEAM_BUTTON} onClick={() => void groups.refetch()}>
            Retry groups
          </button>
        </div>
      )}
      {creating ? (
        <TeamCreateForm
          groups={groups.data ?? []}
          onCreated={(saved) => setCreated(saved.id)}
        />
      ) : (
        <div className="grid items-start gap-4 xl:grid-cols-[20rem_minmax(0,1fr)]">
          <TeamListPanel
            list={list}
            selected={selected}
            page={page}
            adminMode={adminMode}
            changePage={changePage}
          />
          <section
            className="tl-surface min-w-0 space-y-6 rounded-xl p-4"
            aria-label="Selected team"
          >
            {selectedError && (
              <div role="alert">
                <p>
                  {resolveApiErrorMessage(
                    selectedError,
                    'Unable to refresh this team. Existing drafts are preserved.',
                  )}
                </p>
                <button
                  className={`${TEAM_BUTTON} mt-2`}
                  onClick={() => void detail.refetch()}
                >
                  Retry team
                </button>
              </div>
            )}
            {!selected ? (
              <p>Select a team to open its workspace.</p>
            ) : !hidden && team ? (
              <>
                <div>
                  <h2 className="font-display text-xl">{team.name}</h2>
                  <p className="mt-1 whitespace-pre-wrap text-sm">
                    {team.description}
                  </p>
                </div>
                {!adminMode && (
                  <>
                    <nav
                      aria-label="Team resources"
                      className="flex flex-wrap gap-4"
                    >
                      <Link
                        className="font-semibold text-cyan"
                        to={`/alerts?team_id=${team.id}`}
                      >
                        Open shared triage
                      </Link>
                      <Link
                        className="font-semibold text-cyan"
                        to={`/investigations?team_id=${team.id}`}
                      >
                        Open team investigations
                      </Link>
                      <Link className="font-semibold text-cyan" to="/">
                        Open dashboard views
                      </Link>
                    </nav>
                    {hasRequiredPermissions(permissions, ['read:views']) && (
                      <TeamSharedViews
                        key={`views-${team.id}`}
                        team={team}
                        writable={hasRequiredPermissions(permissions, [
                          'write:teams',
                          'write:views',
                        ])}
                      />
                    )}
                    <TeamMembersPanel
                      key={`members-${team.id}`}
                      teamId={team.id}
                    />
                  </>
                )}
                {(team.can_manage || (adminMode && canAdminister)) && (
                  <TeamSettingsEditor
                    key={team.id}
                    team={team}
                    groups={groups.data ?? []}
                    administer={adminMode && canAdminister}
                    onSaved={updateTeam}
                  />
                )}
                {adminMode && (
                  <p className="text-sm">
                    Administration shows team configuration. Reading team
                    content still requires current group membership and its
                    feature permissions.
                  </p>
                )}
              </>
            ) : (
              !selectedError && <p role="status">Loading selected team…</p>
            )}
          </section>
        </div>
      )}
    </main>
  )
}

function TeamSharedViews({
  team,
  writable,
}: {
  team: Team
  writable: boolean
}) {
  const queryClient = useQueryClient()
  const [sourceId, setSourceId] = useState('')
  const [name, setName] = useState('')
  const views = useQuery({
    queryKey: ['views'],
    queryFn: () => apiFetch<SavedView[]>('/views'),
  })
  const share = useMutation({
    mutationFn: () => {
      const source = views.data?.find(
        (view) => view.id === sourceId && !view.team_id,
      )
      if (!source)
        throw new Error(
          'The selected personal view is unavailable. Reload the view list.',
        )
      return apiFetch<SavedView>('/views', {
        method: 'POST',
        body: JSON.stringify({
          name: name.trim() || source.name,
          query_json: source.query_json,
          team_id: team.id,
        }),
      })
    },
    onSuccess: () => {
      setSourceId('')
      setName('')
      void queryClient.invalidateQueries({ queryKey: ['views'] })
    },
  })
  return (
    <section className="space-y-3" aria-label="Team views">
      <h2 className="text-lg font-semibold">Shared monitoring views</h2>
      {views.isError ? (
        <p role="alert">
          {resolveApiErrorMessage(views.error, 'Unable to load saved views.')}{' '}
          <button className={TEAM_BUTTON} onClick={() => void views.refetch()}>
            Retry views
          </button>
        </p>
      ) : (
        <>
          {!views.isLoading &&
            !views.data?.some((view) => view.team_id === team.id) && (
              <p className="text-sm">
                No monitoring views shared with this team yet.
              </p>
            )}
          <ul className="list-inside list-disc text-sm">
            {views.data
              ?.filter((view) => view.team_id === team.id)
              .map((view) => (
                <li key={view.id}>
                  {view.name} · revision {view.revision}
                </li>
              ))}
          </ul>
        </>
      )}
      {writable && (
        <fieldset
          disabled={share.isPending || views.isError}
          className="space-y-2"
        >
          <p className="text-sm">
            Copy a personal view into this team. The layout and search filters
            are shared; scratch notes, selected briefs and personal alert-rule
            selections are excluded.
          </p>
          <label className="block text-sm">
            Personal view
            <select
              className="ml-2 rounded border border-slate/30 bg-white p-2 dark:bg-[#072019]"
              value={sourceId}
              onChange={(event) => setSourceId(event.target.value)}
            >
              <option value="">Choose a personal view</option>
              {views.data
                ?.filter((view) => !view.team_id)
                .map((view) => (
                  <option key={view.id} value={view.id}>
                    {view.name}
                  </option>
                ))}
            </select>
          </label>
          <label className="block text-sm">
            Shared view name
            <input
              className="ml-2 rounded border border-slate/30 bg-white p-2 dark:bg-[#072019]"
              maxLength={255}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Use the personal view name"
            />
          </label>
          <button
            className={TEAM_BUTTON}
            disabled={!sourceId}
            onClick={() => share.mutate()}
          >
            {share.isPending ? 'Sharing…' : 'Share a copy with this team'}
          </button>
        </fieldset>
      )}
      {share.isError && (
        <p role="alert">
          {resolveApiErrorMessage(share.error, 'Unable to share view.')}
        </p>
      )}
      {share.isSuccess && (
        <p role="status">
          Shared view created. Team members can select it from the dashboard.
        </p>
      )}
    </section>
  )
}
