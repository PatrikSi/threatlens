import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

const alertsPageMocks = vi.hoisted(() => ({
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

alertsPageMocks.useUnsavedChangesWarning.mockImplementation(() => createDiscardMock())

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => alertsPageMocks.queryClient,
  useQuery: ({ queryKey, enabled }: { queryKey: unknown[]; enabled?: boolean }) => {
    const [scope, key] = queryKey
    const baseResult = {
      isLoading: false,
      isError: false,
      error: null,
    }

    if (scope === 'alerts' && key !== 'preview') {
      return {
        ...baseResult,
        data: [
          {
            id: 'alert-1',
            user_id: 'user-1',
            name: 'VPN advisories',
            category: 'software',
            keywords: ['vpn', 'gateway'],
            enabled: true,
            severity: 'high',
            revision: 3,
            row_version: 5,
            created_at: '2026-04-20T10:00:00Z',
            updated_at: '2026-04-21T10:00:00Z',
          },
        ],
      }
    }

    if (scope === 'alerts' && key === 'preview') {
      return {
        ...baseResult,
        data: enabled
          ? {
              items: [],
              total: 0,
              page: 1,
              page_size: 5,
            }
          : undefined,
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
  useUnsavedChangesWarning: alertsPageMocks.useUnsavedChangesWarning,
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({
    data: { id: 'admin-1', role: 'admin' },
    isLoading: false,
    isError: false,
  }),
}))

vi.mock('./AlertOperationsWorkspace', () => ({
  AlertOperationsWorkspace: () => createElement('div', null, 'Operations workspace'),
}))

import { AlertsPage } from './AlertsPage'

describe('AlertsPage rendered workflow', () => {
  it('renders labeled alert controls and wires the discard warning', () => {
    const markup = renderToStaticMarkup(createElement(AlertsPage))

    expect(markup).toContain('Alert Interests')
    expect(markup).toContain('role="tablist"')
    expect(markup).toContain('Occurrence triage')
    expect(markup).toContain('Operations')
    expect(markup).toContain('id="alert-occurrences-panel"')
    expect(markup).toContain('tabindex="-1"')
    expect(markup).toContain('Interest Name')
    expect(markup).toContain('Severity')
    expect(markup).toContain('Notification suppression')
    expect(markup).toContain('Keywords (comma-separated)')
    expect(markup).toContain('Current Match Preview')
    expect(markup).toContain('Configured Alerts')
    expect(markup).toContain('Rule revision 3')
    expect(markup).toContain('Version 5')
    expect(markup).toContain('Edit')
    expect(alertsPageMocks.useUnsavedChangesWarning).toHaveBeenCalledWith(
      false,
      'Discard unsaved alert changes?',
    )
  })
})
