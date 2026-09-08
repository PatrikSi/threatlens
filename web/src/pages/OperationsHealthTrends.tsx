import type {
  OperationsHealthHistoryResponse,
  OperationsHealthWindow,
  OperationsStatus,
} from '../types/operations'
import { formatDateTime } from '../utils/datetime'
import { AccessibleTimeSeries } from './AccessibleTimeSeries'
import {
  formatDuration,
  formatWireLabel,
  OPERATIONS_WINDOWS,
  suppressLegacyRecoveryHealth,
} from './operationsHealthPresentation'
import { OperationsStatusChip } from './OperationsStatus'

export function OperationsHealthTrends({
  history,
  window,
  loading,
  fetching,
  error,
  onWindowChange,
  onRetry,
}: {
  history?: OperationsHealthHistoryResponse
  window: OperationsHealthWindow
  loading: boolean
  fetching: boolean
  error: string
  onWindowChange: (window: OperationsHealthWindow) => void
  onRetry: () => void
}) {
  if (!history && loading) {
    return <p role="status" className="px-4 py-12 text-center text-sm text-slate dark:text-slate-300">Loading observed health history...</p>
  }
  if (!history) {
    return (
      <div role="alert" className="m-4 rounded border border-red-300/60 bg-red-50 px-3 py-3 text-sm text-red-900 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-100">
        <p>{error || 'Observed health history is unavailable.'}</p>
        <button type="button" className="mt-3 min-h-11 rounded border border-current px-3 py-2 font-semibold" onClick={onRetry}>Retry history</button>
      </div>
    )
  }
  const displayHistory = suppressLegacyRecoveryHealth(history)
  const coverage = displayHistory.coverage
  const samples = displayHistory.samples
  const transitions = statusTransitions(samples)
  const statusSegments = observedStatusSegments(displayHistory)
  const evidenceTruncated = coverage.anomaly_evidence_truncated ||
    coverage.transition_evidence_truncated
  return (
    <section className="px-3 py-3 sm:px-4 sm:py-4" aria-labelledby="operations-trends-heading">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="operations-trends-heading" className="text-base font-semibold">Observed health history</h2>
          <p className="mt-0.5 text-sm text-slate dark:text-slate-300">Server-recorded observations show when state changed and whether a repair held. Gaps are never interpolated.</p>
        </div>
        <label className="text-xs font-semibold text-slate dark:text-slate-300">
          Time range
          <select
            value={window}
            disabled={fetching}
            onChange={(event) => onWindowChange(event.target.value as OperationsHealthWindow)}
            className="ml-2 min-h-11 rounded border border-slate/30 bg-white px-2 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
          >
            {OPERATIONS_WINDOWS.map((entry) => <option key={entry.value} value={entry.value}>{entry.label}</option>)}
          </select>
        </label>
      </div>

      {error && (
        <div role="alert" className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
          <span>{error}. Displaying the last successful history response for this range.</span>
          <button type="button" className="min-h-11 rounded border border-current px-3 py-2 font-semibold md:min-h-0 md:py-1" onClick={onRetry}>Retry</button>
        </div>
      )}

      <div className="mt-3 grid gap-px overflow-hidden rounded border border-slate/15 bg-slate/15 sm:grid-cols-2 xl:grid-cols-6 dark:border-white/10 dark:bg-white/10">
        <CoverageMetric label="Coverage" value={`${coverage.coverage_percent.toFixed(1)}%`} detail={`${coverage.actual_sample_count}/${coverage.expected_sample_count} expected samples`} tone={coverage.coverage_percent < 90 ? 'warning' : 'default'} />
        <CoverageMetric
          label="Returned"
          value={coverage.returned_sample_count.toLocaleString()}
          detail={`${formatDuration(history.effective_resolution_seconds)} resolution · ${history.downsampling_strategy === 'none' ? 'raw samples' : 'transition/anomaly preserving'}`}
        />
        <CoverageMetric label="Collection gaps" value={coverage.gap_count.toLocaleString()} detail={coverage.largest_gap_seconds == null ? 'no measured gaps' : `largest ${formatDuration(coverage.largest_gap_seconds)}`} tone={coverage.gap_count ? 'warning' : 'default'} />
        <CoverageMetric label="Last sample" value={coverage.last_sample_at ? formatDateTime(coverage.last_sample_at) : 'None'} detail={coverage.collection_stale ? 'collector is stale' : 'collector current'} tone={coverage.collection_stale ? 'danger' : 'default'} />
        <CoverageMetric label="Retention" value={`${history.retention_days}d`} detail={`sampled every ${formatDuration(history.sample_interval_seconds)}`} />
        <CoverageMetric
          label="Incident fidelity"
          value={evidenceTruncated ? 'Partial' : 'Complete'}
          detail={`${coverage.returned_anomaly_sample_count}/${coverage.source_anomaly_sample_count} anomaly samples · ${coverage.returned_transition_count}/${coverage.source_transition_count} transitions`}
          tone={evidenceTruncated ? 'warning' : 'default'}
        />
      </div>

      {coverage.collection_stale && (
        <div role="alert" className="mt-3 rounded border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-100">
          Health collection has stopped advancing. The maintenance worker, scheduler, or database may be unavailable; current live probes remain independent.
        </div>
      )}

      {coverage.gap_intervals_truncated && (
        <div role="alert" className="mt-3 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
          Some collection-gap locations were omitted by the response bound. Trend lines are disabled
          so omitted gaps are not visually interpolated; narrow the time range for continuity detail.
        </div>
      )}

      {evidenceTruncated && (
        <div role="alert" className="mt-3 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
          This range exceeded the bounded response budget. Some anomaly or transition evidence is
          omitted; narrow the time range before
          concluding that an incident did not occur.
        </div>
      )}

      {samples.length === 0 ? (
        <div className="mt-4 rounded border border-dashed border-slate/25 px-4 py-10 text-center dark:border-white/15">
          <h3 className="font-semibold">No observations in this range yet</h3>
          <p className="mt-1 text-sm text-slate dark:text-slate-300">Collection starts after the upgraded maintenance worker processes its first five-minute sample. Live health remains available while history fills.</p>
        </div>
      ) : (
        <>
          <section className="mt-4 rounded border border-slate/15 px-3 py-3 dark:border-white/10" aria-labelledby="status-transitions-heading">
            <div className="flex flex-wrap items-end justify-between gap-2">
              <div>
                <h3 id="status-transitions-heading" className="font-semibold">Status transitions</h3>
                <p className="mt-0.5 text-xs text-slate dark:text-slate-400">Observed changes retained in this response; this is not an uptime or SLO calculation.</p>
              </div>
              <span className="text-xs text-slate dark:text-slate-400">{transitions.length} observed {transitions.length === 1 ? 'state' : 'states'}</span>
            </div>
            <div
              className="relative mt-3 h-3 overflow-hidden rounded-full bg-slate/15 dark:bg-white/10"
              role="img"
              aria-label="Observed status timeline; blank space marks periods without observations."
            >
              {statusSegments.map(({ sample, leftPercent, widthPercent }) => (
                <span
                  key={sample.sampled_at}
                  data-observed-status={sample.overall_status}
                  className={`absolute inset-y-0 ${statusBarClass(sample.overall_status)}`}
                  style={{ left: `${leftPercent}%`, width: `${widthPercent}%` }}
                />
              ))}
            </div>
            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[0.6875rem] text-slate dark:text-slate-400">
              <span>Positioned across the requested range</span>
              <span className="inline-flex items-center gap-1"><span className="h-2 w-3 rounded-sm bg-slate/15 dark:bg-white/10" aria-hidden="true" />Unobserved</span>
            </div>
            <ol className="mt-3 grid gap-2 text-xs sm:grid-cols-2 xl:grid-cols-3">
              {transitions.slice(-12).reverse().map((transition) => (
                <li key={`${transition.sampled_at}-${transition.overall_status}`} className="flex items-center justify-between gap-2 rounded bg-slate/5 px-2 py-2 dark:bg-white/[0.025]">
                  <OperationsStatusChip status={transition.overall_status} />
                  <time dateTime={transition.sampled_at} className="text-right text-slate dark:text-slate-400">{formatDateTime(transition.sampled_at)}</time>
                </li>
              ))}
            </ol>
          </section>

          <div className="mt-3 grid gap-3 xl:grid-cols-2">
            <AccessibleTimeSeries
              title="Worker capacity and load"
              description="Responding workers, observed execution capacity, active tasks, and reserved tasks"
              samples={samples}
              resolutionSeconds={history.effective_resolution_seconds}
              rangeStart={coverage.requested_start}
              rangeEnd={coverage.requested_end}
              gapIntervals={coverage.gap_intervals}
              gapIntervalsTruncated={coverage.gap_intervals_truncated}
              series={[
                { key: 'workers', label: 'Observed workers', color: '#0891b2', value: (sample) => sample.observed_worker_count },
                { key: 'capacity', label: 'Capacity', color: '#059669', value: (sample) => sample.total_capacity },
                { key: 'active', label: 'Active', color: '#d97706', value: (sample) => sample.active_count },
                { key: 'reserved', label: 'Reserved', color: '#dc2626', value: (sample) => sample.reserved_count },
              ]}
            />
            <AccessibleTimeSeries
              title="Durable workflow pressure"
              description="Database-authoritative pending and stale work across monitored workflows"
              samples={samples}
              resolutionSeconds={history.effective_resolution_seconds}
              rangeStart={coverage.requested_start}
              rangeEnd={coverage.requested_end}
              gapIntervals={coverage.gap_intervals}
              gapIntervalsTruncated={coverage.gap_intervals_truncated}
              series={[
                { key: 'pending', label: 'Pending', color: '#0891b2', value: (sample) => sample.backlog_pending_count },
                { key: 'stale', label: 'Stale active', color: '#dc2626', value: (sample) => sample.backlog_stale_count },
              ]}
            />
            <AccessibleTimeSeries
              title="Active findings"
              description="Critical and warning findings recorded in each health sample"
              samples={samples}
              resolutionSeconds={history.effective_resolution_seconds}
              rangeStart={coverage.requested_start}
              rangeEnd={coverage.requested_end}
              gapIntervals={coverage.gap_intervals}
              gapIntervalsTruncated={coverage.gap_intervals_truncated}
              series={[
                { key: 'critical', label: 'Critical', color: '#dc2626', value: (sample) => sample.critical_issue_count },
                { key: 'warning', label: 'Warning', color: '#d97706', value: (sample) => sample.warning_issue_count },
              ]}
            />
            <HistoryFindings history={displayHistory} evidenceTruncated={evidenceTruncated} />
            <WorkerHistoryExceptions history={displayHistory} evidenceTruncated={evidenceTruncated} />
          </div>
        </>
      )}
    </section>
  )
}

