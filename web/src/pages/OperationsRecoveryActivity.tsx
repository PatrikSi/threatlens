import type {
  OperationsRecoverySnapshot,
  SystemOperationRun,
  SystemOperationStatus,
  SystemOperationType,
} from '../types/operations'
import { formatDateTime } from '../utils/datetime'
import {
  formatDuration,
  formatWireLabel,
} from './operationsHealthPresentation'
import {
  OperationRunStatusChip,
  OperationsStatusChip,
} from './OperationsStatus'

export function OperationsRecoveryActivity({
  recovery,
  recoveryLoading,
  recoveryError,
  runs,
  loading,
  updating,
  error,
  page,
  totalPages,
  operationType,
  operationStatus,
  onPageChange,
  onRetryRecovery,
  onRetry,
  onTypeChange,
  onStatusChange,
}: {
  recovery?: OperationsRecoverySnapshot
  recoveryLoading: boolean
  recoveryError: string
  runs: SystemOperationRun[]
  loading: boolean
  updating: boolean
  error: string
  page: number
  totalPages: number
  operationType: SystemOperationType | ''
  operationStatus: SystemOperationStatus | ''
  onPageChange: (page: number) => void
  onRetryRecovery: () => void
  onRetry: () => void
  onTypeChange: (value: SystemOperationType | '') => void
  onStatusChange: (value: SystemOperationStatus | '') => void
}) {
  return (
    <div className="divide-y divide-slate/15 dark:divide-white/10">
      <RecoveryEvidence
        recovery={recovery}
        loading={recoveryLoading}
        error={recoveryError}
        onRetry={onRetryRecovery}
      />
      <OperationHistory
        runs={runs}
        loading={loading}
        updating={updating}
        error={error}
        page={page}
        totalPages={totalPages}
        operationType={operationType}
        operationStatus={operationStatus}
        onPageChange={onPageChange}
        onRetry={onRetry}
        onTypeChange={onTypeChange}
        onStatusChange={onStatusChange}
      />
    </div>
  )
}

function RecoveryEvidence({
  recovery,
  loading,
  error,
  onRetry,
}: {
  recovery?: OperationsRecoverySnapshot
  loading: boolean
  error: string
  onRetry: () => void
}) {
  if (!recovery) {
    return (
      <section className="px-3 py-3 sm:px-4 sm:py-4" aria-labelledby="operations-recovery-heading">
        <h2 id="operations-recovery-heading" className="text-base font-semibold">Recovery evidence</h2>
        <p className="mt-0.5 text-sm text-slate dark:text-slate-300">Backup, verification, and restore-drill records establish recoverability separately from live runtime health.</p>
        {loading ? (
          <p role="status" className="mt-3 rounded border border-dashed border-slate/20 px-3 py-6 text-center text-sm text-slate dark:border-white/10 dark:text-slate-300">
            Loading the latest recovery summary...
          </p>
        ) : (
          <div role="alert" className="mt-3 rounded border border-amber-300/60 bg-amber-50 px-3 py-3 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
            <p>{error || 'The latest recovery summary is unavailable.'} Operation history below remains available.</p>
            <button type="button" className="mt-3 min-h-11 rounded border border-current px-3 py-2 font-semibold" onClick={onRetry}>
              Retry recovery summary
            </button>
          </div>
        )}
      </section>
    )
  }
  const entries: Array<[string, SystemOperationRun | null]> = [
    ['Latest backup', recovery.latest_backup],
    ['Latest verification', recovery.latest_verify],
    ['Latest restore drill', recovery.latest_restore_drill],
    ['Latest restore', recovery.latest_restore],
  ]
  return (
    <section className="px-3 py-3 sm:px-4 sm:py-4" aria-labelledby="operations-recovery-heading">
      <div>
        <h2 id="operations-recovery-heading" className="text-base font-semibold">Recovery evidence</h2>
        <p className="mt-0.5 text-sm text-slate dark:text-slate-300">Backup, verification, and restore-drill records establish recoverability separately from live runtime health.</p>
      </div>
      {error && (
        <div role="alert" className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
          <span>{error}. Displaying the last successful recovery summary.</span>
          <button type="button" className="min-h-11 rounded border border-current px-3 py-2 font-semibold md:min-h-0 md:py-1" onClick={onRetry}>Retry summary</button>
        </div>
      )}
      <div className="mt-3 grid gap-px overflow-hidden rounded border border-slate/15 bg-slate/15 sm:grid-cols-2 xl:grid-cols-4 dark:border-white/10 dark:bg-white/10">
        {entries.map(([label, run]) => (
          <article key={label} className="bg-white px-3 py-3 dark:bg-[#041612]">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate dark:text-slate-400">{label}</p>
            {run ? (
              <>
                <div className="mt-2"><OperationRunStatusChip status={run.status} /></div>
                <dl className="mt-2 space-y-1 text-xs">
                  <EvidenceRow label="Recorded" value={formatDateTime(run.finished_at ?? run.started_at)} />
                  <EvidenceRow label="Duration" value={run.finished_at ? formatDuration(runDurationSeconds(run)) : 'In progress'} />
                  <EvidenceRow label="Source" value={formatWireLabel(run.source)} title={run.source} />
                  <EvidenceRow label="Initiated by" value={run.initiated_by} title={run.initiated_by} />
                </dl>
                {run.error_message && <p className="mt-2 text-xs text-red-700 dark:text-red-300">{run.error_message}</p>}
              </>
            ) : (
              <div className="mt-2"><OperationsStatusChip status="unknown" label="Not recorded" /></div>
            )}
          </article>
        ))}
      </div>
    </section>
  )
}

