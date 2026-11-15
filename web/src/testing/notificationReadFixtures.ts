import type { NotificationAnalyticsResponse, NotificationWebhook, NotificationWebhookDelivery, SMTPAnalyticsResponse, SMTPDelivery, SMTPHook, SMTPTestRun } from '../types/api'
import { createDefaultDraft } from '../pages/notificationWebhookDraft'

export const webhook: NotificationWebhook = {
  ...createDefaultDraft(), id: 'notify-hook', user_id: 'operator', name: 'Private webhook destination',
  url_template: 'https://private-webhook.example.test/receiver', created_at: '2026-11-15T10:00:00Z', updated_at: '2026-11-15T10:00:00Z',
}
export const notificationDelivery: NotificationWebhookDelivery = {
  id: 'notify-delivery', webhook_id: webhook.id, user_id: 'operator', event_type: 'rss_item_new',
  item_id: 'item-1', feed_id: null, item_title: 'Private notification evidence', feed_name: null,
  delivery_kind: 'live', delivery_state: 'failed', attempt_count: 1, not_before: null, claimed_at: null,
  success: false, status_code: 503, duration_ms: 40, timeout_seconds: 10,
  rendered_url: 'https://private-delivery.example.test/receiver', rendered_method: 'POST', rendered_headers: [], rendered_query_params: [],
  rendered_body: '{"event":"synthetic"}', response_body_preview: 'Private webhook response', error: 'Private webhook transport error',
  attempted_at: '2026-11-15T10:00:00Z', warnings: [],
}
export const notificationAnalytics: NotificationAnalyticsResponse = {
  total_deliveries: 19, successful_deliveries: 18, failed_deliveries: 1, success_rate_pct: 94.7, failures_last_24h: 1,
  most_failing_webhook: { webhook_id: webhook.id, webhook_name: 'Private failing webhook', failed_deliveries: 1, last_failure_at: '2026-11-15T10:00:00Z' },
  events: [], queue: { status: 'healthy', ok: true, pending_deliveries: 0, sending_deliveries: 0, stale_sending_deliveries: 0,
    oldest_pending_age_seconds: null, oldest_sending_age_seconds: null, degraded_after_seconds: 60, stale_after_seconds: 120 },
}

const smtpBase: SMTPHook = {
    id: 'smtp-1',
    name: 'SMTP',
    integration_type: 'smtp',
    direction: 'destination',
    enabled: true,
    configured: true,
    schema_version: 1,
    host: 'smtp.example.com',
    port: 587,
    security: 'starttls',
    username: 'relay-user',
    password_configured: true,
    has_unreadable_secret: false,
    from_email: 'threatlens@example.com',
    from_name: 'ThreatLens',
    to_emails: ['analyst@example.com', 'soc@example.com'],
    timeout_seconds: 10,
    event_types: ['rss_item_new'],
    feed_scope: 'all',
    feed_ids: [],
    subject_template: '[ThreatLens] {{ event.type }}: {{ item.title }}',
    html_template: '<h2>{{ event.type }}</h2><p>{{ item.title }}</p>',
    health_status: 'healthy',
    last_test_at: '2026-07-04T10:00:00Z',
    last_success_at: '2026-07-04T10:00:00Z',
    last_error_at: null,
    last_error: null,
    last_test_duration_ms: 42,
    created_at: '2026-07-04T09:00:00Z',
    updated_at: '2026-07-04T09:30:00Z',
    is_default: true,
    uses_shared_credentials: false,
    credential_source_id: null,
    credential_source_name: null,
  }

export const smtpHook: SMTPHook = { ...smtpBase, id: 'smtp-hook', name: 'Private SMTP destination', host: 'private-relay.example.test', username: 'private-smtp-user' }
export const smtpDelivery: SMTPDelivery = {
  id: 'smtp-delivery', hook_id: smtpHook.id, event_type: 'rss_item_new', delivery_kind: 'live', state: 'dead_letter',
  attempt_count: 5, max_attempts: 5, feed_id: null, item_id: null, source_delivery_id: null,
  last_duration_ms: 40, last_error_code: 'transport_error', last_error_message: 'Private SMTP delivery diagnostic', last_error_retryable: false,
  created_at: '2026-11-15T10:00:00Z', updated_at: '2026-11-15T10:00:00Z', completed_at: null, dead_lettered_at: '2026-11-15T10:00:00Z', attempts: [],
}
export const smtpTestRun: SMTPTestRun = {
  id: 'smtp-test-run', hook_id: smtpHook.id, status: 'failed', action: 'send', recipient_email: 'private-test-recipient@example.test',
  used_unsaved_settings: false, duration_ms: 40, error_code: 'transport_error', error_message: 'Private SMTP test diagnostic',
  server_message: null, started_at: '2026-11-15T10:00:00Z', finished_at: '2026-11-15T10:00:00Z',
}
export const smtpAnalytics: SMTPAnalyticsResponse = {
  hook_count: 1, enabled_hook_count: 1, total_deliveries: 8, successful_deliveries: 7, failed_deliveries: 1,
  success_rate_pct: 87.5, failures_last_24h: 1, pending_deliveries: 0, retry_wait_deliveries: 0,
  most_failing_hook: { hook_id: smtpHook.id, hook_name: 'Private failing email destination', failed_deliveries: 1, last_failure_at: '2026-11-15T10:00:00Z' }, events: [],
}
