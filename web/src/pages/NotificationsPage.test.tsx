import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

const notificationsPageMocks = vi.hoisted(() => ({
  queryClient: {
    invalidateQueries: vi.fn(),
  },
  useUnsavedChangesWarning: vi.fn(),
}))

function createDiscardMock() {
  return Object.assign(
    vi.fn((onDiscard?: () => void) => {
      onDiscard?.()
      return true
    }),
    { discardDialog: null },
  )
}

notificationsPageMocks.useUnsavedChangesWarning.mockImplementation(() => createDiscardMock())

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => notificationsPageMocks.queryClient,
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => {
    const [scope, key] = queryKey
    const baseResult = {
      isLoading: false,
      isError: false,
      error: null,
      refetchInterval: false,
    }

    if (scope === 'notifications' && key === 'webhooks' && queryKey.length === 2) {
      return {
        ...baseResult,
        data: [
          {
            id: 'webhook-1',
            user_id: 'user-1',
            name: 'Slack alert relay',
            enabled: true,
            event_type: 'alert_match',
            url_template: 'https://hooks.example.com/notify',
            method: 'POST',
            feed_scope: 'all',
            feed_ids: [],
            query_params: [],
            headers: [],
            body_mode: 'json',
            body_fields: [],
            body_template: null,
            timeout_seconds: 10,
            created_at: '2026-04-20T10:00:00Z',
            updated_at: '2026-04-21T10:00:00Z',
          },
        ],
      }
    }

    if (scope === 'feeds') {
      return {
        ...baseResult,
        data: [
          {
            id: 'feed-1',
            name: 'Vendor advisories',
            url: 'https://example.com/feed.xml',
            description: null,
            site_url: null,
            language: null,
            enabled: true,
            fetch_mode: 'schedule',
            fetch_interval_seconds: 1800,
            schedule_cron: '0 * * * *',
            etag: null,
            last_modified: null,
            last_fetch_at: null,
            last_success_at: null,
            error_count: 0,
            last_error: null,
            has_unreadable_url: false,
            created_at: '2026-04-20T10:00:00Z',
          },
        ],
      }
    }

    if (scope === 'notifications' && key === 'template-variables') {
      return {
        ...baseResult,
        data: [
          {
            key: 'item.title',
            description: 'Current item title',
            example: 'Critical VPN advisory',
          },
        ],
      }
    }

    if (scope === 'notifications' && key === 'analytics') {
      return {
        ...baseResult,
        data: {
          total_deliveries: 8,
          successful_deliveries: 7,
          failed_deliveries: 1,
          success_rate_pct: 87.5,
          failures_last_24h: 1,
          most_failing_webhook: {
            webhook_id: 'webhook-1',
            webhook_name: 'Slack alert relay',
            failed_deliveries: 1,
            last_failure_at: '2026-04-21T09:00:00Z',
          },
          events: [
            {
              event_type: 'alert_match',
              total_deliveries: 8,
              failed_deliveries: 1,
            },
          ],
          queue: {
            status: 'healthy',
            ok: true,
            pending_deliveries: 0,
            sending_deliveries: 0,
            stale_sending_deliveries: 0,
            oldest_pending_age_seconds: null,
            oldest_sending_age_seconds: null,
            degraded_after_seconds: 900,
            stale_after_seconds: 1800,
          },
        },
      }
    }

    return {
      ...baseResult,
      data: undefined,
    }
  },
  useMutation: () => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  }),
}))

vi.mock('../hooks/useUnsavedChangesWarning', () => ({
  useUnsavedChangesWarning: notificationsPageMocks.useUnsavedChangesWarning,
}))

import { NotificationsPage } from './NotificationsPage'

describe('NotificationsPage rendered workflow', () => {
  it('renders labeled webhook configuration controls and wires the discard warning', () => {
    const markup = renderToStaticMarkup(createElement(NotificationsPage))

    expect(markup).toContain('Webhook Notifications')
    expect(markup).toContain('Notification Analytics')
    expect(markup).toContain('Saved Webhooks')
    expect(markup).toContain('Webhook URL')
    expect(markup).toContain('Test webhook')
    expect(notificationsPageMocks.useUnsavedChangesWarning).toHaveBeenCalledWith(false, 'Discard unsaved webhook changes?')
  })
})
