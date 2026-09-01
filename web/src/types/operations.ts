export type OperationsStatus = 'healthy' | 'degraded' | 'critical' | 'unavailable' | 'unknown'
export type SystemOperationType = 'backup' | 'verify' | 'restore_drill' | 'restore' | 'diagnostics'
export type SystemOperationStatus = 'running' | 'succeeded' | 'failed'
export type OperationsHealthWindow = '1h' | '6h' | '24h' | '7d' | '30d'
export type OperationsWorkerReason =
  | 'healthy'
  | 'no_replies'
  | 'probe_failed'
  | 'queue_inventory_unavailable'
  | 'partial_inventory'
  | 'missing_consumers'
  | 'canary_dispatch_unavailable'
  | 'execution_evidence_missing'
  | 'execution_stalled'
  | 'saturated'
export type OperationsWorkerProbeQuality =
  | 'complete'
  | 'partial'
  | 'no_replies'
  | 'failed'
  | 'invalid'
export type OperationsWorkerProbeName =
  | 'ping'
  | 'active_queues'
  | 'stats'
  | 'active'
  | 'reserved'
  | 'scheduled'
export type OperationsQueueExecutionReason =
  | 'fresh'
  | 'missing'
  | 'stale'
  | 'invalid'
  | 'future'
  | 'redis_unavailable'
export type OperationsCanaryDispatchReason =
  | 'healthy'
  | 'missing'
  | 'stale'
  | 'invalid'
  | 'future'
  | 'redis_unavailable'
export type OperationsHealthDownsamplingStrategy =
  | 'none'
  | 'transition_anomaly_preserving'

export interface SystemOperationRun {
  id: string
  operation_type: SystemOperationType
  status: SystemOperationStatus
  initiated_by: string
  source: string
  started_at: string
  finished_at: string | null
  created_at: string
  updated_at: string
  metadata: Record<string, unknown>
  error_code: string | null
  error_message: string | null
}

export interface SystemOperationRunListResponse {
  runs: SystemOperationRun[]
  total: number
  page: number
  page_size: number
}

export interface OperationsApplicationInfo {
  version: string
  schema_revision: string | null
  expected_schema_revision: string
  schema_current: boolean | null
}

export interface OperationsComponentCheck {
  key: string
  label: string
  status: OperationsStatus
  summary: string
  checked_at: string
  metrics: Record<string, boolean | number | string | null | string[]>
}

export interface OperationsStorageIndicator {
  key: string
  label: string
  status: OperationsStatus
  used_bytes: number | null
  total_bytes: number | null
  available_bytes: number | null
  percent_used: number | null
}

export interface OperationsBacklogSnapshot {
  key: string
  label: string
  status: OperationsStatus
  pending_count: number
  active_count: number
  stale_count: number
  failed_count: number
  oldest_pending_age_seconds: number | null
  degraded_after_seconds: number
}

export interface OperationsRecoverySnapshot {
  latest_backup: SystemOperationRun | null
  latest_verify: SystemOperationRun | null
  latest_restore_drill: SystemOperationRun | null
  latest_restore: SystemOperationRun | null
}

export interface OperationsIssue {
  code: string
  severity: 'warning' | 'critical'
  component: string
  summary: string
  effect: string
  recommended_action: string
}

export interface OperationsOverviewResponse {
  generated_at: string
  overall_status: OperationsStatus
  application: OperationsApplicationInfo
  components: OperationsComponentCheck[]
  storage: OperationsStorageIndicator[]
  backlogs: OperationsBacklogSnapshot[]
  recovery: OperationsRecoverySnapshot
  issues: OperationsIssue[]
}

export interface OperationsDiagnosticsResponse {
  schema_version: 2
  generated_at: string
  overview: OperationsOverviewResponse
  recent_runs: SystemOperationRun[]
  recent_runs_truncated: boolean
  worker_topology: OperationsWorkerTopology
  health_history: OperationsHealthHistoryResponse
}

