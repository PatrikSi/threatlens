// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  buildDashboardSavedViewState,
  createDefaultRssWindowFilters,
  type DashboardWindow,
} from './dashboardSavedViews'
import type { SavedView } from '../types/api'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const dashboardPageDomMocks = vi.hoisted(() => ({
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
  deleteMutate: vi.fn(),
  readMutate: vi.fn(),
  saveMutate: vi.fn(),
  updateMutate: vi.fn(),
  views: [] as SavedView[],
  itemsData: [] as Array<{
    id: string
    feed_id: string
    feed_name: string
    title: string
    url: string
    canonical_url: string | null
    summary: string | null
    published_at: string | null
    first_seen_at: string
    status: string
    is_read: boolean
    is_starred: boolean
    tags: string[]
    ai_relevance_label: null
  }>,
  itemDetailById: {} as Record<string, unknown>,
  unsavedChangesWarning: vi.fn(),
}))

function createSavedView(
  id: string,
  name: string,
  windows: DashboardWindow[],
  createdAt: string,
): SavedView {
  return {
    id,
    user_id: 'user-1',
    name,
    created_at: createdAt,
    query_json: buildDashboardSavedViewState(windows, {
      time_range: 'all',
      custom_since_date: '',
      custom_until_date: '',
      rolling_days: '7',
    }),
  }
}

function createNotesWindow(id: string, title: string): DashboardWindow {
  return {
    id,
    type: 'notes',
    title,
    snap: 'full',
    rect: { x: 0, y: 0, width: 1380, height: 760 },
    controls_collapsed: false,
    scratch_note: 'Track pivots here.',
    time_override: null,
    rss_filters: null,
    alert_filters: null,
    selected_daily_brief_id: null,
  }
}

