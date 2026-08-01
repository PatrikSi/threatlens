// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const notificationsPageDomMocks = vi.hoisted(() => ({
  queryClient: {
    invalidateQueries: vi.fn(),
  },
  currentUser: {
    data: {
      id: 'admin-1',
      email: 'admin@example.com',
      role: 'admin',
      is_active: true,
      is_approved: true,
      approved_at: '2026-04-21T10:00:00Z',
      created_at: '2026-04-20T10:00:00Z',
      features: {
        ai_enabled: true,
        ai_configured: true,
        ai_summary_enabled: true,
        ai_relevance_enabled: true,
        ai_daily_brief_enabled: true,
      },
    },
    isLoading: false,
    isError: false,
    error: null,
  },
  saveMutate: vi.fn(),
  deleteMutate: vi.fn(),
  testMutate: vi.fn(),
  retryMutate: vi.fn(),
  retryReset: vi.fn(),
}))

const routerMocks = vi.hoisted(() => ({
  useBlocker: vi.fn(() => ({
    state: 'unblocked' as const,
    proceed: vi.fn(),
    reset: vi.fn(),
  })),
}))

function notificationMutationResult(mutate: ReturnType<typeof vi.fn>, reset = vi.fn()) {
  return {
    mutate,
    isPending: false,
    isError: false,
    error: null,
    reset,
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
  useMutation: (options: { mutationKey?: unknown }) => {
    const mutationKey = Array.isArray(options?.mutationKey) ? options.mutationKey.join(':') : String(options?.mutationKey ?? '')
    if (mutationKey === 'notifications:webhooks:test') {
      return notificationMutationResult(notificationsPageDomMocks.testMutate)
    }
    if (mutationKey === 'notifications:webhooks:retry-delivery') {
      return notificationMutationResult(notificationsPageDomMocks.retryMutate, notificationsPageDomMocks.retryReset)
    }
    if (mutationKey === 'notifications:webhooks:delete') {
      return notificationMutationResult(notificationsPageDomMocks.deleteMutate)
    }
    return notificationMutationResult(notificationsPageDomMocks.saveMutate)
  },
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => notificationsPageDomMocks.currentUser,
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

function pageText() {
  return document.body.textContent ?? ''
}

function setInputValue(input: HTMLInputElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

function setSelectValue(select: HTMLSelectElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')
  descriptor?.set?.call(select, value)
  select.dispatchEvent(new Event('change', { bubbles: true }))
}

afterEach(() => {
  act(() => {
    root?.unmount()
  })
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  notificationsPageDomMocks.deleteMutate.mockReset()
  notificationsPageDomMocks.saveMutate.mockReset()
  notificationsPageDomMocks.testMutate.mockReset()
  notificationsPageDomMocks.retryMutate.mockReset()
  notificationsPageDomMocks.retryReset.mockReset()
  notificationsPageDomMocks.currentUser.data.role = 'admin'
  notificationsPageDomMocks.currentUser.data.features.ai_daily_brief_enabled = true
})

describe('NotificationsPage DOM workflows', () => {
  it('omits AI Daily Brief from webhook event choices when AI is unavailable', () => {
    notificationsPageDomMocks.currentUser.data.features.ai_daily_brief_enabled = false
    const view = renderPage()
    const eventType = view.querySelector<HTMLSelectElement>('#notification-webhook-event-type')

    expect(eventType).not.toBeNull()
    expect(Array.from(eventType!.options).some((option) => option.value === 'daily_digest')).toBe(false)
  })

  it('keeps mobile template variables collapsed until requested', () => {
    const view = renderPage()
    const variables = view.querySelector<HTMLElement>('#webhook-template-variables')
    const toggle = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Show 1')

    expect(variables?.className).toContain('hidden')
    expect(toggle?.getAttribute('aria-expanded')).toBe('false')

    act(() => {
      toggle?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(variables?.className).toContain('block')
    expect(toggle?.getAttribute('aria-expanded')).toBe('true')
  })

  it('renders viewer access as read-only and hides mutation controls', () => {
    notificationsPageDomMocks.currentUser.data.role = 'viewer'
    const view = renderPage()

    expect(pageText()).toContain('Viewer access is read-only')
    expect(pageText()).toContain('Webhook writes unavailable')
    expect(Array.from(view.querySelectorAll('button')).some((button) => button.textContent?.includes('New webhook'))).toBe(false)
    expect(Array.from(view.querySelectorAll('button')).some((button) => button.textContent?.includes('Test webhook'))).toBe(false)
    expect(Array.from(view.querySelectorAll('button')).some((button) => button.textContent?.includes('Delete webhook'))).toBe(false)
    expect(view.querySelector<HTMLInputElement>('#notification-webhook-name')).toBeNull()
    expect(view.querySelector<HTMLButtonElement>('button[aria-label="Remove Headers row 1"]')).toBeNull()
  })

  it('allows analysts to manage webhook settings', () => {
    notificationsPageDomMocks.currentUser.data.role = 'analyst'
    const view = renderPage()

    expect(pageText()).not.toContain('Webhook writes unavailable')
    expect(pageText()).not.toContain('read-only mode')

    const nameInput = view.querySelector<HTMLInputElement>('#notification-webhook-name')
    expect(nameInput).not.toBeNull()

    const testButton = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.includes('Test webhook'))
    expect(testButton).not.toBeUndefined()

    const saveButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Create webhook'),
    )
    expect(saveButton).not.toBeUndefined()
  })

  it('marks the selected webhook and keeps the request-shaping controls accessible', () => {
    const view = renderPage()

    const savedWebhookButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Slack alert relay'),
    )
    expect(savedWebhookButton).not.toBeNull()
    expect(savedWebhookButton?.getAttribute('aria-pressed')).toBe('false')

    act(() => {
      savedWebhookButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(savedWebhookButton?.getAttribute('aria-pressed')).toBe('true')
    expect(notificationsPageDomMocks.retryReset).toHaveBeenCalledTimes(1)
    expect(view.querySelector('label[for="headers-0-key"]')?.textContent).toContain('Headers row 1 key')
    expect(view.querySelector('label[for="headers-0-value"]')?.textContent).toContain('Headers row 1 value')
    expect(view.querySelector('label[for="query-parameters-0-key"]')?.textContent).toContain(
      'Query Parameters row 1 key',
    )
    expect(view.querySelector('button[aria-label="Remove Headers row 1"]')).not.toBeNull()
    expect(
      Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Any feed')?.getAttribute('aria-pressed'),
    ).toBe('true')

    expect(view.querySelector('label[for="notification-sample-feed"]')?.textContent).toContain('Sample feed')

    const sampleFeedSelect = view.querySelector<HTMLSelectElement>('#notification-sample-feed')
    expect(sampleFeedSelect).not.toBeNull()

    act(() => {
      setSelectValue(sampleFeedSelect!, 'feed-1')
    })

    expect(sampleFeedSelect?.value).toBe('feed-1')

    const testButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Test webhook'),
    )
    expect(testButton).not.toBeNull()

    act(() => {
      testButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(notificationsPageDomMocks.testMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        sample_feed_id: 'feed-1',
      }),
    )
  })

  it('protects unsaved webhook changes before opening the delete confirmation', () => {
    const view = renderPage()

    const savedWebhookButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Slack alert relay'),
    )
    expect(savedWebhookButton).not.toBeNull()

    act(() => {
      savedWebhookButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const nameInput = view.querySelector<HTMLInputElement>('#notification-webhook-name')
    expect(nameInput).not.toBeNull()

    act(() => {
      setInputValue(nameInput!, 'Changed webhook name')
    })

    const deleteButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Delete webhook'),
    )
    expect(deleteButton).not.toBeNull()

    act(() => {
      deleteButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Discard unsaved changes?')
    expect(pageText()).toContain('Discard unsaved webhook changes?')
    expect(pageText()).not.toContain('Delete webhook?')

    const discardChangesButton = Array.from(document.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Discard changes'),
    )
    expect(discardChangesButton).not.toBeNull()

    act(() => {
      discardChangesButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Delete webhook?')
    expect(pageText()).toContain('Slack alert relay')

    const confirmDeleteButton = Array.from(document.querySelectorAll('button'))
      .filter((button) => button.textContent?.includes('Delete webhook'))
      .at(-1)
    expect(confirmDeleteButton).not.toBeNull()

    act(() => {
      confirmDeleteButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(notificationsPageDomMocks.deleteMutate).toHaveBeenCalledWith('webhook-1')
  })
})