function HistoryFindings({
  history,
  evidenceTruncated,
}: {
  history: OperationsHealthHistoryResponse
  evidenceTruncated: boolean
}) {
  const findings = history.samples.filter((sample) =>
    sample.issue_codes.length > 0 || nonHealthyNonWorkerComponents(sample).length > 0,
  )
  return (
    <section className="rounded border border-slate/15 px-3 py-3 dark:border-white/10" aria-labelledby="history-findings-heading">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h3 id="history-findings-heading" className="font-semibold">Historical findings</h3>
          <p className="mt-0.5 text-xs text-slate dark:text-slate-400">Component failures and issue codes retained at each observation, including database, Redis, scheduler, storage, and backlog signals.</p>
        </div>
        <span className="text-xs text-slate dark:text-slate-400">Latest 50 shown</span>
      </div>
      {findings.length === 0 ? (
        <p className={`mt-3 text-sm ${evidenceTruncated ? 'text-amber-800 dark:text-amber-300' : 'text-emerald-800 dark:text-emerald-300'}`}>
          {evidenceTruncated
            ? 'No non-worker findings are present in the returned samples; omitted evidence prevents a range-wide conclusion.'
            : 'No non-worker findings were recorded in this range.'}
        </p>
      ) : (
        <div className="mt-3 max-h-80 divide-y divide-slate/15 overflow-auto border-y border-slate/15 dark:divide-white/10 dark:border-white/10">
          {findings.slice(-50).reverse().map((sample) => {
            const componentFindings = nonHealthyNonWorkerComponents(sample)
            return (
              <article key={sample.sampled_at} className="py-2 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <OperationsStatusChip status={sample.overall_status} />
                  <time dateTime={sample.sampled_at} className="text-slate dark:text-slate-400">{formatDateTime(sample.sampled_at)}</time>
                </div>
                {componentFindings.length > 0 && (
                  <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-1" aria-label="Affected components">
                    {componentFindings.map(([key, status]) => (
                      <li key={key}>
                        <span className="font-semibold">{formatComponentKey(key)}:</span>{' '}
                        {formatWireLabel(status)}
                      </li>
                    ))}
                  </ul>
                )}
                {sample.issue_codes.length > 0 && (
                  <p className="mt-1">
                    <span className="font-semibold">Findings:</span>{' '}
                    {sample.issue_codes.map(formatWireLabel).join(', ')}
                  </p>
                )}
              </article>
            )
          })}
        </div>
      )}
    </section>
  )
}

