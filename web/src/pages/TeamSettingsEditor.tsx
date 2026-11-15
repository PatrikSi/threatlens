import { TEAM_BUTTON } from './teamPresentation'
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { useQueryAccessScope } from '../hooks/useQueryAccessScope'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { CopyableIdentifier } from '../components/CopyableIdentifier'
import type { Team } from '../types/teams'

export interface TeamGroupChoice {
  id: string
  name: string
  is_system: boolean
}
const inputClass =
  'mt-1 w-full rounded border border-slate/30 bg-white p-2 dark:bg-[#072019]'

type TeamSettingsProps = {
  team: Team
  groups: TeamGroupChoice[]
  administer: boolean
  onSaved: (team: Team) => void
  unavailable?: boolean
  groupsUnavailable?: boolean
  hidden?: boolean
  readAdminMode?: boolean
}

function teamSettingsDraftChanged(baseline: Team, draft: {
  name: string; description: string; memberGroup: string; managerGroup: string; active: boolean
}) {
  return draft.name !== baseline.name || draft.description !== baseline.description ||
    draft.memberGroup !== baseline.membership_group_id ||
    draft.managerGroup !== (baseline.manager_group_id ?? '') || draft.active !== baseline.active
}

// Keep the same actor's local editor mounted while its server read is hidden.
// The parent keys this boundary by selected resource and administration mode.
export function RetainedTeamSettings({ team, ...props }: Omit<TeamSettingsProps, 'team'> & { team: Team | undefined }) {
  const retained = useRef<Team | undefined>(undefined)
  const opened = useRef(false)
  if (team) retained.current = team
  const current = team ?? retained.current
  if (current?.can_manage || props.administer) opened.current = true
  if (!current || !opened.current) return null
  return <TeamSettingsEditor {...props} team={current} />
}

