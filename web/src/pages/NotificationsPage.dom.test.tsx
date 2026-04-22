// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const notificationsPageDomMocks = vi.hoisted(() => ({
  queryClient: {
    invalidateQueries: vi.fn(),
  },
  saveMutate: vi.fn(),
  deleteMutate: vi.fn(),
  testMutate: vi.fn(),
  retryMutate: vi.fn(),
}))

const routerMocks = vi.hoisted(() => ({
  useBlocker: vi.fn(() => ({
    state: 'unblocked' as const,
    proceed: vi.fn(),
    reset: vi.fn(),
  })),
}))

function notificationMutationResult(mutate: ReturnType<typeof vi.fn>) {
  return {
    mutate,
    isPending: false,
    isError: false,
    error: null,
    reset: vi.fn(),
  }
}

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => notificationsPageDomMocks.queryClient,
  useQuery: ({ queryKey, enabled }: { queryKey: unknown[]; enabled?: boolean }) => {
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
            query_params: [{ key: 'source', value: '{{ feed.name }}' }],
            headers: [{ key: 'Authorization', value: 'Bearer token' }],
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
          total_deliveries: 1,
          successful_deliveries: 1,
          failed_deliveries: 0,
          success_rate_pct: 100,
          failures_last_24h: 0,
          most_failing_webhook: null,
          events: [],
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

    if (scope === 'notifications' && key === 'webhooks' && enabled) {
      return {
        ...baseResult,
        data: {
          deliveries: [],
          page: 1,
          page_size: 10,
          total: 0,
        },
      }
    }

    return {
      ...baseResult,
      data: undefined,
    }
  },
  useMutation: (options: { mutationFn?: unknown }) => {
    const source = String(options?.mutationFn ?? '')
    if (source.includes('/notifications/webhooks/test')) {
      return notificationMutationResult(notificationsPageDomMocks.testMutate)
    }
    if (source.includes('/deliveries/') && source.includes('/retry')) {
      return notificationMutationResult(notificationsPageDomMocks.retryMutate)
    }
    if (source.includes('/notifications/webhooks/${webhookId}') && source.includes("DELETE")) {
      return notificationMutationResult(notificationsPageDomMocks.deleteMutate)
    }
    return notificationMutationResult(notificationsPageDomMocks.saveMutate)
  },
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useBlocker: routerMocks.useBlocker,
  }
})

import { NotificationsPage } from './NotificationsPage'

let root: Root | null = null
let container: HTMLDivElement | null = null

function renderPage() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(<NotificationsPage />)
  })
  return container
}

function setInputValue(input: HTMLInputElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

afterEach(() => {
  act(() => {
    root?.unmount()
  })
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
})

describe('NotificationsPage DOM workflows', () => {
  it('renders accessible request-shaping controls, protects unsaved changes, and confirms webhook deletion through the dialog', () => {
    const view = renderPage()

    const savedWebhookButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Slack alert relay'),
    )
    expect(savedWebhookButton).not.toBeNull()

    act(() => {
      savedWebhookButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(view.querySelector('label[for="headers-0-key"]')?.textContent).toContain('Headers row 1 key')
    expect(view.querySelector('label[for="headers-0-value"]')?.textContent).toContain('Headers row 1 value')
    expect(view.querySelector('label[for="query-parameters-0-key"]')?.textContent).toContain(
      'Query Parameters row 1 key',
    )
    expect(view.querySelector('button[aria-label="Remove Headers row 1"]')).not.toBeNull()
    expect(
      Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Any feed')?.getAttribute('aria-pressed'),
    ).toBe('true')

    const deleteButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Delete webhook'),
    )
    expect(deleteButton).not.toBeNull()

    act(() => {
      deleteButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(view.textContent).toContain('Delete webhook?')
    expect(view.textContent).toContain('Slack alert relay')

    const confirmDeleteButton = Array.from(view.querySelectorAll('button'))
      .filter((button) => button.textContent?.includes('Delete webhook'))
      .at(-1)
    expect(confirmDeleteButton).not.toBeNull()

    act(() => {
      confirmDeleteButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(notificationsPageDomMocks.deleteMutate).toHaveBeenCalledWith('webhook-1')

    const nameInput = view.querySelector<HTMLInputElement>('#notification-webhook-name')
    expect(nameInput).not.toBeNull()

    act(() => {
      setInputValue(nameInput!, 'Changed webhook name')
    })

    const newWebhookButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('New webhook'),
    )
    expect(newWebhookButton).not.toBeNull()

    act(() => {
      newWebhookButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(view.textContent).toContain('Discard unsaved changes?')
    expect(view.textContent).toContain('Discard unsaved webhook changes?')

    const discardChangesButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Discard changes'),
    )
    expect(discardChangesButton).not.toBeNull()

    act(() => {
      discardChangesButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(view.querySelector<HTMLInputElement>('#notification-webhook-name')?.value).toBe('')
  })
})
