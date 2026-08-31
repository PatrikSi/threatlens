// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const integrationsPageDomMocks = vi.hoisted(() => ({
  queryClient: {
    invalidateQueries: vi.fn(),
    setQueryData: vi.fn(),
  },
  saveMutate: vi.fn(),
  testMutate: vi.fn(),
  deleteMutate: vi.fn(),
  replayMutate: vi.fn(),
  deleteShouldFail: false,
  deferReplaySuccess: false,
  pendingReplaySuccess: null as null | (() => void),
  queryData: {} as Record<string, unknown>,
  currentUser: {
    data: {
      id: 'admin-1',
      email: 'admin@example.com',
      role: 'admin' as const,
      access: {
        permissions: ['write:integrations'],
      },
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
  smtpSettings: {
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
  },
  credentialSource: {
    id: 'smtp-2',
    name: 'Backup Relay',
    integration_type: 'smtp',
    direction: 'destination',
    enabled: true,
    configured: true,
    schema_version: 1,
    host: 'backup.example.com',
    port: 465,
    security: 'ssl_tls',
    username: 'backup-user',
    password_configured: true,
    has_unreadable_secret: false,
    from_email: 'backup@example.com',
    from_name: 'ThreatLens Backup',
    to_emails: ['backup-recipient@example.com'],
    timeout_seconds: 10,
    event_types: ['feed_failing'],
    feed_scope: 'all',
    feed_ids: [],
    subject_template: '[ThreatLens] Feed failing',
    html_template: '<p>{{ feed.name }}</p>',
    health_status: 'healthy',
    last_test_at: null,
    last_success_at: null,
    last_error_at: null,
    last_error: null,
    last_test_duration_ms: null,
    created_at: '2026-07-04T10:00:00Z',
    updated_at: '2026-07-04T10:00:00Z',
    is_default: false,
    uses_shared_credentials: false,
    credential_source_id: null,
    credential_source_name: null,
  },
}))

const routerMocks = vi.hoisted(() => ({
  useBlocker: vi.fn(() => ({
    state: 'unblocked' as const,
    proceed: vi.fn(),
    reset: vi.fn(),
  })),
}))

