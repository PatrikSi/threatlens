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
    ai_relevance_label: 'low' | 'medium' | 'high' | null
  }>,
  itemDetailById: {} as Record<string, unknown>,
  alertMatchesData: [] as Array<{
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
    classification: string | null
    is_read: boolean
    is_starred: boolean
    tags: string[]
    ai_relevance_score: number | null
    ai_relevance_label: 'low' | 'medium' | 'high' | null
    ai_status: string | null
    matches: Array<{
      alert_id: string
      alert_name: string
      category: string
      matched_keywords: string[]
    }>
  }>,
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
              : {
                  items: dashboardPageDomMocks.alertMatchesData,
                  total: dashboardPageDomMocks.alertMatchesData.length,
                  page: 1,
                  page_size: 25,
                },
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
  useMutation: (options: { mutationKey?: unknown; onSuccess?: (data: unknown, variables: unknown) => void }) => {
    const mutationKey = Array.isArray(options?.mutationKey) ? options.mutationKey.join(':') : String(options?.mutationKey ?? '')
    if (mutationKey === 'dashboard-saved-views:delete') {
      return {
        mutate: vi.fn((viewId: string) => {
          dashboardPageDomMocks.deleteMutate(viewId)
          options.onSuccess?.(undefined, viewId)
        }),
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

function pageText() {
  return document.body.textContent ?? ''
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

function setSelectValue(select: HTMLSelectElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')
  descriptor?.set?.call(select, value)
  select.dispatchEvent(new Event('change', { bubbles: true }))
}

function createJsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'Content-Type': 'application/json',
    },
  })
}

async function flushAsyncWork() {
  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 0))
  })
}

