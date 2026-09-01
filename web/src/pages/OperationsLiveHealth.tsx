import { CheckCircle2, ChevronRight } from 'lucide-react'

import type {
  OperationsOverviewResponse,
  OperationsWorkerTopology,
} from '../types/operations'
import { formatDateTime } from '../utils/datetime'
import {
  buildOperationsSignals,
  signalKeyForIssue,
  type OperationsSignal,
} from './operationsHealthPresentation'
import {
  OperationsIssueSeverityChip,
  OperationsStatusChip,
  OperationsStatusGlyph,
} from './OperationsStatus'
import { OperationsWorkerDetail } from './OperationsWorkerDetail'

export function OperationsLiveHealth({
  overview,
  selectedSignalKey,
  onSelectSignal,
  workerTopology,
  workerLoading,
  workerFetching,
  workerError,
  onRetryWorkers,
}: {
  overview: OperationsOverviewResponse
  selectedSignalKey: string
  onSelectSignal: (key: string) => void
  workerTopology?: OperationsWorkerTopology
  workerLoading: boolean
  workerFetching: boolean
  workerError: string
  onRetryWorkers: () => void
}) {
  const signals = buildOperationsSignals(overview)
  const selectedSignal = signals.find((signal) => signal.key === selectedSignalKey) ?? signals[0]
  const runtimeComponents = overview.components.filter((component) => ['database', 'redis', 'scheduler'].includes(component.key))
  const runtimeHealthy = runtimeComponents.filter((component) => component.status === 'healthy').length
  const healthyWorkflows = overview.backlogs.filter((backlog) => backlog.status === 'healthy').length
  const dataIntegrity = overview.components.find((component) => component.key === 'encrypted_data')
  const recoveryEvidence = [
    overview.recovery.latest_backup,
    overview.recovery.latest_verify,
    overview.recovery.latest_restore_drill,
  ]
  const recoverySucceeded = recoveryEvidence.filter((run) => run?.status === 'succeeded').length

  return (
    <div className="divide-y divide-slate/15 dark:divide-white/10">
      <section className="grid gap-px overflow-hidden bg-slate/15 dark:bg-white/10 sm:grid-cols-2 xl:grid-cols-4" aria-label="Health dimensions">
        <DimensionSummary label="Runtime" value={`${runtimeHealthy}/${runtimeComponents.length}`} detail="dependencies healthy" status={dimensionStatus(runtimeComponents.map((entry) => entry.status))} />
        <DimensionSummary label="Durable workflows" value={`${healthyWorkflows}/${overview.backlogs.length}`} detail="within threshold" status={dimensionStatus(overview.backlogs.map((entry) => entry.status))} />
        <DimensionSummary label="Data integrity" value={dataIntegrity?.status === 'healthy' ? 'Verified' : 'Attention'} detail={dataIntegrity?.summary ?? 'Not measured'} status={dataIntegrity?.status ?? 'unknown'} />
        <DimensionSummary label="Recovery posture" value={`${recoverySucceeded}/3`} detail="core checks recorded" status={overview.issues.some((issue) => issue.component === 'recovery') ? 'degraded' : recoverySucceeded === 3 ? 'healthy' : 'unknown'} />
      </section>

      <ActiveFindings overview={overview} onSelectSignal={onSelectSignal} />

      <section className="grid min-h-[28rem] min-w-0 lg:grid-cols-[minmax(15rem,0.34fr)_minmax(0,1fr)]" aria-labelledby="operations-signals-heading">
        <div className="border-b border-slate/15 p-3 dark:border-white/10 lg:border-b-0 lg:border-r">
          <div className="flex items-end justify-between gap-2 px-1">
            <h2 id="operations-signals-heading" className="text-sm font-semibold uppercase tracking-wide text-slate dark:text-slate-400">Signals</h2>
            <span className="text-xs text-slate dark:text-slate-400">{signals.length} monitored</span>
          </div>
          <ul className="mt-2 space-y-1">
            {signals.map((signal) => (
              <li key={signal.key}>
                <button
                  type="button"
                  aria-current={selectedSignal?.key === signal.key ? 'true' : undefined}
                  className={`grid w-full grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2 rounded px-2 py-2 text-left transition ${selectedSignal?.key === signal.key ? 'bg-cyan/10 ring-1 ring-cyan/30' : 'hover:bg-slate/5 dark:hover:bg-white/5'}`}
                  onClick={() => onSelectSignal(signal.key)}
                >
                  <OperationsStatusGlyph status={signal.status} className="h-4 w-4" />
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-semibold">{signal.label}</span>
                    <span className="block truncate text-xs capitalize text-slate dark:text-slate-400">{signal.kind}</span>
                  </span>
                  <ChevronRight className="h-4 w-4 text-slate dark:text-slate-400" aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div className="min-w-0 p-3 sm:p-4">
          {selectedSignal?.key === 'workers' ? (
            <OperationsWorkerDetail
              topology={workerTopology}
              loading={workerLoading}
              fetching={workerFetching}
              error={workerError}
              onRetry={onRetryWorkers}
            />
          ) : selectedSignal ? (
            <GenericSignalDetail signal={selectedSignal} overview={overview} />
          ) : (
            <p className="py-8 text-center text-sm text-slate dark:text-slate-300">No health signals are available.</p>
          )}
        </div>
      </section>
    </div>
  )
}

function DimensionSummary({
  label,
  value,
  detail,
  status,
}: {
  label: string
  value: string
  detail: string
  status: OperationsOverviewResponse['overall_status']
}) {
  return (
    <div className="min-w-0 bg-white px-3 py-2.5 dark:bg-[#041612]">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[0.6875rem] font-semibold uppercase tracking-wide text-slate dark:text-slate-400">{label}</p>
        <OperationsStatusGlyph status={status} className="h-4 w-4" />
      </div>
      <p className="mt-0.5 truncate font-mono text-base font-semibold">{value}</p>
      <p className="truncate text-[0.6875rem] text-slate dark:text-slate-400" title={detail}>{detail}</p>
    </div>
  )
}

function ActiveFindings({
  overview,
  onSelectSignal,
}: {
  overview: OperationsOverviewResponse
  onSelectSignal: (key: string) => void
}) {
  const signals = buildOperationsSignals(overview)
  return (
    <section className="px-3 py-3 sm:px-4" aria-labelledby="operations-findings-heading">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 id="operations-findings-heading" className="text-sm font-semibold uppercase tracking-wide text-slate dark:text-slate-400">
            Active findings {overview.issues.length > 0 && `(${overview.issues.length})`}
          </h2>
          <p className="mt-0.5 text-xs text-slate dark:text-slate-400">Prioritized symptoms that need verification or action.</p>
        </div>
      </div>
      {overview.issues.length === 0 ? (
        <p className="mt-2 flex items-center gap-2 rounded border border-emerald-300/50 bg-emerald-50 px-3 py-2 text-sm text-emerald-900 dark:border-emerald-500/25 dark:bg-emerald-500/10 dark:text-emerald-100">
          <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
          No active operational findings.
        </p>
      ) : (
        <div className="mt-2 divide-y divide-slate/15 border-y border-slate/15 dark:divide-white/10 dark:border-white/10">
          {overview.issues.map((issue) => (
            <button
              key={issue.code}
              type="button"
              className="grid w-full gap-2 py-2.5 text-left hover:bg-slate/5 dark:hover:bg-white/[0.025] md:grid-cols-[auto_minmax(0,1fr)_minmax(13rem,0.7fr)_auto] md:items-start"
              onClick={() => onSelectSignal(signalKeyForIssue(issue, signals))}
            >
              <OperationsIssueSeverityChip severity={issue.severity} />
              <span className="min-w-0">
                <span className="block font-semibold">{issue.summary}</span>
                <span className="mt-0.5 block text-sm text-slate dark:text-slate-300">{issue.effect}</span>
              </span>
              <span className="text-sm text-slate dark:text-slate-300">
                <span className="block text-[0.6875rem] font-semibold uppercase tracking-wide text-slate dark:text-slate-400">Next action</span>
                {issue.recommended_action}
              </span>
              <ChevronRight className="hidden h-4 w-4 text-slate md:block dark:text-slate-400" aria-hidden="true" />
            </button>
          ))}
        </div>
      )}
    </section>
  )
}

function GenericSignalDetail({
  signal,
  overview,
}: {
  signal: OperationsSignal
  overview: OperationsOverviewResponse
}) {
  const signals = buildOperationsSignals(overview)
  const issues = overview.issues.filter(
    (issue) => signalKeyForIssue(issue, signals) === signal.key,
  )
  return (
    <article aria-labelledby="selected-signal-heading">
      <div className="flex min-w-0 flex-col gap-3 border-b border-slate/15 pb-4 dark:border-white/10 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate dark:text-slate-400">Selected signal · {signal.kind}</p>
          <h2 id="selected-signal-heading" className="mt-1 text-lg font-semibold">{signal.label}</h2>
          <p className="mt-1 text-sm text-slate dark:text-slate-300">{signal.summary}</p>
          {signal.checkedAt && <p className="mt-1 text-xs text-slate dark:text-slate-400">Observed {formatDateTime(signal.checkedAt)}</p>}
        </div>
        <OperationsStatusChip status={signal.status} />
      </div>

      <section className="mt-4" aria-labelledby="signal-evidence-heading">
        <h3 id="signal-evidence-heading" className="text-sm font-semibold uppercase tracking-wide text-slate dark:text-slate-400">Observed evidence</h3>
        {signal.metrics.length === 0 ? (
          <p className="mt-2 text-sm text-slate dark:text-slate-300">This probe reports state only; no additional numeric evidence is available.</p>
        ) : (
          <dl className="mt-2 grid gap-px overflow-hidden rounded border border-slate/15 bg-slate/15 sm:grid-cols-2 dark:border-white/10 dark:bg-white/10">
            {signal.metrics.map((metric) => (
              <div key={metric.label} className="bg-white px-3 py-2 dark:bg-[#041612]">
                <dt className="text-xs text-slate dark:text-slate-400">{metric.label}</dt>
                <dd className={`mt-0.5 break-words font-mono text-sm font-semibold ${metric.tone === 'danger' ? 'text-red-700 dark:text-red-300' : metric.tone === 'warning' ? 'text-amber-700 dark:text-amber-300' : ''}`}>{metric.value}</dd>
              </div>
            ))}
          </dl>
        )}
      </section>

      {issues.length > 0 && (
        <section className="mt-4 rounded border border-cyan/25 bg-cyan/5 px-3 py-3 dark:border-cyan/20 dark:bg-cyan/[0.04]" aria-labelledby="signal-actions-heading">
          <h3 id="signal-actions-heading" className="text-sm font-semibold uppercase tracking-wide text-slate dark:text-slate-300">Impact and next steps</h3>
          <div className="mt-2 space-y-3">
            {issues.map((issue) => (
              <div key={issue.code} className="grid gap-2 sm:grid-cols-2">
                <div><p className="text-xs font-semibold uppercase text-slate dark:text-slate-400">Impact</p><p className="mt-0.5 text-sm">{issue.effect}</p></div>
                <div><p className="text-xs font-semibold uppercase text-slate dark:text-slate-400">Recommended action</p><p className="mt-0.5 text-sm">{issue.recommended_action}</p></div>
              </div>
            ))}
          </div>
        </section>
      )}
    </article>
  )
}

function dimensionStatus(statuses: OperationsOverviewResponse['components'][number]['status'][]): OperationsOverviewResponse['overall_status'] {
  if (statuses.some((status) => status === 'critical')) return 'critical'
  if (statuses.some((status) => status === 'unavailable')) return 'unavailable'
  if (statuses.some((status) => status === 'degraded')) return 'degraded'
  if (statuses.some((status) => status === 'unknown')) return 'unknown'
  return 'healthy'
}
