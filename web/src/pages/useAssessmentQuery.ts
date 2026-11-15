import { useId } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { useQueryAccessScope } from '../hooks/useQueryAccessScope'
import type { TeamAssessmentResponse } from '../types/articleIntelligence'

export function useAssessmentQuery(itemId: string, teamId: string, verifyAccess: boolean) {
  const verificationId = useId()
  const access = useQueryAccessScope(['team-assessments', itemId, teamId])
  const baseQueryKey = ['team-assessments', itemId, teamId]
  // A fresh recovery check cannot be satisfied by another editor's cache write.
  const queryKey = verifyAccess ? [...baseQueryKey, 'draft-access', verificationId] : baseQueryKey
  const path = `/items/${encodeURIComponent(itemId)}/team-assessment`
  const query = useQuery({
    queryKey,
    ...access.queryOptions(({ signal }) => apiFetch<TeamAssessmentResponse>(`${path}?${new URLSearchParams({ team_id: teamId })}`, { signal })),
    refetchInterval: (state) => ['queued', 'running'].includes(access.currentData(state.state.data)?.assessment?.status ?? '') ? 3000 : 30_000,
    refetchOnMount: verifyAccess ? 'always' : true,
    gcTime: verifyAccess ? 0 : 5 * 60_000,
  })
  const data = verifyAccess && (!query.isFetchedAfterMount || query.isError) ? undefined : query.data
  return { path, query, data, queryKey, baseQueryKey, access }
}
