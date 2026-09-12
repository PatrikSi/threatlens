import { TEAM_BUTTON } from './teamPresentation'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import type { TeamMemberPage } from '../types/teams'

export function TeamMembersPanel({ teamId }: { teamId: string }) {
  const [page, setPage] = useState(1)
  const members = useQuery({
    queryKey: ['teams', teamId, 'members', page],
    queryFn: () =>
      apiFetch<TeamMemberPage>(
        `/teams/${teamId}/members?page=${page}&page_size=50`,
      ),
    refetchInterval: 30_000,
  })
  return (
    <section className="space-y-3" aria-label="Current team members">
      <h2 className="text-lg font-semibold">Current group membership</h2>
      <p className="text-sm text-slate dark:text-slate-300">
        IAM groups determine access. Expired identity-provider assertions and
        inactive accounts do not retain membership. Feature permissions still
        apply.
      </p>
      {members.isError ? (
        <div role="alert">
          <p>
            {resolveApiErrorMessage(
              members.error,
              'Unable to load team members.',
            )}
          </p>
          <button
            className={`${TEAM_BUTTON} mt-2`}
            onClick={() => void members.refetch()}
          >
            Retry members
          </button>
        </div>
      ) : members.data ? (
        <>
          <p className="text-sm">
            {members.data.total} members · Page {page} of{' '}
            {Math.max(1, Math.ceil(members.data.total / 50))}
          </p>
          <ul className="divide-y divide-slate/20">
            {members.data.items.map((member) => (
              <li
                key={member.id}
                className="flex flex-wrap justify-between gap-2 py-2 text-sm"
              >
                <span>{member.email}</span>
                <span>
                  {member.is_manager ? 'Team manager' : 'Team member'} ·{' '}
                  {member.account_role}
                </span>
              </li>
            ))}
          </ul>
          {!members.data.total && <p>No eligible members.</p>}
          <div className="flex gap-2">
            <button
              className={TEAM_BUTTON}
              disabled={page === 1 || members.isFetching}
              onClick={() => setPage((value) => value - 1)}
            >
              Previous members
            </button>
            <button
              className={TEAM_BUTTON}
              disabled={page * 50 >= members.data.total || members.isFetching}
              onClick={() => setPage((value) => value + 1)}
            >
              Next members
            </button>
          </div>
        </>
      ) : (
        <p role="status">Loading members…</p>
      )}
    </section>
  )
}
