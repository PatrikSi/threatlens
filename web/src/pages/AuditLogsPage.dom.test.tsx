// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const auditLogsDomMocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  exportMutate: vi.fn(),
  exportShouldFail: false,
  queryOptions: [] as Array<{
    queryKey: unknown[]
    enabled?: boolean
    queryFn?: () => Promise<unknown>
  }>,
  queryLogs: [] as Array<{
    id: string
    action: string
    resource_type: string
    resource_id: string | null
    resource_label_snapshot?: string | null
    actor_user_id: string | null
    actor_principal_type?: string | null
    actor_principal_id?: string | null
    actor_label_snapshot?: string | null
    credential_kind?: string | null
    credential_id?: string | null
    request_id?: string | null
    source_ip?: string | null
    authorization_elevation_ids?: string[]
    authorization_approval_id?: string | null
    execution_receipt_id?: string | null
    success: boolean
    metadata_json?: Record<string, unknown>
    data_access_redacted?: boolean
    created_at: string
  }>,
}))

vi.mock('../api/client', () => ({ apiFetch: auditLogsDomMocks.apiFetch }))

vi.mock('@tanstack/react-query', () => ({
  useQuery: (options: {
    queryKey: unknown[]
    enabled?: boolean
    queryFn?: () => Promise<unknown>
  }) => {
    auditLogsDomMocks.queryOptions.push(options)
    return {
      data:
        options.enabled === false
          ? undefined
          : { logs: auditLogsDomMocks.queryLogs, total: auditLogsDomMocks.queryLogs.length, page: 1, page_size: 50 },
      isLoading: false,
      isError: false,
      error: null,
    }
  },
  useMutation: (options: { onSuccess?: (payload: unknown) => void; onError?: (error: Error) => void }) => ({
    mutate: vi.fn(() => {
      auditLogsDomMocks.exportMutate()
      if (auditLogsDomMocks.exportShouldFail) {
        options.onError?.(new Error('Failed to export audit logs'))
        return
      }

      options.onSuccess?.({
        logs: [
          {
            id: 'audit-1',
            action: 'item.updated',
            resource_type: 'item',
            resource_id: 'item-1',
            actor_user_id: 'user-1',
            success: true,
            metadata_json: {},
            created_at: '2026-04-21T10:00:00Z',
          },
          {
            id: 'audit-2',
            action: 'feed.created',
            resource_type: 'feed',
            resource_id: 'feed-1',
            actor_user_id: 'user-1',
            success: true,
            metadata_json: {},
            created_at: '2026-04-21T10:05:00Z',
          },
        ],
        total: 2,
        truncated: false,
      })
    }),
    isPending: false,
    isError: false,
    error: null,
  }),
}))

import { AuditLogsPage } from './AuditLogsPage'

let root: Root | null = null
let container: HTMLDivElement | null = null

function renderPage() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(<AuditLogsPage />)
  })
  return container
}

afterEach(() => {
  act(() => {
    root?.unmount()
  })
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  auditLogsDomMocks.exportMutate.mockReset()
  auditLogsDomMocks.apiFetch.mockReset()
  auditLogsDomMocks.exportShouldFail = false
  auditLogsDomMocks.queryOptions = []
  auditLogsDomMocks.queryLogs = []
  vi.restoreAllMocks()
})

