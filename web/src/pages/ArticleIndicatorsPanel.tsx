import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { apiFetch } from '../api/client'
import { accessibleQueryData } from '../api/queryData'
import { resolveApiErrorMessage } from '../api/errors'
import { useCurrentUser } from '../hooks/useCurrentUser'
import type { Indicator, IndicatorPage } from '../types/indicators'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
import { AssessmentTeamPicker } from './AssessmentTeamPicker'
import { IndicatorEvidence } from './IndicatorEvidence'
import { IndicatorReviewDialog } from './IndicatorReviewDialog'

const BUTTON =
  'rounded border border-slate/30 px-3 py-1.5 text-sm disabled:opacity-50'

export function ArticleIndicatorsPanel({ itemId }: { itemId: string }) {
  const [open, setOpen] = useState(false)
  const [teamId, setTeamId] = useState('')
  const user = useCurrentUser()
  const identity = accessibleQueryData(user)
  const permissions = identity?.access?.permissions ?? []
  const effectiveTeamId = hasRequiredPermissions(permissions, ['read:teams'])
    ? teamId
    : ''
  if (!hasRequiredPermissions(permissions, ['read:items'])) return null
  return (
    <details
      className="tl-surface-muted mt-3 space-y-3 rounded p-3"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="cursor-pointer font-semibold">
        Indicators and team verdicts
      </summary>
      <p className="text-sm">
        Review exact source passages and normalization. An extracted mention is
        not a confirmed malicious indicator.
      </p>
      {open && (
        <>
          {hasRequiredPermissions(permissions, ['read:teams']) && (
            <AssessmentTeamPicker
              label="Indicator assessment team"
              emptyLabel="Shared extraction only"
              value={teamId}
              onChange={setTeamId}
            />
          )}
          <IndicatorList
            key={`${itemId}-${effectiveTeamId}`}
            itemId={itemId}
            teamId={effectiveTeamId}
            unavailable={user.isError}
          />
        </>
      )}
    </details>
  )
}

function IndicatorList({
  itemId,
  teamId,
  unavailable,
}: {
  itemId: string
  teamId: string
  unavailable: boolean
}) {
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<{
    indicator: Indicator
    baseline: IndicatorPage
  } | null>(null)
  const query = useQuery({
    queryKey: ['item-indicators', itemId, teamId, page],
    queryFn: ({ signal }) =>
      apiFetch<IndicatorPage>(
        `/items/${itemId}/indicators?page=${page}&page_size=20${teamId ? `&team_id=${teamId}` : ''}`,
        { signal },
      ),
    retry: false,
  })
  const data = accessibleQueryData(query)
  return (
    <div className="space-y-3">
      {query.isPending && <p role="status">Loading indicators…</p>}
      {query.isError && (
        <p role="alert">
          {resolveApiErrorMessage(
            query.error,
            'Indicators could not be refreshed.',
          )}{' '}
          <button className={BUTTON} onClick={() => void query.refetch()}>
            Retry indicators
          </button>
          {page > 1 && (
            <button
              className={BUTTON}
              disabled={query.isFetching}
              onClick={() => setPage(page - 1)}
            >
              Previous indicator page
            </button>
          )}
        </p>
      )}
      {data && (
        <>
          <p className="text-xs">
            {data.total} indicators · Source revision {data.source_revision} ·
            Extraction revision {data.extraction_revision}
          </p>
          {!data.extraction_current && (
            <p role="status">
              Indicator processing has not completed for the current source.
              Existing observations are retained for context; reviews are
              unavailable until processing finishes.
            </p>
          )}
          {!data.total && (
            <p>
              No indicators are available for this article's current extraction.
            </p>
          )}
          {data.total > 0 && data.items.length === 0 && (
            <p role="status">
              The indicator set changed and this page is empty.{' '}
              <button className={BUTTON} onClick={() => setPage(1)}>
                Return to first indicator page
              </button>
            </p>
          )}
          {data.items.map((indicator) => (
            <article
              key={indicator.id}
              className="rounded border border-slate/25 p-3"
            >
              <h4 className="break-all font-mono text-sm">{indicator.value}</h4>
              <p className="mt-1 text-xs">
                {indicator.type} · Team verdict:{' '}
                {indicator.assessment?.verdict ?? 'unreviewed'}
                {indicator.assessment && !indicator.assessment.current
                  ? ' (expired or stale)'
                  : ''}
              </p>
              <details className="mt-2">
                <summary className="cursor-pointer text-sm font-semibold">
                  Evidence and assessment
                </summary>
                <IndicatorEvidence indicator={indicator} />
              </details>
              {teamId && (
                <button
                  className={`${BUTTON} mt-2`}
                  disabled={unavailable || query.isError}
                  onClick={() => setSelected({ indicator, baseline: data })}
                >
                  {data.can_review
                    ? 'Review indicator'
                    : 'Inspect review history'}
                </button>
              )}
            </article>
          ))}
          {data.total > 20 && (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <button
                className={BUTTON}
                disabled={page === 1 || query.isFetching}
                onClick={() => setPage(page - 1)}
              >
                Previous indicators
              </button>
              <span>
                Page {page} of {Math.ceil(data.total / 20)}
              </span>
              <button
                className={BUTTON}
                disabled={page * 20 >= data.total || query.isFetching}
                onClick={() => setPage(page + 1)}
              >
                Next indicators
              </button>
            </div>
          )}
          <div className="flex flex-wrap gap-3 text-sm">
            <button
              className={BUTTON}
              disabled={query.isFetching}
              onClick={() => void query.refetch()}
            >
              Refresh indicators
            </button>
            {teamId && (
              <Link
                className="font-semibold text-cyan"
                to={`/teams?team=${teamId}&panel=indicator-suppressions`}
              >
                Team indicator suppressions
              </Link>
            )}
          </div>
          {selected && (
            <IndicatorReviewDialog
              itemId={itemId}
              teamId={teamId}
              indicator={selected.indicator}
              baseline={selected.baseline}
              writable={data.can_review && !unavailable && !query.isError}
              onClose={() => setSelected(null)}
            />
          )}
        </>
      )}
    </div>
  )
}
