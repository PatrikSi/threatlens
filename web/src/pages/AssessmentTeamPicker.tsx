import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { accessibleQueryData } from '../api/queryData'
import type { TeamPage } from '../types/teams'
import { TEAM_BUTTON } from './teamPresentation'

export function AssessmentTeamPicker({ value, onChange }: { value: string; onChange: (teamId: string) => void }) {
  const [page, setPage] = useState(1)
  const query = useQuery({
    queryKey: ['teams', 'list', false, page, 50],
    queryFn: ({ signal }) => apiFetch<TeamPage>(`/teams?page=${page}&page_size=50`, { signal }),
    staleTime: 30_000,
  })
  const data = accessibleQueryData(query)
  const teams = data?.items ?? []
  return <div className="space-y-2">
    <label className="block text-sm font-semibold">Assessment team
      <select className="mt-1 w-full rounded border border-slate/30 bg-white p-2 dark:bg-[#072019]" value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">Select a team</option>
        {value && !teams.some((team) => team.id === value) && <option value={value}>Selected team ({value.slice(0, 8)})</option>}
        {teams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}
      </select>
    </label>
    {query.isLoading && <p role="status" className="text-sm">Loading teams…</p>}
    {query.isError && <p role="alert" className="text-sm">{resolveApiErrorMessage(query.error, 'Team choices could not be loaded.')} <button className={TEAM_BUTTON} onClick={() => void query.refetch()}>Retry teams</button></p>}
    {data?.total === 0 && <p className="text-sm">Join a team workspace to request a team-specific assessment.</p>}
    {data && data.total > data.page_size && <div className="flex flex-wrap items-center gap-2 text-xs">
      <button className={TEAM_BUTTON} disabled={page <= 1 || query.isFetching} onClick={() => setPage((current) => current - 1)}>Previous teams</button>
      <span>Page {page} of {Math.ceil(data.total / data.page_size)} · {data.total} teams</span>
      <button className={TEAM_BUTTON} disabled={page * data.page_size >= data.total || query.isFetching} onClick={() => setPage((current) => current + 1)}>Next teams</button>
    </div>}
  </div>
}
