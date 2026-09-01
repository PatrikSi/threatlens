import type {
  OperationsBacklogSnapshot,
  OperationsComponentCheck,
  OperationsHealthWindow,
  OperationsIssue,
  OperationsOverviewResponse,
  OperationsStatus,
  OperationsStorageIndicator,
  OperationsWorkerReason,
} from '../types/operations'

export type OperationsView = 'live' | 'trends' | 'recovery'
export type OperationsSignalKind = 'component' | 'workflow' | 'storage'

export interface OperationsSignal {
  key: string
  label: string
  kind: OperationsSignalKind
  status: OperationsStatus
  summary: string
  checkedAt: string
  metrics: Array<{ label: string; value: string; tone?: 'default' | 'warning' | 'danger' }>
}

export const OPERATIONS_VIEWS: Array<{ value: OperationsView; label: string }> = [
  { value: 'live', label: 'Live health' },
  { value: 'trends', label: 'Trends' },
  { value: 'recovery', label: 'Recovery & activity' },
]

export const OPERATIONS_WINDOWS: Array<{ value: OperationsHealthWindow; label: string }> = [
  { value: '1h', label: '1 hour' },
  { value: '6h', label: '6 hours' },
  { value: '24h', label: '24 hours' },
  { value: '7d', label: '7 days' },
  { value: '30d', label: '30 days' },
]

export const STATUS_ORDER: Record<OperationsStatus, number> = {
  critical: 0,
  unavailable: 1,
  degraded: 2,
  unknown: 3,
  healthy: 4,
}

export function readOperationsView(value: string | null): OperationsView {
  return OPERATIONS_VIEWS.some((entry) => entry.value === value)
    ? value as OperationsView
    : 'live'
}

export function readOperationsWindow(value: string | null): OperationsHealthWindow {
  return OPERATIONS_WINDOWS.some((entry) => entry.value === value)
    ? value as OperationsHealthWindow
    : '24h'
}

export function buildOperationsSignals(overview: OperationsOverviewResponse): OperationsSignal[] {
  const signals = [
    applicationSignal(overview),
    ...overview.components.map(componentSignal),
    ...overview.backlogs.map(backlogSignal),
    ...overview.storage.map(storageSignal),
  ]
  return signals.sort((left, right) => {
    const statusDifference = STATUS_ORDER[left.status] - STATUS_ORDER[right.status]
    return statusDifference || left.label.localeCompare(right.label)
  })
}

export function signalKeyForIssue(
  issue: OperationsIssue,
  signals: OperationsSignal[],
): string {
  if (signals.some((signal) => signal.key === issue.component)) return issue.component
  if (issue.component === 'storage') {
    if (issue.code.startsWith('database_')) return 'storage:database'
    if (issue.code.startsWith('filesystem_')) return 'storage:application_filesystem'
    return signals.find((signal) => signal.kind === 'storage' && signal.status !== 'healthy')?.key
      ?? signals.find((signal) => signal.kind === 'storage')?.key
      ?? issue.component
  }
  return issue.component
}

export function defaultSignalKey(overview: OperationsOverviewResponse): string {
  const signals = buildOperationsSignals(overview)
  const issue = overview.issues[0]
  if (issue) {
    const issueSignalKey = signalKeyForIssue(issue, signals)
    const matching = signals.find((signal) => signal.key === issueSignalKey)
    if (matching) return matching.key
  }
  return signals[0]?.key ?? 'database'
}

function componentSignal(component: OperationsComponentCheck): OperationsSignal {
  return {
    key: component.key,
    label: component.label,
    kind: 'component',
    status: component.status,
    summary: component.summary,
    checkedAt: component.checked_at,
    metrics: componentEvidence(component),
  }
}