function WorkerHistoryExceptions({
  history,
  evidenceTruncated,
}: {
  history: OperationsHealthHistoryResponse
  evidenceTruncated: boolean
}) {
  const exceptions = history.samples.filter((sample) => sample.worker_reason !== 'healthy' || sample.missing_queues.length || sample.stale_execution_queues.length)
  return (
    <section className="rounded border border-slate/15 px-3 py-3 dark:border-white/10" aria-labelledby="history-exceptions-heading">
      <h3 id="history-exceptions-heading" className="font-semibold">Worker exceptions</h3>
      <p className="mt-0.5 text-xs text-slate dark:text-slate-400">Samples with incomplete topology, missing consumers, stale execution, or capacity pressure.</p>
      {exceptions.length === 0 ? (
        <p className={`mt-3 text-sm ${evidenceTruncated ? 'text-amber-800 dark:text-amber-300' : 'text-emerald-800 dark:text-emerald-300'}`}>
          {evidenceTruncated
            ? 'No worker exceptions are present in the returned samples; omitted evidence prevents a range-wide conclusion.'
            : 'No worker exceptions were recorded in this range.'}
        </p>
      ) : (
        <div className="mt-3 max-h-80 overflow-auto divide-y divide-slate/15 border-y border-slate/15 dark:divide-white/10 dark:border-white/10">
          {exceptions.slice(-50).reverse().map((sample) => (
            <article key={sample.sampled_at} className="py-2 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <OperationsStatusChip status={sample.worker_status} label={formatWireLabel(sample.worker_reason)} />
                <time dateTime={sample.sampled_at} className="text-slate dark:text-slate-400">{formatDateTime(sample.sampled_at)}</time>
              </div>
              {sample.missing_queues.length > 0 && <p className="mt-1"><span className="font-semibold">Missing consumers:</span> {sample.missing_queues.join(', ')}</p>}
              {sample.stale_execution_queues.length > 0 && <p className="mt-1"><span className="font-semibold">Stale execution:</span> {sample.stale_execution_queues.join(', ')}</p>}
              {sample.worker_inventory_truncated && (
                <p className="mt-1">
                  <span className="font-semibold">Worker inventory:</span>{' '}
                  {sample.responding_worker_count}/{sample.observed_worker_count} detailed
                </p>
              )}
            </article>
          ))}
        </div>
      )}
    </section>
  )
}