async function uploadFile(input: HTMLInputElement, file: File) {
  Object.defineProperty(input, 'files', {
    configurable: true,
    value: [file],
  })
  act(() => {
    input.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await flushAsyncWork()
}

beforeEach(() => {
  dashboardPageDomMocks.currentUser.data.features.ai_relevance_enabled = false
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
  dashboardPageDomMocks.alertMatchesData = []

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
  vi.unstubAllGlobals()
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

    expect(pageText()).toContain('Discard the current edit session?')
    expect(pageText()).toContain('Imported Notes')

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

  it('keeps the active edit-session snapshot when deleting a different saved view', () => {
    const view = renderPage()

    const loadSelect = getSelect('Load saved dashboard view')
    expect(loadSelect).not.toBeNull()

    act(() => {
      setSelectValue(loadSelect!, 'view-rss')
    })

    expect(loadSelect?.value).toBe('view-rss')

    act(() => {
      getButton('Edit Layout')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
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
      getButton('Views')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const deleteButton = Array.from(document.querySelectorAll('button'))
      .filter((button) => button.textContent?.trim() === 'Delete')
      .at(1)
    expect(deleteButton).not.toBeNull()

    act(() => {
      deleteButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Delete saved view?')
    expect(pageText()).toContain('Imported Notes')

    act(() => {
      getButton('Delete view')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(dashboardPageDomMocks.deleteMutate).toHaveBeenCalledWith('view-notes')
    expect(getSelect('Load saved dashboard view')?.value).toBe('view-rss')

    act(() => {
      getButton('Cancel')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(view.querySelector('[aria-label="Notes Panel 1 scratch notes"]')).toBeNull()
    expect(getSelect('Load saved dashboard view')?.value).toBe('view-rss')
    expect(getButton('Edit Layout')).not.toBeNull()
  })

  it('labels saved-view load and delete actions with the view name in Manage Saved Views', () => {
    renderPage()

    act(() => {
      getButton('Views')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(document.querySelector('[aria-label="Load saved view RSS intel"]')).not.toBeNull()
    expect(document.querySelector('[aria-label="Delete saved view RSS intel"]')).not.toBeNull()
    expect(document.querySelector('[aria-label="Load saved view Imported Notes"]')).not.toBeNull()
    expect(document.querySelector('[aria-label="Delete saved view Imported Notes"]')).not.toBeNull()
  })

  it('reports both completed imports and the exact saved view that failed during a partial import', async () => {
    renderPage()

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        createJsonResponse({
          id: 'imported-1',
          name: 'Alpha View',
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'A saved view with that name already exists.' }), {
          status: 409,
          headers: {
            'Content-Type': 'application/json',
          },
        }),
      )
    vi.stubGlobal('fetch', fetchMock)

    act(() => {
      getButton('Views')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const fileInput = document.querySelector<HTMLInputElement>('[aria-label="Import saved dashboard views JSON"]')
    expect(fileInput).not.toBeNull()

    const file = new File(
      [
        JSON.stringify({
          views: [
            { name: 'Alpha View', query_json: { windows: [] } },
            { name: 'Bravo View', query_json: { windows: [] } },
          ],
        }),
      ],
      'views.json',
      { type: 'application/json' },
    )

    await uploadFile(fileInput!, file)

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(pageText()).toContain('Imported 1 saved view before the import stopped: "Alpha View".')
    expect(pageText()).toContain('Failed to import "Bravo View": A saved view with that name already exists.')
    expect(dashboardPageDomMocks.queryClient.invalidateQueries).toHaveBeenCalledWith({ queryKey: ['views'] })
  })

  it('wires the Add Panel menu with expanded state and keyboard navigation', () => {
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

    act(() => {
      menu?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
    })

    expect(document.activeElement?.textContent).toContain('Alerts Panel')

    act(() => {
      menu?.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }))
    })

    expect(document.activeElement?.textContent).toContain('Notes Panel')

    act(() => {
      menu?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true }))
    })

    expect(document.activeElement?.textContent).toContain('RSS Panel')

    act(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })

    expect(document.querySelector('[role="menu"][aria-label="Add dashboard panel"]')).toBeNull()
    expect(document.activeElement).toBe(addPanelButton)
  })

  it('renders alert panel summaries as decoded plain text', () => {
    dashboardPageDomMocks.alertMatchesData = [
      {
        id: 'alert-item-1',
        feed_id: 'feed-1',
        feed_name: 'Vendor Advisories',
        title: 'Exploit chain observed',
        url: 'https://example.com/alert-item',
        canonical_url: null,
        summary: '<p>Attackers chained <a href="https://example.com">edge bugs</a>&#8212;patch now.</p>',
        published_at: '2026-04-21T11:00:00Z',
        first_seen_at: '2026-04-21T11:00:00Z',
        status: 'content_fetched',
        classification: null,
        is_read: false,
        is_starred: false,
        tags: [],
        ai_relevance_score: null,
        ai_relevance_label: null,
        ai_status: null,
        matches: [
          {
            alert_id: 'alert-1',
            alert_name: 'Edge threats',
            category: 'threat',
            matched_keywords: ['edge'],
          },
        ],
      },
    ]
    renderPage()

    act(() => {
      getButton('Edit Layout')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const addPanelButton = getButton('Add Panel')
    expect(addPanelButton).not.toBeNull()

    act(() => {
      addPanelButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const alertsPanelButton = Array.from(document.querySelectorAll('[role="menuitem"]')).find((button) =>
      button.textContent?.includes('Alerts Panel'),
    )
    expect(alertsPanelButton).not.toBeNull()

    act(() => {
      alertsPanelButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Attackers chained edge bugs—patch now.')
    expect(pageText()).not.toContain('<p>')
    expect(pageText()).not.toContain('&#8212;')
  })

  it('exposes pressed state for dashboard filter chips and view-mode toggles', () => {
    const view = renderPage()

    const allFeedsButton = view.querySelector<HTMLButtonElement>('[aria-label="RSS Panel 1 all feeds"]')
    const allTagsButton = view.querySelector<HTMLButtonElement>('[aria-label="RSS Panel 1 all tags"]')
    const viewModeGroup = view.querySelector('[aria-label="RSS Panel 1 view mode"]')
    const expandedButton = Array.from(viewModeGroup?.querySelectorAll<HTMLButtonElement>('button') ?? []).find((button) =>
      button.textContent?.trim() === 'Expanded',
    )
    const compactButton = Array.from(viewModeGroup?.querySelectorAll<HTMLButtonElement>('button') ?? []).find((button) =>
      button.textContent?.trim() === 'Compact',
    )
    const expandedPressed = expandedButton?.getAttribute('aria-pressed')
    const compactPressed = compactButton?.getAttribute('aria-pressed')

    expect(allFeedsButton?.getAttribute('aria-pressed')).toBe('true')
    expect(allTagsButton?.getAttribute('aria-pressed')).toBe('true')
    expect([expandedPressed, compactPressed].sort()).toEqual(['false', 'true'])

    act(() => {
      ;(expandedPressed === 'true' ? compactButton : expandedButton)?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect([expandedButton?.getAttribute('aria-pressed'), compactButton?.getAttribute('aria-pressed')].sort()).toEqual([
      'false',
      'true',
    ])
    expect(expandedButton?.getAttribute('aria-pressed')).not.toBe(expandedPressed)
    expect(compactButton?.getAttribute('aria-pressed')).not.toBe(compactPressed)
  })

  it('uses a keyboard-focusable button to open saved-view JSON import', () => {
    renderPage()

    act(() => {
      getButton('Views')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const importButton = Array.from(document.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Import JSON')
    const importInput = document.querySelector<HTMLInputElement>('[aria-label="Import saved dashboard views JSON"]')
    const clickSpy = vi.fn()

    expect(importButton?.tagName).toBe('BUTTON')
    expect(importInput).not.toBeNull()

    Object.defineProperty(importInput!, 'click', {
      configurable: true,
      value: clickSpy,
    })

    act(() => {
      importButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(clickSpy).toHaveBeenCalledTimes(1)
  })

  it('keeps floating layout chrome minimal while preserving drag resize', () => {
    const view = renderPage()

    act(() => {
      getButton('Edit Layout')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const layoutSelect = view.querySelector<HTMLSelectElement>('[aria-label="RSS Panel 1 panel layout"]')
    expect(layoutSelect).not.toBeNull()
    const floatingOption = Array.from(layoutSelect!.options).find((option) => option.value === 'free')
    expect(floatingOption?.textContent).toBe('Floating')

    act(() => {
      setSelectValue(layoutSelect!, 'free')
    })

    expect(pageText()).not.toContain('Keyboard panel controls')
    expect(document.querySelector('[aria-label="Move RSS Panel 1 left"]')).toBeNull()
    expect(document.querySelector('[aria-label="Make RSS Panel 1 wider"]')).toBeNull()
    expect(document.querySelector('[aria-label="Resize panel"]')).not.toBeNull()
  })

  it('uses subtle semantic chip tones for starred, tagged, and AI relevance item state', () => {
    dashboardPageDomMocks.currentUser.data.features.ai_relevance_enabled = true
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
        is_read: true,
        is_starred: true,
        tags: ['vendor:microsoft', 'priority', 'exploitability'],
        ai_relevance_label: 'high',
      },
    ]

    renderPage()

    const starredChip = Array.from(document.querySelectorAll('span')).find((span) => span.textContent === 'Starred')
    expect(starredChip?.className).toContain('tl-chip-starred')

    const aiChip = Array.from(document.querySelectorAll('span')).find((span) => span.textContent === 'AI High')
    expect(aiChip?.className).toContain('tl-chip-ai-high')

    const tagChip = Array.from(document.querySelectorAll('span')).find((span) => span.textContent === '#vendor:microsoft')
    expect(tagChip?.className).toContain('tl-chip-tag')
    expect(pageText()).not.toContain('#priority')
  })

  it('auto-marks unread items as read on expansion and tracks dirty note drafts', () => {
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

    expect(dashboardPageDomMocks.readMutate).toHaveBeenCalledWith({
      itemId: 'item-1',
      isRead: true,
    })

    const notesTextarea = view.querySelector<HTMLTextAreaElement>('[aria-label="Analyst notes for Critical vendor bulletin"]')
    expect(notesTextarea).not.toBeNull()

    act(() => {
      setInputValue(notesTextarea!, 'Need to validate exploit path')
    })

    const lastUnsavedWarningCall = dashboardPageDomMocks.unsavedChangesWarning.mock.calls.at(-1)
    expect(lastUnsavedWarningCall?.[0]).toBe(true)
    expect(lastUnsavedWarningCall?.[1]).toContain('unsaved dashboard note drafts')
  })

  it('opens a right-side original article preview from the RSS row source action', () => {
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
        feed_id: 'feed-1',
        feed_name: 'Vendor Advisories',
        source_guid: null,
        title: 'Critical vendor bulletin',
        url: 'https://example.com/items/1',
        canonical_url: null,
        summary: 'Summary text',
        published_at: '2026-04-21T11:00:00Z',
        first_seen_at: '2026-04-21T11:00:00Z',
        status: 'content_fetched',
        classification: null,
        last_error: null,
        tags: [],
        state: {
          is_read: false,
          is_starred: false,
          note: '',
          updated_at: '2026-04-21T11:00:00Z',
        },
        article: {
          final_url: 'https://publisher.example.com/articles/critical-vendor-bulletin',
          retrieved_at: '2026-04-21T11:01:00Z',
          http_status: 200,
          content_type: 'text/html',
          title_extracted: 'Critical vendor bulletin',
          text: 'Extracted text',
          extraction_method: 'readability',
          language: 'en',
          word_count: 120,
          fetch_ms: 90,
          error: null,
        },
        ai_insight: null,
      },
    }

    const view = renderPage()
    expect(view.querySelector('[aria-controls="rss-item-detail-item-1"]')?.getAttribute('aria-expanded')).toBe('false')

    const previewButton = getButton('Preview Original')
    expect(previewButton).not.toBeNull()

    act(() => {
      previewButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const previewDialog = document.querySelector<HTMLElement>('[aria-labelledby="article-preview-title"]')
    expect(previewDialog).not.toBeNull()
    expect(previewDialog?.textContent).toContain('Vendor Advisories')
    expect(previewDialog?.textContent).toContain('Critical vendor bulletin')

    const iframe = previewDialog?.querySelector<HTMLIFrameElement>('iframe')
    expect(iframe?.getAttribute('src')).toContain('/items/item-1/article-preview')
    expect(iframe?.getAttribute('src')).not.toBe('https://publisher.example.com/articles/critical-vendor-bulletin')
    expect(iframe?.getAttribute('sandbox')).toContain('allow-popups')
    expect(iframe?.getAttribute('sandbox')).not.toContain('allow-scripts')

    const originalLink = previewDialog?.querySelector<HTMLAnchorElement>('a[href="https://example.com/items/1"]')
    expect(originalLink?.textContent).toContain('Open Original')

    const resizeHandle = previewDialog?.querySelector<HTMLElement>('[aria-label="Resize article preview width"]')
    expect(resizeHandle).not.toBeNull()
    expect(previewDialog?.style.width).toBe('704px')

    act(() => {
      resizeHandle!.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 700 }))
    })

    expect(iframe?.className).toContain('pointer-events-none')

    act(() => {
      document.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 600 }))
      document.dispatchEvent(new MouseEvent('pointerup', { bubbles: true }))
    })

    expect(previewDialog?.style.width).toBe('804px')
    expect(iframe?.className).not.toContain('pointer-events-none')
    expect(window.localStorage.getItem('threatlens.article-preview.width.v1')).toBe('804')

    const closeButton = previewDialog?.querySelector<HTMLButtonElement>('[aria-label="Close original article preview"]')
    expect(closeButton).not.toBeNull()

    act(() => {
      closeButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(document.querySelector('[aria-labelledby="article-preview-title"]')).toBeNull()

    act(() => {
      previewButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const reopenedPreviewDialog = document.querySelector<HTMLElement>('[aria-labelledby="article-preview-title"]')
    expect(reopenedPreviewDialog?.style.width).toBe('804px')
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
