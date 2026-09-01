import { useMutation, useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  CheckCircle2,
  CircleHelp,
  Clock3,
  XCircle,
  type LucideIcon,
} from 'lucide-react'
import { useState } from 'react'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { SettingsPageHeader } from '../components/SettingsPageHeader'
import {
  OperationsBacklogSnapshot,
  OperationsComponentCheck,
  OperationsDiagnosticsResponse,
  OperationsOverviewResponse,
  OperationsRecoverySnapshot,
  OperationsStatus,
  SystemOperationRun,
  SystemOperationRunListResponse,
  SystemOperationStatus,
  SystemOperationType,
} from '../types/api'
import { formatDateTime } from '../utils/datetime'

const OVERVIEW_REFRESH_MS = 30_000
const RUN_PAGE_SIZE = 20

export function OperationsPage() {
  const [runPage, setRunPage] = useState(1)
  const [operationType, setOperationType] = useState<SystemOperationType | ''>('')
  const [operationStatus, setOperationStatus] = useState<SystemOperationStatus | ''>('')
  const [downloadMessage, setDownloadMessage] = useState('')
  const [downloadError, setDownloadError] = useState('')

  const overviewQuery = useQuery({
    queryKey: ['operations', 'overview'],
    queryFn: () => apiFetch<OperationsOverviewResponse>('/operations/overview'),
    refetchInterval: OVERVIEW_REFRESH_MS,
    staleTime: 15_000,
  })
  const runsQuery = useQuery({
    queryKey: ['operations', 'runs', runPage, operationType, operationStatus],
    queryFn: () => {
      const params = new URLSearchParams({ page: String(runPage), page_size: String(RUN_PAGE_SIZE) })
      if (operationType) params.set('operation_type', operationType)
      if (operationStatus) params.set('status', operationStatus)
      return apiFetch<SystemOperationRunListResponse>(`/operations/runs?${params.toString()}`)
    },
  })
  const diagnostics = useMutation({
    mutationFn: () => apiFetch<OperationsDiagnosticsResponse>('/operations/diagnostics'),
    onSuccess: (payload) => {
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
      const objectUrl = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = objectUrl
      anchor.download = `threatlens-diagnostics-${payload.generated_at.replace(/[:.]/g, '-')}.json`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(objectUrl)
      setDownloadError('')
      setDownloadMessage('Diagnostic snapshot downloaded.')
    },
    onError: (error) => {
      setDownloadMessage('')
      setDownloadError(resolveApiErrorMessage(error, 'Diagnostic snapshot could not be downloaded'))
    },
  })

  const overview = overviewQuery.data
  const totalRunPages = Math.max(1, Math.ceil((runsQuery.data?.total ?? 0) / RUN_PAGE_SIZE))
  const lastUpdated = overviewQuery.dataUpdatedAt ? new Date(overviewQuery.dataUpdatedAt) : null
  const overviewError = overviewQuery.isError
    ? resolveApiErrorMessage(
        overviewQuery.error,
        overview
          ? 'System health could not be refreshed'
          : 'System health could not be loaded',
      )
    : ''
  const overviewActionLabel = overviewQuery.isLoading
    ? 'Loading...'
    : overviewQuery.isFetching
      ? overview
        ? 'Refreshing...'
        : 'Retrying...'
      : overview
        ? 'Refresh'
        : overviewQuery.isError
          ? 'Retry system health'
          : 'Refresh'

  return (
    <div className="space-y-3">
      <SettingsPageHeader
        scope="System"
        title="System health"
        description={overview
          ? `ThreatLens ${overview.application.version} · schema ${overview.application.schema_revision ?? 'unavailable'}`
          : overviewQuery.isError
            ? 'Deployment health is unavailable.'
            : 'Loading deployment health...'}
        badges={overview ? <StatusChip status={overview.overall_status} /> : undefined}
        actions={(
          <div className="grid w-full grid-cols-2 gap-2 sm:flex sm:w-auto">
            <button
              type="button"
              className="min-h-11 rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-60 dark:border-cyan-900/40"
              disabled={overviewQuery.isLoading || overviewQuery.isFetching}
              onClick={() => void overviewQuery.refetch()}
            >
              {overviewActionLabel}
            </button>
            <button
              type="button"
              className="min-h-11 rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-60 dark:bg-cyan dark:text-[#053c2e]"
              disabled={diagnostics.isPending}
              onClick={() => diagnostics.mutate()}
            >
              {diagnostics.isPending ? 'Preparing...' : 'Download diagnostics'}
            </button>
          </div>
        )}
      >
        <div className="py-3">
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate dark:text-slate-400">
            <span>Probe snapshot: {overview ? formatDateTime(overview.generated_at) : 'not available'}</span>
            <span>Browser refresh: {lastUpdated ? formatDateTime(lastUpdated.toISOString()) : 'not yet'}</span>
            <span>Auto-refresh: every {formatDuration(OVERVIEW_REFRESH_MS / 1_000)}</span>
          </div>
          {overviewError && (
            <div
              role="alert"
              className="mt-3 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100"
            >
              {overview ? `${overviewError}. Displaying the last successful snapshot.` : overviewError}
            </div>
          )}
          {downloadError && <InlineMessage tone="error">{downloadError}</InlineMessage>}
          {downloadMessage && <InlineMessage tone="status">{downloadMessage}</InlineMessage>}
        </div>
      </SettingsPageHeader>

      <section className="tl-surface min-w-0 overflow-hidden rounded-xl">
        {!overview && overviewQuery.isLoading && (
          <p className="px-4 py-8 text-center text-sm text-slate dark:text-slate-300">Loading system health...</p>
        )}

        {overview && (
          <div className="divide-y divide-slate/15 dark:divide-white/10">
            <OverviewSummary overview={overview} />
            <IssuesSection issues={overview.issues} />
            <ComponentsSection components={overview.components} />
            <BacklogsSection backlogs={overview.backlogs} />
            <RecoverySection recovery={overview.recovery} />
            <StorageSection storage={overview.storage} />
          </div>
        )}

        <RunsSection
          runs={runsQuery.data?.runs ?? []}
          loading={runsQuery.isLoading}
          updating={runsQuery.isFetching && Boolean(runsQuery.data)}
          error={runsQuery.isError ? resolveApiErrorMessage(runsQuery.error, 'Operation history could not be loaded') : ''}
          page={runPage}
          totalPages={totalRunPages}
          operationType={operationType}
          operationStatus={operationStatus}
          onPageChange={setRunPage}
          onRetry={() => void runsQuery.refetch()}
          onTypeChange={(value) => {
            setRunPage(1)
            setOperationType(value)
          }}
          onStatusChange={(value) => {
            setRunPage(1)
            setOperationStatus(value)
          }}
        />
      </section>
    </div>
  )
}

