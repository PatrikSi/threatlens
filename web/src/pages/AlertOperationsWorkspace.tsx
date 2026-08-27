import { useEffect, useRef } from 'react'

import { resolveApiErrorMessage } from '../api/errors'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type { AlertEvaluationRequest } from '../types/alerts'
import { formatDateTime } from '../utils/datetime'
import {
  useAlertOperationsController,
  type AlertOperationsController,
  type AlertOperationsStateFilter,
} from './useAlertOperationsController'

export function AlertOperationsWorkspace({ active = true }: { active?: boolean }) {
  const controller = useAlertOperationsController(active)
  const data = controller.evaluationsQuery.data

  return (
    <section className="tl-surface min-w-0 overflow-hidden rounded-xl" aria-labelledby="alert-operations-heading">
      <header className="border-b border-slate/20 px-3 py-3 dark:border-white/10 sm:px-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 id="alert-operations-heading" className="font-display text-xl">Alert operations</h2>
            <p className="mt-1 max-w-3xl text-sm text-slate dark:text-slate-300">
              Inspect failed evaluations, review retry history, and replay recoverable dead letters.
            </p>
          </div>
          <button
            type="button"
            className="min-h-10 rounded border border-slate/30 px-3 py-1.5 text-sm font-semibold disabled:opacity-60 dark:border-white/15"
            disabled={controller.evaluationsQuery.isFetching || controller.metricsQuery.isFetching}
            onClick={() => {
              void controller.evaluationsQuery.refetch()
              void controller.metricsQuery.refetch()
            }}
          >
            {controller.evaluationsQuery.isFetching || controller.metricsQuery.isFetching ? 'Refreshing...' : 'Refresh'}
          </button>
        </div>
        <OperationsMetrics controller={controller} />
        <label htmlFor="alert-operations-state" className="mt-3 block text-xs font-semibold text-slate dark:text-slate-300">
          Evaluation state
        </label>
        <select
          id="alert-operations-state"
          className="mt-1 min-h-10 w-full rounded border border-slate/30 bg-white px-3 py-1.5 text-sm sm:max-w-64 dark:border-cyan-900/40 dark:bg-[#072019]"
          value={controller.stateFilter}
          onChange={(event) => controller.changeStateFilter(event.target.value as AlertOperationsStateFilter)}
        >
          <option value="failures">Needs attention</option>
          <option value="dead_letter">Dead letter</option>
          <option value="retry_wait">Retry scheduled</option>
          <option value="all">All evaluations</option>
        </select>
      </header>

      {controller.feedback && (
        <p role="status" className="border-b border-slate/15 bg-cyan/10 px-4 py-2 text-sm dark:border-white/10">
          {controller.feedback}
        </p>
      )}
      {controller.evaluationsQuery.isError && data && (
        <OperationsError
          error={controller.evaluationsQuery.error}
          fallback="Alert evaluations could not be refreshed"
          onRetry={() => void controller.evaluationsQuery.refetch()}
          compact
        />
      )}
      {!data && controller.evaluationsQuery.isLoading && (
        <p role="status" aria-busy="true" className="px-4 py-10 text-center text-sm text-slate dark:text-slate-300">
          Loading alert operations...
        </p>
      )}
      {!data && controller.evaluationsQuery.isError && (
        <OperationsError
          error={controller.evaluationsQuery.error}
          fallback="Alert operations could not be loaded"
          onRetry={() => void controller.evaluationsQuery.refetch()}
        />
      )}
      {data && data.items.length === 0 && (
        <div className="px-4 py-10 text-center">
          <h3 className="font-semibold">No evaluations in this state</h3>
          <p className="mt-1 text-sm text-slate dark:text-slate-300">There is currently no alert evaluation work requiring inspection.</p>
        </div>
      )}
      {data && data.items.length > 0 && (
        <div className="lg:grid lg:grid-cols-[minmax(0,1.1fr)_minmax(340px,0.9fr)]">
          <EvaluationQueue controller={controller} />
          <EvaluationDetail controller={controller} />
        </div>
      )}
      {controller.replayTarget && (
        <ReplayConfirmation controller={controller} request={controller.replayTarget} />
      )}
    </section>
  )
}