function applicationSignal(overview: OperationsOverviewResponse): OperationsSignal {
  const application = overview.application
  const status: OperationsStatus = application.schema_current === true
    ? 'healthy'
    : application.schema_current === false
      ? 'critical'
      : 'unknown'
  const summary = application.schema_current === true
    ? 'The running application and database migration revisions match.'
    : application.schema_current === false
      ? 'The running application and database migration revisions do not match.'
      : 'Application and database migration compatibility could not be confirmed.'
  return {
    key: 'application',
    label: 'Application and schema',
    kind: 'component',
    status,
    summary,
    checkedAt: overview.generated_at,
    metrics: [
      { label: 'Application version', value: application.version },
      { label: 'Applied schema', value: application.schema_revision ?? 'Unavailable' },
      { label: 'Expected schema', value: application.expected_schema_revision },
    ],
  }
}

function backlogSignal(backlog: OperationsBacklogSnapshot): OperationsSignal {
  const oldest = backlog.oldest_pending_age_seconds
  return {
    key: backlog.key,
    label: backlog.label,
    kind: 'workflow',
    status: backlog.status,
    summary: oldest == null
      ? 'No durable work is currently waiting.'
      : `Oldest durable work has waited ${formatDuration(oldest)}; the warning threshold is ${formatDuration(backlog.degraded_after_seconds)}.`,
    checkedAt: '',
    metrics: [
      { label: 'Pending', value: backlog.pending_count.toLocaleString() },
      { label: 'Active', value: backlog.active_count.toLocaleString() },
      { label: 'Stale active', value: backlog.stale_count.toLocaleString(), tone: backlog.stale_count ? 'danger' : 'default' },
      { label: 'Retained failures', value: backlog.failed_count.toLocaleString() },
    ],
  }
}

function storageSignal(storage: OperationsStorageIndicator): OperationsSignal {
  const hasCapacity = storage.total_bytes != null && storage.percent_used != null
  return {
    key: `storage:${storage.key}`,
    label: storage.label,
    kind: 'storage',
    status: storage.status,
    summary: hasCapacity
      ? `${formatBytes(storage.used_bytes)} used of ${formatBytes(storage.total_bytes)}; ${formatBytes(storage.available_bytes)} available.`
      : storage.used_bytes == null
        ? 'Storage measurement is unavailable.'
        : `${formatBytes(storage.used_bytes)} logical size; capacity is not visible to this probe.`,
    checkedAt: '',
    metrics: hasCapacity
      ? [{ label: 'Used', value: `${storage.percent_used?.toFixed(1)}%`, tone: storage.status === 'critical' ? 'danger' : storage.status === 'degraded' ? 'warning' : 'default' }]
      : [],
  }
}

function componentEvidence(component: OperationsComponentCheck): OperationsSignal['metrics'] {
  const metrics = component.metrics
  if (component.key === 'workers') {
    const workerCount = numberMetric(metrics, 'worker_count')
    const required = stringArrayMetric(metrics, 'required_queues')
    const covered = stringArrayMetric(metrics, 'covered_queues')
    const missing = stringArrayMetric(metrics, 'missing_queues')
    const entries: OperationsSignal['metrics'] = []
    if (workerCount != null) entries.push({ label: 'Responding workers', value: workerCount.toLocaleString() })
    if (required) entries.push({ label: 'Queue coverage', value: `${covered?.length ?? 0}/${required.length}` })
    if (missing?.length) entries.push({ label: 'Missing consumers', value: missing.join(', '), tone: 'danger' })
    return entries
  }
  if (component.key === 'scheduler') {
    const threshold = numberMetric(metrics, 'stale_after_seconds')
    const schedulerAge = numberMetric(metrics, 'scheduler_age_seconds')
    const roundTripAge = numberMetric(metrics, 'worker_round_trip_age_seconds')
    return [
      { label: 'Scheduler heartbeat', value: heartbeatEvidence(schedulerAge, threshold, stringMetric(metrics, 'scheduler_reason')) },
      { label: 'Worker round trip', value: heartbeatEvidence(roundTripAge, threshold, stringMetric(metrics, 'worker_round_trip_reason')) },
    ]
  }
  if (component.key === 'encrypted_data') {
    const total = numberMetric(metrics, 'total_records')
    const unreadable = numberMetric(metrics, 'unreadable_fields')
    const entries: OperationsSignal['metrics'] = []
    if (total != null) entries.push({ label: 'Records inspected', value: total.toLocaleString() })
    if (unreadable != null) entries.push({ label: 'Unreadable fields', value: unreadable.toLocaleString(), tone: unreadable ? 'danger' : 'default' })
    return entries
  }
  return []
}

