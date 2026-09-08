export type LifecycleCategory =
  | 'intelligence'
  | 'detection'
  | 'integrations'
  | 'security'
  | 'governance'
  | 'system'

export type LifecycleTargetKey =
  | 'article_content'
  | 'audit_logs'
  | 'action_approval_history'
  | 'ai_task_history'
  | 'ai_usage_history'
  | 'tag_feedback_history'
  | 'integration_run_history'
  | 'inactive_auth_sessions'
  | 'system_health_samples'
  | 'integration_delivery_history'
  | 'integration_event_history'
  | 'integration_metrics'
  | 'closed_alert_history'
  | 'alert_activity_history'
  | 'alert_evaluation_history'
  | 'alert_metrics'

export type LifecycleScheduleCadence = 'daily' | 'weekly'
export type LifecycleRunStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'partial'
  | 'failed'
  | 'cancelled'
export type LifecycleRunTrigger = 'manual' | 'scheduled'

export interface LifecyclePolicy {
  target_key: LifecycleTargetKey
  enabled: boolean
  retention_days: number
  schedule_cadence: LifecycleScheduleCadence
  schedule_hour_utc: number
  schedule_weekday: number | null
  max_records_per_run: number
  options: Record<string, boolean>
  revision: number
  next_run_at: string | null
  last_run_at: string | null
  last_run_status: LifecycleRunStatus | null
  configuration_updated_at: string
  updated_at: string
  updated_by: string | null
}

export interface LifecycleSafeguardDefinition {
  key: string
  label: string
  description: string
  default_enabled: boolean
}

export interface LifecyclePreview {
  id: string
  target_key: LifecycleTargetKey
  policy_revision: number
  generated_at: string
  cutoff_at: string
  expires_at: string
  observed_at: string
  eligible_count: number
  protected_count: number
  protected_counts: Record<string, number>
  eligible_bytes: number | null
  oldest_candidate_at: string | null
  count_is_lower_bound: boolean
  is_partial: boolean
}

export interface LifecycleTarget {
  key: LifecycleTargetKey
  label: string
  category: LifecycleCategory
  description: string
  action_description: string
  cutoff_description: string
  min_retention_days: number
  max_retention_days: number
  default_retention_days: number
  safeguards: LifecycleSafeguardDefinition[]
  policy: LifecyclePolicy
  latest_preview: LifecyclePreview | null
}

export interface LifecycleOverviewResponse {
  generated_at: string
  targets: LifecycleTarget[]
}

export interface LifecyclePolicyDraft {
  enabled: boolean
  retention_days: number
  schedule_cadence: LifecycleScheduleCadence
  schedule_hour_utc: number
  schedule_weekday: number | null
  max_records_per_run: number
  options: Record<string, boolean>
}

export interface LifecyclePreviewRequest {
  target_key: LifecycleTargetKey
  expected_revision: number
  draft: LifecyclePolicyDraft
}

export interface LifecyclePolicyUpdateRequest extends LifecyclePolicyDraft {
  expected_revision: number
  preview_id?: string
  confirmation?: 'PURGE'
  reason?: string
}

export interface LifecycleRun {
  id: string
  target_key: LifecycleTargetKey
  trigger_source: LifecycleRunTrigger
  status: LifecycleRunStatus
  policy_revision: number
  policy_snapshot: Record<string, unknown>
  cutoff_at: string
  max_records: number
  reason: string | null
  requested_by: string | null
  evaluated_count: number
  affected_count: number
  affected_bytes: number | null
  protected_count: number
  skipped_count: number
  batch_count: number
  remaining_count: number | null
  cancellation_requested_at: string | null
  cancellation_requested_by: string | null
  cancellation_reason: string | null
  cancel_requested: boolean
  queued_at: string
  scheduled_for: string | null
  started_at: string | null
  heartbeat_at: string | null
  finished_at: string | null
  error_code: string | null
  error_message: string | null
  stop_reason: string | null
  details: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface LifecycleRunListResponse {
  runs: LifecycleRun[]
  total: number
  page: number
  page_size: number
}

export interface LifecycleRunRequest {
  target_key: LifecycleTargetKey
  expected_revision: number
  preview_id: string
  confirmation: 'PURGE'
  reason: string
}

export interface LifecycleRunCancelRequest {
  reason: string
}