function nonHealthyNonWorkerComponents(
  sample: OperationsHealthHistoryResponse['samples'][number],
): [string, OperationsStatus][] {
  return Object.entries(sample.component_statuses).filter(
    ([key, status]) => key !== 'component:workers' && key !== 'workers' && status !== 'healthy',
  )
}

function formatComponentKey(key: string): string {
  const [family, component = family] = key.split(':', 2)
  const componentLabel = formatWireLabel(component)
  if (!key.includes(':') || family === 'component') return componentLabel
  if (family === 'storage') return `${componentLabel} storage`
  if (family === 'backlog') return `${componentLabel} backlog`
  return `${formatWireLabel(family)} ${componentLabel}`
}

function CoverageMetric({ label, value, detail, tone = 'default' }: { label: string; value: string; detail: string; tone?: 'default' | 'warning' | 'danger' }) {
  return (
    <div className="min-w-0 bg-white px-3 py-2.5 dark:bg-[#041612]">
      <p className="text-[0.6875rem] font-semibold uppercase tracking-wide text-slate dark:text-slate-400">{label}</p>
      <p className={`mt-0.5 truncate font-mono text-sm font-semibold ${tone === 'danger' ? 'text-red-700 dark:text-red-300' : tone === 'warning' ? 'text-amber-700 dark:text-amber-300' : ''}`} title={value}>{value}</p>
      <p className="truncate text-[0.6875rem] text-slate dark:text-slate-400" title={detail}>{detail}</p>
    </div>
  )
}