function mutationResult(mutate: ReturnType<typeof vi.fn>) {
  return {
    mutate,
    reset: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  }
}

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => integrationsPageDomMocks.queryClient,
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => {
    const key = queryKey.join(':')
    const baseResult = {
      isLoading: false,
      isError: false,
      error: null,
    }
    if (key === 'integrations:smtp:hooks') {
      integrationsPageDomMocks.queryData[key] ??= [integrationsPageDomMocks.smtpSettings, integrationsPageDomMocks.credentialSource]
      return {
        ...baseResult,
        data: integrationsPageDomMocks.queryData[key],
      }
    }
    if (key === 'integrations:smtp:analytics') {
      integrationsPageDomMocks.queryData[key] ??= {
        hook_count: 2,
        enabled_hook_count: 2,
        total_deliveries: 4,
        successful_deliveries: 3,
        failed_deliveries: 1,
        success_rate_pct: 75,
        failures_last_24h: 1,
        pending_deliveries: 0,
        retry_wait_deliveries: 0,
        most_failing_hook: {
          hook_id: 'smtp-1',
          hook_name: 'SMTP',
          failed_deliveries: 1,
          last_failure_at: '2026-07-04T11:00:00Z',
        },
        events: [{ event_type: 'rss_item_new', total_deliveries: 4, failed_deliveries: 1 }],
      }
      return {
        ...baseResult,
        data: integrationsPageDomMocks.queryData[key],
      }
    }
    if (key === 'integrations:smtp:template-defaults') {
      integrationsPageDomMocks.queryData[key] ??= [
        {
          send_for: 'rss_item_new',
          event_types: ['rss_item_new'],
          subject_template: '[ThreatLens] New item: {{ item.title }}',
          html_template: '<h2>New item</h2><p>{{ item.title }}</p>',
        },
        {
          send_for: 'alert_match',
          event_types: ['alert_match'],
          subject_template: '[ThreatLens] Alert match: {{ alert.primary_name }}',
          html_template: '<h2>Alert match</h2><p>{{ alert.primary_name }}</p>',
        },
        {
          send_for: 'all',
          event_types: ['rss_item_new', 'alert_match', 'feed_failing', 'webhook_failed', 'daily_digest'],
          subject_template: '[ThreatLens] {{ event.type }}: {{ item.title }}',
          html_template: '<h2>{{ event.type }}</h2>',
        },
      ]
      return {
        ...baseResult,
        data: integrationsPageDomMocks.queryData[key],
      }
    }
    if (key === 'feeds') {
      integrationsPageDomMocks.queryData[key] ??= [
        {
          id: 'feed-1',
          name: 'Example Feed',
          url: 'https://example.com/rss.xml',
          description: null,
          site_url: 'https://example.com',
          language: null,
          enabled: true,
          fetch_mode: 'interval',
          fetch_interval_seconds: 1800,
          schedule_cron: null,
          etag: null,
          last_modified: null,
          last_fetch_at: null,
          last_success_at: null,
          next_fetch_at: null,
          error_count: 0,
          last_error: null,
          has_unreadable_url: false,
          created_at: '2026-07-04T09:00:00Z',
        },
      ]
      return {
        ...baseResult,
        data: integrationsPageDomMocks.queryData[key],
      }
    }
    if (key === 'notifications:template-variables') {
      integrationsPageDomMocks.queryData[key] ??= [
        {
          key: 'item.title',
          description: 'Item title',
          example: 'Example intrusion activity observed',
        },
      ]
      return {
        ...baseResult,
        data: integrationsPageDomMocks.queryData[key],
      }
    }
    if (key === 'integrations:smtp:hooks:smtp-1:deliveries:1') {
      integrationsPageDomMocks.queryData[key] ??= {
        total: 1,
        page: 1,
        page_size: 10,
        deliveries: [
          {
            id: 'delivery-1',
            hook_id: 'smtp-1',
            event_type: 'rss_item_new',
            delivery_kind: 'live',
            state: 'dead_letter',
            attempt_count: 2,
            max_attempts: 2,
            feed_id: 'feed-1',
            item_id: null,
            source_delivery_id: null,
            last_duration_ms: 25,
            last_error_code: 'smtp_error',
            last_error_message: 'Relay rejected the message',
            last_error_retryable: false,
            created_at: '2026-07-04T10:00:00Z',
            updated_at: '2026-07-04T10:01:00Z',
            completed_at: null,
            dead_lettered_at: '2026-07-04T10:01:00Z',
            attempts: [],
          },
        ],
      }
      return {
        ...baseResult,
        data: integrationsPageDomMocks.queryData[key],
      }
    }
    if (key === 'integrations:smtp:hooks:smtp-1:test-runs:1') {
      integrationsPageDomMocks.queryData[key] ??= {
        total: 11,
        page: 1,
        page_size: 10,
        runs: [
          {
            id: 'test-run-failed',
            hook_id: 'smtp-1',
            status: 'failed',
            action: 'send',
            recipient_email: 'analyst@example.com',
            used_unsaved_settings: true,
            duration_ms: 842,
            error_code: 'authentication_error',
            error_message: 'SMTP authentication failed.',
            server_message: '535 5.7.8 Credentials rejected',
            started_at: '2026-07-04T12:00:00Z',
            finished_at: '2026-07-04T12:00:01Z',
          },
        ],
      }
      return {
        ...baseResult,
        data: integrationsPageDomMocks.queryData[key],
      }
    }
    if (key === 'integrations:smtp:hooks:smtp-1:test-runs:2') {
      integrationsPageDomMocks.queryData[key] ??= {
        total: 11,
        page: 2,
        page_size: 10,
        runs: [
          {
            id: 'test-run-succeeded',
            hook_id: 'smtp-1',
            status: 'succeeded',
            action: 'connection',
            recipient_email: null,
            used_unsaved_settings: false,
            duration_ms: 34,
            error_code: null,
            error_message: null,
            server_message: 'Connection and authentication succeeded.',
            started_at: '2026-07-03T12:00:00Z',
            finished_at: '2026-07-03T12:00:00Z',
          },
        ],
      }
      return {
        ...baseResult,
        data: integrationsPageDomMocks.queryData[key],
      }
    }
    return { ...baseResult, data: undefined }
  },
  useMutation: (options: {
    mutationKey?: unknown
    onSuccess?: (result: unknown, variables: unknown) => void
    onError?: (error: unknown, variables: unknown) => void
  }) => {
    const mutationKey = Array.isArray(options?.mutationKey) ? options.mutationKey.join(':') : String(options?.mutationKey ?? '')
    if (mutationKey === 'integrations:smtp:hooks:test') {
      return mutationResult(integrationsPageDomMocks.testMutate)
    }
    if (mutationKey === 'integrations:smtp:hooks:delete') {
      return mutationResult(
        vi.fn((hookId: string) => {
          integrationsPageDomMocks.deleteMutate(hookId)
          if (integrationsPageDomMocks.deleteShouldFail) {
            options.onError?.(new Error('SMTP hook deletion failed.'), hookId)
            return
          }
          options.onSuccess?.(undefined, hookId)
        }),
      )
    }
    if (mutationKey === 'integrations:smtp:deliveries:replay') {
      return mutationResult(
        vi.fn((variables: { hookId: string; deliveryId: string }) => {
          integrationsPageDomMocks.replayMutate(variables)
          const complete = () => options.onSuccess?.({ queued: true }, variables)
          if (integrationsPageDomMocks.deferReplaySuccess) {
            integrationsPageDomMocks.pendingReplaySuccess = complete
            return
          }
          complete()
        }),
      )
    }
    return mutationResult(integrationsPageDomMocks.saveMutate)
  },
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useBlocker: routerMocks.useBlocker,
  }
})

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => integrationsPageDomMocks.currentUser,
}))

