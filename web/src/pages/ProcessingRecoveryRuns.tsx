import { useEffect, useRef } from 'react'
import { resolveApiErrorMessage } from '../api/errors'
import { formatDateTime } from '../utils/datetime'
import { processingAccessError, processingStageLabel, processingStateLabel } from './processingModel'
import type { ProcessingWorkspaceController } from './useProcessingWorkspace'

export function ProcessingRecoveryRuns({ controller }: { controller: ProcessingWorkspaceController }) {
  const { runsQuery, runQuery, scope, busy, canWrite } = controller
  const runs = processingAccessError(runsQuery.error) ? undefined : runsQuery.data
  const run = processingAccessError(runQuery.error) ? undefined : runQuery.data
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { if (scope.run) heading.current?.focus() }, [scope.run])
  return (
    <section className="space-y-3 border-t border-slate/20 p-3 dark:border-white/15" aria-labelledby="processing-runs-heading">
      <h3 id="processing-runs-heading" className="font-display text-lg">Your recovery runs</h3>
      <p className="text-sm text-slate dark:text-slate-300">Runs continue after navigation. Open a run to keep its status in this page’s URL. Access is checked again before processing each selected record.</p>
      {runsQuery.isLoading && <p role="status">Loading recovery runs...</p>}
      {runsQuery.isError && <p role="alert">{resolveApiErrorMessage(runsQuery.error, 'Recovery history could not be loaded. Refresh processing to retry.')}</p>}
      {runs?.items.length === 0 && <p className="text-sm">No recovery runs on this page.</p>}
      {runs && runs.items.length > 0 && (
        <ul className="flex flex-wrap gap-2" aria-label="Recovery runs">
          {runs.items.map((entry) => (
            <li key={entry.id}>
              <button type="button" className="min-h-11 rounded border border-slate/20 px-3 py-2 text-left text-sm disabled:opacity-50 dark:border-white/15"
                aria-pressed={scope.run === entry.id} disabled={busy} onClick={() => controller.changeScope('work_run', entry.id)}>
                {formatDateTime(entry.created_at)} · {processingStateLabel(entry.status)} · {entry.access_limited ? 'Source details restricted' : `${entry.total_count} records`}
              </button>
            </li>
          ))}
        </ul>
      )}
      {runs && (runs.has_more || scope.runCursor) && (
        <nav aria-label="Recovery run pages" className="flex gap-3 text-sm">
          <button type="button" className="min-h-11 rounded border px-3 py-2 disabled:opacity-50" disabled={busy || runsQuery.isFetching || !scope.runCursor} onClick={() => controller.changeScope('work_runs_cursor', '')}>Newest runs</button>
          <button type="button" className="min-h-11 rounded border px-3 py-2 disabled:opacity-50" disabled={busy || runsQuery.isFetching || !runs.has_more || !runs.next_cursor} onClick={() => controller.changeScope('work_runs_cursor', runs.next_cursor ?? '')}>Older runs</button>
        </nav>
      )}
      {scope.run && (
        <section aria-labelledby="processing-run-heading" className="rounded border border-slate/20 p-3 dark:border-white/15">
          <h4 ref={heading} tabIndex={-1} id="processing-run-heading" className="font-semibold">Selected recovery run</h4>
          {runQuery.isLoading && <p role="status" className="mt-2">Loading recovery status...</p>}
          {runQuery.isError && <p role="alert" className="mt-2">{resolveApiErrorMessage(runQuery.error, 'This run could not be loaded. Refresh processing to retry.')}</p>}
          {run && <>
            <p role="status" aria-live="polite" aria-atomic="true" className="mt-2 text-sm">
              {run.access_limited ? `${processingStateLabel(run.status)}. Source details are unavailable with current access.`
                : `${processingStateLabel(run.status)}: ${run.completed_count} completed, ${run.failed_count} failed, ${run.cancelled_count} cancelled of ${run.total_count} selected records.`}
            </p>
            <p className="mt-1 text-xs text-slate dark:text-slate-300">Updated <time dateTime={run.updated_at}>{formatDateTime(run.updated_at)}</time>{runQuery.isError ? ' · Last known status' : ''}</p>
            {run.can_cancel && <button type="button" className="mt-3 min-h-11 rounded border px-3 py-2 text-sm font-semibold disabled:opacity-50"
              disabled={!canWrite || busy || runQuery.isError} onClick={() => controller.openCancel(run)}>Cancel remaining work</button>}
            <ul className="mt-3 divide-y divide-slate/15 text-sm dark:divide-white/10" aria-label="Selected recovery results">
              {run.items.map((item) => <li key={`${item.item_id}:${item.stage}`} className="py-2">
                <p className="break-words font-semibold">{item.title ?? 'Source details unavailable'}{item.feed_name ? ` · ${item.feed_name}` : ''}</p>
                <p>{processingStageLabel(item.stage)} · {processingStateLabel(item.state)}</p>
                {item.message && <p className="mt-1">{item.message}</p>}
                {item.reason && <p className="mt-1 break-all text-xs text-slate dark:text-slate-300">Reason: {item.reason}</p>}
              </li>)}
            </ul>
            <details className="mt-3 text-xs"><summary className="min-h-11 cursor-pointer py-2 font-semibold md:min-h-0">Run identifier</summary><p className="break-all">{run.id}</p></details>
          </>}
        </section>
      )}
    </section>
  )
}