function statusTransitions(samples: OperationsHealthHistoryResponse['samples']): OperationsHealthHistoryResponse['samples'] {
  return samples.filter((sample, index) => index === 0 || samples[index - 1].overall_status !== sample.overall_status)
}

function observedStatusSegments(history: OperationsHealthHistoryResponse) {
  const requestedStart = Date.parse(history.coverage.requested_start)
  const requestedEnd = Date.parse(history.coverage.requested_end)
  const rangeMs = requestedEnd - requestedStart
  const sourceIntervalMs = history.sample_interval_seconds * 1_000
  if (!Number.isFinite(rangeMs) || rangeMs <= 0) return []
  const timedSamples = history.samples
    .map((sample) => ({ sample, time: Date.parse(sample.sampled_at) }))
    .filter(({ time }) => Number.isFinite(time))
  return timedSamples.flatMap(({ sample, time }, index) => {
    const previousTime = timedSamples[index - 1]?.time
    const nextTime = timedSamples[index + 1]?.time
    const previousGap = history.coverage.gap_intervals_truncated || (
      previousTime != null && history.coverage.gap_intervals.some((gap) =>
        statusIntervalOverlapsGap(previousTime, time, gap),
      )
    )
    const nextGap = history.coverage.gap_intervals_truncated || (
      nextTime != null && history.coverage.gap_intervals.some((gap) =>
        statusIntervalOverlapsGap(time, nextTime, gap),
      )
    )
    const observedStart = previousTime != null && !previousGap
      ? (previousTime + time) / 2
      : time - sourceIntervalMs / 2
    const observedEnd = nextTime != null && !nextGap
      ? (time + nextTime) / 2
      : time + sourceIntervalMs / 2
    const boundedStart = Math.max(requestedStart, observedStart)
    const boundedEnd = Math.min(requestedEnd, observedEnd)
    if (boundedEnd <= boundedStart) return []
    return [{
      sample,
      leftPercent: ((boundedStart - requestedStart) / rangeMs) * 100,
      widthPercent: ((boundedEnd - boundedStart) / rangeMs) * 100,
    }]
  })
}

function statusIntervalOverlapsGap(
  start: number,
  end: number,
  gap: OperationsHealthHistoryResponse['coverage']['gap_intervals'][number],
): boolean {
  if (gap.kind !== 'internal') return false
  const gapStart = Date.parse(gap.start_at)
  const gapEnd = Date.parse(gap.end_at)
  return Number.isFinite(gapStart) && Number.isFinite(gapEnd) && gapStart < end && gapEnd > start
}

function statusBarClass(status: OperationsStatus): string {
  if (status === 'healthy') return 'bg-emerald-500'
  if (status === 'degraded') return 'bg-amber-500'
  if (status === 'critical') return 'bg-red-500'
  if (status === 'unavailable') return 'bg-orange-500'
  return 'bg-slate-400'
}
