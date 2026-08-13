import type { NotificationEventType } from './notifications'

export type IntegrationType = 'smtp' | 'webhook'
export type IntegrationDirection = 'destination'
export type IntegrationHealthStatus = 'unknown' | 'healthy' | 'warning' | 'error'
export type SMTPSecurityMode = 'starttls' | 'ssl_tls' | 'none'

export interface IntegrationConnector {
  integration_type: IntegrationType
  direction: IntegrationDirection
  display_name: string
  description: string
  config_schema_version: number
  supports_test: boolean
  capabilities: string[]
}

export interface IntegrationSummary {
  id: string
  name: string
  integration_type: IntegrationType
  direction: IntegrationDirection
  enabled: boolean
  configured: boolean
  health_status: IntegrationHealthStatus
  last_success_at: string | null
  last_error_at: string | null
  last_error: string | null
  updated_at: string
}

export interface SMTPSettings {
  id: string
  name: string
  integration_type: 'smtp'
  direction: 'destination'
  enabled: boolean
  configured: boolean
  schema_version: number
  host: string | null
  port: number
  security: SMTPSecurityMode
  username: string | null
  password_configured: boolean
  has_unreadable_secret: boolean
  from_email: string | null
  from_name: string | null
  to_emails: string[]
  timeout_seconds: number
  event_types: NotificationEventType[]
  feed_scope: 'all' | 'selected'
  feed_ids: string[]
  subject_template: string
  html_template: string
  health_status: IntegrationHealthStatus
  last_test_at: string | null
  last_success_at: string | null
  last_error_at: string | null
  last_error: string | null
  last_test_duration_ms: number | null
  created_at: string
  updated_at: string
}

export interface SMTPSettingsUpdateRequest {
  enabled: boolean
  host: string | null
  port: number
  security: SMTPSecurityMode
  username: string | null
  password?: string | null
  clear_password?: boolean
  from_email: string | null
  from_name: string | null
  to_emails: string[]
  timeout_seconds: number
  event_types: NotificationEventType[]
  feed_scope: 'all' | 'selected'
  feed_ids: string[]
  subject_template: string
  html_template: string
}

export interface SMTPTestRequest {
  send_email?: boolean
  recipient_email?: string | null
  settings?: SMTPSettingsUpdateRequest | null
}

export interface SMTPTestResponse {
  success: boolean
  action: 'connection' | 'send'
  duration_ms: number | null
  recipient_email: string | null
  error_code: string | null
  error: string | null
  server_message: string | null
  tested_at: string
  used_unsaved_settings: boolean
}

export interface SMTPHook extends SMTPSettings {
  is_default: boolean
  uses_shared_credentials: boolean
  credential_source_id: string | null
  credential_source_name: string | null
}

export interface SMTPHookWriteRequest {
  name: string
  credential_source_id: string | null
  settings: SMTPSettingsUpdateRequest
}

export interface SMTPHookTestRequest {
  hook_id?: string | null
  hook?: SMTPHookWriteRequest | null
  send_email?: boolean
  recipient_email?: string | null
}

export interface SMTPTemplateDefault {
  send_for: NotificationEventType | 'all'
  event_types: NotificationEventType[]
  subject_template: string
  html_template: string
}

export interface SMTPAnalyticsEventSummary {
  event_type: NotificationEventType
  total_deliveries: number
  failed_deliveries: number
}

export interface SMTPAnalyticsHookSummary {
  hook_id: string
  hook_name: string
  failed_deliveries: number
  last_failure_at: string | null
}

export interface SMTPAnalyticsResponse {
  hook_count: number
  enabled_hook_count: number
  total_deliveries: number
  successful_deliveries: number
  failed_deliveries: number
  success_rate_pct: number
  failures_last_24h: number
  pending_deliveries: number
  retry_wait_deliveries: number
  most_failing_hook: SMTPAnalyticsHookSummary | null
  events: SMTPAnalyticsEventSummary[]
}

export interface SMTPDeliveryAttempt {
  attempt_number: number
  status: string
  started_at: string
  finished_at: string | null
  duration_ms: number | null
  error_code: string | null
  error_message: string | null
  retryable: boolean | null
  recipient_count: number | null
  accepted_count: number | null
}

export interface SMTPDelivery {
  id: string
  hook_id: string
  event_type: NotificationEventType
  delivery_kind: 'live' | 'replay'
  state: 'pending' | 'sending' | 'retry_wait' | 'succeeded' | 'failed' | 'dead_letter'
  attempt_count: number
  max_attempts: number
  feed_id: string | null
  item_id: string | null
  source_delivery_id: string | null
  last_duration_ms: number | null
  last_error_code: string | null
  last_error_message: string | null
  last_error_retryable: boolean | null
  created_at: string
  updated_at: string
  completed_at: string | null
  dead_lettered_at: string | null
  attempts: SMTPDeliveryAttempt[]
}

export interface SMTPDeliveryListResponse {
  deliveries: SMTPDelivery[]
  total: number
  page: number
  page_size: number
}

export interface SMTPTestRun {
  id: string
  hook_id: string
  status: 'succeeded' | 'failed'
  action: 'connection' | 'send' | null
  recipient_email: string | null
  used_unsaved_settings: boolean
  duration_ms: number | null
  error_code: string | null
  error_message: string | null
  server_message: string | null
  started_at: string
  finished_at: string | null
}

export interface SMTPTestRunListResponse {
  runs: SMTPTestRun[]
  total: number
  page: number
  page_size: number
}

export interface IntegrationDeliveryReplayResponse {
  source_delivery_id: string
  delivery_id: string
  state: 'pending'
  queued: boolean
}
