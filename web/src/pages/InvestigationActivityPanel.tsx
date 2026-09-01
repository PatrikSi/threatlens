import { resolveApiErrorMessage } from '../api/errors'
import { CopyableIdentifier } from '../components/CopyableIdentifier'
import type { InvestigationActivity } from '../types/investigations'
import { formatDateTime } from '../utils/datetime'
import { formatInvestigationActivitySummary } from './investigationPageModel'
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
              </div>
              <div className="min-w-0">
                <p className="break-words font-semibold">
                  {formatInvestigationActivitySummary(activity)}
                </p>
                <p className="mt-0.5 break-words text-xs text-slate dark:text-slate-400">
                  Recorded by {activity.actor_email ?? 'System or deleted account'}
                </p>
                <ActivityTechnicalDetails activity={activity} />
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

function ActivityTechnicalDetails({ activity }: { activity: InvestigationActivity }) {
  const hasPayload = Object.keys(activity.details).length > 0
  return (
    <details className="mt-1 text-xs">
      <summary className="min-h-11 cursor-pointer py-2 font-semibold text-slate md:min-h-0 md:py-1 dark:text-slate-300">
        Technical details
      </summary>
      <dl className="grid min-w-0 gap-x-4 gap-y-2 sm:grid-cols-2">
        <div className="min-w-0">
          <dt className="text-slate dark:text-slate-400">Activity ID</dt>
          <dd className="mt-0.5">
            <CopyableIdentifier label="Activity ID" value={activity.id} />
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-slate dark:text-slate-400">Event code</dt>
          <dd className="mt-0.5 break-all font-mono text-[11px]">{activity.action}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-slate dark:text-slate-400">Recorded timestamp</dt>
          <dd className="mt-0.5 break-all font-mono text-[11px]">{activity.created_at}</dd>
        </div>
        {activity.actor_user_id && (
          <div className="min-w-0">
            <dt className="text-slate dark:text-slate-400">Actor user ID</dt>
            <dd className="mt-0.5">
              <CopyableIdentifier label="Actor user ID" value={activity.actor_user_id} />
            </dd>
          </div>
        )}
        {activity.entity_type && (
          <div className="min-w-0">
            <dt className="text-slate dark:text-slate-400">Target type</dt>
            <dd className="mt-0.5 break-all font-mono text-[11px]">{activity.entity_type}</dd>
          </div>
        )}
        {activity.entity_id && (
          <div className="min-w-0">
            <dt className="text-slate dark:text-slate-400">Target ID</dt>
            <dd className="mt-0.5">
              <CopyableIdentifier label="Target ID" value={activity.entity_id} />
            </dd>
          </div>
        )}
      </dl>
      {hasPayload && (
        <div className="mt-2">
          <p className="font-semibold text-slate dark:text-slate-300">Event payload</p>
          <pre className="mt-1 max-w-full overflow-x-auto whitespace-pre-wrap break-words rounded border border-slate/15 bg-slate/5 p-2 font-mono text-[11px] dark:border-white/10 dark:bg-white/[0.025]">
            {JSON.stringify(activity.details, null, 2)}
          </pre>
        </div>
      )}
    </details>
  )
}