import { IntegrationsSettingsPage } from './IntegrationsSettingsPage'

let root: Root | null = null
let container: HTMLDivElement | null = null

function renderPage() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(<IntegrationsSettingsPage />)
  })
  return container
}

function setInputValue(input: HTMLInputElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

function setTextAreaValue(textarea: HTMLTextAreaElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')
  descriptor?.set?.call(textarea, value)
  textarea.dispatchEvent(new Event('input', { bubbles: true }))
}

function getButton(text: string) {
  return Array.from(document.querySelectorAll('button')).find((button) => button.textContent?.includes(text)) ?? null
}

afterEach(() => {
  act(() => {
    root?.unmount()
  })
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  integrationsPageDomMocks.queryClient.invalidateQueries.mockReset()
  integrationsPageDomMocks.queryClient.setQueryData.mockReset()
  integrationsPageDomMocks.saveMutate.mockReset()
  integrationsPageDomMocks.testMutate.mockReset()
  integrationsPageDomMocks.deleteMutate.mockReset()
  integrationsPageDomMocks.replayMutate.mockReset()
  integrationsPageDomMocks.deleteShouldFail = false
  integrationsPageDomMocks.deferReplaySuccess = false
  integrationsPageDomMocks.pendingReplaySuccess = null
  integrationsPageDomMocks.currentUser.data.features.ai_daily_brief_enabled = true
  integrationsPageDomMocks.currentUser.data.access.permissions = ['write:integrations']
  routerMocks.useBlocker.mockReset()
  routerMocks.useBlocker.mockImplementation(() => ({
    state: 'unblocked' as const,
    proceed: vi.fn(),
    reset: vi.fn(),
  }))
})

describe('IntegrationsSettingsPage DOM workflows', () => {
  it('keeps email delivery inspectable but disables writes without write:integrations', () => {
    integrationsPageDomMocks.currentUser.data.access.permissions = ['read:integrations']
    const view = renderPage()

    expect(view.textContent).toContain('Read-only access')
    expect(view.textContent).toContain('permission to manage email delivery')
    expect(view.firstElementChild?.className).toContain('space-y-3')
    expect(view.querySelector('#smtp-hook-name')?.closest('section')?.className).toContain('p-3')
    expect(view.querySelector<HTMLInputElement>('#smtp-hook-name')?.matches(':disabled')).toBe(true)
    expect(view.querySelector<HTMLSelectElement>('#smtp-send-for')?.matches(':disabled')).toBe(true)
    expect(view.querySelector<HTMLInputElement>('#smtp-host')?.matches(':disabled')).toBe(true)
    expect(view.querySelector<HTMLTextAreaElement>('#smtp-to-emails')?.matches(':disabled')).toBe(true)
    expect(getButton('New destination')?.disabled).toBe(true)
    expect(getButton('Save destination')?.matches(':disabled')).toBe(true)
    expect(getButton('Test connection')?.matches(':disabled')).toBe(true)
    expect(getButton('Replay dead-letter delivery')?.disabled).toBe(true)

    const backupDestination = getButton('Backup Relay')
    expect(backupDestination?.disabled).toBe(false)
    act(() => {
      backupDestination?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(view.querySelector<HTMLInputElement>('#smtp-hook-name')?.value).toBe('Backup Relay')
    expect(getButton('Delete destination')?.matches(':disabled')).toBe(true)
    expect(integrationsPageDomMocks.saveMutate).not.toHaveBeenCalled()
    expect(integrationsPageDomMocks.testMutate).not.toHaveBeenCalled()
    expect(integrationsPageDomMocks.replayMutate).not.toHaveBeenCalled()
  })

  it('omits AI daily brief from email events and all-events selection when AI is unavailable', () => {
    integrationsPageDomMocks.currentUser.data.features.ai_daily_brief_enabled = false
    const view = renderPage()
    const sendFor = view.querySelector<HTMLSelectElement>('#smtp-send-for')

    expect(sendFor).not.toBeNull()
    expect(Array.from(sendFor!.options).some((option) => option.value === 'daily_digest')).toBe(false)

    act(() => {
      setSelectValue(sendFor!, 'all')
    })
    act(() => {
      getButton('Save destination')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(integrationsPageDomMocks.saveMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        hook: expect.objectContaining({
          settings: expect.objectContaining({
            event_types: ['rss_item_new', 'alert_match', 'feed_failing', 'webhook_failed'],
          }),
        }),
      }),
    )
  })

  it('keeps the email destination delete confirmation open and renders failures', () => {
    integrationsPageDomMocks.deleteShouldFail = true
    renderPage()

    act(() => {
      getButton('Backup Relay')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    act(() => {
      getButton('Delete destination')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    act(() => {
      Array.from(document.querySelectorAll('button'))
        .filter((button) => button.textContent?.trim() === 'Delete destination')
        .at(-1)
        ?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(document.body.textContent).toContain('Delete email destination?')
    expect(document.body.textContent).toContain('Backup Relay')
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('SMTP hook deletion failed.')
  })

  it('renders email delivery settings without exposing the saved password', () => {
    const view = renderPage()

    expect(view.textContent).toContain('Email delivery')
    expect(view.textContent).toContain('Email destinations')
    expect(view.textContent).toContain('Backup Relay')
    expect(view.textContent).toContain('75.0%')
    const passwordInput = view.querySelector<HTMLInputElement>('#smtp-password')
    expect(passwordInput).not.toBeNull()
    expect(passwordInput?.value).toBe('')
    expect(passwordInput?.placeholder).toBe('Saved password configured')
    expect(view.querySelector<HTMLTextAreaElement>('#smtp-to-emails')?.value).toBe('analyst@example.com\nsoc@example.com')
  })

  it('tests current unsaved form values and saves typed password replacements', () => {
    const view = renderPage()

    act(() => {
      setInputValue(view.querySelector<HTMLInputElement>('#smtp-host')!, 'draft.example.com')
    })
    act(() => {
      setTextAreaValue(view.querySelector<HTMLTextAreaElement>('#smtp-to-emails')!, 'draft@example.com, soc@example.com')
    })
    act(() => {
      getCheckboxByLabel(view, 'Send a test email')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    act(() => {
      setInputValue(view.querySelector<HTMLInputElement>('#smtp-test-recipient')!, 'analyst@example.com')
    })

    act(() => {
      getButton('Send test email')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(integrationsPageDomMocks.testMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        send_email: true,
        recipient_email: 'analyst@example.com',
        hook_id: 'smtp-1',
        hook: expect.objectContaining({
          name: 'SMTP',
          settings: expect.objectContaining({
            host: 'draft.example.com',
            to_emails: ['draft@example.com', 'soc@example.com'],
            event_types: ['rss_item_new'],
            subject_template: '[ThreatLens] {{ event.type }}: {{ item.title }}',
          }),
        }),
      }),
    )

    act(() => {
      setInputValue(view.querySelector<HTMLInputElement>('#smtp-password')!, 'new-secret')
    })
    act(() => {
      getButton('Save destination')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(integrationsPageDomMocks.saveMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        hookId: 'smtp-1',
        hook: expect.objectContaining({
          settings: expect.objectContaining({
            host: 'draft.example.com',
            password: 'new-secret',
            to_emails: ['draft@example.com', 'soc@example.com'],
          }),
        }),
      }),
    )
  })

  it('loads event defaults and can reuse an existing SMTP credential source', () => {
    const view = renderPage()
    const sendFor = view.querySelector<HTMLSelectElement>('#smtp-send-for')!

    act(() => setSelectValue(sendFor, 'alert_match'))
    expect(view.querySelector<HTMLInputElement>('#smtp-subject-template')?.value).toBe(
      '[ThreatLens] Alert match: {{ alert.primary_name }}',
    )
    expect(view.querySelector<HTMLTextAreaElement>('#smtp-html-template')?.value).toContain('{{ alert.primary_name }}')

    act(() => setSelectValue(view.querySelector<HTMLSelectElement>('#smtp-credential-source')!, 'smtp-2'))
    expect(view.querySelector('#smtp-password')).toBeNull()
    expect(view.textContent).toContain('backup.example.com')
    expect(view.textContent).toContain('Backup Relay')

    act(() => {
      getButton('Save destination')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(integrationsPageDomMocks.saveMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        hook: expect.objectContaining({
          credential_source_id: 'smtp-2',
          settings: expect.objectContaining({
            host: null,
            username: null,
            event_types: ['alert_match'],
          }),
        }),
      }),
    )
    const payload = integrationsPageDomMocks.saveMutate.mock.calls[0][0].hook.settings
    expect(payload).not.toHaveProperty('password')
  })

  it('shows dead-letter delivery history and requests replay confirmation', () => {
    const view = renderPage()
    expect(view.textContent).toContain('Dead letter')
    expect(view.textContent).toContain('Relay rejected the message')

    act(() => {
      getButton('Replay dead-letter delivery')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(document.body.textContent).toContain('Replay dead-letter delivery?')
    act(() => {
      getButton('Replay delivery')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(integrationsPageDomMocks.replayMutate).toHaveBeenCalledWith({
      hookId: 'smtp-1',
      deliveryId: 'delivery-1',
    })
  })

  it('shows paginated SMTP test diagnostics separately from deliveries', () => {
    const view = renderPage()

    act(() => {
      getButton('Tests')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(view.textContent).toContain('authentication_error')
    expect(view.textContent).toContain('SMTP authentication failed.')
    expect(view.textContent).toContain('535 5.7.8 Credentials rejected')
    expect(view.textContent).toContain('Recipient: analyst@example.com')
    expect(view.textContent).toContain('Draft settings')
    expect(view.textContent).toContain('Run ID: test-run-failed')
    expect(view.textContent).toContain('Page 1 of 2')

    act(() => {
      getButton('Next')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(view.textContent).toContain('Connection and authentication only')
    expect(view.textContent).toContain('Saved settings')
    expect(view.textContent).toContain('Connection and authentication succeeded.')
    expect(view.textContent).toContain('Page 2 of 2')
  })

  it('invalidates the replayed hook delivery cache even after selection changes', () => {
    integrationsPageDomMocks.deferReplaySuccess = true
    renderPage()

    act(() => {
      getButton('Replay dead-letter delivery')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    act(() => {
      getButton('Replay delivery')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    act(() => {
      getButton('Backup Relay')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    act(() => {
      integrationsPageDomMocks.pendingReplaySuccess?.()
    })

    expect(integrationsPageDomMocks.queryClient.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ['integrations', 'smtp', 'hooks', 'smtp-1', 'deliveries'],
    })
  })
})

function setSelectValue(select: HTMLSelectElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')
  descriptor?.set?.call(select, value)
  select.dispatchEvent(new Event('change', { bubbles: true }))
}

function getCheckboxByLabel(rootNode: HTMLElement, labelText: string) {
  return Array.from(rootNode.querySelectorAll('label'))
    .find((label) => label.textContent?.includes(labelText))
    ?.querySelector<HTMLInputElement>('input[type="checkbox"]') ?? null
}
