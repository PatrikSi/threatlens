import { TEAM_BUTTON } from './teamPresentation'
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'

import { ApiError, apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { useQueryAccessScope } from '../hooks/useQueryAccessScope'
import { useCurrentUser } from '../hooks/useCurrentUser'
import type { SavedView } from '../types/savedViews'
import type { Team, TeamPage } from '../types/teams'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
import { TeamCreateForm } from './TeamCreateForm'
import { TeamListPanel } from './TeamListPanel'
import { TeamMembersPanel } from './TeamMembersPanel'
import { TeamAIGovernance } from './TeamAIGovernance'
import { TeamIntegrations } from './TeamIntegrations'
import { TeamHuntWorklist } from './TeamHuntWorklist'
import { TeamIndicatorPanels } from './TeamIndicatorPanels'
import { RetainedTeamSettings, type TeamGroupChoice } from './TeamSettingsEditor'

function accessLost(error: unknown) {
  return error instanceof ApiError && [401, 403, 404].includes(error.status)
}

function showTeamDetails(adminMode: boolean, panel: string | null) {
  return adminMode || !['ai-context', 'ai-governance', 'indicator-suppressions', 'hunts', 'integrations'].includes(panel ?? '')
}

function readTeamDirectoryPage(params: URLSearchParams) {
  const page = Number(params.get('page'))
  return Number.isSafeInteger(page) && page > 0 && page <= 1_000_000 ? page : 1
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
  const page = readTeamDirectoryPage(params)
  const creating = canAdminister && params.get('create') === '1'
  const listAccess = useQueryAccessScope(['teams', 'list', adminMode])
  const detailAccess = useQueryAccessScope(['teams', 'detail', selected, adminMode])
  const groupsAccess = useQueryAccessScope(['teams', 'group-choices'])
  const list = useQuery({
    queryKey: ['teams', 'list', adminMode, page, 25],
    ...listAccess.queryOptions(() =>
      apiFetch<TeamPage>(
        `/teams${adminMode ? '/admin' : ''}?page=${page}&page_size=25`,
      )),
    refetchInterval: 30_000,
  })
  const detail = useQuery({
    queryKey: ['teams', 'detail', selected, adminMode],
    ...detailAccess.queryOptions(() =>
      apiFetch<Team>(`/teams/${adminMode ? 'admin/' : ''}${selected}`)),
    enabled: Boolean(selected) && !creating,
    refetchInterval: 30_000,
  })
  const groups = useQuery({
    queryKey: ['teams', 'group-choices'],
    ...groupsAccess.queryOptions(() => apiFetch<TeamGroupChoice[]>('/iam/groups')),
    enabled: canAdminister,
  })
  useEffect(() => {
    if (!created) return
    setParams({ mode: 'admin', team: created })
    setCreated(null)
    void queryClient.invalidateQueries({ queryKey: ['teams'] })
  }, [created, queryClient, setParams])
  const detailsPanel = showTeamDetails(adminMode, params.get('panel'))
  const team = detail.data
  const changePage = (next: number) =>
    setParams((current) => {
      const updated = new URLSearchParams(current)
      updated.set('page', String(next))
      updated.delete('team')
      return updated
    })
  const selectedError = detail.error
  const hidden = detailAccess.denied || accessLost(selectedError) || accessLost(user.error)
  const updateTeam = (saved: Team) =>
    void queryClient.invalidateQueries({ queryKey: ['teams', 'detail', saved.id, adminMode], exact: true })

  return (
    <section className="space-y-4">
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
            accessDenied={listAccess.denied}
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
                  <TeamWorkspaceResources team={team} permissions={permissions}
                    panel={params.get('panel')} unavailable={user.isError || detail.isError} />
                )}
                <AdminTeamAIGovernance teamId={team.id} enabled={adminMode} permissions={permissions} unavailable={user.isError || detail.isError} />
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
            {selected && detailsPanel && <RetainedTeamSettings
              key={`${selected}:${adminMode}`}
              team={team}
              groups={groups.data ?? []}
              administer={adminMode && canAdminister}
              readAdminMode={adminMode}
              hidden={hidden || !team}
              unavailable={user.isError || detail.isError}
              groupsUnavailable={groups.isError || groupsAccess.denied}
              onSaved={updateTeam}
            />}
          </section>
        </div>
      )}
    </section>
  )
}

