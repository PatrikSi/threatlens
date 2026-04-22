import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

const alertsPageMocks = vi.hoisted(() => ({
  confirmDiscard: vi.fn(() => true),
  queryClient: {
    invalidateQueries: vi.fn(),
  },
  useUnsavedChangesWarning: vi.fn(),
}))

alertsPageMocks.useUnsavedChangesWarning.mockImplementation(() => alertsPageMocks.confirmDiscard)

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

import { AlertsPage } from './AlertsPage'

describe('AlertsPage rendered workflow', () => {
  it('renders labeled alert controls and wires the discard warning', () => {
    const markup = renderToStaticMarkup(createElement(AlertsPage))

    expect(markup).toContain('Alert Interests')
    expect(markup).toContain('Interest Name')
    expect(markup).toContain('Keywords (comma-separated)')
    expect(markup).toContain('Current Match Preview')
    expect(markup).toContain('Configured Alerts')
    expect(markup).toContain('Edit')
    expect(alertsPageMocks.useUnsavedChangesWarning).toHaveBeenCalledWith(false, 'Discard unsaved alert changes?')
  })
})
