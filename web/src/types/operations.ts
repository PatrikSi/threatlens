export type OperationsStatus = 'healthy' | 'degraded' | 'critical' | 'unavailable' | 'unknown'
export type SystemOperationType = 'backup' | 'verify' | 'restore_drill' | 'restore' | 'diagnostics'
export type SystemOperationStatus = 'running' | 'succeeded' | 'failed'

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
  schema_version: 1
  generated_at: string
  overview: OperationsOverviewResponse
  recent_runs: SystemOperationRun[]
  recent_runs_truncated: boolean
}
