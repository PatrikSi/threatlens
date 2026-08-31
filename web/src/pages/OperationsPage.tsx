import { useMutation, useQuery } from '@tanstack/react-query'
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
    <div className="space-y-4">
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
            <span>Snapshot: {overview ? formatDateTime(overview.generated_at) : 'not available'}</span>
            <span>Browser refresh: {lastUpdated ? formatDateTime(lastUpdated.toISOString()) : 'not yet'}</span>
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

function IssuesSection({ issues }: { issues: OperationsOverviewResponse['issues'] }) {
  if (issues.length === 0) {
    return (
      <section className="px-4 py-4 sm:px-5" aria-labelledby="operations-issues-heading">
        <h2 id="operations-issues-heading" className="text-sm font-semibold uppercase text-slate dark:text-slate-400">
          Attention
        </h2>
        <p className="mt-2 text-sm text-green-800 dark:text-green-300">No operational issues are currently reported.</p>
      </section>
    )
  }
  return (
    <section className="px-4 py-4 sm:px-5" aria-labelledby="operations-issues-heading">
      <h2 id="operations-issues-heading" className="text-sm font-semibold uppercase text-slate dark:text-slate-400">
        Attention ({issues.length})
      </h2>
      <div className="mt-3 divide-y divide-slate/15 border-y border-slate/15 dark:divide-white/10 dark:border-white/10">
        {issues.map((issue) => (
          <article key={issue.code} className="grid gap-2 py-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className={issue.severity === 'critical' ? 'tl-chip tl-chip-danger' : 'tl-chip tl-chip-warning'}>
                  {formatWireLabel(issue.severity)}
                </span>
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
    <section className="px-4 py-4 sm:px-5" aria-labelledby="operations-components-heading">
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
            <p className="mt-1 text-xs text-slate dark:text-slate-400">Checked {formatDateTime(component.checked_at)}</p>
          </article>
        ))}
      </div>
      <div className="mt-3 hidden overflow-x-auto sm:block">
        <table className="min-w-[680px] w-full text-left text-sm">
          <thead>
            <tr className="border-b border-slate/20 dark:border-white/10">
              <th scope="col" className="px-2 py-2">Component</th>
              <th scope="col" className="px-2 py-2">Status</th>
              <th scope="col" className="px-2 py-2">Summary</th>
              <th scope="col" className="px-2 py-2">Checked</th>
            </tr>
          </thead>
          <tbody>
            {components.map((component) => (
              <tr key={component.key} className="border-b border-slate/10 last:border-0 dark:border-white/5">
                <td className="px-2 py-2 font-semibold">{component.label}</td>
                <td className="px-2 py-2"><StatusChip status={component.status} /></td>
                <td className="px-2 py-2 text-slate dark:text-slate-300">{component.summary}</td>
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

function BacklogsSection({ backlogs }: { backlogs: OperationsBacklogSnapshot[] }) {
  return (
    <section className="px-4 py-4 sm:px-5" aria-labelledby="operations-backlogs-heading">
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
                <Metric label="Stale" value={backlog.stale_count} />
                <Metric label="Failed" value={backlog.failed_count} />
              </dl>
              <p className="mt-3 text-xs text-slate dark:text-slate-400">
                Oldest pending: {formatDuration(backlog.oldest_pending_age_seconds)}
              </p>
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
    <section className="px-4 py-4 sm:px-5" aria-labelledby="operations-recovery-heading">
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
                <p className="mt-2 text-sm">{formatDateTime(run.finished_at ?? run.started_at)}</p>
                {run.error_message && <p className="mt-1 text-xs text-red-700 dark:text-red-300">{run.error_message}</p>}
              </>
            ) : (
              <p className="mt-2 text-sm text-slate dark:text-slate-300">No recorded run</p>
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
    <section className="px-4 py-4 sm:px-5" aria-labelledby="operations-storage-heading">
      <h2 id="operations-storage-heading" className="text-sm font-semibold uppercase text-slate dark:text-slate-400">
        Storage indicators
      </h2>
      <div className="mt-3 divide-y divide-slate/15 border-y border-slate/15 dark:divide-white/10 dark:border-white/10">
        {storage.map((indicator) => (
          <div key={indicator.key} className="grid gap-2 py-3 sm:grid-cols-[minmax(0,1fr)_auto_auto] sm:items-center">
            <div className="flex items-center justify-between gap-3 sm:block">
              <span className="font-semibold">{indicator.label}</span>
              <span className="sm:hidden"><StatusChip status={indicator.status} /></span>
            </div>
            <span className="text-sm text-slate dark:text-slate-300">
              {formatBytes(indicator.used_bytes)} / {formatBytes(indicator.total_bytes)}
              {indicator.percent_used == null ? '' : ` (${indicator.percent_used.toFixed(1)}%)`}
            </span>
            <span className="hidden sm:block"><StatusChip status={indicator.status} /></span>
          </div>
        ))}
      </div>
    </section>
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
    <section className="border-t border-slate/15 px-4 py-4 dark:border-white/10 sm:px-5" aria-labelledby="operations-runs-heading">
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
      {loading && <p className="py-6 text-center text-sm text-slate dark:text-slate-300">Loading operation history...</p>}
      {updating && !loading && (
        <p role="status" className="mt-3 text-sm text-slate dark:text-slate-300">
          Updating operation history for the selected filters...
        </p>
      )}
      {!loading && !error && runs.length === 0 && (
        <p className="mt-3 border-y border-dashed border-slate/20 py-5 text-center text-sm text-slate dark:border-white/10 dark:text-slate-300">
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
      <div className="mt-4 grid grid-cols-[auto_1fr_auto] items-center gap-2 text-sm sm:flex sm:justify-between">
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

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-xs text-slate dark:text-slate-400">{label}</dt>
      <dd className="font-mono font-semibold">{value.toLocaleString()}</dd>
    </div>
  )
}

function StatusChip({ status }: { status: OperationsStatus }) {
  const className = status === 'healthy'
    ? 'tl-chip tl-chip-neutral'
    : status === 'degraded'
      ? 'tl-chip tl-chip-warning'
      : status === 'critical' || status === 'unavailable'
        ? 'tl-chip tl-chip-danger'
        : 'tl-chip'
  return <span className={className}>{formatWireLabel(status)}</span>
}

function RunStatusChip({ status }: { status: SystemOperationStatus }) {
  const className = status === 'succeeded'
    ? 'tl-chip tl-chip-neutral'
    : status === 'failed'
      ? 'tl-chip tl-chip-danger'
      : 'tl-chip tl-chip-warning'
  return <span className={className}>{formatWireLabel(status)}</span>
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
