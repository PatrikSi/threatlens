import type { UseQueryResult } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ApiError } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import type { TeamPage } from '../types/teams'
import { TEAM_BUTTON } from './teamPresentation'

export function TeamListPanel({
  list,
  selected,
  page,
  adminMode,
  changePage,
}: {
  list: UseQueryResult<TeamPage, Error>
  selected: string
  page: number
  adminMode: boolean
  changePage: (page: number) => void
}) {
  const accessLost = (error: unknown) =>
    error instanceof ApiError && [401, 403, 404].includes(error.status)
  return (
    <section
      className="tl-surface space-y-3 rounded-xl p-4"
      aria-label="Team list"
    >
      <h2 className="text-lg font-semibold">
        {adminMode ? 'All configured teams' : 'My teams'}
      </h2>
      {list.isError && (
        <p role="alert">
          {resolveApiErrorMessage(list.error, 'Unable to load teams.')}{' '}
          <button className={TEAM_BUTTON} onClick={() => void list.refetch()}>
            Retry teams
          </button>
        </p>
      )}
      {!accessLost(list.error) && list.data && (
        <>
          <p className="text-sm">
            {list.data.total} teams · Page {page} of{' '}
            {Math.max(1, Math.ceil(list.data.total / 25))}
          </p>
          {!list.data.items.length && (
            <p className="text-sm">
              No teams in this scope. An IAM administrator can create a team and
              connect its groups.
            </p>
          )}
          <ul className="space-y-2">
            {list.data.items.map((entry) => (
              <li key={entry.id}>
                <Link
                  aria-current={entry.id === selected ? 'page' : undefined}
                  className="block rounded border border-slate/20 p-3 hover:border-cyan aria-[current=page]:border-cyan"
                  to={`/teams?${new URLSearchParams({ ...(adminMode ? { mode: 'admin' } : {}), page: String(page), team: entry.id })}`}
                >
                  <span className="font-semibold">{entry.name}</span>
                  <span className="ml-2 text-xs">
                    {entry.active
                      ? entry.can_manage
                        ? 'Manager'
                        : adminMode
                          ? 'Configured'
                          : 'Member access'
                      : 'Inactive'}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          <div className="flex gap-2">
            <button
              className={TEAM_BUTTON}
              disabled={page <= 1 || list.isFetching}
              onClick={() => changePage(page - 1)}
            >
              Previous teams
            </button>
            <button
              className={TEAM_BUTTON}
              disabled={page * 25 >= list.data.total || list.isFetching}
              onClick={() => changePage(page + 1)}
            >
              Next teams
            </button>
          </div>
        </>
      )}
      {list.isLoading && <p role="status">Loading teams…</p>}
    </section>
  )
}
