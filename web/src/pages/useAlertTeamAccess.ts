import { useQuery, useQueryClient, type QueryClient, type QueryKey } from '@tanstack/react-query'
import { useEffect, useMemo, useSyncExternalStore } from 'react'

import { ApiError, apiFetch } from '../api/client'
import type { Team } from '../types/teams'

/** A metadata permission denial does not prove that evidence permission was withdrawn. */
export function knownTeamAccessLoss(error: unknown): ApiError | null {
  if (!(error instanceof ApiError)) return null
  if (
    error.status === 401 ||
    error.status === 404 ||
    (error.status === 403 && error.code === 'team_actor_unavailable')
  ) return error
  return null
}

type EvidenceReads = {
  detail: QueryKey
  activity: QueryKey
  list: QueryKey
  listTeams: readonly string[]
}

function createTeamWithdrawals(client: QueryClient) {
  let sequence = 0
  let revision = 0
  const withdrawals = new Map<string, { error: ApiError; after: number }>()
  const pendingReads = new Map<string, number>()
  const acceptedReads = new Map<string, number>()
  const listeners = new Set<() => void>()
  const notify = () => { revision += 1; for (const listener of listeners) listener() }
  const withdraw = (teamId: string, error: ApiError) => {
    if (withdrawals.get(teamId)?.error === error) return
    withdrawals.set(teamId, { error, after: ++sequence })
    notify()
  }
  client.getQueryCache().subscribe((event) => {
    const key = event.query.queryKey
    if (key.length === 2 && key[0] === 'teams' && typeof key[1] === 'string' && key[1]
      && event.type === 'updated' && (event.action.type === 'error' || event.action.type === 'failed')) {
      // Record the actual rejected request before batched observer delivery can
      // replace it with a later outage. Both shared metadata readers use this key.
      const loss = knownTeamAccessLoss(event.action.error)
      if (loss) withdraw(key[1], loss)
    }
    if (key[0] !== 'alerts' || key[1] !== 'occurrences') return
    const hash = event.query.queryHash
    if (event.type === 'removed') {
      pendingReads.delete(hash)
      acceptedReads.delete(hash)
    } else if (event.type === 'updated' && event.action.type === 'fetch') {
      pendingReads.set(hash, ++sequence)
    } else if (event.type === 'updated' && event.action.type === 'success' && !event.action.manual) {
      // A cache write is not a read authorization. Nor can a request started
      // before the withdrawal establish fresh evidence after it completes.
      acceptedReads.set(hash, pendingReads.get(hash) ?? -1)
      notify()
    }
  })
  return {
    snapshot: () => revision,
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener) } },
    withdraw,
    loss: (teams: readonly string[], key: QueryKey) => {
      if (!teams.some((team) => withdrawals.has(team))) return null
      const hash = client.getQueryCache().find({ queryKey: key, exact: true })?.queryHash
      const accepted = hash ? acceptedReads.get(hash) ?? -1 : -1
      return teams.map((team) => withdrawals.get(team)).find((entry) => entry && accepted <= entry.after)?.error ?? null
    },
  }
}

const teamWithdrawals = new WeakMap<QueryClient, ReturnType<typeof createTeamWithdrawals>>()

export function useAlertTeamAccess(
  active: boolean,
  teamId: string | null | undefined,
  evidence: EvidenceReads,
) {
  const client = useQueryClient()
  const withdrawals = useMemo(() => {
    let store = teamWithdrawals.get(client)
    if (!store) {
      store = createTeamWithdrawals(client)
      teamWithdrawals.set(client, store)
    }
    return store
  }, [client])
  useSyncExternalStore(withdrawals.subscribe, withdrawals.snapshot, withdrawals.snapshot)
  const query = useQuery({
    queryKey: ['teams', teamId],
    queryFn: ({ signal }) => apiFetch<Team>(`/teams/${encodeURIComponent(teamId ?? '')}`, { signal }),
    enabled: active && Boolean(teamId),
    staleTime: 30_000,
    retry: false,
  })
  const currentLoss = teamId ? knownTeamAccessLoss(query.error ?? query.failureReason) : null
  useEffect(() => {
    if (currentLoss && teamId) withdrawals.withdraw(teamId, currentLoss)
  }, [currentLoss, teamId, withdrawals])

  const selectedTeam = teamId ? [teamId] : []
  const collectionCurrentLoss = teamId && evidence.listTeams.includes(teamId) ? currentLoss : null
  return {
    ...query,
    currentLoss,
    accessLoss: currentLoss ?? withdrawals.loss(selectedTeam, evidence.detail),
    activityAccessLoss: currentLoss ?? withdrawals.loss(selectedTeam, evidence.activity),
    collectionAccessLoss: collectionCurrentLoss ?? withdrawals.loss(evidence.listTeams, evidence.list),
  }
}
