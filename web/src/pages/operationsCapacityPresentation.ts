import type { OperationsHealthHistorySample } from '../types/operations'
import type { TimeSeriesDefinition } from './AccessibleTimeSeries'
import { formatDuration } from './operationsHealthPresentation'

const STAGES = [
  { key: 'classification', label: 'Classification', color: '#0891b2' },
  { key: 'tagging', label: 'Tagging', color: '#059669' },
  { key: 'exports', label: 'Exports', color: '#d97706' },
]
const DATABASE_EVENTS = ['database_lock_timeout', 'database_statement_timeout', 'database_deadline', 'database_pool_timeout']

function metric(sample: OperationsHealthHistorySample, key: string): number | null {
  const value = sample.runtime_metrics?.[key]
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
}

function eventTotal(sample: OperationsHealthHistorySample, keys: string[]): number | null {
  const values = keys.map((key) => metric(sample, `${key}_last_15m`))
  return values.every((value) => value !== null) ? values.reduce<number>((sum, value) => sum + (value ?? 0), 0) : null
}

type CapacityTrend = {
  key: string
  title: string
  description: string
  series: TimeSeriesDefinition[]
  formatValue?: (value: number) => string
}

export const CAPACITY_TRENDS: CapacityTrend[] = [
  {
    key: 'freshness', title: 'Oldest pending work',
    description: 'Age of the oldest outstanding obligation. Zero means no pending work was recorded; missing observations remain gaps.',
    series: STAGES.map((stage) => ({ ...stage, value: (sample) => {
      const backlog = sample.backlogs?.find((entry) => entry.key === stage.key)
      if (!backlog) return null
      return backlog.pending_count === 0 ? 0 : backlog.oldest_pending_age_seconds
    } })),
    formatValue: formatDuration,
  },
  {
    key: 'backlog', title: 'Pending pipeline and export work',
    description: 'Outstanding classification and tagging obligations, plus queued exports. These counts are recorded independently of broker queue length.',
    series: STAGES.map((stage) => ({ ...stage, value: (sample) => sample.backlogs?.find((entry) => entry.key === stage.key)?.pending_count ?? null })),
  },
  {
    key: 'database-counts', title: 'Database connections and lock waiters',
    description: 'Connections and lock waiters for the current database and runtime role. This does not cover other database roles.',
    series: [
      { key: 'database_connections', label: 'Connections', color: '#0891b2', value: (sample) => metric(sample, 'database_connections') },
      { key: 'database_lock_waiters', label: 'Lock waiters', color: '#dc2626', value: (sample) => metric(sample, 'database_lock_waiters') },
    ],
  },
  {
    key: 'database-age', title: 'Database waits and transaction age',
    description: 'Oldest active lock wait and transaction, in the current database and runtime role.',
    series: [
      { key: 'database_oldest_lock_wait_seconds', label: 'Oldest lock wait', color: '#dc2626', value: (sample) => metric(sample, 'database_oldest_lock_wait_seconds') },
      { key: 'database_oldest_transaction_seconds', label: 'Oldest transaction', color: '#0891b2', value: (sample) => metric(sample, 'database_oldest_transaction_seconds') },
    ],
    formatValue: formatDuration,
  },
  {
    key: 'memory', title: 'Collecting container memory usage',
    description: 'Percentage of the collecting container’s configured memory limit. This is not total deployment or worker memory. Missing limits remain unknown.',
    series: [{ key: 'container_memory_percent', label: 'Container memory used', color: '#d97706', value: (sample) => metric(sample, 'container_memory_percent') }],
    formatValue: (value) => `${value.toFixed(1)}%`,
  },
  {
    key: 'deadlines', title: 'Recent timeout and deadline events',
    description: 'Each point records the latest 15 minute buckets. Overlapping samples must not be summed. Counters are shared and best effort; unavailable counters remain gaps.',
    series: [
      { key: 'database_events', label: 'Database events', color: '#dc2626', value: (sample) => eventTotal(sample, DATABASE_EVENTS) },
      { key: 'outbound_events', label: 'Outbound deadlines', color: '#0891b2', value: (sample) => eventTotal(sample, ['outbound_deadline']) },
      { key: 'export_events', label: 'Export deadlines', color: '#d97706', value: (sample) => eventTotal(sample, ['export_transfer_deadline', 'export_generation_deadline']) },
    ],
  },
]