describe('AuditLogsPage DOM workflows', () => {
  it('keeps audit filters visibly labeled and clears them together', () => {
    const view = renderPage()

    expect(view.querySelector('h1')?.textContent).toBe('Audit log')
    expect(view.firstElementChild?.className).toContain('space-y-3')
    expect(view.querySelector('#audit-log-action-filter')?.closest('section')?.className).toContain('p-3')
    expect(view.querySelector('label[for="audit-log-action-filter"]')?.textContent).toContain('Event')
    expect(view.querySelector('label[for="audit-log-actor-filter"]')?.textContent).toContain('Actor/principal ID')
    const eventInput = view.querySelector<HTMLInputElement>('#audit-log-action-filter')
    const clearFilters = Array.from(view.querySelectorAll<HTMLButtonElement>('button'))
      .find((button) => button.textContent?.trim() === 'Clear filters')
    expect(eventInput).not.toBeNull()
    expect(view.querySelector<HTMLInputElement>('#audit-log-actor-filter')).not.toBeNull()
    expect(clearFilters?.disabled).toBe(true)

    act(() => setInputValue(eventInput!, 'item.updated'))
    expect(clearFilters?.disabled).toBe(false)
    act(() => clearFilters?.click())
    expect(eventInput?.value).toBe('')
  })

  it('renders labeled mobile records alongside the desktop audit table', () => {
    auditLogsDomMocks.queryLogs = [
      {
        id: 'audit-mobile-1',
        action: 'integrations.smtp.delivery_succeeded',
        resource_type: 'integration_delivery',
        resource_id: 'delivery-1',
        resource_label_snapshot: 'Primary SMTP delivery',
        actor_user_id: '00000000-0000-4000-8000-000000000001',
        actor_principal_type: 'user',
        actor_principal_id: '00000000-0000-4000-8000-000000000001',
        actor_label_snapshot: 'analyst@example.com',
        credential_kind: 'session_cookie',
        credential_id: '00000000-0000-4000-8000-000000000002',
        request_id: 'request-smtp-1',
        source_ip: '192.0.2.20',
        authorization_elevation_ids: [],
        authorization_approval_id: null,
        execution_receipt_id: null,
        success: true,
        metadata_json: { delivery_kind: 'live' },
        data_access_redacted: false,
        created_at: '2026-04-21T10:00:00Z',
      },
    ]

    const view = renderPage()
    const mobileRecords = view.querySelector('[aria-label="Audit events"]')
    const mobileRecord = mobileRecords?.querySelector('details')
    const desktopTable = view.querySelector('table')?.parentElement

    expect(mobileRecord?.open).toBe(false)
    expect(mobileRecords?.textContent).toContain('integrations.smtp.delivery_succeeded')
    expect(mobileRecords?.textContent).toContain('Primary SMTP delivery')
    expect(mobileRecords?.textContent).toContain('analyst@example.com')
    expect(mobileRecord?.querySelector('summary')?.textContent).not.toContain('00000000-0000-4000-8000-000000000001')
    expect(desktopTable?.className).toContain('hidden')
    expect(desktopTable?.className).toContain('sm:block')
    expect(Array.from(view.querySelectorAll('th')).every((heading) => heading.getAttribute('scope') === 'col')).toBe(true)

    act(() => {
      mobileRecord?.querySelector<HTMLElement>('summary')?.click()
    })

    expect(mobileRecord?.open).toBe(true)
    expect(mobileRecord?.textContent).toContain('Principal ID')
    expect(mobileRecord?.textContent).toContain('00000000-0000-4000-8000-000000000001')
    expect(mobileRecord?.textContent).toContain('request-smtp-1')
    expect(mobileRecord?.textContent).toContain('192.0.2.20')
    expect(mobileRecord?.textContent).toContain('delivery_kind')
  })

  it('renders anonymous authentication attempts without mislabeling them as system activity', () => {
    auditLogsDomMocks.queryLogs = [
      {
        id: 'audit-login-failure',
        action: 'auth.login',
        resource_type: 'user',
        resource_id: null,
        resource_label_snapshot: 'unknown@example.com',
        actor_user_id: null,
        actor_principal_type: 'anonymous',
        actor_principal_id: null,
        actor_label_snapshot: null,
        credential_kind: null,
        credential_id: null,
        request_id: 'failed-login-request',
        source_ip: '198.51.100.12',
        authorization_elevation_ids: [],
        authorization_approval_id: null,
        execution_receipt_id: null,
        success: false,
        metadata_json: { reason: 'invalid_credentials' },
        data_access_redacted: false,
        created_at: '2026-04-21T10:00:00Z',
      },
    ]

    const view = renderPage()

    expect(view.textContent).toContain('Sign-in attempt')
    expect(view.textContent).toContain('Unauthenticated')
    expect(view.textContent).toContain('unknown@example.com')
    expect(view.textContent?.toLowerCase()).not.toContain('system')
  })

  it('recognizes legacy user actors when only actor_user_id was retained', () => {
    auditLogsDomMocks.queryLogs = [
      {
        id: 'audit-legacy-user',
        action: 'users.update',
        resource_type: 'user',
        resource_id: '00000000-0000-4000-8000-000000000020',
        resource_label_snapshot: 'target@example.com',
        actor_user_id: '00000000-0000-4000-8000-000000000010',
        actor_principal_type: null,
        actor_principal_id: null,
        actor_label_snapshot: 'legacy-admin@example.com',
        success: true,
        metadata_json: {},
        created_at: '2026-04-21T10:00:00Z',
      },
    ]

    const view = renderPage()
    const mobileRecord = view.querySelector('[aria-label="Audit events"] details')
    act(() => {
      mobileRecord?.querySelector<HTMLElement>('summary')?.click()
    })
    const principalTypeLabel = Array.from(mobileRecord?.querySelectorAll('p') ?? [])
      .find((entry) => entry.textContent === 'Principal type')

    expect(mobileRecord?.textContent).toContain('legacy-admin@example.com')
    expect(principalTypeLabel?.parentElement?.textContent).toContain('User')
    expect(principalTypeLabel?.parentElement?.textContent).not.toContain('Unauthenticated')
  })

  it('does not infer an anonymous actor when legacy identity was not recorded', () => {
    auditLogsDomMocks.queryLogs = [
      {
        id: 'audit-legacy-system',
        action: 'maintenance.completed',
        resource_type: 'system_operation',
        resource_id: 'maintenance-1',
        actor_user_id: null,
        actor_principal_type: null,
        actor_principal_id: null,
        actor_label_snapshot: null,
        success: true,
        metadata_json: {},
        created_at: '2026-04-21T10:00:00Z',
      },
    ]

    const view = renderPage()
    const mobileRecord = view.querySelector('[aria-label="Audit events"] details')
    act(() => {
      mobileRecord?.querySelector<HTMLElement>('summary')?.click()
    })

    expect(mobileRecord?.textContent).toContain('Actor not recorded')
    expect(mobileRecord?.textContent).toContain('Principal typeNot recorded')
    expect(mobileRecord?.textContent).not.toContain('Unauthenticated')
  })

  it('announces audit export success and failure through live regions', () => {
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:audit-export'),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    const view = renderPage()
    const exportButton = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.includes('Export JSON'))
    expect(exportButton).not.toBeNull()

    act(() => {
      exportButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const notice = view.querySelector('[role="status"][aria-live="polite"][aria-atomic="true"]')
    expect(notice).not.toBeNull()
    expect(notice?.textContent).toContain('Exported 2 logs.')

    auditLogsDomMocks.exportShouldFail = true

    act(() => {
      exportButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const errorNotice = view.querySelector('[role="alert"][aria-live="assertive"][aria-atomic="true"]')
    expect(errorNotice).not.toBeNull()
    expect(errorNotice?.textContent).toContain('Failed to export audit logs')
  })

  it('validates actor UUID filters before querying or exporting', () => {
    const view = renderPage()
    const actorInput = view.querySelector<HTMLInputElement>('#audit-log-actor-filter')
    const exportButton = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.includes('Export JSON'))

    expect(actorInput).not.toBeNull()
    expect(exportButton).not.toBeNull()

    act(() => {
      setInputValue(actorInput!, 'not-a-uuid')
    })

    expect(view.textContent).toContain('Actor/principal ID must be a valid UUID.')
    expect(auditLogsDomMocks.queryOptions.at(-1)?.enabled).toBe(false)
    expect(exportButton?.hasAttribute('disabled')).toBe(true)
  })

  it('uses the stable principal identifier for exact actor filtering', async () => {
    const view = renderPage()
    const actorInput = view.querySelector<HTMLInputElement>(
      '#audit-log-actor-filter',
    )
    const principalId = '00000000-0000-4000-8000-000000000001'
    auditLogsDomMocks.apiFetch.mockResolvedValue({
      logs: [],
      total: 0,
      page: 1,
      page_size: 50,
    })

    act(() => setInputValue(actorInput!, principalId))
    await auditLogsDomMocks.queryOptions.at(-1)?.queryFn?.()

    expect(auditLogsDomMocks.apiFetch).toHaveBeenCalledWith(
      `/audit-logs?page=1&page_size=50&actor_principal_id=${principalId}`,
    )
  })
})

function setInputValue(input: HTMLInputElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}
