// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const feedsPageDomMocks = vi.hoisted(() => ({
  queryClient: {
    invalidateQueries: vi.fn(),
  },
  deleteMutate: vi.fn(),
  bulkDeleteMutate: vi.fn(),
  bulkSetEnabledMutate: vi.fn(),
}))

const feedsData = [
  {
    id: 'feed-1',
    name: 'Vendor Advisories',
    url: 'https://example.com/vendor.xml',
    description: null,
    site_url: null,
    language: null,
    enabled: true,
    fetch_mode: 'interval',
    fetch_interval_seconds: 1800,
    schedule_cron: null,
    etag: null,
    last_modified: null,
    last_fetch_at: null,
    last_success_at: null,
    error_count: 0,
    last_error: null,
    created_at: '2026-04-21T10:00:00Z',
  },
  {
    id: 'feed-2',
    name: 'Edge Advisories',
    url: 'https://example.com/edge.xml',
    description: null,
    site_url: null,
    language: null,
    enabled: false,
    fetch_mode: 'schedule',
    fetch_interval_seconds: 900,
    schedule_cron: '0 * * * *',
    etag: null,
    last_modified: null,
    last_fetch_at: null,
    last_success_at: null,
    error_count: 0,
    last_error: null,
    created_at: '2026-04-20T10:00:00Z',
  },
] as const

function feedMutationResult(mutate: ReturnType<typeof vi.fn>) {
  return {
    mutate,
    isPending: false,
    isError: false,
    error: null,
  }
}

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => feedsPageDomMocks.queryClient,
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => {
    const [scope] = queryKey
    const baseResult = {
      isLoading: false,
      isError: false,
      error: null,
      refetchInterval: false,
    }

    if (scope === 'feeds') {
      return {
        ...baseResult,
        data: feedsData,
      }
    }

    return {
      ...baseResult,
      data: undefined,
    }
  },
  useMutation: (options: { mutationFn?: unknown }) => {
    const source = String(options?.mutationFn ?? '')
    if (source.includes('Promise.allSettled') && source.includes('payload.enabled')) {
      return feedMutationResult(feedsPageDomMocks.bulkSetEnabledMutate)
    }
    if (source.includes('Promise.allSettled') && source.includes('DELETE')) {
      return feedMutationResult(feedsPageDomMocks.bulkDeleteMutate)
    }
    if (source.includes('/feeds/${id}') && source.includes('DELETE')) {
      return feedMutationResult(feedsPageDomMocks.deleteMutate)
    }
    return feedMutationResult(vi.fn())
  },
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({
    data: {
      role: 'admin',
    },
  }),
}))

vi.mock('../hooks/useUnsavedChangesWarning', () => ({
  useUnsavedChangesWarning: vi.fn(() => Object.assign(vi.fn(), { discardDialog: null })),
}))

import { FeedsPage } from './FeedsPage'

let root: Root | null = null
let container: HTMLDivElement | null = null

function renderPage() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(<FeedsPage />)
  })
  return container
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
  feedsPageDomMocks.deleteMutate.mockReset()
  feedsPageDomMocks.bulkDeleteMutate.mockReset()
  feedsPageDomMocks.bulkSetEnabledMutate.mockReset()
})

describe('FeedsPage DOM workflows', () => {
  it('keeps the critical feed controls labeled and surfaces unsaved schedule changes after editing', () => {
    const view = renderPage()

    expect(view.querySelector('label[for="feed-rss-url"]')).not.toBeNull()
    expect(view.querySelector('label[for="feed-name"]')).not.toBeNull()
    expect(view.querySelector('label[for="feed-description"]')).not.toBeNull()
    expect(view.querySelector('label[for="feed-site-url"]')).not.toBeNull()
    expect(view.querySelector('label[for="feed-language"]')).not.toBeNull()
    expect(view.querySelector('label[for="feed-fetch-interval"]')).not.toBeNull()
    expect(view.querySelector('label[for="feed-search"]')).not.toBeNull()
    expect(view.querySelector('label[for="feed-sort"]')).not.toBeNull()
    expect(view.querySelector('label[for="feed-fetch-mode-feed-1"]')).not.toBeNull()
    expect(view.querySelector('label[for="feed-interval-seconds-feed-1"]')).not.toBeNull()

    const searchInput = view.querySelector<HTMLInputElement>('#feed-search')
    expect(searchInput).not.toBeNull()

    act(() => {
      setInputValue(searchInput!, 'Vendor')
    })

    expect(view.textContent).toContain('Showing 1 of 2 feeds')

    const feedModeSelect = view.querySelector<HTMLSelectElement>('#feed-fetch-mode-feed-1')
    expect(feedModeSelect).not.toBeNull()

    act(() => {
      setSelectValue(feedModeSelect!, 'schedule')
    })

    expect(feedModeSelect!.value).toBe('schedule')
    expect(view.textContent).toContain('Unsaved schedule')
  })

  it('confirms feed deletion before removing the feed', () => {
    const view = renderPage()

    const feedRow = Array.from(view.querySelectorAll('div')).find((node) =>
      node.textContent?.includes('Vendor Advisories') && node.textContent?.includes('Refresh') && node.textContent?.includes('Delete'),
    )
    expect(feedRow).not.toBeNull()

    const deleteButton = Array.from(feedRow!.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Delete')
    expect(deleteButton).not.toBeNull()

    act(() => {
      deleteButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(view.textContent).toContain('Delete feed?')
    expect(view.textContent).toContain('Vendor Advisories')

    const confirmButton = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Delete feed')
    expect(confirmButton).not.toBeNull()

    act(() => {
      confirmButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(feedsPageDomMocks.deleteMutate).toHaveBeenCalledWith('feed-1')
  })

  it('confirms bulk enable actions before mutating filtered feeds', () => {
    const view = renderPage()

    const bulkEnableButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Enable Disabled (Filtered)'),
    )
    expect(bulkEnableButton).not.toBeNull()

    act(() => {
      bulkEnableButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(view.textContent).toContain('Enable filtered feeds?')
    expect(view.textContent).toContain('Edge Advisories')

    const confirmButton = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Enable feeds')
    expect(confirmButton).not.toBeNull()

    act(() => {
      confirmButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(feedsPageDomMocks.bulkSetEnabledMutate).toHaveBeenCalledWith({
      ids: ['feed-2'],
      enabled: true,
    })
  })
})