function OverviewSummary({ overview }: { overview: OperationsOverviewResponse }) {
  const healthyComponents = overview.components.filter((component) => component.status === 'healthy').length
  const healthyBacklogs = overview.backlogs.filter((backlog) => backlog.status === 'healthy').length
  const criticalIssues = overview.issues.filter((issue) => issue.severity === 'critical').length
  const warningIssues = overview.issues.length - criticalIssues
  const description = overview.overall_status === 'healthy'
    ? 'All monitored dependencies and work queues are within their configured thresholds.'
    : overview.overall_status === 'critical'
      ? criticalIssues
        ? `${criticalIssues} critical ${pluralize('issue', criticalIssues)} ${criticalIssues === 1 ? 'requires' : 'require'} immediate attention.`
        : 'A critical component or work queue requires immediate attention.'
      : 'One or more checks are degraded, unavailable, or awaiting operator attention.'

  return (
    <section
      className="grid gap-3 px-4 py-3 sm:px-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center"
      aria-labelledby="operations-overview-heading"
    >
      <div className="flex min-w-0 items-start gap-3">
        <StatusGlyph status={overview.overall_status} className="mt-0.5 h-5 w-5 shrink-0" />
        <div className="min-w-0">
          <h2 id="operations-overview-heading" className="font-semibold text-ink dark:text-slate-100">
            {overallHealthHeading(overview.overall_status)}
          </h2>
          <p className="mt-0.5 text-sm text-slate dark:text-slate-300">{description}</p>
        </div>
      </div>
      <dl className="grid grid-cols-3 divide-x divide-slate/15 overflow-hidden rounded border border-slate/15 dark:divide-white/10 dark:border-white/10">
        <SummaryMetric
          label="Components"
          value={`${healthyComponents}/${overview.components.length}`}
          detail="healthy"
        />
        <SummaryMetric
          label="Queues"
          value={`${healthyBacklogs}/${overview.backlogs.length}`}
          detail="within threshold"
        />
        <SummaryMetric
          label="Findings"
          value={overview.issues.length.toLocaleString()}
          detail={criticalIssues ? `${criticalIssues} critical` : warningIssues ? `${warningIssues} warning` : 'none active'}
        />
      </dl>
    </section>
  )
}

