import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { accessibleQueryData } from '../api/queryData'
import { captureSessionLease } from '../api/sessionLifecycle'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type { IndicatorPublication } from '../types/indicatorPublications'
import { createSecureRequestId } from '../utils/secureRandomId'
import { TEAM_BUTTON } from './teamPresentation'

interface Consumer {
  id: string; name: string; expires_at: string; retired_at: string | null
  revoked_at: string | null; sequence: number; replay_floor: number
  generation: number; last_poll_at: string | null
  last_reconciled_at?: string | null; last_reconcile_attempt_at?: string | null
  reconciliation_error_at?: string | null; reconciliation_error_code?: string | null
}
type Command = { path: string; method: 'POST' | 'DELETE'; body?: object }
type Confirmation = { row: Consumer; operation: 'retire' | 'archive' | 'revoke' }
const CONFIRMATION_COPY = {
  retire: ['Retire consumer', 'Queue withdrawals for this receiver and stop new subscriptions. Its credential remains available to acknowledge withdrawals.'],
  archive: ['Archive consumer', 'Remove this retired consumer after every withdrawal is acknowledged. The receiver credential will stop working.'],
  revoke: ['Revoke consumer credential', 'Immediately block this receiver credential. Outstanding withdrawals remain unacknowledged until access is restored by rotating its credential.'],
} as const
const SELECT_CLASS = 'rounded border bg-white p-2 text-black dark:bg-[#072019]'

export function PublicationConsumers({ teamId, publications }: { teamId: string; publications: IndicatorPublication[] }) {
  const [open, setOpen] = useState(false)
  return <section className="space-y-3 rounded border p-3">
    <button type="button" className={TEAM_BUTTON} aria-expanded={open} onClick={() => setOpen(!open)}>Publication consumers</button>
    <p className="text-sm">Team managers can register receivers for ordered updates and withdrawal acknowledgements.</p>
    {open && <ConsumerManager key={teamId} teamId={teamId} publications={publications} />}
  </section>
}

