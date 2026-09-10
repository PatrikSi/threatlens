import { useState } from 'react'
import type { OperationsHealthHistoryResponse } from '../types/operations'
import { formatDateTime } from '../utils/datetime'
import { AccessibleTimeSeries } from './AccessibleTimeSeries'
import { CAPACITY_TRENDS } from './operationsCapacityPresentation'
import { formatDuration } from './operationsHealthPresentation'
import { OperationsStatusChip } from './OperationsStatus'

export function OperationsCapacityTrends({ history }: { history: OperationsHealthHistoryResponse }) {
  const [selected, setSelected] = useState('freshness')
  const trend = CAPACITY_TRENDS.find((entry) => entry.key === selected) ?? CAPACITY_TRENDS[0]
  const latest = history.samples.at(-1)
  const backlogs = latest?.backlogs?.filter((entry) => ['classification', 'tagging', 'exports'].includes(entry.key)) ?? []
  return (
    <section className="space-y-3 rounded border border-slate/15 p-3 xl:col-span-2 dark:border-white/10" aria-labelledby="capacity-trends-heading">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 id="capacity-trends-heading" className="font-semibold">Freshness and runtime pressure</h3>
        <label className="text-sm font-semibold">Capacity trend
          <select className="ml-2 min-h-11 max-w-full rounded border bg-white px-2 py-1 dark:bg-[#041612]" value={selected} onChange={(event) => setSelected(event.target.value)}>
            {CAPACITY_TRENDS.map((entry) => <option key={entry.key} value={entry.key}>{entry.title}</option>)}
          </select>
        </label>
      </div>
      <AccessibleTimeSeries
        title={trend.title} description={trend.description} series={trend.series} formatValue={trend.formatValue}
        samples={history.samples} resolutionSeconds={history.effective_resolution_seconds}
        rangeStart={history.coverage.requested_start} rangeEnd={history.coverage.requested_end}
        gapIntervals={history.coverage.gap_intervals} gapIntervalsTruncated={history.coverage.gap_intervals_truncated}
      />
      <div className="overflow-x-auto" tabIndex={0} role="group" aria-label="Latest freshness data">
        {backlogs.length > 0 ? <table className="w-full text-left text-sm">
          <caption className="pb-2 text-left text-xs text-slate dark:text-slate-300">Latest returned freshness sample: {latest ? formatDateTime(latest.sampled_at) : 'Unavailable'}. Values describe this observation, not a whole-range total.</caption>
          <thead><tr>{['Workflow', 'Status', 'Pending', 'Oldest pending', 'Warning threshold'].map((label) => <th scope="col" className="px-2 py-2" key={label}>{label}</th>)}</tr></thead>
          <tbody className="divide-y divide-slate/15 dark:divide-white/10">{backlogs.map((backlog) => <tr key={backlog.key}>
            <th scope="row" className="px-2 py-2 font-semibold">{backlog.label}</th>
            <td className="px-2 py-2"><OperationsStatusChip status={backlog.status} /></td>
            <td className="px-2 py-2">{backlog.pending_count}</td>
            <td className="px-2 py-2">{backlog.pending_count === 0 ? 'No pending work' : backlog.oldest_pending_age_seconds === null ? 'Unknown' : formatDuration(backlog.oldest_pending_age_seconds)}</td>
            <td className="px-2 py-2">{formatDuration(backlog.degraded_after_seconds)}</td>
          </tr>)}</tbody>
        </table> : <p className="text-sm text-slate dark:text-slate-300">Per-workflow freshness is unavailable in the latest returned sample. Older observations may predate this instrumentation.</p>}
      </div>
    </section>
  )
}