function SummaryMetric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="min-w-0 px-3 py-2">
      <dt className="text-[0.6875rem] font-semibold uppercase tracking-wide text-slate dark:text-slate-400">{label}</dt>
      <dd className="mt-0.5 flex flex-wrap items-baseline gap-x-1.5">
        <span className="font-mono text-base font-semibold text-ink dark:text-slate-100">{value}</span>
        <span className="text-[0.6875rem] text-slate dark:text-slate-400">{detail}</span>
      </dd>
    </div>
  )
}

function IssuesSection({ issues }: { issues: OperationsOverviewResponse['issues'] }) {
  if (issues.length === 0) {
    return (
      <section className="px-4 py-3 sm:px-5" aria-labelledby="operations-issues-heading">
        <h2 id="operations-issues-heading" className="text-sm font-semibold uppercase text-slate dark:text-slate-400">
          Attention
        </h2>
        <p className="mt-2 flex items-center gap-2 text-sm text-green-800 dark:text-green-300">
          <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden="true" />
          No operational issues are currently reported.
        </p>
      </section>
    )
  }
  return (
    <section className="px-4 py-3 sm:px-5" aria-labelledby="operations-issues-heading">
      <h2 id="operations-issues-heading" className="text-sm font-semibold uppercase text-slate dark:text-slate-400">
        Attention ({issues.length})
      </h2>
      <div className="mt-3 divide-y divide-slate/15 border-y border-slate/15 dark:divide-white/10 dark:border-white/10">
        {issues.map((issue) => (
          <article key={issue.code} className="grid gap-2 py-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <IssueSeverityChip severity={issue.severity} />
                <h3 className="font-semibold text-ink dark:text-slate-100">{issue.summary}</h3>
              </div>
              <p className="mt-1 text-sm text-slate dark:text-slate-300">{issue.effect}</p>
            </div>
            <div>
              <p className="text-xs font-semibold uppercase text-slate dark:text-slate-400">Recommended action</p>
              <p className="mt-1 text-sm text-ink dark:text-slate-200">{issue.recommended_action}</p>
            </div>
          </article>
        ))}
      </div>
    </section>
  )
}

