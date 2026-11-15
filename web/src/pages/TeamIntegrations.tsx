import { useCallback, useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { apiFetch } from '../api/client'
import { accessibleQueryData } from '../api/queryData'
import { resolveApiErrorMessage } from '../api/errors'
import { useQueryAccessScope } from '../hooks/useQueryAccessScope'
import { AutomationExecutions } from './AutomationExecutions'
import { TeamIntegrationConfiguration } from './TeamIntegrationConfiguration'
import { TEAM_BUTTON } from './teamPresentation'

type Destination = {
  id: string
  name: string
  enabled: boolean
  custodian_user_id: string | null
  ownership_revision: number
  event_type: string
}
type Personal = { id: string; name: string; ownership_revision?: number }
type Credential = {
  id: string
  name: string
  expires_at: string
  revoked_at: string | null
}
const inputClass =
  'rounded border border-slate/30 bg-white p-2 dark:bg-[#072019]'

export function TeamIntegrations({
  teamId,
  unavailable,
}: {
  teamId: string
  unavailable: boolean
}) {
  const cache = useQueryClient()
  const access = useQueryAccessScope(['teams', teamId, 'integrations'])
  const personalAccess = useQueryAccessScope(['notifications', 'webhooks'])
  const [retained, setRetained] = useState<{ teamId: string; rows: Record<string, Destination> }>({ teamId, rows: {} })
  const retainDraft = useCallback((row: Destination, dirty: boolean) => {
    setRetained((current) => {
      const rows = current.teamId === teamId ? current.rows : {}
      if (dirty ? rows[row.id] === row : !rows[row.id]) return current
      const next = { ...rows }
      if (dirty) next[row.id] = row
      else delete next[row.id]
      return { teamId, rows: next }
    })
  }, [teamId])
  const [selected, setSelected] = useState('')
  const base = `/teams/${teamId}/integrations`
  const query = useQuery({
    queryKey: ['teams', teamId, 'integrations'],
    ...access.queryOptions(({ signal }) =>
      apiFetch<{ items: Destination[] }>(base, { signal })),
    enabled: !unavailable,
  })
  const personal = useQuery({
    queryKey: ['notifications', 'webhooks'],
    ...personalAccess.queryOptions(({ signal }) =>
      apiFetch<Personal[]>('/notifications/webhooks', { signal })),
    enabled: !unavailable,
  })
  const adopt = useMutation({
    mutationFn: ({ id, revision }: { id: string; revision: number }) => {
      if (unavailable || cache.getQueryState(['teams', teamId, 'integrations'])?.status === 'error' || access.denied) throw new Error('Refresh team integrations before transferring a destination.')
      const owned = access.currentData<{ items: Destination[] }>(cache.getQueryData(['teams', teamId, 'integrations']))?.items.find((row) => row.id === id)
      const source = cache.getQueryState(['notifications', 'webhooks'])?.status === 'error' ? undefined : personalAccess.currentData<Personal[]>(cache.getQueryData(['notifications', 'webhooks']))?.find((row) => row.id === id)
      if (!(owned?.ownership_revision === revision || source && (source.ownership_revision ?? 1) === revision)) throw new Error('Reload the selected destination before transferring it.')
      const capture = access.captureResult()
      return apiFetch(`${base}/${id}/adopt`, {
        method: 'POST',
        body: JSON.stringify({ expected_revision: revision }),
      }).then(capture)
    },
    onSuccess: (result) => {
      if (access.currentData(result) !== undefined) setSelected('')
      void cache.invalidateQueries({
        queryKey: ['teams', teamId, 'integrations'],
      })
      void cache.invalidateQueries({ queryKey: ['notifications', 'webhooks'] })
    },
  })
  const data = accessibleQueryData(query)
  const personalData = accessibleQueryData(personal) ?? []
  const rows = data?.items ?? []
  const retainedRows = Object.values(retained.teamId === teamId ? retained.rows : {}).filter((row) => !rows.some((current) => current.id === row.id))
  useEffect(() => { if (access.denied || personalAccess.denied) setSelected('') }, [access.denied, personalAccess.denied])
  return (
    <section className="space-y-3" aria-label="Team integrations">
      <h2 className="font-display text-lg">Team integrations</h2>
      <p className="text-sm">
        Destinations and execution history belong to the team. A delivery
        custodian provides current source permissions. When they leave,
        deliveries pause until a manager explicitly adopts the destination.
        Existing withdrawals remain available to receiver credentials.
      </p>
      <p className="text-sm">
        Create and test a personal webhook in{' '}
        <Link className="text-cyan" to="/settings/integrations/webhooks">
          notification settings
        </Link>
        , then transfer it here. Adoption keeps its destination and clones its
        credentials; pending work is rechecked before delivery.
      </p>
      <fieldset
        disabled={
          unavailable || adopt.isPending || query.isError || personal.isError || access.denied || personalAccess.denied
        }
        className="flex flex-wrap gap-2"
      >
        <label>
          Personal destination{' '}
          <select
            className={inputClass}
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
          >
            <option value="">Choose destination</option>
            {personalData.map((row) => (
              <option key={row.id} value={row.id}>
                {row.name}
              </option>
            ))}
          </select>
        </label>
        <button
          className={TEAM_BUTTON}
          disabled={!selected}
          onClick={() => {
            const row = personalData.find((entry) => entry.id === selected)
            if (row)
              adopt.mutate({
                id: row.id,
                revision: row.ownership_revision ?? 1,
              })
          }}
        >
          Transfer to this team
        </button>
      </fieldset>
      {query.isPending && <p role="status">Loading team integrations…</p>}
      {query.isError && (
        <p role="alert">
          {resolveApiErrorMessage(
            query.error,
            'Team integrations could not be loaded.',
          )}{' '}
          <button className={TEAM_BUTTON} onClick={() => void query.refetch()}>
            Retry integrations
          </button>
        </p>
      )}
      {personal.isError && (
        <p role="alert">
          {resolveApiErrorMessage(
            personal.error,
            'Personal destinations could not be loaded.',
          )}{' '}
          <button
            className={TEAM_BUTTON}
            onClick={() => void personal.refetch()}
          >
            Retry destinations
          </button>
        </p>
      )}
      {adopt.isError && (
        <p role="alert">
          {resolveApiErrorMessage(
            adopt.error,
            'The destination could not be adopted. Reload before retrying.',
          )}
        </p>
      )}
      {data?.items.length === 0 && <p>No team integrations yet.</p>}
      {[...rows, ...retainedRows].map((row) => (
        <DestinationCard
          key={row.id}
          row={row}
          base={base}
          teamId={teamId}
          hidden={!rows.some((current) => current.id === row.id)}
          onDirtyChange={retainDraft}
          unavailable={unavailable || query.isError || adopt.isPending || access.denied}
          onAdopt={() =>
            adopt.mutate({ id: row.id, revision: row.ownership_revision })
          }
          onChanged={() => void query.refetch()}
        />
      ))}
    </section>
  )
}

function DestinationCard({
  row,
  base,
  teamId,
  hidden,
  onDirtyChange,
  unavailable,
  onAdopt,
  onChanged,
}: {
  row: Destination
  base: string
  teamId: string
  hidden: boolean
  onDirtyChange: (row: Destination, dirty: boolean) => void
  unavailable: boolean
  onAdopt: () => void
  onChanged: () => void
}) {
  const cache = useQueryClient()
  const access = useQueryAccessScope(['teams', teamId, 'integrations'])
  const reportDirty = useCallback((dirty: boolean) => onDirtyChange(row, dirty), [onDirtyChange, row])
  const [credentialsOpen, setCredentialsOpen] = useState(false)
  const [configurationOpen, setConfigurationOpen] = useState(false)
  const [executionsOpen, setExecutionsOpen] = useState(false)
  const toggle = useMutation({
    mutationFn: () => {
      const current = cache.getQueryState(['teams', teamId, 'integrations'])?.status === 'error' ? undefined
        : access.currentData<{ items: Destination[] }>(cache.getQueryData(['teams', teamId, 'integrations']))?.items.find((entry) => entry.id === row.id)
      if (unavailable || hidden || !current || current.ownership_revision !== row.ownership_revision) throw new Error('Reload this destination before changing its state.')
      return apiFetch(`${base}/${row.id}/enabled`, {
        method: 'PATCH',
        body: JSON.stringify({
          expected_revision: row.ownership_revision,
          enabled: !row.enabled,
        }),
      })
    },
    onSuccess: () => {
      onChanged()
      void cache.invalidateQueries({ queryKey: ['teams', 'integration-configuration', `${base}/${row.id}`] })
    },
  })
  const configuration = configurationOpen && <TeamIntegrationConfiguration
    base={`${base}/${row.id}`} teamId={teamId} hidden={hidden}
    unavailable={unavailable} onChanged={onChanged} onDirtyChange={reportDirty}
  />
  return (
    <>
    {!hidden && <article className="space-y-2 rounded border border-slate/30 p-3">
      <h3 className="font-semibold">{row.name}</h3>
      <p className="text-sm">
        {row.event_type} · {row.enabled ? 'Enabled' : 'Paused'} ·{' '}
        {row.custodian_user_id
          ? 'Custodian assigned'
          : 'Custodian departed; adoption required'}
      </p>
      <div className="flex flex-wrap gap-2">
        <button
          className={TEAM_BUTTON}
          disabled={unavailable || toggle.isPending}
          onClick={onAdopt}
        >
          Adopt under my access
        </button>
        <button
          className={TEAM_BUTTON}
          disabled={unavailable || toggle.isPending}
          onClick={() => toggle.mutate()}
        >
          {row.enabled ? 'Pause destination' : 'Enable destination'}
        </button>
        <button
          className={TEAM_BUTTON}
          aria-expanded={credentialsOpen}
          disabled={unavailable}
          onClick={() => setCredentialsOpen(!credentialsOpen)}
        >
          Receiver credentials
        </button>
      </div>
      <button
        className={TEAM_BUTTON}
        aria-expanded={configurationOpen}
        disabled={unavailable}
        onClick={() => setConfigurationOpen(true)}
      >
        Edit destination configuration
      </button>
      <button
        className={TEAM_BUTTON}
        disabled={unavailable}
        onClick={() => setExecutionsOpen(true)}
      >
        Execution history
      </button>
      {executionsOpen && (
        <AutomationExecutions
          webhookId={row.id}
          writable={!unavailable}
          onClose={() => setExecutionsOpen(false)}
        />
      )}
      {toggle.isError && (
        <p role="alert">
          {resolveApiErrorMessage(
            toggle.error,
            'Destination state could not be changed.',
          )}
        </p>
      )}
      {credentialsOpen && (
        <ReceiverCredentials
          key={row.id}
          base={`${base}/${row.id}/receiver-credentials`}
          unavailable={unavailable}
          parentGeneration={access.generation}
          teamId={teamId}
          destinationId={row.id}
        />
      )}
    </article>}
    {configuration}
    </>
  )
}

function ReceiverCredentials({
  base,
  unavailable,
  parentGeneration,
  teamId,
  destinationId,
}: {
  base: string
  unavailable: boolean
  parentGeneration: number
  teamId: string
  destinationId: string
}) {
  const [name, setName] = useState('SIEM receiver')
  const [token, setToken] = useState('')
  const access = useQueryAccessScope(['teams', 'receiver-credentials', base])
  const client = useQueryClient()
  const parentAccess = useQueryAccessScope(['teams', teamId, 'integrations'])
  function parentReadable() {
    const key = ['teams', teamId, 'integrations']
    return client.getQueryState(key)?.status !== 'error'
      && parentAccess.currentData({ generation: parentGeneration, value: true })
      && parentAccess.currentData<{ items: Destination[] }>(client.getQueryData(key))?.items.some((row) => row.id === destinationId)
  }
  const queryKey = ['teams', 'receiver-credentials', base, parentGeneration]
  const query = useQuery({
    queryKey,
    ...access.queryOptions(({ signal }) =>
      apiFetch<{ items: Credential[] }>(base, { signal })),
    enabled: !unavailable,
  })
  const create = useMutation({
    mutationFn: () => {
      if (unavailable || !parentReadable() || access.denied || client.getQueryState(queryKey)?.status === 'error') throw new Error('Reload receiver credentials before creating a token.')
      const capture = access.captureResult()
      return apiFetch<{ token: string }>(base, {
        method: 'POST',
        body: JSON.stringify({
          name,
          expires_at: new Date(Date.now() + 90 * 86400000).toISOString(),
        }),
      }).then(capture)
    },
    onSuccess: (result) => {
      const saved = access.currentData(result)
      if (saved && !unavailable && parentReadable() && client.getQueryState(queryKey)?.status !== 'error') setToken(saved.token)
      void query.refetch()
    },
  })
  const revoke = useMutation({
    mutationFn: (id: string) => {
      const current = client.getQueryState(queryKey)?.status === 'error' ? undefined
        : access.currentData<{ items: Credential[] }>(client.getQueryData(queryKey))?.items.find((row) => row.id === id)
      if (unavailable || !parentReadable() || !current || current.revoked_at) throw new Error('Reload receiver credentials before revoking the selected token.')
      return apiFetch(`${base}/${id}`, { method: 'DELETE' })
    },
    onSuccess: () => {
      setToken('')
      void query.refetch()
    },
  })
  useEffect(() => {
    if (unavailable || query.isError) setToken('')
  }, [unavailable, query.isError])
  const data = accessibleQueryData(query)
  const busy =
    unavailable || create.isPending || revoke.isPending || query.isError
  return (
    <section
      aria-label="Receiver credentials"
      className="space-y-2 border-t border-slate/20 pt-2"
    >
      <p className="text-sm">
        Tokens expire after 90 days and only submit statuses or acknowledge
        withdrawals for this destination. They cannot read articles, execute
        hunts, or access other destinations. Save a new token now; it will not
        be shown again.
      </p>
      <fieldset disabled={busy}>
        <label>
          Credential name{' '}
          <input
            className={inputClass}
            maxLength={120}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>{' '}
        <button
          className={TEAM_BUTTON}
          disabled={!name.trim()}
          onClick={() => {
            setToken('')
            create.mutate()
          }}
        >
          Create receiver credential
        </button>
      </fieldset>
      {token && !busy && (
        <label className="block">
          New receiver token{' '}
          <input
            className={`${inputClass} w-full font-mono`}
            readOnly
            value={token}
            onFocus={(event) => event.target.select()}
          />
        </label>
      )}
      {data?.items.map((row) => (
        <div className="flex flex-wrap items-center gap-2 text-sm" key={row.id}>
          {row.name} · expires {new Date(row.expires_at).toLocaleDateString()}{' '}
          {row.revoked_at ? (
            '(revoked)'
          ) : (
            <button
              className={TEAM_BUTTON}
              disabled={busy}
              onClick={() => revoke.mutate(row.id)}
            >
              Revoke {row.name}
            </button>
          )}
        </div>
      ))}
      {(query.error || create.error || revoke.error) && (
        <p role="alert">
          {resolveApiErrorMessage(
            query.error || create.error || revoke.error,
            'Receiver credentials could not be updated.',
          )}{' '}
          <button className={TEAM_BUTTON} onClick={() => void query.refetch()}>
            Reload credentials
          </button>
        </p>
      )}
    </section>
  )
}
