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
    const key = Array.isArray(queryKey) ? queryKey[0] : queryKey
    const baseResult = {
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
    }

    if (key === 'feeds' || key === 'views' || key === 'tags' || key === 'alerts' || key === 'ai') {
      return { ...baseResult, data: [] }
    }

    return { ...baseResult, data: null }
  },
  useQueries: ({ queries }: { queries: Array<{ queryKey: unknown }> }) =>
    queries.map((query) => {
      const key = Array.isArray(query.queryKey) ? query.queryKey[0] : query.queryKey
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

vi.mock('../hooks/useUnsavedChangesWarning', () => ({
  useUnsavedChangesWarning: vi.fn(),
}))

import { DashboardPage } from './DashboardPage'

describe('DashboardPage rendered controls', () => {
  it('renders explicit accessible names for the primary dashboard toolbar workflow', () => {
    const markup = renderToStaticMarkup(createElement(DashboardPage))

    expect(markup).toContain('aria-label="Search across all dashboard panels"')
    expect(markup).toContain('aria-label="Dashboard time range"')
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
    expect(markup).toContain('More Filters')
  })
})