export interface OperationsWorkerProbe {
  probe: OperationsWorkerProbeName
  quality: OperationsWorkerProbeQuality
  responder_count: number
  observed_responder_count: number
  responses_truncated: boolean
  missing_responder_count: number
  invalid_response_count: number
  duration_ms: number
}

export interface OperationsWorkerNode {
  name: string
  queues: string[]
  responded_to: OperationsWorkerProbeName[]
  missing_responses: OperationsWorkerProbeName[]
  ping_ok: boolean | null
  capacity: number | null
  active_count: number | null
  reserved_count: number | null
  scheduled_count: number | null
  processed_total: number | null
  uptime_seconds: number | null
  saturated: boolean
}

export interface OperationsWorkerQueue {
  key: string
  label: string
  service_hint: string
  required: boolean
  status: OperationsStatus
  consumers: string[]
  consumer_count: number
  capacity: number | null
  active_count: number | null
  reserved_count: number | null
  scheduled_count: number | null
  saturated: boolean
  execution_reason: OperationsQueueExecutionReason
  execution_heartbeat_at: string | null
  execution_age_seconds: number | null
  execution_worker: string | null
}

export interface OperationsWorkerTopology {
  generated_at: string
  status: OperationsStatus
  reason: OperationsWorkerReason
  timeout_seconds: number
  duration_ms: number
  responding_worker_count: number
  observed_worker_count: number
  worker_inventory_truncated: boolean
  total_capacity: number | null
  active_count: number | null
  reserved_count: number | null
  scheduled_count: number | null
  missing_queues: string[]
  stale_execution_queues: string[]
  missing_execution_evidence_queues: string[]
  canary_dispatch_ok: boolean
  canary_dispatch_reason: OperationsCanaryDispatchReason
  canary_dispatch_heartbeat_at: string | null
  canary_dispatch_age_seconds: number | null
  probes: OperationsWorkerProbe[]
  workers: OperationsWorkerNode[]
  queues: OperationsWorkerQueue[]
}

export interface OperationsHealthHistoryCoverage {
  requested_start: string
  requested_end: string
  first_sample_at: string | null
  last_sample_at: string | null
  expected_sample_count: number
  actual_sample_count: number
  returned_sample_count: number
  missing_sample_count: number
  coverage_percent: number
  gap_count: number
  largest_gap_seconds: number | null
  collection_stale: boolean
  source_anomaly_sample_count: number
  returned_anomaly_sample_count: number
  source_transition_count: number
  returned_transition_count: number
  anomaly_evidence_truncated: boolean
  transition_evidence_truncated: boolean
  gap_intervals: OperationsHealthGapInterval[]
  returned_gap_interval_count: number
  gap_intervals_truncated: boolean
}

export interface OperationsHealthGapInterval {
  start_at: string
  end_at: string
  duration_seconds: number
  kind: 'leading' | 'internal' | 'trailing' | 'window'
}

export interface OperationsHealthHistorySample {
  sampled_at: string
  overall_status: OperationsStatus
  component_statuses: Record<string, OperationsStatus>
  worker_status: OperationsStatus
  worker_reason: OperationsWorkerReason
  responding_worker_count: number
  observed_worker_count: number
  worker_inventory_truncated: boolean
  total_capacity: number | null
  active_count: number | null
  reserved_count: number | null
  scheduled_count: number | null
  missing_queues: string[]
  stale_execution_queues: string[]
  backlog_pending_count: number
  backlog_stale_count: number
  critical_issue_count: number
  warning_issue_count: number
  issue_codes: string[]
}

export interface OperationsHealthHistoryResponse {
  generated_at: string
  window: OperationsHealthWindow
  sample_interval_seconds: number
  effective_resolution_seconds: number
  downsampling_strategy: OperationsHealthDownsamplingStrategy
  retention_days: number
  coverage: OperationsHealthHistoryCoverage
  samples: OperationsHealthHistorySample[]
}
