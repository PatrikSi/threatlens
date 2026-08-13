export interface NotificationWebhookField {
  key: string
  value: string
}

export interface NotificationTemplateVariable {
  key: string
  description: string
  example: string
}

export type NotificationEventType = 'rss_item_new' | 'alert_match' | 'feed_failing' | 'webhook_failed' | 'daily_digest' | 'report_ready'

export interface NotificationWebhook {
  id: string
  user_id: string
  name: string
  enabled: boolean
  event_type: NotificationEventType
  url_template: string
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  feed_scope: 'all' | 'selected'
  feed_ids: string[]
  query_params: NotificationWebhookField[]
  headers: NotificationWebhookField[]
  body_mode: 'none' | 'json' | 'form' | 'raw'
  body_fields: NotificationWebhookField[]
  body_template: string | null
  timeout_seconds: number
  secrets_redacted?: boolean
  created_at: string
  updated_at: string
}

export interface NotificationWebhookWriteRequest {
  name: string
  enabled: boolean
  event_type: NotificationEventType
  url_template: string
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  feed_scope: 'all' | 'selected'
  feed_ids: string[]
  query_params: NotificationWebhookField[]
  headers: NotificationWebhookField[]
  body_mode: 'none' | 'json' | 'form' | 'raw'
  body_fields: NotificationWebhookField[]
  body_template: string | null
  timeout_seconds: number
}

export interface NotificationWebhookTestResponse {
  success: boolean
  status_code: number | null
  duration_ms: number | null
  rendered_url: string
  rendered_method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  rendered_headers: NotificationWebhookField[]
  rendered_query_params: NotificationWebhookField[]
  rendered_body: string | null
  response_body_preview: string | null
  error: string | null
}

export interface NotificationWebhookDelivery {
  id: string
  webhook_id: string
  user_id: string
  event_type: NotificationEventType
  item_id: string | null
  feed_id: string | null
  item_title: string | null
  feed_name: string | null
  delivery_kind: 'live' | 'retry'
  delivery_state: 'pending' | 'sending' | 'succeeded' | 'failed'
  attempt_count: number
  not_before: string | null
  claimed_at: string | null
  success: boolean
  status_code: number | null
  duration_ms: number | null
  timeout_seconds: number
  rendered_url: string
  rendered_method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  rendered_headers: NotificationWebhookField[]
  rendered_query_params: NotificationWebhookField[]
  rendered_body: string | null
  response_body_preview: string | null
  error: string | null
  attempted_at: string
  warnings: string[]
}

export interface NotificationWebhookDeliveryListResponse {
  deliveries: NotificationWebhookDelivery[]
  total: number
  page: number
  page_size: number
}

export interface NotificationAnalyticsEventSummary {
  event_type: NotificationEventType
  total_deliveries: number
  failed_deliveries: number
}

export interface NotificationAnalyticsWebhookSummary {
  webhook_id: string
  webhook_name: string
  failed_deliveries: number
  last_failure_at: string | null
}

export interface NotificationQueueSnapshot {
  status: 'healthy' | 'degraded' | 'critical'
  ok: boolean
  pending_deliveries: number
  sending_deliveries: number
  stale_sending_deliveries: number
  oldest_pending_age_seconds: number | null
  oldest_sending_age_seconds: number | null
  degraded_after_seconds: number
  stale_after_seconds: number
}

export interface NotificationAnalyticsResponse {
  total_deliveries: number
  successful_deliveries: number
  failed_deliveries: number
  success_rate_pct: number
  failures_last_24h: number
  most_failing_webhook: NotificationAnalyticsWebhookSummary | null
  events: NotificationAnalyticsEventSummary[]
  queue: NotificationQueueSnapshot
}