function ComponentsSection({ components }: { components: OperationsComponentCheck[] }) {
  return (
    <section className="px-4 py-3 sm:px-5" aria-labelledby="operations-components-heading">
      <h2 id="operations-components-heading" className="text-sm font-semibold uppercase text-slate dark:text-slate-400">
        Components
      </h2>
      <div className="mt-3 space-y-2 sm:hidden">
        {components.map((component) => (
          <article key={component.key} className="border-b border-slate/15 pb-3 last:border-0 dark:border-white/10">
            <div className="flex items-start justify-between gap-3">
              <h3 className="font-semibold">{component.label}</h3>
              <StatusChip status={component.status} />
            </div>
            <p className="mt-1 text-sm text-slate dark:text-slate-300">{component.summary}</p>
            <ComponentEvidence component={component} />
            <p className="mt-1 text-xs text-slate dark:text-slate-400">Checked {formatDateTime(component.checked_at)}</p>
          </article>
        ))}
      </div>
      <div className="mt-3 hidden overflow-x-auto sm:block">
        <table className="min-w-[840px] w-full text-left text-sm">
          <thead>
            <tr className="border-b border-slate/20 dark:border-white/10">
              <th scope="col" className="px-2 py-2">Component</th>
              <th scope="col" className="px-2 py-2">Status</th>
              <th scope="col" className="px-2 py-2">Summary</th>
              <th scope="col" className="px-2 py-2">Operational evidence</th>
              <th scope="col" className="px-2 py-2">Checked</th>
            </tr>
          </thead>
          <tbody>
            {components.map((component) => (
              <tr key={component.key} className="border-b border-slate/10 last:border-0 dark:border-white/5">
                <td className="px-2 py-2 font-semibold">{component.label}</td>
                <td className="px-2 py-2"><StatusChip status={component.status} /></td>
                <td className="px-2 py-2 text-slate dark:text-slate-300">{component.summary}</td>
                <td className="max-w-sm px-2 py-2"><ComponentEvidence component={component} compact /></td>
                <td className="whitespace-nowrap px-2 py-2 text-xs text-slate dark:text-slate-400">
                  {formatDateTime(component.checked_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function ComponentEvidence({ component, compact = false }: { component: OperationsComponentCheck; compact?: boolean }) {
  const entries = componentEvidence(component)
  if (entries.length === 0) {
    return compact
      ? <span className="text-xs text-slate dark:text-slate-400" aria-label="No additional metrics">—</span>
      : null
  }
  return (
    <dl className={`${compact ? '' : 'mt-2'} flex flex-wrap gap-x-3 gap-y-1 text-xs`}>
      {entries.map(([label, value, tone]) => (
        <div key={label} className="flex min-w-0 gap-1">
          <dt className="text-slate dark:text-slate-400">{label}:</dt>
          <dd className={evidenceToneClassName(tone)}>
            {value}
          </dd>
        </div>
      ))}
    </dl>
  )
}

type EvidenceEntry = [label: string, value: string, tone?: 'default' | 'warning' | 'danger']

function componentEvidence(component: OperationsComponentCheck): EvidenceEntry[] {
  const metrics = component.metrics
  if (component.key === 'workers') {
    const workerCount = numberMetric(metrics, 'worker_count')
    const required = stringArrayMetric(metrics, 'required_queues')
    const covered = stringArrayMetric(metrics, 'covered_queues')
    const missing = stringArrayMetric(metrics, 'missing_queues')
    const entries: EvidenceEntry[] = []
    if (workerCount != null) entries.push(['Workers', workerCount.toLocaleString()])
    if (required) entries.push(['Queue coverage', `${covered?.length ?? 0}/${required.length}`])
    if (missing?.length) entries.push(['Missing', missing.join(', '), 'danger'])
    return entries
  }
  if (component.key === 'scheduler') {
    const threshold = numberMetric(metrics, 'stale_after_seconds')
    const schedulerAge = numberMetric(metrics, 'scheduler_age_seconds')
    const roundTripAge = numberMetric(metrics, 'worker_round_trip_age_seconds')
    const schedulerReason = stringMetric(metrics, 'scheduler_reason')
    const roundTripReason = stringMetric(metrics, 'worker_round_trip_reason')
    const unhealthyTone = component.status === 'degraded' ? 'warning' : 'danger'
    return [
      ['Scheduler heartbeat', formatHeartbeatEvidence(schedulerAge, threshold, schedulerReason), schedulerReason && schedulerReason !== 'healthy' ? unhealthyTone : 'default'],
      ['Worker round trip', formatHeartbeatEvidence(roundTripAge, threshold, roundTripReason), roundTripReason && roundTripReason !== 'healthy' ? unhealthyTone : 'default'],
    ]
  }
  if (component.key === 'encrypted_data') {
    const totalRecords = numberMetric(metrics, 'total_records')
    const unreadableFields = numberMetric(metrics, 'unreadable_fields')
    const scanComplete = booleanMetric(metrics, 'scan_complete')
    const scannedAt = stringMetric(metrics, 'inventory_scanned_at')
    const entries: EvidenceEntry[] = []
    if (totalRecords != null) entries.push(['Records inspected', totalRecords.toLocaleString()])
    if (unreadableFields != null) entries.push(['Unreadable fields', unreadableFields.toLocaleString(), unreadableFields ? 'danger' : 'default'])
    if (scanComplete != null) entries.push(['Coverage', scanComplete ? 'Complete inventory' : 'Bounded sample'])
    if (scannedAt) entries.push(['Inventory scanned', formatDateTime(scannedAt)])
    return entries
  }
  return []
}

function evidenceToneClassName(tone: EvidenceEntry[2]): string {
  if (tone === 'danger') return 'font-semibold text-red-700 dark:text-red-300'
  if (tone === 'warning') return 'font-semibold text-amber-700 dark:text-amber-300'
  return 'font-medium text-ink dark:text-slate-200'
}

function BacklogsSection({ backlogs }: { backlogs: OperationsBacklogSnapshot[] }) {
  return (
    <section className="px-4 py-3 sm:px-5" aria-labelledby="operations-backlogs-heading">
      <h2 id="operations-backlogs-heading" className="text-sm font-semibold uppercase text-slate dark:text-slate-400">
        Work queues
      </h2>
      {backlogs.length === 0 ? (
        <p className="mt-2 text-sm text-slate dark:text-slate-300">No durable backlogs were reported.</p>
      ) : (
        <div className="mt-3 grid gap-px overflow-hidden border border-slate/15 bg-slate/15 sm:grid-cols-[repeat(auto-fit,minmax(18rem,1fr))] dark:border-white/10 dark:bg-white/10">
          {backlogs.map((backlog) => (
            <article key={backlog.key} className="min-w-0 bg-white px-3 py-3 dark:bg-[#041612]">
              <div className="flex items-start justify-between gap-3">
                <h3 className="font-semibold">{backlog.label}</h3>
                <StatusChip status={backlog.status} />
              </div>
              <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
                <Metric label="Pending" value={backlog.pending_count} />
                <Metric label="Active" value={backlog.active_count} />
                <Metric label="Stale active" value={backlog.stale_count} tone={backlog.stale_count ? 'danger' : 'default'} />
                <Metric label="Failed records" value={backlog.failed_count} />
              </dl>
              <div className="mt-3 border-t border-slate/15 pt-2 text-xs text-slate dark:border-white/10 dark:text-slate-400">
                {backlog.oldest_pending_age_seconds == null
                  ? `No pending work · warning threshold ${formatDuration(backlog.degraded_after_seconds)}`
                  : `Oldest pending ${formatDuration(backlog.oldest_pending_age_seconds)} · warning threshold ${formatDuration(backlog.degraded_after_seconds)}`}
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}

function RecoverySection({ recovery }: { recovery: OperationsRecoverySnapshot }) {
  const entries: Array<[string, SystemOperationRun | null]> = [
    ['Latest backup', recovery.latest_backup],
    ['Latest verification', recovery.latest_verify],
    ['Latest restore drill', recovery.latest_restore_drill],
    ['Latest restore', recovery.latest_restore],
  ]
  return (
    <section className="px-4 py-3 sm:px-5" aria-labelledby="operations-recovery-heading">
      <h2 id="operations-recovery-heading" className="text-sm font-semibold uppercase text-slate dark:text-slate-400">
        Recovery evidence
      </h2>
      <div className="mt-3 grid gap-px overflow-hidden border border-slate/15 bg-slate/15 sm:grid-cols-2 xl:grid-cols-4 dark:border-white/10 dark:bg-white/10">
        {entries.map(([label, run]) => (
          <article key={label} className="bg-white px-3 py-3 dark:bg-[#041612]">
            <p className="text-xs font-semibold uppercase text-slate dark:text-slate-400">{label}</p>
            {run ? (
              <>
                <div className="mt-2"><RunStatusChip status={run.status} /></div>
                <dl className="mt-2 space-y-1 text-xs">
                  <div className="flex justify-between gap-3">
                    <dt className="text-slate dark:text-slate-400">Recorded</dt>
                    <dd className="text-right font-medium">{formatDateTime(run.finished_at ?? run.started_at)}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-slate dark:text-slate-400">Duration</dt>
                    <dd className="font-medium">{run.finished_at ? formatDuration(runDurationSeconds(run)) : 'In progress'}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-slate dark:text-slate-400">Source</dt>
                    <dd className="truncate font-medium" title={run.source}>{formatWireLabel(run.source)}</dd>
                  </div>
                </dl>
                {run.error_message && <p className="mt-1 text-xs text-red-700 dark:text-red-300">{run.error_message}</p>}
              </>
            ) : (
              <div className="mt-2"><StatusChip status="unknown" label="Not recorded" /></div>
            )}
          </article>
        ))}
      </div>
    </section>
  )
}

function StorageSection({ storage }: { storage: OperationsOverviewResponse['storage'] }) {
  if (storage.length === 0) return null
  return (
    <section className="px-4 py-3 sm:px-5" aria-labelledby="operations-storage-heading">
      <h2 id="operations-storage-heading" className="text-sm font-semibold uppercase text-slate dark:text-slate-400">
        Storage indicators
      </h2>
      <div className="mt-3 divide-y divide-slate/15 border-y border-slate/15 dark:divide-white/10 dark:border-white/10">
        {storage.map((indicator) => (
          <div key={indicator.key} className="grid gap-2 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(16rem,0.8fr)_auto] sm:items-center">
            <div className="flex items-center justify-between gap-3 sm:block">
              <span className="font-semibold">{indicator.label}</span>
              <span className="sm:hidden"><StorageStatusChip indicator={indicator} /></span>
            </div>
            <StorageEvidence indicator={indicator} />
            <span className="hidden sm:block"><StorageStatusChip indicator={indicator} /></span>
          </div>
        ))}
      </div>
    </section>
  )
}

function StorageStatusChip({ indicator }: { indicator: OperationsOverviewResponse['storage'][number] }) {
  if (indicator.total_bytes == null || indicator.percent_used == null) {
    return <StatusChip status="unknown" label={indicator.used_bytes == null ? 'Unavailable' : 'Capacity unknown'} />
  }
  return <StatusChip status={indicator.status} />
}

function StorageEvidence({ indicator }: { indicator: OperationsOverviewResponse['storage'][number] }) {
  if (indicator.total_bytes == null || indicator.percent_used == null) {
    return (
      <div className="text-sm text-slate dark:text-slate-300">
        <span>{indicator.used_bytes == null ? 'Measurement unavailable' : `${formatBytes(indicator.used_bytes)} logical size`}</span>
        <span className="mt-0.5 block text-xs text-slate dark:text-slate-400">Capacity is not visible to this probe.</span>
      </div>
    )
  }
  const barClassName = indicator.status === 'critical'
    ? 'bg-red-500'
    : indicator.status === 'degraded'
      ? 'bg-amber-500'
      : 'bg-emerald-500'
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap justify-between gap-x-3 text-xs text-slate dark:text-slate-300">
        <span>{formatBytes(indicator.used_bytes)} used of {formatBytes(indicator.total_bytes)}</span>
        <span>{formatBytes(indicator.available_bytes)} available · {indicator.percent_used.toFixed(1)}%</span>
      </div>
      <div
        role="progressbar"
        aria-label={`${indicator.label} usage`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={indicator.percent_used}
        className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate/15 dark:bg-white/10"
      >
        <div className={`h-full rounded-full ${barClassName}`} style={{ width: `${indicator.percent_used}%` }} />
      </div>
    </div>
  )
}

function RunsSection({
  runs,
  loading,
  updating,
  error,
  page,
  totalPages,
  operationType,
  operationStatus,
  onPageChange,
  onRetry,
  onTypeChange,
  onStatusChange,
}: {
  runs: SystemOperationRun[]
  loading: boolean
  updating: boolean
  error: string
  page: number
  totalPages: number
  operationType: SystemOperationType | ''
  operationStatus: SystemOperationStatus | ''
  onPageChange: (page: number) => void
  onRetry: () => void
  onTypeChange: (value: SystemOperationType | '') => void
  onStatusChange: (value: SystemOperationStatus | '') => void
}) {
  return (
    <section className="border-t border-slate/15 px-4 py-3 dark:border-white/10 sm:px-5" aria-labelledby="operations-runs-heading">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h2 id="operations-runs-heading" className="text-sm font-semibold uppercase text-slate dark:text-slate-400">
          Operation history
        </h2>
        <div className="grid w-full grid-cols-2 gap-2 sm:flex sm:w-auto">
          <label className="text-xs font-semibold text-slate dark:text-slate-300">
            Type
            <select
              value={operationType}
              onChange={(event) => onTypeChange(event.target.value as SystemOperationType | '')}
              className="mt-1 block min-h-11 w-full rounded border border-slate/30 bg-white px-2 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            >
              <option value="">All</option>
              <option value="backup">Backup</option>
              <option value="verify">Verification</option>
              <option value="restore_drill">Restore drill</option>
              <option value="restore">Restore</option>
              <option value="diagnostics">Diagnostics</option>
            </select>
          </label>
          <label className="text-xs font-semibold text-slate dark:text-slate-300">
            Status
            <select
              value={operationStatus}
              onChange={(event) => onStatusChange(event.target.value as SystemOperationStatus | '')}
              className="mt-1 block min-h-11 w-full rounded border border-slate/30 bg-white px-2 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            >
              <option value="">All</option>
              <option value="running">Running</option>
              <option value="succeeded">Succeeded</option>
              <option value="failed">Failed</option>
            </select>
          </label>
        </div>
      </div>
      {error && (
        <div
          role="alert"
          className="mt-3 flex flex-wrap items-center justify-between gap-2 text-sm text-red-700 dark:text-red-300"
        >
          <span>
            {error}
            {runs.length > 0 ? ' The last loaded operation history remains visible.' : ''}
          </span>
          <button
            type="button"
            className="min-h-11 rounded border border-current px-3 py-2 font-semibold"
            onClick={onRetry}
            disabled={updating}
          >
            {updating ? 'Retrying...' : 'Retry history'}
          </button>
        </div>
      )}
      {loading && <p className="py-4 text-center text-sm text-slate dark:text-slate-300">Loading operation history...</p>}
      {updating && !loading && (
        <p role="status" className="mt-3 text-sm text-slate dark:text-slate-300">
          Updating operation history for the selected filters...
        </p>
      )}
      {!loading && !error && runs.length === 0 && (
        <p className="mt-3 border-y border-dashed border-slate/20 py-4 text-center text-sm text-slate dark:border-white/10 dark:text-slate-300">
          No operation runs match these filters.
        </p>
      )}
      <div
        aria-busy={updating}
        className={`mt-3 space-y-2 sm:hidden ${updating ? 'opacity-60' : ''}`}
      >
        {runs.map((run) => <RunMobileRow key={run.id} run={run} />)}
      </div>
      {runs.length > 0 && (
        <div
          aria-busy={updating}
          className={`mt-3 hidden overflow-x-auto sm:block ${updating ? 'opacity-60' : ''}`}
        >
          <table className="min-w-[760px] w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate/20 dark:border-white/10">
                <th scope="col" className="px-2 py-2">Started</th>
                <th scope="col" className="px-2 py-2">Operation</th>
                <th scope="col" className="px-2 py-2">Status</th>
                <th scope="col" className="px-2 py-2">Source</th>
                <th scope="col" className="px-2 py-2">Result</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id} className="border-b border-slate/10 last:border-0 dark:border-white/5">
                  <td className="whitespace-nowrap px-2 py-2">{formatDateTime(run.started_at)}</td>
                  <td className="px-2 py-2 font-semibold">{formatOperationType(run.operation_type)}</td>
                  <td className="px-2 py-2"><RunStatusChip status={run.status} /></td>
                  <td className="px-2 py-2 text-slate dark:text-slate-300">{run.source}</td>
                  <td className="max-w-sm px-2 py-2 text-slate dark:text-slate-300">
                    {run.error_message ?? (run.finished_at ? formatDuration(runDurationSeconds(run)) : 'In progress')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="mt-3 grid grid-cols-[auto_1fr_auto] items-center gap-2 text-sm sm:flex sm:justify-between">
        <button
          type="button"
          className="min-h-11 rounded border border-slate/30 px-3 py-2 disabled:opacity-50 dark:border-cyan-900/40"
          disabled={page <= 1 || updating}
          onClick={() => onPageChange(page - 1)}
        >
          Previous
        </button>
        <span className="text-center">Page {page} of {totalPages}</span>
        <button
          type="button"
          className="min-h-11 rounded border border-slate/30 px-3 py-2 disabled:opacity-50 dark:border-cyan-900/40"
          disabled={page >= totalPages || updating}
          onClick={() => onPageChange(page + 1)}
        >
          Next
        </button>
      </div>
    </section>
  )
}

function RunMobileRow({ run }: { run: SystemOperationRun }) {
  return (
    <article className="border-b border-slate/15 pb-3 last:border-0 dark:border-white/10">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold">{formatOperationType(run.operation_type)}</h3>
          <p className="mt-0.5 text-xs text-slate dark:text-slate-400">{formatDateTime(run.started_at)}</p>
        </div>
        <RunStatusChip status={run.status} />
      </div>
      <p className="mt-2 text-sm text-slate dark:text-slate-300">
        {run.error_message ?? (run.finished_at ? formatDuration(runDurationSeconds(run)) : 'In progress')}
      </p>
    </article>
  )
}

function Metric({ label, value, tone = 'default' }: { label: string; value: number; tone?: 'default' | 'danger' }) {
  return (
    <div>
      <dt className="text-xs text-slate dark:text-slate-400">{label}</dt>
      <dd className={`font-mono font-semibold ${tone === 'danger' ? 'text-red-700 dark:text-red-300' : ''}`}>
        {value.toLocaleString()}
      </dd>
    </div>
  )
}

function StatusChip({ status, label }: { status: OperationsStatus; label?: string }) {
  const presentation = statusPresentation(status)
  const Icon = presentation.icon
  return (
    <span className={`tl-chip ${presentation.chipClassName}`} data-status={status}>
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      {label ?? formatWireLabel(status)}
    </span>
  )
}

function RunStatusChip({ status }: { status: SystemOperationStatus }) {
  const presentation = status === 'succeeded'
    ? { icon: CheckCircle2, chipClassName: 'tl-chip-success' }
    : status === 'failed'
      ? { icon: XCircle, chipClassName: 'tl-chip-danger' }
      : { icon: Clock3, chipClassName: 'tl-chip-warning' }
  const Icon = presentation.icon
  return (
    <span className={`tl-chip ${presentation.chipClassName}`} data-operation-status={status}>
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      {formatWireLabel(status)}
    </span>
  )
}

function IssueSeverityChip({ severity }: { severity: OperationsOverviewResponse['issues'][number]['severity'] }) {
  const critical = severity === 'critical'
  const Icon = critical ? XCircle : AlertTriangle
  return (
    <span className={`tl-chip ${critical ? 'tl-chip-danger' : 'tl-chip-warning'}`} data-issue-severity={severity}>
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      {formatWireLabel(severity)}
    </span>
  )
}

function StatusGlyph({ status, className = '' }: { status: OperationsStatus; className?: string }) {
  const presentation = statusPresentation(status)
  const Icon = presentation.icon
  return <Icon className={`${presentation.iconClassName} ${className}`} aria-hidden="true" />
}

function statusPresentation(status: OperationsStatus): {
  icon: LucideIcon
  chipClassName: string
  iconClassName: string
} {
  if (status === 'healthy') {
    return { icon: CheckCircle2, chipClassName: 'tl-chip-success', iconClassName: 'text-emerald-600 dark:text-emerald-400' }
  }
  if (status === 'degraded') {
    return { icon: AlertTriangle, chipClassName: 'tl-chip-warning', iconClassName: 'text-amber-600 dark:text-amber-400' }
  }
  if (status === 'critical' || status === 'unavailable') {
    return { icon: XCircle, chipClassName: 'tl-chip-danger', iconClassName: 'text-red-600 dark:text-red-400' }
  }
  return { icon: CircleHelp, chipClassName: 'tl-chip-neutral', iconClassName: 'text-slate dark:text-slate-400' }
}

function InlineMessage({ tone, children }: { tone: 'error' | 'status'; children: React.ReactNode }) {
  return (
    <p
      role={tone === 'error' ? 'alert' : 'status'}
      aria-live={tone === 'error' ? 'assertive' : 'polite'}
      className={`mt-3 text-sm ${tone === 'error' ? 'text-red-700 dark:text-red-300' : 'text-green-800 dark:text-green-300'}`}
    >
      {children}
    </p>
  )
}

function formatDuration(seconds: number | null): string {
  if (seconds == null) return 'none'
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3_600) return `${Math.round(seconds / 60)}m`
  if (seconds < 86_400) return `${Math.round(seconds / 3_600)}h`
  return `${Math.round(seconds / 86_400)}d`
}

function formatHeartbeatEvidence(ageSeconds: number | null, thresholdSeconds: number | null, reason: string | null): string {
  if (ageSeconds == null) return reason ? formatWireLabel(reason) : 'Unavailable'
  const reasonLabel = reason && reason !== 'healthy' ? ` · ${formatWireLabel(reason)}` : ''
  if (thresholdSeconds == null) return `${formatDuration(ageSeconds)} ago${reasonLabel}`
  return `${formatDuration(ageSeconds)} ago · ${formatDuration(thresholdSeconds)} freshness window${reasonLabel}`
}

function numberMetric(metrics: OperationsComponentCheck['metrics'], key: string): number | null {
  const value = metrics[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function stringMetric(metrics: OperationsComponentCheck['metrics'], key: string): string | null {
  const value = metrics[key]
  return typeof value === 'string' && value ? value : null
}

function booleanMetric(metrics: OperationsComponentCheck['metrics'], key: string): boolean | null {
  const value = metrics[key]
  return typeof value === 'boolean' ? value : null
}

function stringArrayMetric(metrics: OperationsComponentCheck['metrics'], key: string): string[] | null {
  const value = metrics[key]
  return Array.isArray(value) && value.every((entry) => typeof entry === 'string') ? value : null
}

function pluralize(noun: string, count: number): string {
  return count === 1 ? noun : `${noun}s`
}

function overallHealthHeading(status: OperationsStatus): string {
  return status === 'healthy' ? 'All monitored systems operational' : `System health ${status}`
}

function formatBytes(bytes: number | null): string {
  if (bytes == null) return 'unavailable'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let value = bytes
  let unitIndex = 0
  while (value >= 1_024 && unitIndex < units.length - 1) {
    value /= 1_024
    unitIndex += 1
  }
  return `${value >= 10 || unitIndex === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unitIndex]}`
}

function runDurationSeconds(run: SystemOperationRun): number | null {
  if (!run.finished_at) return null
  const duration = (Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1_000
  return Number.isFinite(duration) && duration >= 0 ? duration : null
}

function formatOperationType(value: SystemOperationType): string {
  return value === 'restore_drill'
    ? 'Restore drill'
    : value.charAt(0).toUpperCase() + value.slice(1)
}

function formatWireLabel(value: string): string {
  const label = value.replaceAll('_', ' ')
  return label.charAt(0).toUpperCase() + label.slice(1)
}
