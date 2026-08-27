import { resolveApiErrorMessage } from '../api/errors'
import { formatDateTime } from '../utils/datetime'
import { formatInvestigationActivityAction } from './investigationPageModel'
import { InvestigationPagination } from './InvestigationListWorkspace'
import { InvestigationRefreshWarning } from './InvestigationShared'
import type { InvestigationDetailController } from './useInvestigationDetail'

export function InvestigationActivityPanel({
  controller,
}: {
  controller: InvestigationDetailController
}) {
  const query = controller.activityQuery
  const data = query.data

  return (
    <section aria-labelledby="investigation-activity-heading" className="min-w-0">
      <div>
        <h2 id="investigation-activity-heading" className="text-base font-semibold">
          Activity{data ? ` (${data.total})` : ''}
        </h2>
        <p className="mt-0.5 text-sm text-slate dark:text-slate-300">
          Review the append-only history of collaboration and investigation changes.
        </p>
      </div>

      {query.isFetching && data && (
        <p role="status" className="mt-2 text-xs text-slate dark:text-slate-400">
          Updating activity...
        </p>
      )}

      {query.isError && data && (
        <div className="mt-3">
          <InvestigationRefreshWarning onRetry={() => void query.refetch()}>
            {resolveApiErrorMessage(query.error, 'Activity could not be refreshed')} The last loaded
            page remains visible.
          </InvestigationRefreshWarning>
        </div>
      )}
      {!data && query.isLoading && (
        <p role="status" className="py-8 text-center text-sm text-slate dark:text-slate-300">
          Loading investigation activity...
        </p>
      )}
      {!data && query.isError && (
        <div
          role="alert"
          className="mt-3 rounded border border-red-300/70 bg-red-50 px-3 py-3 text-sm text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-200"
        >
          <p>{resolveApiErrorMessage(query.error, 'Investigation activity could not be loaded')}</p>
          <button
            type="button"
            className="mt-3 min-h-11 rounded border border-red-400 px-3 py-2 font-semibold md:min-h-0 md:py-1"
            onClick={() => void query.refetch()}
          >
            Retry
          </button>
        </div>
      )}

      {data && data.activities.length === 0 && (
        <p className="py-8 text-center text-sm text-slate dark:text-slate-300">
          No activity has been recorded.
        </p>
      )}
      {data && data.activities.length > 0 && (
        <ol className="mt-4 divide-y divide-slate/15 border-y border-slate/15 dark:divide-white/10 dark:border-white/10">
          {data.activities.map((activity) => (
            <li
              key={activity.id}
              className="grid min-w-0 gap-2 py-3 md:grid-cols-[190px_minmax(0,1fr)]"
            >
              <div className="min-w-0 text-xs text-slate dark:text-slate-400">
                <time dateTime={activity.created_at}>{formatDateTime(activity.created_at)}</time>
                <code className="mt-1 block break-all text-[10px]">{activity.created_at}</code>
              </div>
              <div className="min-w-0">
                <p className="font-semibold">
                  {formatInvestigationActivityAction(activity.action)}
                </p>
                <p className="mt-0.5 break-all text-xs text-slate dark:text-slate-400">
                  Actor: {activity.actor_email ?? 'System or deleted account'} · event:{' '}
                  <code>{activity.action}</code>
                </p>
                {(activity.entity_type || activity.entity_id) && (
                  <p className="mt-1 break-all text-xs text-slate dark:text-slate-400">
                    Target: {activity.entity_type ?? 'entity'}
                    {activity.entity_id ? ` ${activity.entity_id}` : ''}
                  </p>
                )}
                {Object.keys(activity.details).length > 0 && (
                  <details className="mt-1 text-xs">
                    <summary className="min-h-11 cursor-pointer py-2 font-semibold text-slate md:min-h-0 md:py-1 dark:text-slate-300">
                      Event details
                    </summary>
                    <pre className="mt-1 max-w-full overflow-x-auto whitespace-pre-wrap break-words rounded border border-slate/15 bg-slate/5 p-2 font-mono text-[11px] dark:border-white/10 dark:bg-white/[0.025]">
                      {JSON.stringify(activity.details, null, 2)}
                    </pre>
                  </details>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}

      {data && (
        <InvestigationPagination
          page={data.page}
          total={data.total}
          pageSize={data.page_size}
          disabled={query.isFetching}
          onPageChange={controller.setActivityPage}
        />
      )}
    </section>
  )
}