function OperationsMetrics({ controller }: { controller: AlertOperationsController }) {
  if (controller.metricsQuery.isError) {
    return (
      <OperationsError
        error={controller.metricsQuery.error}
        fallback="Thirty-day occurrence metrics could not be loaded"
        onRetry={() => void controller.metricsQuery.refetch()}
        compact
      />
    )
  }
  const entries = [
    ['Your occurrences (30 days)', controller.metrics.total],
    ['Open', controller.metrics.open],
    ['Critical', controller.metrics.critical],
    ['Suppressed', controller.metrics.suppressed],
  ] as const
  return (
    <dl className="mt-3 grid grid-cols-2 gap-px overflow-hidden border border-slate/15 bg-slate/15 sm:grid-cols-4 dark:border-white/10 dark:bg-white/10">
      {entries.map(([label, value]) => (
        <div key={label} className="bg-white px-2.5 py-2 dark:bg-[#041612] sm:px-3">
          <dt className="text-xs text-slate dark:text-slate-400">{label}</dt>
          <dd className="mt-0.5 text-lg font-semibold">{controller.metricsQuery.isLoading ? '--' : value}</dd>
        </div>
      ))}
    </dl>
  )
}

function EvaluationQueue({ controller }: { controller: AlertOperationsController }) {
  const data = controller.evaluationsQuery.data!
  const pages = Math.max(1, Math.ceil(data.total / data.page_size))
  return (
    <div className="min-w-0 border-b border-slate/15 lg:border-b-0 lg:border-r dark:border-white/10">
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-slate/15 text-xs text-slate dark:border-white/10 dark:text-slate-400">
            <tr><th className="px-3 py-2">State</th><th className="px-3 py-2">Source</th><th className="px-3 py-2">Attempts</th><th className="px-3 py-2">Updated</th></tr>
          </thead>
          <tbody className="divide-y divide-slate/10 dark:divide-white/10">
            {data.items.map((request) => (
              <tr
                key={request.id}
                aria-selected={controller.selectedId === request.id}
                className={controller.selectedId === request.id ? 'bg-cyan/10' : 'hover:bg-slate/5 dark:hover:bg-white/5'}
              >
                <td className="p-0">
                  <button
                    type="button"
                    className="w-full px-3 py-3 text-left font-semibold"
                    aria-label={`${evaluationAccessibleName(request)}, table row`}
                    aria-pressed={controller.selectedId === request.id}
                    onClick={() => controller.select(request.id)}
                  >
                    {stateLabel(request.state)}
                  </button>
                </td>
                <td className="px-3 py-3">{request.active_source}</td>
                <td className="px-3 py-3">{request.attempt_count}/{request.max_attempts}</td>
                <td className="whitespace-nowrap px-3 py-3 text-xs">{formatDateTime(request.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="divide-y divide-slate/10 md:hidden dark:divide-white/10">
        {data.items.map((request) => (
          <li key={request.id}>
            <button
              type="button"
              className={`w-full px-3 py-3 text-left ${controller.selectedId === request.id ? 'bg-cyan/10' : ''}`}
              aria-label={`${evaluationAccessibleName(request)}, mobile row`}
              aria-pressed={controller.selectedId === request.id}
              onClick={() => controller.select(request.id)}
            >
              <span className="flex items-center justify-between gap-2"><strong>{stateLabel(request.state)}</strong><span className="text-xs">{request.active_source}</span></span>
              <span className="mt-1 block text-xs text-slate dark:text-slate-400">Attempt {request.attempt_count} of {request.max_attempts} · {formatDateTime(request.updated_at)}</span>
            </button>
          </li>
        ))}
      </ul>
      <div className="flex flex-col gap-2 border-t border-slate/15 px-3 py-2 text-sm sm:flex-row sm:items-center sm:justify-between dark:border-white/10">
        <span className="text-center sm:text-left">Page {data.page} of {pages} · {data.total} results</span>
        <div className="grid grid-cols-2 gap-2 sm:flex">
          <button type="button" className="min-h-11 rounded border border-slate/25 px-3 disabled:opacity-50 sm:min-h-9 sm:px-2 dark:border-white/15" disabled={data.page <= 1} onClick={() => controller.setPage(data.page - 1)}>Previous</button>
          <button type="button" className="min-h-11 rounded border border-slate/25 px-3 disabled:opacity-50 sm:min-h-9 sm:px-2 dark:border-white/15" disabled={data.page >= pages} onClick={() => controller.setPage(data.page + 1)}>Next</button>
        </div>
      </div>
    </div>
  )
}

function EvaluationDetail({ controller }: { controller: AlertOperationsController }) {
  const request = controller.detailQuery.data
  const detailRef = useRef<HTMLElement>(null)
  useEffect(() => {
    if (request?.id === controller.selectedId) detailRef.current?.focus()
  }, [controller.selectedId, request?.id])
  if (controller.detailQuery.isLoading && !request) {
    return <p role="status" className="px-4 py-10 text-sm">Loading evaluation detail...</p>
  }
  if (controller.detailQuery.isError && !request) {
    return (
      <OperationsError
        error={controller.detailQuery.error}
        fallback="Evaluation detail could not be loaded"
        onRetry={() => void controller.detailQuery.refetch()}
      />
    )
  }
  if (!request) return null
  return (
    <aside
      ref={detailRef}
      tabIndex={-1}
      role="region"
      aria-live="polite"
      className="min-w-0 px-3 py-3 outline-none focus-visible:ring-2 focus-visible:ring-cyan sm:px-4"
      aria-labelledby="alert-evaluation-detail-heading"
    >
      {controller.detailQuery.isError && (
        <OperationsError
          error={controller.detailQuery.error}
          fallback="The displayed detail could not be refreshed"
          onRetry={() => void controller.detailQuery.refetch()}
          compact
        />
      )}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 id="alert-evaluation-detail-heading" className="font-semibold">Evaluation detail</h3>
          <p className="mt-1 break-all font-mono text-xs text-slate dark:text-slate-400">{request.id}</p>
        </div>
        {request.state === 'dead_letter' && (
          <button type="button" className="min-h-10 rounded bg-ink px-3 py-1.5 text-sm font-semibold text-white dark:bg-cyan dark:text-[#053c2e]" onClick={() => controller.setReplayTarget(request)}>Replay</button>
        )}
      </div>
      {request.last_error_message && (
        <div role="alert" className="mt-3 border border-red-300/70 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-800/60 dark:bg-red-950/20 dark:text-red-200">
          <strong>{request.last_error_code?.replaceAll('_', ' ') ?? 'Evaluation failed'}</strong>
          <p className="mt-1">{request.last_error_message}</p>
        </div>
      )}
      <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
        <DetailTerm label="State" value={stateLabel(request.state)} />
        <DetailTerm label="Active source" value={request.active_source} />
        <DetailTerm label="Accepted matches" value={String(request.accepted_match_count)} />
        <DetailTerm label="Occurrences" value={String(request.occurrence_count)} />
        <DetailTerm label="Attempts" value={`${request.attempt_count}/${request.max_attempts}`} />
        <DetailTerm label="Dispatch attempts" value={String(request.dispatch_attempt_count)} />
        <DetailTerm label="Dispatch failures" value={String(request.dispatch_failure_count)} />
        <DetailTerm
          label="Last dispatch failure"
          value={request.last_dispatch_failed_at ? formatDateTime(request.last_dispatch_failed_at) : 'None'}
        />
        <DetailTerm label="Available" value={formatDateTime(request.available_at)} />
        <DetailTerm label="Item ID" value={request.item_id} />
        <DetailTerm label="Content hash" value={request.item_content_hash} />
      </dl>
      <h4 className="mt-4 border-t border-slate/15 pt-3 text-sm font-semibold dark:border-white/10">Activity</h4>
      {controller.activityQuery.isLoading && <p role="status" className="mt-2 text-sm">Loading activity...</p>}
      {controller.activityQuery.isError && <OperationsError error={controller.activityQuery.error} fallback="Evaluation activity could not be loaded" onRetry={() => void controller.activityQuery.refetch()} compact />}
      {controller.activityQuery.data?.items.length === 0 && <p className="mt-2 text-sm text-slate dark:text-slate-300">No activity has been recorded.</p>}
      {controller.activityQuery.data && controller.activityQuery.data.items.length > 0 && (
        <>
          <ol className="mt-2 max-h-72 divide-y divide-slate/10 overflow-y-auto dark:divide-white/10">
            {controller.activityQuery.data.items.map((entry) => (
              <li key={entry.id} className="py-2 text-sm">
                <strong>{entry.action.replaceAll('_', ' ')}</strong>
                <time className="mt-0.5 block text-xs text-slate dark:text-slate-400" dateTime={entry.created_at}>{formatDateTime(entry.created_at)}</time>
                {Object.keys(entry.details_json).length > 0 && (
                  <dl className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-slate dark:text-slate-300">
                    {Object.entries(entry.details_json).map(([key, value]) => (
                      <div key={key} className="min-w-0">
                        <dt>{key.replaceAll('_', ' ')}</dt>
                        <dd className="break-words font-medium text-ink dark:text-white">{formatOperationDetail(value)}</dd>
                      </div>
                    ))}
                  </dl>
                )}
              </li>
            ))}
          </ol>
          {controller.activityQuery.data.total > controller.activityQuery.data.page_size && (
            <div className="mt-2 flex flex-col gap-2 text-xs sm:flex-row sm:items-center sm:justify-between">
              <span className="text-center sm:text-left">Activity page {controller.activityQuery.data.page} of {Math.ceil(controller.activityQuery.data.total / controller.activityQuery.data.page_size)}</span>
              <div className="grid grid-cols-2 gap-2 sm:flex">
                <button
                  type="button"
                  className="min-h-11 rounded border border-slate/25 px-2 disabled:opacity-50 sm:min-h-9 dark:border-white/15"
                  disabled={controller.activityQuery.data.page <= 1}
                  onClick={() => controller.setActivityPage(controller.activityQuery.data!.page - 1)}
                >
                  Previous activity
                </button>
                <button
                  type="button"
                  className="min-h-11 rounded border border-slate/25 px-2 disabled:opacity-50 sm:min-h-9 dark:border-white/15"
                  disabled={
                    controller.activityQuery.data.page * controller.activityQuery.data.page_size
                    >= controller.activityQuery.data.total
                  }
                  onClick={() => controller.setActivityPage(controller.activityQuery.data!.page + 1)}
                >
                  Next activity
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </aside>
  )
}

function ReplayConfirmation({ controller, request }: { controller: AlertOperationsController; request: AlertEvaluationRequest }) {
  return (
    <ConfirmDialog
      open
      title="Replay dead-letter evaluation?"
      description="The original immutable item version will be evaluated again. Duplicate occurrences and integration events remain protected by idempotency constraints."
      confirmLabel="Replay evaluation"
      confirmTone="primary"
      isConfirming={controller.replay.isPending}
      onCancel={() => controller.setReplayTarget(null)}
      onConfirm={() => controller.replay.mutate(request)}
    >
      {controller.replay.isError && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-300">
          {resolveApiErrorMessage(controller.replay.error, 'The evaluation could not be replayed')}
        </p>
      )}
    </ConfirmDialog>
  )
}

function DetailTerm({ label, value }: { label: string; value: string }) {
  return <div className="min-w-0"><dt className="text-xs text-slate dark:text-slate-400">{label}</dt><dd className="mt-0.5 break-words font-medium">{value}</dd></div>
}

function OperationsError({
  error,
  fallback,
  onRetry,
  compact = false,
}: {
  error: unknown
  fallback: string
  onRetry: () => void
  compact?: boolean
}) {
  return (
    <div role="alert" className={compact ? 'mt-3 border border-amber-300/70 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700/50 dark:bg-amber-950/20 dark:text-amber-200' : 'px-4 py-10 text-center'}>
      <p>{resolveApiErrorMessage(error, fallback)}</p>
      <button type="button" className="mt-2 min-h-9 rounded border border-current px-3 text-sm font-semibold" onClick={onRetry}>Retry</button>
    </div>
  )
}

function stateLabel(state: AlertEvaluationRequest['state']): string {
  return state.replaceAll('_', ' ')
}

function evaluationAccessibleName(request: AlertEvaluationRequest): string {
  return [
    `Evaluation ${request.id}`,
    stateLabel(request.state),
    `source ${request.active_source}`,
    `attempt ${request.attempt_count} of ${request.max_attempts}`,
    `item ${request.item_id}`,
  ].join(', ')
}

function formatOperationDetail(value: unknown): string {
  if (value === null || value === undefined) return 'None'
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return 'Unavailable'
  }
}