function TeamWorkspaceResources({ team, permissions, panel, unavailable }: {
  team: Team
  permissions: string[]
  panel: string | null
  unavailable: boolean
}) {
  const detailsPanel = showTeamDetails(false, panel)
  return (
    <>
      <nav
        aria-label="Team resources"
        className="flex flex-wrap gap-4"
      >
        <Link
          className="font-semibold text-cyan"
          to={`/alerts?view=occurrences&queue_scope=team&team_id=${team.id}`}
        >
          Open shared triage
        </Link>
        <Link
          className="font-semibold text-cyan"
          to={`/investigations?team_id=${team.id}`}
        >
          Open team investigations
        </Link>
        <Link className="font-semibold text-cyan" aria-current={panel === 'hunts' ? 'page' : undefined} to={`/teams?team=${team.id}&panel=hunts`}>Team hunt queue</Link>
        <Link className="font-semibold text-cyan" to={`/teams?team=${team.id}&panel=ai-governance`}>AI destinations</Link>
        {team.can_manage && hasRequiredPermissions(permissions, ['write:teams', 'write:notifications']) && <Link className="font-semibold text-cyan" to={`/teams?team=${team.id}&panel=integrations`}>Team integrations</Link>}
        <Link className="font-semibold text-cyan" to="/">
          Open dashboard views
        </Link>
      </nav>
      <TeamIndicatorPanels teamId={team.id} panel={panel} permissions={permissions} unavailable={unavailable} />
      {detailsPanel && hasRequiredPermissions(permissions, ['read:views']) && (
        <TeamSharedViews
          key={`views-${team.id}`}
          team={team}
          writable={hasRequiredPermissions(permissions, [
            'write:teams',
            'write:views',
          ])}
        />
      )}
      {panel === 'hunts' && hasRequiredPermissions(permissions, ['read:items']) && (
        <TeamHuntWorklist key={team.id} teamId={team.id}
          writable={hasRequiredPermissions(permissions, ['write:teams'])}
          canInvestigate={hasRequiredPermissions(permissions, ['write:investigations'])}
          unavailable={unavailable} />
      )}
      {panel === 'integrations' && team.can_manage && hasRequiredPermissions(permissions, ['write:teams', 'write:notifications']) && <TeamIntegrations key={team.id} teamId={team.id} unavailable={unavailable} />}
      {panel === 'ai-governance' && <TeamAIGovernance key={team.id} teamId={team.id} unavailable={unavailable} />}
      {detailsPanel && <TeamMembersPanel
        key={`members-${team.id}`}
        teamId={team.id}
      />}
    </>
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
  const viewsAccess = useQueryAccessScope(['views'])
  const views = useQuery({
    queryKey: ['views'],
    ...viewsAccess.queryOptions(() => apiFetch<SavedView[]>('/views')),
  })
  useEffect(() => { if (viewsAccess.denied) setSourceId('') }, [viewsAccess.denied])
  const share = useMutation({
    mutationFn: () => {
      const current = queryClient.getQueryState(['views'])?.status === 'error' ? undefined
        : viewsAccess.currentData<SavedView[]>(queryClient.getQueryData(['views']))
      const source = current?.find(
        (view) => view.id === sourceId && !view.team_id,
      )
      if (!source)
        throw new Error(
          'The selected personal view is unavailable. Reload the view list.',
        )
      const capture = viewsAccess.captureResult()
      const submitted = { sourceId, name }
      return apiFetch<SavedView>('/views', {
        method: 'POST',
        body: JSON.stringify({
          name: name.trim() || source.name,
          query_json: source.query_json,
          team_id: team.id,
        }),
      }).then((saved) => capture({ saved, submitted }))
    },
    onSuccess: (result) => {
      const accepted = viewsAccess.currentData(result)
      if (accepted) {
        setSourceId((current) => current === accepted.submitted.sourceId ? '' : current)
        setName((current) => current === accepted.submitted.name ? '' : current)
      }
      void queryClient.invalidateQueries({ queryKey: ['views'] })
      void queryClient.invalidateQueries({ queryKey: ['dashboard', 'views'] })
    },
  })
  const confirmDiscard = useUnsavedChangesWarning(Boolean(sourceId || name || share.isPending), 'You have an unsaved shared monitoring view. Leave without saving?')
  return (
    <section className="space-y-3" aria-label="Team views">
      {confirmDiscard.discardDialog}
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
          disabled={share.isPending || views.isError || viewsAccess.denied || !views.data}
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

function AdminTeamAIGovernance({ teamId, enabled, permissions, unavailable }: { teamId: string; enabled: boolean; permissions: string[]; unavailable: boolean }) {
  return enabled && permissions.includes('write:ai') ? <TeamAIGovernance key={teamId} teamId={teamId} admin unavailable={unavailable} /> : null
}
