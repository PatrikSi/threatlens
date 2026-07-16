// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const auditLogsDomMocks = vi.hoisted(() => ({
  exportMutate: vi.fn(),
  exportShouldFail: false,
  queryOptions: [] as Array<{ queryKey: unknown[]; enabled?: boolean }>,
  queryLogs: [] as Array<{
    id: string
    action: string
    resource_type: string
    resource_id: string | null
    actor_user_id: string | null
    success: boolean
    metadata: Record<string, unknown>
    created_at: string
  }>,
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: (options: { queryKey: unknown[]; enabled?: boolean }) => {
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
            metadata: {},
            created_at: '2026-04-21T10:00:00Z',
          },
          {
            id: 'audit-2',
            action: 'feed.created',
            resource_type: 'feed',
            resource_id: 'feed-1',
            actor_user_id: 'user-1',
            success: true,
            metadata: {},
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
  auditLogsDomMocks.exportShouldFail = false
  auditLogsDomMocks.queryOptions = []
  auditLogsDomMocks.queryLogs = []
  vi.restoreAllMocks()
})

describe('AuditLogsPage DOM workflows', () => {
  it('keeps the audit filters programmatically labeled', () => {
    const view = renderPage()

    expect(view.querySelector('label[for="audit-log-action-filter"]')?.textContent).toContain('Filter audit logs by action')
    expect(view.querySelector('label[for="audit-log-actor-filter"]')?.textContent).toContain(
      'Filter audit logs by actor user ID',
    )
    expect(view.querySelector<HTMLInputElement>('#audit-log-action-filter')).not.toBeNull()
    expect(view.querySelector<HTMLInputElement>('#audit-log-actor-filter')).not.toBeNull()
  })

  it('renders labeled mobile records alongside the desktop audit table', () => {
    auditLogsDomMocks.queryLogs = [
      {
        id: 'audit-mobile-1',
        action: 'integrations.smtp.delivery_succeeded',
        resource_type: 'integration_delivery',
        resource_id: 'delivery-1',
        actor_user_id: null,
        success: true,
        metadata: {},
        created_at: '2026-04-21T10:00:00Z',
      },
    ]

    const view = renderPage()
    const mobileRecords = view.querySelector('[aria-label="Audit log entries"]')
    const desktopTable = view.querySelector('table')?.parentElement

    expect(mobileRecords?.textContent).toContain('integrations.smtp.delivery_succeeded')
    expect(mobileRecords?.textContent).toContain('integration_delivery:delivery-1')
    expect(mobileRecords?.textContent).toContain('system')
    expect(desktopTable?.className).toContain('hidden')
    expect(desktopTable?.className).toContain('sm:block')
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

    expect(view.textContent).toContain('Actor user ID must be a valid UUID.')
    expect(auditLogsDomMocks.queryOptions.at(-1)?.enabled).toBe(false)
    expect(exportButton?.hasAttribute('disabled')).toBe(true)
  })
})

function setInputValue(input: HTMLInputElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}
