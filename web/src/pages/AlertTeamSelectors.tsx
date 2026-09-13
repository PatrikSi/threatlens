import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { accessibleQueryData } from '../api/queryData'
import { useCurrentUser } from '../hooks/useCurrentUser'
import type { TeamMemberPage, TeamPage } from '../types/teams'
import { hasRequiredPermissions } from '../workspace/workspaceModel'

const selectClassName = 'mt-1 min-h-10 w-full rounded border border-slate/30 bg-white px-2 py-1.5 text-sm dark:border-white/20 dark:bg-[#072019]'

export function AlertQueueScopePicker({ value, onChange, disabled = false, allowAll = true, label = 'Queue ownership' }: {
  value: string
  onChange: (value: string) => void
  disabled?: boolean
  allowAll?: boolean
  label?: string
}) {
  const user = useCurrentUser()
  const [page, setPage] = useState(1)
  const canRead = hasRequiredPermissions(user.data?.access?.permissions ?? [], ['read:teams'])
  const query = useQuery({
    queryKey: ['teams', 'list', false, page, 50],
    queryFn: ({ signal }) => apiFetch<TeamPage>(`/teams?page=${page}&page_size=50`, { signal }),
    enabled: canRead,
    staleTime: 30_000,
  })
  const data = accessibleQueryData(query)
  const teams = data?.items ?? []
  const selectedMissing = value.startsWith('team:') && !teams.some((team) => value === `team:${team.id}`)
  return (
    <div className="min-w-0">
      <label className="block text-sm font-semibold">
        {label}
        <select className={selectClassName} value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
          {allowAll && <>
            <option value="all">All accessible queues</option>
            <option value="team">All team queues</option>
          </>}
          <option value="personal">My personal queue</option>
          {selectedMissing && <option value={value}>Selected team ({value.slice(5, 13)})</option>}
          {teams.map((team) => <option key={team.id} value={`team:${team.id}`}>{team.name}</option>)}
        </select>
      </label>
      {query.isLoading && canRead && <p role="status" className="mt-1 text-xs">Loading team choices...</p>}
      {query.isError && canRead && (
        <p role="alert" className="mt-1 text-xs text-red-700 dark:text-red-300">
          {resolveApiErrorMessage(query.error, 'Team choices could not be loaded')}{' '}
          <button type="button" className="underline" onClick={() => { void query.refetch() }}>Retry teams</button>
        </p>
      )}
      {data && data.total > data.page_size && (
        <SelectorPages page={page} total={data.total} pageSize={data.page_size} disabled={query.isFetching || disabled} onChange={setPage} label="team choices" />
      )}
    </div>
  )
}

export function AlertAssigneePicker({ teamId, value, onChange, disabled }: {
  teamId: string
  value: string
  onChange: (value: string) => void
  disabled: boolean
}) {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const query = useQuery({
    queryKey: ['teams', teamId, 'members', page, search],
    queryFn: ({ signal }) => apiFetch<TeamMemberPage>(
      `/teams/${encodeURIComponent(teamId)}/members?${new URLSearchParams({ page: String(page), page_size: '50', q: search })}`, { signal },
    ),
    staleTime: 30_000,
  })
  const data = accessibleQueryData(query)
  const members = data?.items ?? []
  return (
    <div className="space-y-2">
      <label className="block text-xs font-semibold">
        Find teammate by email
        <input className={selectClassName} value={search} disabled={disabled} maxLength={255}
          onChange={(event) => { setSearch(event.target.value); setPage(1) }} />
      </label>
      <label className="block text-xs font-semibold">
        Assignee
        <select className={selectClassName} value={value} disabled={disabled || query.isLoading || query.isError}
          onChange={(event) => onChange(event.target.value)}>
          <option value="">Unassigned</option>
          {value && !members.some((member) => member.id === value) && <option value={value}>Selected analyst ({value.slice(0, 8)})</option>}
          {members.map((member) => <option key={member.id} value={member.id}>{member.email}</option>)}
        </select>
      </label>
      {query.isLoading && <p role="status" className="text-xs">Loading eligible teammates...</p>}
      {query.isError && <p role="alert" className="text-xs text-red-700 dark:text-red-300">
        {resolveApiErrorMessage(query.error, 'Teammates could not be loaded')}{' '}
        <button type="button" className="underline" onClick={() => { void query.refetch() }}>Retry teammates</button>
      </p>}
      {data && data.total > data.page_size && <SelectorPages page={page} total={data.total} pageSize={data.page_size}
        disabled={disabled || query.isFetching} onChange={setPage} label="teammates" />}
    </div>
  )
}

function SelectorPages({ page, total, pageSize, disabled, onChange, label }: {
  page: number; total: number; pageSize: number; disabled: boolean; onChange: (page: number) => void; label: string
}) {
  return <div className="mt-1 flex items-center gap-2 text-xs">
    <button type="button" className="min-h-8 underline" disabled={disabled || page <= 1} onClick={() => onChange(page - 1)}>Previous {label}</button>
    <span>Page {page} / {Math.ceil(total / pageSize)} · {total} {label}</span>
    <button type="button" className="min-h-8 underline" disabled={disabled || page * pageSize >= total} onClick={() => onChange(page + 1)}>Next {label}</button>
  </div>
}
