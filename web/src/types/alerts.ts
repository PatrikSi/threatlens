import type { ItemListEntry } from './items'

export interface AlertInterest {
  id: string
  user_id: string
  name: string
  category: string
  keywords: string[]
  enabled: boolean
  severity?: AlertSeverity
  revision?: number
  row_version?: number
  durable_since?: string | null
  suppression_until?: string | null
  suppression_reason?: string | null
  created_at: string
  updated_at: string
}

export type AlertSeverity = 'low' | 'medium' | 'high' | 'critical'
export type AlertOccurrenceState = 'new' | 'acknowledged' | 'investigating' | 'closed'
export type AlertClosureDisposition =
  | 'true_positive'
  | 'false_positive'
  | 'benign'
  | 'duplicate'
  | 'informational'
  | 'other'

export interface AlertOccurrence {
  id: string
  alert_interest_id: string | null
  rule_id_snapshot: string
  owner_user_id: string
  item_id: string | null
  item_id_snapshot: string
  integration_event_id: string | null
  rule_revision: number
  item_content_hash: string
  alert_name_snapshot: string
  alert_category_snapshot: string
  alert_keywords_snapshot: string[]
  matched_keywords: string[]
  source_snapshot_json: Record<string, unknown>
  severity_snapshot: AlertSeverity
  lifecycle_state: AlertOccurrenceState
  is_suppressed: boolean
  suppressed_at: string | null
  suppression_reason: string | null
  is_snoozed: boolean
  snoozed_until: string | null
  snooze_reason: string | null
  closure_disposition: string | null
  acknowledged_at: string | null
  acknowledged_by_user_id: string | null
  investigating_at: string | null
  investigating_by_user_id: string | null
  closed_at: string | null
  closed_by_user_id: string | null
  version: number
  created_at: string
  updated_at: string
}

export interface AlertOccurrenceListResponse {
  items: AlertOccurrence[]
  total: number
  page: number
  page_size: number
}

export interface AlertOccurrenceActivity {
  id: string
  occurrence_id: string
  actor_user_id: string | null
  action: string
  details_json: Record<string, unknown>
  created_at: string
}

export interface AlertOccurrenceActivityListResponse {
  items: AlertOccurrenceActivity[]
  total: number
  page: number
  page_size: number
}

export interface AlertOccurrenceBulkResponse {
  items: AlertOccurrence[]
  updated: number
}

export interface AlertBackfillCandidate {
  item_id: string
  content_hash: string
  title: string
  first_seen_at: string
}

export interface AlertBackfillPreviewResponse {
  preview_token: string
  expires_at: string
  candidates: AlertBackfillCandidate[]
  matched_count: number
  returned_count: number
  truncated: boolean
  has_more: boolean
  next_cursor_first_seen_at: string | null
  next_cursor_item_id: string | null
  notifications_enabled: false
}

export interface AlertBackfillApplyResponse {
  accepted: number
  existing: number
  skipped: number
  enqueue_failed: boolean
  has_more: boolean
  next_cursor_first_seen_at: string | null
  next_cursor_item_id: string | null
  notifications_enabled: false
}

export type AlertEvaluationState =
  | 'pending'
  | 'processing'
  | 'retry_wait'
  | 'succeeded'
  | 'dead_letter'
export type AlertEvaluationSource = 'live' | 'reconciliation' | 'backfill' | 'replay'

export interface AlertEvaluationRequest {
  id: string
  item_id: string
  item_content_hash: string
  state: AlertEvaluationState
  source: Exclude<AlertEvaluationSource, 'replay'>
  active_source: AlertEvaluationSource
  notify: boolean
  respect_rule_cutover: boolean
  attempt_count: number
  max_attempts: number
  dispatch_attempt_count: number
  dispatch_failure_count: number
  version: number
  accepted_rule_count: number
  accepted_match_count: number
  degraded_owner_count: number
  degraded_owners_json: Array<Record<string, unknown>>
  evaluated_rule_count: number
  occurrence_count: number
  backfill_count: number
  accepted_at: string
  available_at: string
  dispatch_claimed_at: string | null
  last_dispatch_failed_at: string | null
  claimed_at: string | null
  lease_expires_at: string | null
  completed_at: string | null
  last_backfill_at: string | null
  last_replayed_at: string | null
  last_error_code: string | null
  last_error_message: string | null
  created_at: string
  updated_at: string
}

export interface AlertEvaluationListResponse {
  items: AlertEvaluationRequest[]
  total: number
  page: number
  page_size: number
}

export interface AlertEvaluationActivity {
  id: string
  request_id: string
  actor_user_id: string | null
  action: string
  details_json: Record<string, unknown>
  created_at: string
}

export interface AlertEvaluationActivityListResponse {
  items: AlertEvaluationActivity[]
  total: number
  page: number
  page_size: number
}

export interface AlertEvaluationReplayResponse {
  request: AlertEvaluationRequest
  enqueue_failed: boolean
}

export interface AlertOccurrenceMetric {
  id: string
  bucket_start: string
  owner_user_id: string
  severity: AlertSeverity
  lifecycle_state: AlertOccurrenceState
  suppressed: boolean
  occurrence_count: number
  created_at: string
  updated_at: string
}

export interface AlertOccurrenceMetricListResponse {
  items: AlertOccurrenceMetric[]
  truncated: boolean
}

export interface AlertMatchReference {
  alert_id: string
  alert_name: string
  category: string
  matched_keywords: string[]
}

export interface AlertMatchEntry extends ItemListEntry {
  matches: AlertMatchReference[]
}

export interface AlertMatchListResponse {
  items: AlertMatchEntry[]
  total: number
  page: number
  page_size: number
}