function ConsumerManager({ teamId, publications }: { teamId: string; publications: IndicatorPublication[] }) {
  const client = useQueryClient()
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const [name, setName] = useState('')
  const creation = useRef<{ name: string; id: string } | null>(null)
  const [secret, setSecret] = useState<string | null>(null)
  const [consumerId, setConsumerId] = useState('')
  const [publicationId, setPublicationId] = useState('')
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null)
  const endpoint = `/teams/${teamId}/publication-consumers`
  const consumers = useQuery({
    queryKey: ['publication-consumers', teamId], retry: false,
    queryFn: ({ signal }) => apiFetch<Consumer[]>(endpoint, { signal }),
  })
  const action = useMutation({
    mutationFn: async (input: Command) => {
      const lease = captureSessionLease()
      const response = await apiFetch<Consumer & { token?: string } | undefined>(endpoint + input.path, {
        method: input.method,
        ...(input.body ? { body: JSON.stringify(input.body) } : {}),
      })
      lease.assertCurrent()
      return response?.token
    },
    onSuccess: (token) => {
      void client.invalidateQueries({ queryKey: ['publication-consumers', teamId] })
      if (!mounted.current) return
      if (token) { setSecret(token); creation.current = null }
      setConfirmation(null)
    },
  })
  const accessLost = action.error instanceof ApiError && [401, 403, 404].includes(action.error.status)
  const values = accessLost ? undefined : accessibleQueryData(consumers)
  const blocked = action.isPending || !values
  const alreadyRegistered = action.error instanceof ApiError && action.error.code === 'consumer_already_registered'
  const eligibleConsumers = values?.filter((row) => !row.revoked_at && !row.retired_at) ?? []
  const currentPublications = publications.filter((row) => row.status !== 'withdrawn')
  const canSubscribe = eligibleConsumers.some((row) => row.id === consumerId)
    && currentPublications.some((row) => row.id === publicationId)
  function refreshAccess() {
    setSecret(null)
    setConfirmation(null)
    action.reset()
    void consumers.refetch()
  }
  function confirmAction() {
    if (!confirmation) return
    const { row, operation } = confirmation
    action.mutate({
      path: `/${row.id}${operation === 'revoke' ? '' : `/${operation}`}`,
      method: operation === 'revoke' ? 'DELETE' : 'POST',
    })
  }
  return <div className="space-y-3">
    <p className="text-sm">Receiver credentials can read opaque publication IDs and acknowledge changes. Configure a separate current scoped API credential for evidence downloads. Custodian access loss permanently withdraws that consumer’s subscriptions.</p>
    {consumers.isPending && <p role="status">Loading consumers…</p>}
    {(consumers.error || accessLost) && <p role="alert">
      {resolveApiErrorMessage(consumers.error ?? action.error, 'Consumer administration requires current team-manager and article access.')}
      {' '}<button type="button" className={TEAM_BUTTON} onClick={refreshAccess}>Retry consumer access</button>
    </p>}
    {values && <>
      {secret && <div className="rounded border border-amber-500 p-3" role="status">
        <p>Copy this receiver token now. It is shown once; rotation invalidates the previous token.</p>
        <code className="break-all select-all">{secret}</code>
        <br />
        <button type="button" className={TEAM_BUTTON} onClick={() => { setSecret(null); action.reset() }}>I have stored the token</button>
      </div>}
      <form onSubmit={(event) => {
        event.preventDefault()
        const label = name.trim()
        if (!creation.current || creation.current.name !== label) creation.current = { name: label, id: createSecureRequestId() }
        action.mutate({ path: '', method: 'POST', body: { name: label, idempotency_key: creation.current.id } })
      }}>
        <fieldset disabled={blocked || !!secret} className="flex flex-wrap gap-2">
          <label>Consumer name <input className="rounded border p-2 text-black" value={name} maxLength={100} required onChange={(e) => setName(e.target.value)} /></label>
          <button className={TEAM_BUTTON} disabled={!name.trim() || alreadyRegistered}>Register consumer</button>
        </fieldset>
      </form>
      <button type="button" className={TEAM_BUTTON} disabled={action.isPending} onClick={refreshAccess}>Refresh consumers</button>
      {alreadyRegistered && <p role="status">The registration succeeded, but the original token cannot be retrieved. Refresh consumers, then rotate the existing consumer’s credential.</p>}
      <ul className="space-y-2">{values.map((row) => <li key={row.id} className="rounded border p-2 text-sm">
        <strong>{row.name}</strong>
        {' · '}{row.retired_at ? 'Retired · ' : ''}{row.revoked_at ? 'Revoked' : `Expires ${new Date(row.expires_at).toLocaleDateString()}`} · {row.sequence} changes
        {' · last receiver poll '}{row.last_poll_at ? new Date(row.last_poll_at).toLocaleString() : 'Never'}
        <p>Last successful reconciliation: {row.last_reconciled_at ? new Date(row.last_reconciled_at).toLocaleString() : 'Not completed'}</p>
        {row.reconciliation_error_at && <p role="status" className="text-amber-800 dark:text-amber-200">
          Reconciliation failed at {new Date(row.reconciliation_error_at).toLocaleString()}. Pending updates remain outstanding; automatic retry will continue.
          {row.reconciliation_error_code && <> Reference: {row.reconciliation_error_code}.</>}
        </p>}
        <div className="flex flex-wrap gap-2">
          <button type="button" className={TEAM_BUTTON} disabled={blocked || !!row.retired_at}
            onClick={() => setConfirmation({ row, operation: 'retire' })}>Retire and withdraw</button>
          <button type="button" className={TEAM_BUTTON} disabled={blocked || !row.retired_at}
            onClick={() => setConfirmation({ row, operation: 'archive' })}>Archive acknowledged consumer</button>
          <button type="button" className={TEAM_BUTTON} disabled={blocked || !!secret}
            onClick={() => action.mutate({ path: `/${row.id}/rotate`, method: 'POST' })}>Rotate credential</button>
          <button type="button" className={TEAM_BUTTON} disabled={blocked || !!row.revoked_at}
            onClick={() => setConfirmation({ row, operation: 'revoke' })}>Revoke credential</button>
        </div>
      </li>)}</ul>
      <p className="text-sm">Retirement queues withdrawals and keeps the credential available for acknowledgements. Archive only after the receiver has acknowledged every withdrawal. Revocation immediately blocks the credential. Teams can register up to 20 consumers.</p>
      {values.length === 0 && <p>No registered consumers.</p>}
      <fieldset disabled={blocked} className="flex flex-wrap gap-2">
        <legend className="font-medium">Subscribe a consumer to an approved publication</legend>
        <label>Consumer <select className={SELECT_CLASS} value={consumerId} onChange={(e) => setConsumerId(e.target.value)}>
          <option value="">Select consumer</option>
          {eligibleConsumers.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}
        </select></label>
        <label>Publication <select className={SELECT_CLASS} value={publicationId} onChange={(e) => setPublicationId(e.target.value)}>
          <option value="">Select publication on this page</option>
          {currentPublications.map((row) => <option key={row.id} value={row.id}>{row.format} · revision {row.revision} · {new Date(row.created_at).toLocaleString()}</option>)}
        </select></label>
        <button type="button" className={TEAM_BUTTON} disabled={!canSubscribe} onClick={() => action.mutate({ path: `/${consumerId}/subscriptions`, method: 'POST', body: { publication_id: publicationId } })}>Subscribe publication</button>
      </fieldset>
      {action.isSuccess && !secret && <p role="status">Consumer updated.</p>}
    </>}
    {action.error && !accessLost && !confirmation && <p role="alert">{resolveApiErrorMessage(action.error, 'Consumer update failed. Retry with current access.')}</p>}
    <ConfirmDialog open={!!confirmation && !!values} title={confirmation ? `${CONFIRMATION_COPY[confirmation.operation][0]}: ${confirmation.row.name}` : ''}
      description={confirmation ? CONFIRMATION_COPY[confirmation.operation][1] : ''}
      confirmLabel="Confirm consumer change" isConfirming={action.isPending} onConfirm={confirmAction} onCancel={() => setConfirmation(null)}>
      {action.error && !accessLost && <p role="alert">{resolveApiErrorMessage(action.error, 'Consumer update failed. Retry with current access.')}</p>}
    </ConfirmDialog>
  </div>
}