export function workerReasonCopy(reason: OperationsWorkerReason): {
  headline: string
  explanation: string
} {
  const values: Record<OperationsWorkerReason, { headline: string; explanation: string }> = {
    healthy: {
      headline: 'Worker execution paths are responding',
      explanation: 'Required consumers replied, advertised the expected queues, and have fresh execution evidence.',
    },
    no_replies: {
      headline: 'No workers answered the control probe',
      explanation: 'Redis or the Celery control path may be unavailable, or every worker process may be stopped.',
    },
    probe_failed: {
      headline: 'Worker telemetry could not be collected',
      explanation: 'The probe failed before worker availability could be established. Treat topology as unknown.',
    },
    queue_inventory_unavailable: {
      headline: 'Queue subscriptions could not be verified',
      explanation: 'Workers answered, but their queue inventory did not. Missing consumers cannot be inferred safely.',
    },
    partial_inventory: {
      headline: 'Worker telemetry is incomplete',
      explanation: 'Some responding workers did not answer every diagnostic command. Visible coverage may be incomplete.',
    },
    missing_consumers: {
      headline: 'Required queues have no responding consumer',
      explanation: 'Work routed to the named queues will wait until a correctly configured worker subscribes.',
    },
    canary_dispatch_unavailable: {
      headline: 'Queue execution canaries are not being dispatched',
      explanation: 'Workers answered, but the scheduler heartbeat is unavailable or stale. Queue canaries cannot distinguish a worker stall from a scheduler outage until dispatch resumes.',
    },
    execution_evidence_missing: {
      headline: 'Queue execution has not been proven yet',
      explanation: 'Consumers are advertised, but no recent queue canary has completed. This may be startup delay or stalled execution.',
    },
    execution_stalled: {
      headline: 'One or more queue execution paths are stale',
      explanation: 'A consumer is advertised, but its execution canary has stopped advancing.',
    },
    saturated: {
      headline: 'Worker capacity is saturated',
      explanation: 'All observed execution slots are busy while additional work remains reserved.',
    },
  }
  return values[reason]
}

export function formatDuration(seconds: number | null): string {
  if (seconds == null) return 'unavailable'
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3_600) return `${Math.round(seconds / 60)}m`
  if (seconds < 86_400) return `${Math.round(seconds / 3_600)}h`
  return `${Math.round(seconds / 86_400)}d`
}

export function formatBytes(bytes: number | null): string {
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

export function formatWireLabel(value: string): string {
  const label = value.replaceAll('_', ' ')
  return label.charAt(0).toUpperCase() + label.slice(1)
}

function heartbeatEvidence(age: number | null, threshold: number | null, reason: string | null): string {
  if (age == null) return reason ? formatWireLabel(reason) : 'Unavailable'
  const reasonLabel = reason && reason !== 'healthy' ? ` · ${formatWireLabel(reason)}` : ''
  return threshold == null
    ? `${formatDuration(age)} ago${reasonLabel}`
    : `${formatDuration(age)} ago · ${formatDuration(threshold)} freshness window${reasonLabel}`
}

function numberMetric(metrics: OperationsComponentCheck['metrics'], key: string): number | null {
  const value = metrics[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function stringMetric(metrics: OperationsComponentCheck['metrics'], key: string): string | null {
  const value = metrics[key]
  return typeof value === 'string' && value ? value : null
}

function stringArrayMetric(metrics: OperationsComponentCheck['metrics'], key: string): string[] | null {
  const value = metrics[key]
  return Array.isArray(value) && value.every((entry) => typeof entry === 'string') ? value : null
}
