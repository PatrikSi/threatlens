// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { NotificationDeliveryHistory } from './NotificationDeliveryHistory'
import { NotificationWebhookDialogs } from './NotificationWebhookDialogs'
import { SavedWebhooksCard } from './NotificationWebhookCards'
import { useNotificationWebhooksController } from './useNotificationWebhooksController'
import { createDefaultDraft } from './notificationWebhookDraft'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({
  data: { role: 'analyst', access: { permissions: ['write:notifications'] }, features: {} },
  isLoading: false, isError: false,
}) }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.clearAllMocks() })

function RetryHarness() {
  const controller = useNotificationWebhooksController()
  return <>
    <SavedWebhooksCard controller={controller} />
    <NotificationDeliveryHistory controller={controller} />
    <NotificationWebhookDialogs controller={controller} />
  </>
}

it('announces actionable HTTP 409 details after the retry dialog closes without implying another attempt is safe', async () => {
  const pending = deferred<never>()
  const retryPath = '/notifications/webhooks/hook-1/deliveries/delivery-1/retry'
  const detail = 'This delivery has no successfully rendered request and cannot be retried or replayed. Reduce or correct the webhook template, preview a stored event, then wait for a new matching event.'
  const webhook = { ...createDefaultDraft(), id: 'hook-1', name: 'SOC destination', user_id: 'analyst', created_at: '', updated_at: '' }
  const delivery = {
    id: 'delivery-1', webhook_id: webhook.id, user_id: 'analyst', event_type: 'article.ai.ready',
    item_id: 'item-1', feed_id: 'feed-1', item_title: 'Campaign evidence', feed_name: 'SOC feed',
    delivery_kind: 'live', delivery_state: 'failed', attempt_count: 1, not_before: null, claimed_at: null,
    success: false, status_code: null, duration_ms: null, timeout_seconds: 10,
    rendered_url: 'https://soc.example.test', rendered_method: 'POST', rendered_headers: [], rendered_query_params: [],
    rendered_body: null, response_body_preview: null, error: 'Rendered request body exceeded its byte limit.',
    attempted_at: '2026-10-01T00:00:00Z', warnings: [],
  }
  vi.mocked(apiFetch).mockImplementation((path) => {
    if (path === retryPath) return pending.promise
    if (path === '/notifications/webhooks') return Promise.resolve([webhook])
    if (path.includes('/deliveries?')) return Promise.resolve({ deliveries: [delivery], total: 1, page: 1, page_size: 10 })
    return Promise.resolve([])
  })
  view = await mountIntel(<RetryHarness />)
  act(() => view!.host.querySelector<HTMLButtonElement>('button[aria-pressed="false"]')!.click())
  await settle()
  const details = view.host.querySelector('details')!
  act(() => { details.open = true; intelButton(view!.host, 'Retry failed delivery').click() })
  await settle()
  expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain('Retry failed delivery?')
  act(() => intelButton(document, 'Retry delivery').click())
  await settle()
  expect(document.querySelector('[role="alertdialog"]')).not.toBeNull()
  await act(async () => pending.reject(new ApiError(detail, 409, retryPath, null, { requestId: 'render-retry-409' })))
  await settle()
  expect(document.querySelector('[role="alertdialog"]')).toBeNull()
  const alert = view.host.querySelector('[role="alert"]')
  expect(alert?.textContent).toContain(detail)
  expect(alert?.textContent).toContain('Request reference: render-retry-409.')
  expect(alert?.textContent).not.toContain('Try again')
  expect(vi.mocked(apiFetch).mock.calls.filter(([path]) => path === retryPath)).toHaveLength(1)
})