export function TeamSettingsEditor({
  team,
  groups,
  administer,
  onSaved,
  unavailable = false,
  groupsUnavailable = false,
  hidden = false,
  readAdminMode = false,
}: TeamSettingsProps) {
  const queryClient = useQueryClient()
  const access = useQueryAccessScope(['teams', 'detail', team.id, readAdminMode])
  const [baseline, setBaseline] = useState(team)
  const [baselineGeneration, setBaselineGeneration] = useState(access.generation)
  const [name, setName] = useState(team.name)
  const [description, setDescription] = useState(team.description)
  const [memberGroup, setMemberGroup] = useState(team.membership_group_id)
  const [managerGroup, setManagerGroup] = useState(team.manager_group_id ?? '')
  const [active, setActive] = useState(team.active)
  const [notice, setNotice] = useState('')
  const dirty = teamSettingsDraftChanged(baseline, { name, description, memberGroup, managerGroup, active })
  const cleanRecovery = !dirty && baselineGeneration !== access.generation
  useEffect(() => {
    if (hidden || !cleanRecovery) return
    const current = access.currentData<Team>(queryClient.getQueryData(['teams', 'detail', team.id, readAdminMode]))
    if (!current) return
    setBaseline(current)
    setName(current.name)
    setDescription(current.description)
    setMemberGroup(current.membership_group_id)
    setManagerGroup(current.manager_group_id ?? '')
    setActive(current.active)
    setBaselineGeneration(access.captureResult()(true).generation)
    setNotice('')
  }, [access, cleanRecovery, hidden, queryClient, readAdminMode, team.id])
  const discard = useUnsavedChangesWarning(
    dirty,
    'Discard unsaved team changes?',
  )
  const save = useMutation({
    mutationFn: (kind: 'metadata' | 'bindings') => {
      const readState = queryClient.getQueryState(['teams', 'detail', team.id, readAdminMode])
      if (unavailable || hidden || access.denied || readState?.status === 'error'
        || (kind === 'metadata' ? !team.can_manage : !administer || groupsUnavailable || queryClient.getQueryState(['teams', 'group-choices'])?.status === 'error')) {
        throw new Error('Refresh the current team before changing it. Your draft is preserved.')
      }
      const capture = access.captureResult()
      return apiFetch<Team>(
        `/teams/${team.id}${kind === 'bindings' ? '/bindings' : ''}`,
        {
          method: kind === 'bindings' ? 'PUT' : 'PATCH',
          body: JSON.stringify(
            kind === 'bindings'
              ? {
                  expected_revision: baseline.revision,
                  membership_group_id: memberGroup,
                  manager_group_id: managerGroup || null,
                  active,
                }
              : { expected_revision: baseline.revision, name, description },
          ),
        },
      ).then(capture)
    },
    onSuccess: (result, kind) => {
      const saved = access.currentData(result)
      if (!saved) return
      setBaseline(saved)
      setBaselineGeneration(result.generation)
      if (kind === 'metadata') {
        setName(saved.name)
        setDescription(saved.description)
      } else {
        setMemberGroup(saved.membership_group_id)
        setManagerGroup(saved.manager_group_id ?? '')
        setActive(saved.active)
      }
      setNotice('Team saved.')
      onSaved(saved)
      void queryClient.invalidateQueries({ queryKey: ['teams'] })
      void queryClient.invalidateQueries({ queryKey: ['views'] })
      void queryClient.invalidateQueries({ queryKey: ['investigations'] })
    },
  })
  const reload = () =>
    discard(() => {
      const admission = access.captureResult()(true)
      const current = access.currentData<Team>(queryClient.getQueryData(['teams', 'detail', team.id, readAdminMode]))
        ?? (admission.generation === 0 ? team : undefined)
      if (!current || !access.currentData(admission)) return
      setBaseline(current)
      setName(current.name)
      setDescription(current.description)
      setMemberGroup(current.membership_group_id)
      setManagerGroup(current.manager_group_id ?? '')
      setActive(current.active)
      setBaselineGeneration(admission.generation)
      save.reset()
    })
  if (hidden || cleanRecovery) return <>{discard.discardDialog}</>
  return (
    <section className="space-y-3" aria-label="Team settings">
      {discard.discardDialog}
      <h2 className="text-lg font-semibold">Team settings</h2>
      {team.revision !== baseline.revision && (
        <p role="status">
          This team changed elsewhere. Your draft still uses revision{' '}
          {baseline.revision}.
        </p>
      )}
      {save.isError && (
        <p role="alert">
          {resolveApiErrorMessage(
            save.error,
            'Unable to save team. Your draft is preserved.',
          )}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <fieldset disabled={save.isPending} className="space-y-3">
        <label className="block text-sm">
          Name
          <input
            className={inputClass}
            maxLength={120}
            disabled={!team.can_manage}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label className="block text-sm">
          Description
          <textarea
            className={inputClass}
            maxLength={4000}
            disabled={!team.can_manage}
            rows={3}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>
        <button
          className={TEAM_BUTTON}
          disabled={
            unavailable || access.denied || !team.can_manage ||
            !name.trim() ||
            (name === baseline.name && description === baseline.description)
          }
          onClick={() => save.mutate('metadata')}
        >
          Save team details
        </button>
        {!team.can_manage && administer && (
          <p className="text-sm">
            Team details are managed by the designated manager group. IAM
            administrators can recover access by changing group bindings.
          </p>
        )}
        {administer && (
          <>
            <label className="block text-sm">
              Member group
              <select
                className={inputClass}
                value={memberGroup}
                onChange={(event) => setMemberGroup(event.target.value)}
              >
                <option value={baseline.membership_group_id}>
                  {groups.find((group) => group.id === baseline.membership_group_id)?.name ?? 'Unavailable member group'}
                </option>
                {groups
                  .filter(
                    (group) =>
                      !group.is_system &&
                      group.id !== baseline.membership_group_id,
                  )
                  .map((group) => (
                    <option key={group.id} value={group.id}>
                      {group.name}
                    </option>
                  ))}
              </select>
            </label>
            <CopyableIdentifier label="Member group ID" value={memberGroup} />
            <label className="block text-sm">
              Manager group
              <select
                className={inputClass}
                value={managerGroup}
                onChange={(event) => setManagerGroup(event.target.value)}
              >
                <option value="">No delegated manager</option>
                {baseline.manager_group_id && (
                  <option value={baseline.manager_group_id}>
                    {groups.find((group) => group.id === baseline.manager_group_id)?.name ?? 'Unavailable manager group'}
                  </option>
                )}
                {groups
                  .filter(
                    (group) =>
                      !group.is_system &&
                      group.id !== baseline.manager_group_id,
                  )
                  .map((group) => (
                    <option key={group.id} value={group.id}>
                      {group.name}
                    </option>
                  ))}
              </select>
            </label>
            {managerGroup && <CopyableIdentifier label="Manager group ID" value={managerGroup} />}
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={active}
                onChange={(event) => setActive(event.target.checked)}
              />
              Team is active
            </label>
            <p className="text-sm">
              Changing groups changes access to existing team content.
              Deactivation preserves content and blocks member access until
              reactivated.
            </p>
            <button
              className={TEAM_BUTTON}
              disabled={
                unavailable || groupsUnavailable || access.denied || (memberGroup === baseline.membership_group_id &&
                managerGroup === (baseline.manager_group_id ?? '') &&
                active === baseline.active)
              }
              onClick={() => save.mutate('bindings')}
            >
              Apply access changes
            </button>
          </>
        )}
        <button className={`${TEAM_BUTTON} ml-2`} onClick={reload}>
          Discard draft and reload
        </button>
      </fieldset>
    </section>
  )
}
