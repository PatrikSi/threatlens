import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

const dashboardPageRenderMocks = vi.hoisted(() => ({
  currentUser: {
    data: {
      id: 'user-1',
      role: 'admin',
      features: {
        ai_summary_enabled: false,
        ai_relevance_enabled: false,
        ai_daily_brief_enabled: false,
      },
    },
    isLoading: false,
    isError: false,
    error: null,
  },
  queryClient: {
    invalidateQueries: vi.fn(),
    setQueriesData: vi.fn(),
    setQueryData: vi.fn(),
  },
}))

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => dashboardPageRenderMocks.queryClient,
  useQuery: ({ queryKey }: { queryKey: unknown }) => {
    const parts = Array.isArray(queryKey) ? queryKey : [queryKey]
    const key = parts[0] === 'dashboard' ? parts[1] : parts[0]
    const baseResult = {
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
    }

    if (key === 'feeds' || key === 'views' || key === 'tags' || key === 'alert-interests' || key === 'daily-briefs') {
      return { ...baseResult, data: [] }
    }

    return { ...baseResult, data: null }
  },
  useQueries: ({ queries }: { queries: Array<{ queryKey: unknown }> }) =>
    queries.map((query) => {
      const parts = Array.isArray(query.queryKey) ? query.queryKey : [query.queryKey]
      const key = parts[0] === 'dashboard' ? parts[1] : parts[0]
      if (key === 'items') {
        return {
          data: { items: [], total: 0, page: 1, page_size: 25 },
          isLoading: false,
          isFetching: false,
          isError: false,
          error: null,
        }
      }
      if (key === 'alert-matches') {
        return {
          data: { items: [], total: 0, page: 1, page_size: 25 },
          isLoading: false,
          isFetching: false,
          isError: false,
          error: null,
        }
      }

      return {
        data: undefined,
        isLoading: false,
        isFetching: false,
        isError: false,
        error: null,
      }
    }),
  useMutation: () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
    variables: null,
  }),
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => dashboardPageRenderMocks.currentUser,
}))

vi.mock('../workspace/useWorkspace', () => ({
  useWorkspace: () => ({
    isLoading: false,
    effective: { role: 'admin' },
    userContext: { role: 'admin' },
    model: { dashboardPanelIds: ['rss'] },
  }),
}))

vi.mock('../hooks/useUnsavedChangesWarning', () => ({
  useUnsavedChangesWarning: vi.fn(() =>
    Object.assign(
      vi.fn((onDiscard?: () => void) => {
        onDiscard?.()
        return true
      }),
      { discardDialog: null },
    ),
  ),
}))

import { DashboardPage } from './DashboardPage'

describe('DashboardPage rendered controls', () => {
  it('renders explicit accessible names for the primary dashboard toolbar workflow', () => {
    const markup = renderToStaticMarkup(createElement(DashboardPage))

    expect(markup).toContain('aria-label="Search across all dashboard panels"')
    expect(markup).toContain('aria-label="Dashboard time range"')
    expect(markup).toContain('grid-cols-[minmax(0,1fr)_112px_auto]')
    expect(markup).toContain('aria-label="Load saved dashboard view"')
    expect(markup).toContain('Views')
    expect(markup).toContain('Edit Layout')
  })

  it('renders explicit accessible names for the default RSS panel controls', () => {
    const markup = renderToStaticMarkup(createElement(DashboardPage))

    expect(markup).toContain('aria-label="RSS Panel 1 search query"')
    expect(markup).toContain('aria-label="RSS Panel 1 time range"')
    expect(markup).toContain('aria-label="RSS Panel 1 sort order"')
    expect(markup).toContain('aria-label="RSS Panel 1 results per page"')
    expect(markup).toContain('10 per page')
    expect(markup).toContain('More Filters')
  })
})
