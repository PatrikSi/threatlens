// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const auditLogsDomMocks = vi.hoisted(() => ({
  exportMutate: vi.fn(),
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({
    data: { logs: [], total: 0, page: 1, page_size: 50 },
    isLoading: false,
    isError: false,
    error: null,
  }),
  useMutation: () => ({
    mutate: auditLogsDomMocks.exportMutate,
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
})