function createRssWindow(id: string, title: string): DashboardWindow {
  return {
    id,
    type: 'rss',
    title,
    snap: 'full',
    rect: { x: 0, y: 0, width: 1380, height: 760 },
    controls_collapsed: false,
    scratch_note: '',
    time_override: null,
    rss_filters: createDefaultRssWindowFilters(),
    alert_filters: null,
    selected_daily_brief_id: null,
  }
}

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => dashboardPageDomMocks.queryClient,
  useQuery: ({ queryKey }: { queryKey: unknown }) => {
    const key = Array.isArray(queryKey) ? queryKey[0] : queryKey
    const baseResult = {
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
    }

    if (key === 'feeds') {
      return { ...baseResult, data: [] }
    }

    if (key === 'views') {
      return { ...baseResult, data: dashboardPageDomMocks.views }
    }

    if (key === 'tags' || key === 'alerts' || key === 'ai') {
      return { ...baseResult, data: [] }
    }

    return { ...baseResult, data: null }
  },
  useQueries: ({ queries }: { queries: Array<{ queryKey: unknown }> }) =>
    queries.map((query) => {
      const key = Array.isArray(query.queryKey) ? query.queryKey[0] : query.queryKey
      if (key === 'items' || key === 'alert-matches') {
        return {
          data:
            key === 'items'
              ? { items: dashboardPageDomMocks.itemsData, total: dashboardPageDomMocks.itemsData.length, page: 1, page_size: 25 }
              : { items: [], total: 0, page: 1, page_size: 25 },
          isLoading: false,
          isFetching: false,
          isError: false,
          error: null,
        }
      }

      if (key === 'item') {
        const queryKey = Array.isArray(query.queryKey) ? query.queryKey : []
        const itemId = typeof queryKey[1] === 'string' ? queryKey[1] : ''
        return {
          data: itemId ? dashboardPageDomMocks.itemDetailById[itemId] : undefined,
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
  useMutation: (options: { mutationKey?: unknown }) => {
    const mutationKey = Array.isArray(options?.mutationKey) ? options.mutationKey.join(':') : String(options?.mutationKey ?? '')
    if (mutationKey === 'dashboard-saved-views:delete') {
      return {
        mutate: dashboardPageDomMocks.deleteMutate,
        mutateAsync: vi.fn(),
        isPending: false,
        isError: false,
        error: null,
        variables: null,
      }
    }

    if (mutationKey === 'dashboard-saved-views:create') {
      return {
        mutate: dashboardPageDomMocks.saveMutate,
        mutateAsync: vi.fn(),
        isPending: false,
        isError: false,
        error: null,
        variables: null,
      }
    }

    if (mutationKey === 'dashboard-saved-views:update') {
      return {
        mutate: dashboardPageDomMocks.updateMutate,
        mutateAsync: vi.fn(),
        isPending: false,
        isError: false,
        error: null,
        variables: null,
      }
    }

    if (mutationKey === 'items:read') {
      return {
        mutate: dashboardPageDomMocks.readMutate,
        mutateAsync: vi.fn(),
        isPending: false,
        isError: false,
        error: null,
        variables: null,
      }
    }

    return {
      mutate: vi.fn(),
      mutateAsync: vi.fn(),
      isPending: false,
      isError: false,
      error: null,
      variables: null,
    }
  },
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => dashboardPageDomMocks.currentUser,
}))

vi.mock('../hooks/useUnsavedChangesWarning', () => ({
  useUnsavedChangesWarning: dashboardPageDomMocks.unsavedChangesWarning.mockImplementation(() =>
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

let root: Root | null = null
let container: HTMLDivElement | null = null

function renderPage() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(<DashboardPage />)
  })
  return container
}

function getButton(text: string) {
  return Array.from(document.querySelectorAll('button')).find((button) => button.textContent?.includes(text)) ?? null
}

function getSelect(label: string) {
  return document.querySelector<HTMLSelectElement>(`[aria-label="${label}"]`)
}

function setInputValue(input: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const prototype =
    input instanceof window.HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype
  const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value')
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

beforeEach(() => {
  dashboardPageDomMocks.views = [
    createSavedView(
      'view-rss',
      'RSS intel',
      [createRssWindow('rss-view-1', 'RSS Panel 1')],
      '2026-04-21T09:00:00.000Z',
    ),
    createSavedView(
      'view-notes',
      'Imported Notes',
      [createNotesWindow('notes-view-1', 'Imported Notes')],
      '2026-04-21T10:00:00.000Z',
    ),
  ]
  dashboardPageDomMocks.itemsData = []
  dashboardPageDomMocks.itemDetailById = {}

  window.localStorage.clear()
  Object.defineProperty(window, 'innerWidth', {
    configurable: true,
    value: 1440,
    writable: true,
  })
})

afterEach(() => {
  act(() => {
    root?.unmount()
  })
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  window.localStorage.clear()
  dashboardPageDomMocks.deleteMutate.mockReset()
  dashboardPageDomMocks.readMutate.mockReset()
  dashboardPageDomMocks.saveMutate.mockReset()
  dashboardPageDomMocks.updateMutate.mockReset()
  dashboardPageDomMocks.unsavedChangesWarning.mockClear()
  dashboardPageDomMocks.queryClient.invalidateQueries.mockReset()
  dashboardPageDomMocks.queryClient.setQueriesData.mockReset()
  dashboardPageDomMocks.queryClient.setQueryData.mockReset()
})

describe('DashboardPage DOM workflows', () => {
  it('uses the create saved-view mutation through the shared dashboard seam', () => {
    renderPage()

    act(() => {
      getButton('Edit Layout')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    act(() => {
      getButton('Clear Loaded View')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const nameInput = document.querySelector<HTMLInputElement>('[aria-label="Saved dashboard view name"]')
    expect(nameInput).not.toBeNull()

    act(() => {
      setInputValue(nameInput!, 'Analyst workspace')
    })

    act(() => {
      getButton('Save New View')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(dashboardPageDomMocks.saveMutate).toHaveBeenCalledTimes(1)
    expect(dashboardPageDomMocks.saveMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'Analyst workspace',
        query: expect.objectContaining({
          schema_version: 1,
          version: 6,
          windows: expect.any(Array),
        }),
      }),
    )
  })

  it('confirms before replacing an active edit session with another saved view', () => {
    const view = renderPage()

    act(() => {
      getButton('Edit Layout')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const loadSelect = getSelect('Load saved dashboard view')
    expect(loadSelect).not.toBeNull()

    act(() => {
      loadSelect!.value = 'view-notes'
      loadSelect!.dispatchEvent(new Event('change', { bubbles: true }))
    })

    expect(view.textContent).toContain('Discard the current edit session?')
    expect(view.textContent).toContain('Imported Notes')

    act(() => {
      getButton('Load saved view')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(getSelect('Load saved dashboard view')?.value).toBe('view-notes')
    expect(view.querySelector('[aria-label="Imported Notes scratch notes"]')).not.toBeNull()
    expect(getButton('Edit Layout')).not.toBeNull()
  })

  it('restores the edit-session snapshot when canceling a layout draft', () => {
    const view = renderPage()

    act(() => {
      getButton('Edit Layout')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const nameInput = document.querySelector<HTMLInputElement>('[aria-label="Saved dashboard view name"]')
    expect(nameInput).not.toBeNull()

    act(() => {
      nameInput!.value = 'Draft workspace'
      nameInput!.dispatchEvent(new Event('input', { bubbles: true }))
    })

    act(() => {
      getButton('Add Panel')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const notesMenuButton = Array.from(document.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Notes Panel ('),
    )
    expect(notesMenuButton).not.toBeNull()

    act(() => {
      notesMenuButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(view.querySelector('[aria-label="Notes Panel 1 scratch notes"]')).not.toBeNull()

    act(() => {
      getButton('Cancel')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(view.querySelector('[aria-label="Notes Panel 1 scratch notes"]')).toBeNull()
    expect(getButton('Edit Layout')).not.toBeNull()
  })

  it('wires the Add Panel menu with expanded state and menu focus', () => {
    renderPage()

    act(() => {
      getButton('Edit Layout')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const addPanelButton = getButton('Add Panel')
    expect(addPanelButton).not.toBeNull()
    expect(addPanelButton?.getAttribute('aria-expanded')).toBe('false')

    act(() => {
      addPanelButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(addPanelButton?.getAttribute('aria-expanded')).toBe('true')
    const menu = document.querySelector('[role="menu"][aria-label="Add dashboard panel"]')
    expect(menu).not.toBeNull()
    expect(document.activeElement?.textContent).toContain('RSS Panel')
  })

  it('does not auto-mark unread items as read on expansion and tracks dirty note drafts', () => {
    dashboardPageDomMocks.itemsData = [
      {
        id: 'item-1',
        feed_id: 'feed-1',
        feed_name: 'Vendor Advisories',
        title: 'Critical vendor bulletin',
        url: 'https://example.com/items/1',
        canonical_url: null,
        summary: 'Summary text',
        published_at: '2026-04-21T11:00:00Z',
        first_seen_at: '2026-04-21T11:00:00Z',
        status: 'content_fetched',
        is_read: false,
        is_starred: false,
        tags: [],
        ai_relevance_label: null,
      },
    ]
    dashboardPageDomMocks.itemDetailById = {
      'item-1': {
        id: 'item-1',
        title: 'Critical vendor bulletin',
        url: 'https://example.com/items/1',
        summary: 'Summary text',
        state: {
          is_read: false,
          is_starred: false,
          note: '',
          updated_at: '2026-04-21T11:00:00Z',
        },
        article: null,
        classification: null,
        ai_insight: null,
      },
    }

    const view = renderPage()
    const itemToggleButton = view.querySelector<HTMLButtonElement>('[aria-controls="rss-item-detail-item-1"]')
    expect(itemToggleButton).not.toBeNull()

    act(() => {
      itemToggleButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(dashboardPageDomMocks.readMutate).not.toHaveBeenCalled()

    const notesTextarea = view.querySelector<HTMLTextAreaElement>('[aria-label="Analyst notes for Critical vendor bulletin"]')
    expect(notesTextarea).not.toBeNull()

    act(() => {
      setInputValue(notesTextarea!, 'Need to validate exploit path')
    })

    const lastUnsavedWarningCall = dashboardPageDomMocks.unsavedChangesWarning.mock.calls.at(-1)
    expect(lastUnsavedWarningCall?.[0]).toBe(true)
    expect(lastUnsavedWarningCall?.[1]).toContain('unsaved dashboard note drafts')
  })

  it('confirms before clearing a loaded saved view when note drafts are still dirty', () => {
    dashboardPageDomMocks.itemsData = [
      {
        id: 'item-1',
        feed_id: 'feed-1',
        feed_name: 'Vendor Advisories',
        title: 'Critical vendor bulletin',
        url: 'https://example.com/items/1',
        canonical_url: null,
        summary: 'Summary text',
        published_at: '2026-04-21T11:00:00Z',
        first_seen_at: '2026-04-21T11:00:00Z',
        status: 'content_fetched',
        is_read: false,
        is_starred: false,
        tags: [],
        ai_relevance_label: null,
      },
    ]
    dashboardPageDomMocks.itemDetailById = {
      'item-1': {
        id: 'item-1',
        title: 'Critical vendor bulletin',
        url: 'https://example.com/items/1',
        summary: 'Summary text',
        state: {
          is_read: false,
          is_starred: false,
          note: '',
          updated_at: '2026-04-21T11:00:00Z',
        },
        article: null,
        classification: null,
        ai_insight: null,
      },
    }

    const view = renderPage()
    const loadSelect = getSelect('Load saved dashboard view')
    expect(loadSelect).not.toBeNull()

    act(() => {
      loadSelect!.value = 'view-rss'
      loadSelect!.dispatchEvent(new Event('change', { bubbles: true }))
    })

    const itemToggleButton = view.querySelector<HTMLButtonElement>('[aria-controls="rss-item-detail-item-1"]')
    expect(itemToggleButton).not.toBeNull()

    act(() => {
      itemToggleButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const notesTextarea = view.querySelector<HTMLTextAreaElement>('[aria-label="Analyst notes for Critical vendor bulletin"]')
    expect(notesTextarea).not.toBeNull()

    act(() => {
      setInputValue(notesTextarea!, 'Need to validate exploit path')
    })

    act(() => {
      loadSelect!.value = ''
      loadSelect!.dispatchEvent(new Event('change', { bubbles: true }))
    })

    expect(getSelect('Load saved dashboard view')?.value).toBe('')
    const lastUnsavedWarningCall = dashboardPageDomMocks.unsavedChangesWarning.mock.calls.at(-1)
    expect(lastUnsavedWarningCall?.[0]).toBe(true)
    expect(lastUnsavedWarningCall?.[1]).toContain('unsaved dashboard note drafts')
  })
})
