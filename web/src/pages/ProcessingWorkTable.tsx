import { formatDateTime } from '../utils/datetime'
import { formatDuration } from './operationsHealthPresentation'
import { processingStageLabel, processingStateLabel, processingWorkKey } from './processingModel'
import type { ProcessingWorkspaceController } from './useProcessingWorkspace'

export function ProcessingWorkTable({ controller }: { controller: ProcessingWorkspaceController }) {
  const { rows, selected, busy, canWrite, workQuery } = controller
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">Incomplete processing work. One record per article and processing stage.</caption>
        <thead className="border-y border-slate/20 text-xs dark:border-white/15">
          <tr>{['Select', 'Article and source', 'Stage and status', 'Pending age and attempts', 'Next retry and details'].map((label) => <th key={label} scope="col" className="px-3 py-2">{label}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-slate/15 dark:divide-white/10">
          {rows.map((row) => {
            const key = processingWorkKey(row)
            const stage = processingStageLabel(row.stage)
            return (
              <tr key={key}>
                <td className="px-3 py-3 align-top">
                  <input type="checkbox" className="h-4 w-4" aria-label={`Select ${stage} for ${row.title}`}
                    checked={selected.some((entry) => processingWorkKey(entry) === key)}
                    disabled={!canWrite || busy || !row.can_retry || workQuery.isError}
                    onChange={() => controller.toggle(row)} />
                </td>
                <th scope="row" className="max-w-sm px-3 py-3 align-top font-normal">
                  <p className="break-words font-semibold">{row.title}</p>
                  <button type="button" className="mt-1 min-h-11 text-left text-xs font-semibold text-cyan hover:underline disabled:opacity-50 md:min-h-0"
                    disabled={busy} onClick={() => controller.changeScope('work_feed', row.feed_id)} aria-label={`Filter processing by source ${row.feed_name}`}>
                    {row.feed_name}
                  </button>
                </th>
                <td className="px-3 py-3 align-top">
                  <p>{stage}</p>
                  <p className="mt-1 font-semibold">{processingStateLabel(row.state)}</p>
                  {!row.can_retry && <p className="mt-1 text-xs text-slate dark:text-slate-300">Not eligible for manual retry</p>}
                </td>
                <td className="px-3 py-3 align-top">
                  <p>{formatDuration(row.age_seconds)}</p>
                  <p className="mt-1 text-xs">{row.attempts} attempts</p>
                  <p className="mt-1 text-xs text-slate dark:text-slate-300">First seen <time dateTime={row.first_seen_at}>{formatDateTime(row.first_seen_at)}</time></p>
                </td>
                <td className="max-w-sm px-3 py-3 align-top">
                  <p>{row.next_retry_at ? <time dateTime={row.next_retry_at}>{formatDateTime(row.next_retry_at)}</time> : 'No retry scheduled'}</p>
                  {row.message && <p className="mt-1 break-words text-xs">{row.message}</p>}
                  <details className="mt-2 text-xs">
                    <summary className="min-h-11 cursor-pointer py-2 font-semibold md:min-h-0 md:py-0">Technical details</summary>
                    <dl className="mt-1 space-y-1 break-all">
                      <div><dt className="font-semibold">Article ID</dt><dd>{row.item_id}</dd></div>
                      {row.reason && <div><dt className="font-semibold">Reason code</dt><dd>{row.reason}</dd></div>}
                    </dl>
                  </details>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