function EvidenceRow({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-slate dark:text-slate-400">{label}</dt>
      <dd className="truncate text-right font-medium" title={title}>{value}</dd>
    </div>
  )
}

function OperationHistory({
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
    <section className="px-3 py-3 sm:px-4 sm:py-4" aria-labelledby="operations-runs-heading">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="operations-runs-heading" className="text-base font-semibold">Operation history</h2>
          <p className="mt-0.5 text-sm text-slate dark:text-slate-300">Auditable host recovery and diagnostic operations.</p>
        </div>
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
        <div role="alert" className="mt-3 flex flex-wrap items-center justify-between gap-2 text-sm text-red-700 dark:text-red-300">
          <span>{error}{runs.length > 0 ? ' The last loaded operation history remains visible.' : ''}</span>
          <button type="button" className="min-h-11 rounded border border-current px-3 py-2 font-semibold" onClick={onRetry} disabled={updating}>{updating ? 'Retrying...' : 'Retry history'}</button>
        </div>
      )}
      {loading && <p role="status" className="py-6 text-center text-sm text-slate dark:text-slate-300">Loading operation history...</p>}
      {updating && !loading && <p role="status" className="mt-3 text-sm text-slate dark:text-slate-300">Updating operation history for the selected filters...</p>}
      {!loading && !error && runs.length === 0 && <p className="mt-3 border-y border-dashed border-slate/20 py-6 text-center text-sm text-slate dark:border-white/10 dark:text-slate-300">No operation runs match these filters.</p>}
      {runs.length > 0 && (
        <div aria-busy={updating} className={`mt-3 overflow-x-auto ${updating ? 'opacity-60' : ''}`}>
          <table className="w-full min-w-[860px] text-left text-sm">
            <thead>
              <tr className="border-b border-slate/20 dark:border-white/10">
                <th scope="col" className="px-2 py-2">Started</th>
                <th scope="col" className="px-2 py-2">Operation</th>
                <th scope="col" className="px-2 py-2">Status</th>
                <th scope="col" className="px-2 py-2">Initiator</th>
                <th scope="col" className="px-2 py-2">Source</th>
                <th scope="col" className="px-2 py-2">Result</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id} className="border-b border-slate/10 last:border-0 dark:border-white/5">
                  <td className="whitespace-nowrap px-2 py-2">{formatDateTime(run.started_at)}</td>
                  <td className="px-2 py-2 font-semibold">{formatOperationType(run.operation_type)}</td>
                  <td className="px-2 py-2"><OperationRunStatusChip status={run.status} /></td>
                  <td className="max-w-[14rem] truncate px-2 py-2" title={run.initiated_by}>{run.initiated_by}</td>
                  <td className="px-2 py-2 text-slate dark:text-slate-300" title={run.source}>{formatWireLabel(run.source)}</td>
                  <td className="max-w-sm px-2 py-2 text-slate dark:text-slate-300">{run.error_code && <span className="mr-1 font-mono text-xs">{run.error_code}:</span>}{run.error_message ?? (run.finished_at ? formatDuration(runDurationSeconds(run)) : 'In progress')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="mt-3 grid grid-cols-[auto_1fr_auto] items-center gap-2 text-sm sm:flex sm:justify-between">
        <button type="button" className="min-h-11 rounded border border-slate/30 px-3 py-2 disabled:opacity-50 dark:border-cyan-900/40" disabled={page <= 1 || updating} onClick={() => onPageChange(page - 1)}>Previous</button>
        <span className="text-center">Page {page} of {totalPages}</span>
        <button type="button" className="min-h-11 rounded border border-slate/30 px-3 py-2 disabled:opacity-50 dark:border-cyan-900/40" disabled={page >= totalPages || updating} onClick={() => onPageChange(page + 1)}>Next</button>
      </div>
    </section>
  )
}

function runDurationSeconds(run: SystemOperationRun): number | null {
  if (!run.finished_at) return null
  const duration = (Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1_000
  return Number.isFinite(duration) && duration >= 0 ? duration : null
}

function formatOperationType(value: SystemOperationType): string {
  return value === 'restore_drill' ? 'Restore drill' : formatWireLabel(value)
}
