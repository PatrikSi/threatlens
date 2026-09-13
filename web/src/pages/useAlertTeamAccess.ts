import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

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

type TeamAccessWithdrawal = {
  teamId: string
  error: ApiError
  evidenceUpdatedAt: number
}

export function useAlertTeamAccess(
  active: boolean,
  teamId: string | null | undefined,
  evidenceUpdatedAt: number,
) {
  const query = useQuery({
    queryKey: ['teams', teamId],
    queryFn: ({ signal }) => apiFetch<Team>(`/teams/${encodeURIComponent(teamId ?? '')}`, { signal }),
    enabled: active && Boolean(teamId),
    staleTime: 30_000,
    retry: false,
  })
  const currentLoss = teamId ? knownTeamAccessLoss(query.error ?? query.failureReason) : null
  const [withdrawal, setWithdrawal] = useState<TeamAccessWithdrawal | null>(null)
  useEffect(() => {
    if (!currentLoss || !teamId) return
    setWithdrawal((previous) => {
      if (previous?.error === currentLoss && previous.teamId === teamId) return previous
      return { teamId, error: currentLoss, evidenceUpdatedAt }
    })
  }, [currentLoss, evidenceUpdatedAt, teamId])

  // Successful metadata recovery alone cannot re-expose an earlier evidence snapshot.
  const awaitingEvidence = withdrawal !== null && withdrawal.teamId === teamId &&
    evidenceUpdatedAt <= withdrawal.evidenceUpdatedAt
  return {
    ...query,
    currentLoss,
    accessLoss: currentLoss ?? (awaitingEvidence ? withdrawal.error : null),
  }
}
