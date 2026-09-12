import { TEAM_BUTTON } from './teamPresentation'
import { FormEvent, useState } from 'react'
import { useMutation } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import type { Team } from '../types/teams'
import type { TeamGroupChoice } from './TeamSettingsEditor'

export function TeamCreateForm({
  groups,
  onCreated,
}: {
  groups: TeamGroupChoice[]
  onCreated: (team: Team) => void
}) {
  const [name, setName] = useState('')
  const [key, setKey] = useState('')
  const [members, setMembers] = useState('')
  const [managers, setManagers] = useState('')
  const [committed, setCommitted] = useState(false)
  const discard = useUnsavedChangesWarning(
    !committed && Boolean(name || key || members || managers),
    'Discard the unfinished team?',
  )
  const create = useMutation({
    mutationFn: () =>
      apiFetch<Team>('/teams', {
        method: 'POST',
        body: JSON.stringify({
          name,
          key,
          membership_group_id: members,
          manager_group_id: managers || null,
        }),
      }),
    onSuccess: (saved) => {
      setCommitted(true)
      onCreated(saved)
    },
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!create.isPending) create.mutate()
  }
  const input =
    'mt-1 w-full rounded border border-slate/30 bg-white p-2 dark:bg-[#072019]'
  return (
    <form onSubmit={submit} className="tl-surface space-y-3 rounded-xl p-4">
      {discard.discardDialog}
      <h2 className="text-lg font-semibold">Create a named team</h2>
      <p className="text-sm">
        Choose existing IAM groups. This creates an access boundary for shared
        resources; it does not grant additional feature permissions.
      </p>
      {create.isError && (
        <p role="alert">
          {resolveApiErrorMessage(
            create.error,
            'Unable to create team. Your draft is preserved.',
          )}
        </p>
      )}
      <fieldset disabled={create.isPending} className="space-y-3">
        <label className="block text-sm">
          Team name
          <input
            required
            maxLength={120}
            className={input}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label className="block text-sm">
          Stable key
          <input
            required
            pattern="[a-z][a-z0-9-]{1,62}[a-z0-9]"
            maxLength={64}
            className={input}
            value={key}
            onChange={(event) => setKey(event.target.value)}
          />
          <span className="text-xs">
            3–64 lowercase letters, numbers and hyphens; start with a letter.
          </span>
        </label>
        <label className="block text-sm">
          Member group
          <select
            required
            className={input}
            value={members}
            onChange={(event) => setMembers(event.target.value)}
          >
            <option value="">Choose a group</option>
            {groups
              .filter((group) => !group.is_system)
              .map((group) => (
                <option key={group.id} value={group.id}>
                  {group.name}
                </option>
              ))}
          </select>
        </label>
        <label className="block text-sm">
          Manager group
          <select
            className={input}
            value={managers}
            onChange={(event) => setManagers(event.target.value)}
          >
            <option value="">No delegated manager</option>
            {groups
              .filter((group) => !group.is_system)
              .map((group) => (
                <option key={group.id} value={group.id}>
                  {group.name}
                </option>
              ))}
          </select>
        </label>
        <button
          type="submit"
          className={TEAM_BUTTON}
          disabled={!name.trim() || !key || !members}
        >
          {create.isPending ? 'Creating…' : 'Create team'}
        </button>
      </fieldset>
    </form>
  )
}
