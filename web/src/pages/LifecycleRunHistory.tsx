import { useMutation } from '@tanstack/react-query'
import { ChevronDown, Square } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { ApiError } from '../api/client'
import { isAmbiguousMutationError } from '../api/mutationResilience'
import { resolveApiErrorMessage } from '../api/errors'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type {
  LifecycleRun,
  LifecycleRunStatus,
  LifecycleRunTrigger,
  LifecycleTarget,
} from '../types/lifecycle'
import { formatDateTime } from '../utils/datetime'
import {
  formatBytes,
  formatDuration,
  formatWireLabel,
} from './operationsHealthPresentation'
import {
  cancelLifecycleRun,
  newLifecycleRequestKey,
} from './lifecycleApi'
import { LIFECYCLE_WEEKDAYS } from './lifecycleModel'
import { LifecycleRunStatusChip } from './LifecycleStatus'

export interface LifecycleRunFilters {
  targetKey: string
  status: LifecycleRunStatus | ''
  trigger: LifecycleRunTrigger | ''
}

export function LifecycleRunHistory({
  targets,
  runs,
  total,
  page,
  pageSize,
  filters,
  loading,
  fetching,
  error,
  canWrite,
  onPageChange,
  onFiltersChange,
  onRetry,
  onRunChanged,
}: {
  targets: LifecycleTarget[]
  runs: LifecycleRun[]
  total: number
  page: number
  pageSize: number
  filters: LifecycleRunFilters
  loading: boolean
  fetching: boolean
  error: string
  canWrite: boolean
  onPageChange: (page: number) => void
  onFiltersChange: (filters: LifecycleRunFilters) => void
  onRetry: () => void
  onRunChanged: (run: LifecycleRun) => void
}) {
  const [expandedRunId, setExpandedRunId] = useState<string | null>(null)
  const [cancelRun, setCancelRun] = useState<LifecycleRun | null>(null)
  const targetsByKey = new Map(targets.map((target) => [target.key, target]))
  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  return (
    <section className="tl-surface overflow-hidden rounded-xl" aria-labelledby="lifecycle-history-heading">
      <div className="border-b border-slate/15 px-3 py-3 sm:px-4 dark:border-white/10">
        <div>
          <h2 id="lifecycle-history-heading" className="text-base font-semibold">Run history</h2>
          <p className="mt-0.5 text-xs text-slate dark:text-slate-400">Auditable scheduled and manual cleanup activity.</p>
        </div>
      </div>
      <div className="grid gap-2 border-b border-slate/15 px-3 py-2 sm:grid-cols-3 sm:px-4 dark:border-white/10">
        <FilterSelect label="Dataset" value={filters.targetKey} onChange={(targetKey) => onFiltersChange({ ...filters, targetKey })} options={[['', 'All datasets'], ...targets.map((target) => [target.key, target.label] as const)]} />
        <FilterSelect
          label="Status"
          value={filters.status}
          onChange={(status) => onFiltersChange({
            ...filters,
            status: status as LifecycleRunStatus | '',
          })}
          options={[
            ['', 'All statuses'],
            ['queued', 'Queued'],
            ['running', 'Running'],
            ['succeeded', 'Succeeded'],
            ['partial', 'Partially completed'],
            ['failed', 'Failed'],
            ['cancelled', 'Cancelled'],
          ]}
        />
        <FilterSelect
          label="Trigger"
          value={filters.trigger}
          onChange={(trigger) => onFiltersChange({
            ...filters,
            trigger: trigger as LifecycleRunTrigger | '',
          })}
          options={[
            ['', 'All triggers'],
            ['manual', 'Manual'],
            ['scheduled', 'Scheduled'],
          ]}
        />
      </div>

      {error && (
        <div
          role="alert"
          className="mx-3 mt-3 flex flex-wrap items-center justify-between gap-2 text-sm text-red-700 sm:mx-4 dark:text-red-300"
        >
          <span>
            {error}
            {runs.length > 0 ? ' The last loaded results remain visible.' : ''}
          </span>
          <button
            type="button"
            className="min-h-11 rounded border border-current px-3 py-2 font-semibold md:min-h-0"
            onClick={onRetry}
          >
            Retry
          </button>
        </div>
      )}
      {loading && <p role="status" className="px-4 py-10 text-center text-sm text-slate dark:text-slate-300">Loading lifecycle run history...</p>}
      {!loading && !error && runs.length === 0 && <p className="px-4 py-10 text-center text-sm text-slate dark:text-slate-300">No lifecycle runs match these filters.</p>}
      {runs.length > 0 && (
        <div className={`overflow-x-auto ${fetching ? 'opacity-60' : ''}`} aria-busy={fetching}>
          <table className="w-full table-fixed text-left text-sm md:min-w-[940px] md:table-auto">
            <thead>
              <tr className="border-b border-slate/15 text-xs uppercase tracking-wide text-slate dark:border-white/10 dark:text-slate-400">
                <th scope="col" className="hidden px-3 py-2 md:table-cell">Queued</th>
                <th scope="col" className="w-[48%] px-3 py-2 md:w-auto">Dataset</th>
                <th scope="col" className="w-[30%] px-3 py-2 md:w-auto">Status</th>
                <th scope="col" className="hidden px-3 py-2 md:table-cell">Trigger</th>
                <th scope="col" className="hidden px-3 py-2 md:table-cell">Affected</th>
                <th scope="col" className="hidden px-3 py-2 md:table-cell">Requested by</th>
                <th scope="col" className="w-[22%] px-3 py-2 text-right md:w-auto">Actions</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => {
                const expanded = expandedRunId === run.id
                const cancellable = canWrite
                  && ['queued', 'running'].includes(run.status)
                  && !run.cancel_requested
                return (
                  <RunRows
                    key={run.id}
                    run={run}
                    target={targetsByKey.get(run.target_key)}
                    expanded={expanded}
                    cancellable={cancellable}
                    onToggle={() => setExpandedRunId(expanded ? null : run.id)}
                    onCancel={() => setCancelRun(run)}
                  />
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="grid grid-cols-[auto_1fr_auto] items-center gap-2 border-t border-slate/15 px-3 py-3 text-sm sm:px-4 dark:border-white/10">
        <button type="button" disabled={page <= 1 || fetching} className="min-h-11 rounded border border-slate/25 px-3 py-2 disabled:opacity-50 md:min-h-0 dark:border-white/10" onClick={() => onPageChange(page - 1)}>Previous</button>
        <span className="text-center">Page {page} of {totalPages} · {total.toLocaleString()} runs</span>
        <button type="button" disabled={page >= totalPages || fetching} className="min-h-11 rounded border border-slate/25 px-3 py-2 disabled:opacity-50 md:min-h-0 dark:border-white/10" onClick={() => onPageChange(page + 1)}>Next</button>
      </div>
      <LifecycleCancelDialog
        run={cancelRun}
        targetLabel={cancelRun ? targetsByKey.get(cancelRun.target_key)?.label ?? formatWireLabel(cancelRun.target_key) : ''}
        onClose={() => setCancelRun(null)}
        onCancelled={(updated) => { setCancelRun(null); onRunChanged(updated) }}
        onConflict={() => {
          setCancelRun(null)
          onRetry()
        }}
      />
    </section>
  )
}

function RunRows({
  run,
  target,
  expanded,
  cancellable,
  onToggle,
  onCancel,
}: {
  run: LifecycleRun
  target?: LifecycleTarget
  expanded: boolean
  cancellable: boolean
  onToggle: () => void
  onCancel: () => void
}) {
  const targetLabel = target?.label ?? formatWireLabel(run.target_key)
  return (
    <>
      <tr className="border-b border-slate/10 dark:border-white/5">
        <td className="hidden whitespace-nowrap px-3 py-2 md:table-cell">{formatDateTime(run.queued_at)}</td>
        <td className="px-3 py-2 font-semibold">
          {targetLabel}
          <span className="mt-0.5 block text-[0.6875rem] font-normal text-slate dark:text-slate-400 md:hidden">
            {formatDateTime(run.queued_at)} · {run.affected_count.toLocaleString()} affected
          </span>
        </td>
        <td className="px-3 py-2">
          <LifecycleRunStatusChip status={run.status} />
          {run.cancel_requested && (
            <span className="ml-2 text-xs text-amber-700 dark:text-amber-300">
              Cancellation requested
            </span>
          )}
        </td>
        <td className="hidden px-3 py-2 capitalize md:table-cell">{run.trigger_source}</td>
        <td className="hidden px-3 py-2 font-mono md:table-cell">{run.affected_count.toLocaleString()}</td>
        <td className="hidden max-w-[12rem] truncate px-3 py-2 md:table-cell" title={run.requested_by ?? undefined}>{run.requested_by ?? 'System scheduler'}</td>
        <td className="px-3 py-2">
          <div className="flex justify-end gap-1">
            {cancellable && (
              <button
                type="button"
                className="inline-flex min-h-11 items-center gap-1 rounded border border-red-300/70 px-2 py-1.5 text-xs font-semibold text-red-700 md:min-h-0 dark:border-red-500/40 dark:text-red-300"
                onClick={onCancel}
              >
                <Square className="h-3.5 w-3.5" aria-hidden="true" /> Cancel
              </button>
            )}
            <button
              type="button"
              aria-expanded={expanded}
              aria-label={`${expanded ? 'Hide' : 'Show'} details for ${targetLabel} lifecycle run`}
              className="inline-flex min-h-11 min-w-11 items-center justify-center rounded border border-slate/25 md:min-h-0 md:min-w-0 md:px-2 md:py-1.5 dark:border-white/10"
              onClick={onToggle}
            >
              <ChevronDown
                className={`h-4 w-4 transition-transform ${
                  expanded ? 'rotate-180' : ''
                }`}
                aria-hidden="true"
              />
            </button>
          </div>
        </td>
      </tr>
      {expanded && <tr className="border-b border-slate/10 bg-slate/5 dark:border-white/5 dark:bg-white/[0.025]"><td colSpan={7} className="px-3 py-3"><RunDetails run={run} target={target} /></td></tr>}
    </>
  )
}

function RunDetails({
  run,
  target,
}: {
  run: LifecycleRun
  target?: LifecycleTarget
}) {
  const duration = run.started_at && run.finished_at
    ? Math.max(0, (Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1_000)
    : null
  const snapshot = runPolicySnapshot(run.policy_snapshot, target)
  const remainingLowerBound = run.details.remaining_count_is_lower_bound === true
  const recoveryContext = runRecoveryContext(run.details)
  return (
    <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.7fr)]">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs sm:grid-cols-4">
        <Detail label="Policy revision" value={String(run.policy_revision)} />
        <Detail label="Captured retention" value={snapshot.retention} />
        <Detail label="Captured schedule" value={snapshot.schedule} />
        <Detail label="Run cap" value={run.max_records.toLocaleString()} />
        <Detail label="Cutoff" value={formatDateTime(run.cutoff_at)} />
        <Detail label="Scheduled for" value={run.scheduled_for ? formatDateTime(run.scheduled_for) : 'Manual run'} />
        <Detail label="Started" value={run.started_at ? formatDateTime(run.started_at) : 'Not started'} />
        <Detail label="Last heartbeat" value={run.heartbeat_at ? formatDateTime(run.heartbeat_at) : 'Not recorded'} />
        <Detail label="Finished" value={run.finished_at ? formatDateTime(run.finished_at) : 'Not finished'} />
        <Detail label="Duration" value={duration == null ? run.status === 'running' ? 'In progress' : 'Not available' : formatDuration(duration)} />
        <Detail label="Evaluated" value={run.evaluated_count.toLocaleString()} />
        <Detail label="Affected" value={run.affected_count.toLocaleString()} />
        <Detail
          label="Affected size"
          value={run.affected_bytes == null
            ? 'Not measured'
            : formatBytes(run.affected_bytes)}
        />
        <Detail label="Protected" value={run.protected_count.toLocaleString()} />
        <Detail label="Skipped" value={run.skipped_count.toLocaleString()} />
        <Detail label="Batches" value={run.batch_count.toLocaleString()} />
        <Detail label="Remaining" value={run.remaining_count == null ? 'Not measured' : `${remainingLowerBound ? 'At least ' : ''}${run.remaining_count.toLocaleString()}`} />
      </dl>
      <div className="text-xs">
        <p className="font-semibold">Operational context</p>
        <p className="mt-1 text-slate dark:text-slate-300">{run.reason ?? 'No manual reason; initiated by the configured schedule.'}</p>
        <p className="mt-2 text-slate dark:text-slate-300">
          <span className="font-semibold">Captured safeguards:</span>{' '}
          {snapshot.safeguards}
        </p>
        {snapshot.disabledSafeguards && (
          <p className="mt-1 text-slate dark:text-slate-300">
            <span className="font-semibold">Disabled safeguards:</span>{' '}
            {snapshot.disabledSafeguards}
          </p>
        )}
        {run.cancellation_requested_at && (
          <p className="mt-2 text-slate dark:text-slate-300">
            <span className="font-semibold">Cancellation requested:</span>{' '}
            {formatDateTime(run.cancellation_requested_at)} by {run.cancellation_requested_by ?? 'an operator'}
            {run.cancellation_reason ? ` · ${run.cancellation_reason}` : ''}
          </p>
        )}
        {recoveryContext && (
          <p className="mt-2 text-slate dark:text-slate-300">
            <span className="font-semibold">Execution recovery:</span>{' '}
            {recoveryContext}
          </p>
        )}
        {run.stop_reason && (
          <p className="mt-2 text-slate dark:text-slate-300">
            <span className="font-semibold">Stop reason:</span>{' '}
            {formatWireLabel(run.stop_reason)}
          </p>
        )}
        {run.error_message && <p className="mt-2 text-red-700 dark:text-red-300"><span className="font-mono">{run.error_code ?? 'cleanup_failed'}:</span> {run.error_message}</p>}
        <p className="mt-2 font-mono text-[0.6875rem] text-slate dark:text-slate-400">Run ID: {run.id}</p>
      </div>
    </div>
  )
}

function LifecycleCancelDialog({
  run,
  targetLabel,
  onClose,
  onCancelled,
  onConflict,
}: {
  run: LifecycleRun | null
  targetLabel: string
  onClose: () => void
  onCancelled: (run: LifecycleRun) => void
  onConflict: () => void
}) {
  const [reason, setReason] = useState('')
  const requestKey = useRef(newLifecycleRequestKey('cancel'))
  const mutation = useMutation({
    mutationFn: () => cancelLifecycleRun(run!.id, { reason: reason.trim() }, requestKey.current),
    onSuccess: onCancelled,
    onError: (error) => {
      if (!isAmbiguousMutationError(error)) {
        requestKey.current = newLifecycleRequestKey('cancel')
      }
    },
  })
  const resetMutation = mutation.reset
  useEffect(() => {
    if (!run) return
    setReason('')
    requestKey.current = newLifecycleRequestKey('cancel')
    resetMutation()
  }, [resetMutation, run])
  const error = mutation.isError
    ? isAmbiguousMutationError(mutation.error)
      ? 'The cancellation outcome is unknown. Refresh run history before retrying.'
      : resolveApiErrorMessage(mutation.error, 'Cancellation could not be requested')
    : ''
  const outcomeUnknown = mutation.isError
    && isAmbiguousMutationError(mutation.error)
  const stateConflict = mutation.error instanceof ApiError
    && mutation.error.status === 409
  return (
    <ConfirmDialog
      open={Boolean(run)}
      title={`Cancel ${targetLabel} cleanup?`}
      description="Cancellation stops future batches. Records already cleaned up are not restored."
      confirmLabel="Request cancellation"
      confirmingLabel="Requesting..."
      confirmTone="danger"
      isConfirming={mutation.isPending}
      confirmDisabled={reason.trim().length < 10 || outcomeUnknown || stateConflict}
      cancelDisabled={mutation.isPending}
      onCancel={onClose}
      onConfirm={() => mutation.mutate()}
    >
      <label className="block font-semibold">
        Cancellation reason
        <textarea
          rows={2}
          maxLength={500}
          value={reason}
          disabled={outcomeUnknown}
          className="mt-1 block w-full rounded border border-slate/30 bg-white px-3 py-2 font-normal dark:border-cyan-900/40 dark:bg-[#072019]"
          onChange={(event) => setReason(event.target.value)}
        />
        <span className="mt-1 block text-xs font-normal text-slate dark:text-slate-400">
          At least 10 characters; recorded in the audit trail.
        </span>
      </label>
      {error && (
        <div role="alert" className="text-red-700 dark:text-red-300">
          <p>{error}</p>
          {stateConflict && (
            <button
              type="button"
              className="mt-2 min-h-11 rounded border border-current px-3 py-2 font-semibold md:min-h-0"
              onClick={onConflict}
            >
              Refresh run history
            </button>
          )}
        </div>
      )}
    </ConfirmDialog>
  )
}

function Detail({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-slate dark:text-slate-400">{label}</dt><dd className="mt-0.5 break-words font-semibold">{value}</dd></div>
}

function runPolicySnapshot(
  snapshot: Record<string, unknown>,
  target?: LifecycleTarget,
) {
  const retentionDays = typeof snapshot.retention_days === 'number'
    ? snapshot.retention_days
    : null
  const cadence = snapshot.schedule_cadence
  const hour = typeof snapshot.schedule_hour_utc === 'number'
    ? snapshot.schedule_hour_utc
    : null
  const weekday = typeof snapshot.schedule_weekday === 'number'
    ? snapshot.schedule_weekday
    : null
  const schedule = cadence === 'daily' && hour != null
    ? `Daily at ${String(hour).padStart(2, '0')}:00 UTC`
    : cadence === 'weekly' && hour != null && weekday != null
      ? `${LIFECYCLE_WEEKDAYS[weekday] ?? 'Unknown day'} at ${String(hour).padStart(2, '0')}:00 UTC`
      : 'Not captured'
  const options = snapshot.options
  const optionEntries = options && typeof options === 'object'
    ? Object.entries(options)
    : []
  const safeguardLabel = (key: string) => target?.safeguards.find(
    (safeguard) => safeguard.key === key,
  )?.label ?? formatWireLabel(key)
  const safeguards = optionEntries
    .filter(([, enabled]) => enabled === true)
    .map(([key]) => safeguardLabel(key))
    .join(', ') || (optionEntries.length > 0 ? 'None enabled' : 'Not applicable')
  const disabledSafeguards = optionEntries
    .filter(([, enabled]) => enabled === false)
    .map(([key]) => safeguardLabel(key))
    .join(', ')
  return {
    retention: retentionDays == null
      ? 'Not captured'
      : `${retentionDays.toLocaleString()} days`,
    schedule,
    safeguards,
    disabledSafeguards,
  }
}

function runRecoveryContext(details: Record<string, unknown>): string {
  const labels: Array<[string, string, string]> = [
    ['publication_recovery_count', 'publication fallback', 'publication fallbacks'],
    ['continuation_count', 'continuation', 'continuations'],
    ['transient_retry_count', 'transient retry', 'transient retries'],
    ['stale_lease_recovery_count', 'stale lease recovery', 'stale lease recoveries'],
  ]
  const entries = labels.flatMap(([key, singular, plural]) => {
    const value = details[key]
    return typeof value === 'number' && Number.isFinite(value) && value > 0
      ? [`${value.toLocaleString()} ${value === 1 ? singular : plural}`]
      : []
  })
  const lastError = details.last_transient_error_code
  if (typeof lastError === 'string' && lastError) {
    entries.push(`last transient error: ${formatWireLabel(lastError)}`)
  }
  return entries.join(' · ')
}

function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: ReadonlyArray<readonly [string, string]>
  onChange: (value: string) => void
}) {
  return (
    <label className="text-xs font-semibold text-slate dark:text-slate-300">
      {label}
      <select
        value={value}
        className="mt-1 block min-h-11 w-full rounded border border-slate/30 bg-white px-2 py-2 text-sm font-normal md:min-h-0 dark:border-cyan-900/40 dark:bg-[#072019]"
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map(([optionValue, optionLabel]) => (
          <option key={optionValue} value={optionValue}>{optionLabel}</option>
        ))}
      </select>
    </label>
  )
}
