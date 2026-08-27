import { resolveApiErrorMessage } from '../api/errors'
import type { AlertOccurrence, AlertOccurrenceState, AlertSeverity } from '../types/alerts'
import {
  formatAlertOccurrenceState,
  formatAlertSeverity,
} from './alertOccurrenceModel'

export function AlertOccurrenceStateChip({ state }: { state: AlertOccurrenceState }) {
  const className = state === 'closed'
    ? 'tl-chip tl-chip-success'
    : state === 'investigating'
      ? 'tl-chip tl-chip-warning'
      : state === 'new'
        ? 'tl-chip tl-chip-info'
        : 'tl-chip tl-chip-neutral'
  return <span className={className}>{formatAlertOccurrenceState(state)}</span>
}

export function AlertSeverityChip({ severity }: { severity: AlertSeverity }) {
  const className = severity === 'critical'
    ? 'tl-chip tl-chip-danger'
    : severity === 'high'
      ? 'tl-chip tl-chip-warning'
      : severity === 'medium'
        ? 'tl-chip tl-chip-info'
        : 'tl-chip tl-chip-neutral'
  return <span className={className}>{formatAlertSeverity(severity)}</span>
}

export function AlertOccurrenceStateFlags({ occurrence }: { occurrence: AlertOccurrence }) {
  const snoozeExpired = Boolean(occurrence.snoozed_until) && !occurrence.is_snoozed
  return (
    <div className="flex flex-wrap gap-1.5">
      {occurrence.is_suppressed && <span className="tl-chip tl-chip-warning">Suppressed</span>}
      {occurrence.is_snoozed && <span className="tl-chip tl-chip-neutral">Snoozed</span>}
      {snoozeExpired && <span className="tl-chip tl-chip-neutral">Snooze expired</span>}
    </div>
  )
}

export function AlertOccurrenceRefreshWarning({
  error,
  fallback,
  onRetry,
}: {
  error: unknown
  fallback: string
  onRetry: () => void
}) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center justify-between gap-2 border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/20 dark:text-amber-200"
    >
      <span>{resolveApiErrorMessage(error, fallback)} The last loaded data remains visible.</span>
      <button
        type="button"
        className="min-h-9 rounded border border-amber-400 px-2.5 py-1 text-xs font-semibold dark:border-amber-700"
        onClick={onRetry}
      >
        Retry refresh
      </button>
    </div>
  )
}

export function AlertOccurrencePageError({
  error,
  fallback,
  onRetry,
}: {
  error: unknown
  fallback: string
  onRetry: () => void
}) {
  return (
    <div
      role="alert"
      className="border border-red-300/70 bg-red-50 px-4 py-5 text-sm text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-200"
    >
      <p>{resolveApiErrorMessage(error, fallback)}</p>
      <button
        type="button"
        className="mt-3 min-h-10 rounded border border-red-400 px-3 py-1.5 text-xs font-semibold dark:border-red-700"
        onClick={onRetry}
      >
        Retry
      </button>
    </div>
  )
}
