// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const feedsPageDomMocks = vi.hoisted(() => ({
  queryClient: {
    invalidateQueries: vi.fn(),
  },
  bulkRefreshMutate: vi.fn(),
  deleteMutate: vi.fn(),
  bulkDeleteMutate: vi.fn(),
  bulkSetEnabledMutate: vi.fn(),
  importMutate: vi.fn(),
  currentUser: {
    id: 'admin-user',
    role: 'admin',
  } as { id: string; role: string } | null,
  bulkRefreshResult: null as
    | { attempted: number; succeeded: number; failed: number; failedFeedNames: string[] }
    | null,
  bulkDeleteResult: null as
    | { attempted: number; succeeded: number; failed: number; failedFeedNames: string[] }
    | null,
  bulkSetEnabledResult: null as
    | { enabled: boolean; attempted: number; succeeded: number; failed: number; failedFeedNames: string[] }
    | null,
}))

const routerMocks = vi.hoisted(() => {
  const blocker = {
    state: 'unblocked' as 'unblocked' | 'blocked',
    proceed: vi.fn(),
    reset: vi.fn(),
  }

  return {
    blocker,
    useBlocker: vi.fn(() => ({ ...blocker })),
  }
})

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
    has_unreadable_url: false,
    created_at: '2026-04-21T10:00:00Z',
  },
  {
    id: 'feed-2',
    name: 'Orange Cyberdefense',
    url: '',
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
    last_error: 'Stored feed URL cannot be decrypted.',
    has_unreadable_url: true,
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
    const [scope, key] = queryKey
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

    if (scope === 'health' && key === 'encrypted-data') {
      return {
        ...baseResult,
        data: {
          ok: false,
          status: 'critical',
          scanned_at: '2026-04-23T08:00:00Z',
          warnings: [],
          require_explicit_app_data_encryption_key: true,
          using_derived_app_data_encryption_key: false,
          startup_scan: {
            completed_at: '2026-04-23T07:59:00Z',
            status: 'critical',
            error: null,
            total_unreadable_records: 1,
            total_unreadable_fields: 1,
          },
          feeds: {
            total_records: 2,
            encrypted_records: 2,
            unreadable_records: 1,
            encrypted_fields: 2,
            unreadable_fields: 1,
          },
          notification_webhooks: {
            total_records: 0,
            encrypted_records: 0,
            unreadable_records: 0,
            encrypted_fields: 0,
            unreadable_fields: 0,
          },
          notification_delivery_snapshots: {
            total_records: 0,
            encrypted_records: 0,
            unreadable_records: 0,
            encrypted_fields: 0,
            unreadable_fields: 0,
          },
          summary: {
            total_records: 2,
            encrypted_records: 2,
            unreadable_records: 1,
            encrypted_fields: 2,
            unreadable_fields: 1,
          },
        },
      }
    }

    return {
      ...baseResult,
      data: undefined,
    }
  },
  useMutation: (options: { mutationKey?: unknown; onSuccess?: (result: unknown, variables: unknown) => void }) => {
    const mutationKey = Array.isArray(options?.mutationKey) ? options.mutationKey.join(':') : String(options?.mutationKey ?? '')
    if (mutationKey === 'feeds:bulk-refresh') {
      return feedMutationResult(
        vi.fn((feeds: Array<(typeof feedsData)[number]>) => {
          feedsPageDomMocks.bulkRefreshMutate(feeds)
          options.onSuccess?.(
            feedsPageDomMocks.bulkRefreshResult ?? {
              attempted: feeds.length,
              succeeded: feeds.length,
              failed: 0,
              failedFeedNames: [],
            },
            feeds,
          )
        }),
      )
    }
    if (mutationKey === 'feeds:bulk-set-enabled') {
      return feedMutationResult(
        vi.fn((payload: { feeds: Array<(typeof feedsData)[number]>; enabled: boolean }) => {
          feedsPageDomMocks.bulkSetEnabledMutate(payload)
          options.onSuccess?.(
            feedsPageDomMocks.bulkSetEnabledResult ?? {
              enabled: payload.enabled,
              attempted: payload.feeds.length,
              succeeded: payload.feeds.length,
              failed: 0,
              failedFeedNames: [],
            },
            payload,
          )
        }),
      )
    }
    if (mutationKey === 'feeds:bulk-delete') {
      return feedMutationResult(
        vi.fn(
          (
            feeds: Array<(typeof feedsData)[number]>,
            mutateOptions?: { onSuccess?: (result: unknown, variables: unknown) => void },
          ) => {
          feedsPageDomMocks.bulkDeleteMutate(feeds)
          const result =
            feedsPageDomMocks.bulkDeleteResult ?? {
              attempted: feeds.length,
              succeeded: feeds.length,
              failed: 0,
              failedFeedNames: [],
            }
          options.onSuccess?.(result, feeds)
          mutateOptions?.onSuccess?.(result, feeds)
        }),
      )
    }
    if (mutationKey === 'feeds:delete') {
      return feedMutationResult(feedsPageDomMocks.deleteMutate)
    }
    if (mutationKey === 'feeds:import') {
      return feedMutationResult(feedsPageDomMocks.importMutate)
    }
    return feedMutationResult(vi.fn())
  },
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({
    data: feedsPageDomMocks.currentUser,
  }),
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useBlocker: routerMocks.useBlocker,
  }
})

import { FeedsPage } from './FeedsPage'
import { getFeedScheduleDraftStorageKey } from './feedScheduleDraft'

let root: Root | null = null
let container: HTMLDivElement | null = null

function renderPage({ clearSessionStorage = true }: { clearSessionStorage?: boolean } = {}) {
  container = document.createElement('div')
  document.body.appendChild(container)
  if (clearSessionStorage) {
    window.sessionStorage.clear()
  }
  root = createRoot(container)
  act(() => {
    root?.render(<FeedsPage />)
  })
  return container
}

function pageText() {
  return document.body.textContent ?? ''
}

function rerenderPage() {
  act(() => {
    root?.render(<FeedsPage />)
  })
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

function setCheckboxValue(input: HTMLInputElement, checked: boolean) {
  if (input.checked === checked) {
    return
  }
  input.click()
}

afterEach(() => {
  act(() => {
    root?.unmount()
  })
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  window.sessionStorage.clear()
  feedsPageDomMocks.bulkRefreshMutate.mockReset()
  feedsPageDomMocks.deleteMutate.mockReset()
  feedsPageDomMocks.bulkDeleteMutate.mockReset()
  feedsPageDomMocks.bulkSetEnabledMutate.mockReset()
  feedsPageDomMocks.importMutate.mockReset()
  feedsPageDomMocks.bulkRefreshResult = null
  feedsPageDomMocks.bulkDeleteResult = null
  feedsPageDomMocks.bulkSetEnabledResult = null
  feedsPageDomMocks.currentUser = {
    id: 'admin-user',
    role: 'admin',
  }
  routerMocks.blocker.state = 'unblocked'
  routerMocks.blocker.proceed.mockReset()
  routerMocks.blocker.reset.mockReset()
})

describe('FeedsPage DOM workflows', () => {
  it('preserves user-scoped schedule drafts when feeds load before the current user id', () => {
    feedsPageDomMocks.currentUser = null
    const scopedKey = getFeedScheduleDraftStorageKey('admin-user')
    const persistedDrafts = {
      'feed-1': {
        fetchMode: 'schedule',
        intervalSeconds: '1800',
        scheduleCron: '*/15 * * * *',
      },
    }
    window.sessionStorage.setItem(scopedKey, JSON.stringify(persistedDrafts))
    const view = renderPage({ clearSessionStorage: false })

    expect(view.querySelector<HTMLSelectElement>('#feed-fetch-mode-feed-1')?.value).toBe('interval')

    feedsPageDomMocks.currentUser = {
      id: 'admin-user',
      role: 'admin',
    }
    rerenderPage()

    expect(view.querySelector<HTMLSelectElement>('#feed-fetch-mode-feed-1')?.value).toBe('schedule')
    expect(JSON.parse(window.sessionStorage.getItem(scopedKey) ?? '{}')).toEqual(persistedDrafts)
    expect(pageText()).toContain('Unsaved schedule')
  })

  it('keeps the critical feed controls labeled and surfaces unsaved schedule changes after editing', () => {
    const view = renderPage()

    expect(view.querySelector('label[for="feed-rss-url"]')).not.toBeNull()
    expect(view.querySelector('label[for="feed-name"]')).not.toBeNull()
    expect(view.querySelector('label[for="feed-description"]')).not.toBeNull()
    expect(view.querySelector('label[for="feed-site-url"]')).not.toBeNull()
    expect(view.querySelector('label[for="feed-language"]')).not.toBeNull()
    expect(view.querySelector('label[for="feed-fetch-interval"]')).not.toBeNull()
    expect(view.querySelector('label[for="feed-search"]')).not.toBeNull()
    expect(view.querySelector('label[for="feed-status-filter"]')).not.toBeNull()
    expect(view.querySelector('label[for="feed-sort"]')).not.toBeNull()
    expect(view.querySelector('label[for="feed-fetch-mode-feed-1"]')).not.toBeNull()
    expect(view.querySelector('label[for="feed-interval-seconds-feed-1"]')).not.toBeNull()
    expect(
      Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Import JSON'),
    ).not.toBeNull()

    const searchInput = view.querySelector<HTMLInputElement>('#feed-search')
    expect(searchInput).not.toBeNull()

    act(() => {
      setInputValue(searchInput!, 'Vendor')
    })

    expect(pageText()).toContain('Showing 1 of 2 feeds')

    const feedModeSelect = view.querySelector<HTMLSelectElement>('#feed-fetch-mode-feed-1')
    expect(feedModeSelect).not.toBeNull()

    act(() => {
      setSelectValue(feedModeSelect!, 'schedule')
    })

    expect(feedModeSelect!.value).toBe('schedule')
    expect(pageText()).toContain('Unsaved schedule')
  })

  it('shows invalid cron validation before saving a feed schedule', () => {
    const view = renderPage()

    const feedModeSelect = view.querySelector<HTMLSelectElement>('#feed-fetch-mode-feed-1')
    expect(feedModeSelect).not.toBeNull()

    act(() => {
      setSelectValue(feedModeSelect!, 'schedule')
    })

    const cronInput = view.querySelector<HTMLInputElement>('#feed-schedule-cron-feed-1')
    expect(cronInput).not.toBeNull()

    act(() => {
      setInputValue(cronInput!, 'not a cron')
    })

    const saveButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.trim() === 'Save schedule',
    )
    expect(pageText()).toContain('Schedule must be a valid five-field cron expression.')
    expect(saveButton?.hasAttribute('disabled')).toBe(true)
  })

  it('protects unsaved feed schedule edits before opening the delete confirmation', () => {
    const view = renderPage()

    const feedModeSelect = view.querySelector<HTMLSelectElement>('#feed-fetch-mode-feed-1')
    expect(feedModeSelect).not.toBeNull()

    act(() => {
      setSelectValue(feedModeSelect!, 'schedule')
    })

    const feedRow = Array.from(view.querySelectorAll('div')).find((node) =>
      node.textContent?.includes('Vendor Advisories') && node.textContent?.includes('Refresh') && node.textContent?.includes('Delete'),
    )
    expect(feedRow).not.toBeNull()

    const deleteButton = Array.from(feedRow!.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Delete')
    expect(deleteButton).not.toBeNull()

    act(() => {
      deleteButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Discard unsaved changes?')
    expect(pageText()).toContain('You have unsaved feed changes. Leave without saving?')
    expect(pageText()).not.toContain('Delete feed?')

    const discardChangesButton = Array.from(document.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Discard changes'),
    )
    expect(discardChangesButton).not.toBeNull()

    act(() => {
      discardChangesButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Delete feed?')
    expect(pageText()).toContain('Vendor Advisories')

    const confirmButton = Array.from(document.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Delete feed')
    expect(confirmButton).not.toBeNull()

    act(() => {
      confirmButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(feedsPageDomMocks.deleteMutate).toHaveBeenCalledWith('feed-1')
  })

  it('treats a dirty create-feed draft as unsaved work before navigation', () => {
    const view = renderPage()

    const urlInput = view.querySelector<HTMLInputElement>('#feed-rss-url')
    expect(urlInput).not.toBeNull()

    act(() => {
      setInputValue(urlInput!, 'https://example.com/new-feed.xml')
    })

    routerMocks.blocker.state = 'blocked'
    rerenderPage()

    expect(pageText()).toContain('Discard unsaved changes?')
    expect(pageText()).toContain('You have unsaved feed changes. Leave without saving?')
  })

  it('confirms bulk enable actions before mutating filtered feeds', () => {
    const view = renderPage()
    feedsPageDomMocks.bulkSetEnabledResult = {
      enabled: true,
      attempted: 1,
      succeeded: 0,
      failed: 1,
      failedFeedNames: ['Orange Cyberdefense'],
    }

    const bulkEnableButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Enable Disabled (Filtered)'),
    )
    expect(bulkEnableButton).not.toBeNull()

    act(() => {
      bulkEnableButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Enable filtered feeds?')
    expect(pageText()).toContain('Orange Cyberdefense')

    const confirmButton = Array.from(document.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Enable feeds')
    expect(confirmButton).not.toBeNull()

    act(() => {
      confirmButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(feedsPageDomMocks.bulkSetEnabledMutate).toHaveBeenCalledWith({
      feeds: [expect.objectContaining({ id: 'feed-2', name: 'Orange Cyberdefense' })],
      enabled: true,
    })
    expect(pageText()).toContain('Enabled 0/1 feed. Failed: Orange Cyberdefense.')
  })

  it('reports failed feed names after a partial bulk refresh', () => {
    const view = renderPage()
    feedsPageDomMocks.bulkRefreshResult = {
      attempted: 2,
      succeeded: 1,
      failed: 1,
      failedFeedNames: ['Orange Cyberdefense'],
    }

    const bulkRefreshButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Refresh Filtered'),
    )
    expect(bulkRefreshButton).not.toBeNull()

    act(() => {
      bulkRefreshButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(feedsPageDomMocks.bulkRefreshMutate).toHaveBeenCalledWith([
      expect.objectContaining({ id: 'feed-1', name: 'Vendor Advisories' }),
      expect.objectContaining({ id: 'feed-2', name: 'Orange Cyberdefense' }),
    ])
    expect(pageText()).toContain('Refresh queued for 1/2 feeds. Failed: Orange Cyberdefense.')
  })

  it('reports failed feed names after a partial bulk delete', () => {
    const view = renderPage()
    feedsPageDomMocks.bulkDeleteResult = {
      attempted: 1,
      succeeded: 0,
      failed: 1,
      failedFeedNames: ['Orange Cyberdefense'],
    }

    const bulkDeleteButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Delete Disabled (Filtered)'),
    )
    expect(bulkDeleteButton).not.toBeNull()

    act(() => {
      bulkDeleteButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Delete filtered disabled feeds?')
    expect(pageText()).toContain('Orange Cyberdefense')

    const confirmButton = Array.from(document.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Delete feeds')
    expect(confirmButton).not.toBeNull()

    act(() => {
      confirmButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(feedsPageDomMocks.bulkDeleteMutate).toHaveBeenCalledWith([
      expect.objectContaining({ id: 'feed-2', name: 'Orange Cyberdefense' }),
    ])
    expect(pageText()).toContain('Deleted 0/1 feed. Failed: Orange Cyberdefense.')
  })

  it('surfaces unreadable feed warnings, filters broken feeds, and confirms bulk broken-feed deletion', () => {
    const view = renderPage()

    expect(pageText()).toContain('1 stored feed has unreadable encrypted URLs.')
    expect(pageText()).toContain('Delete Broken Feeds')

    const showBrokenButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Show Broken Feeds'),
    )
    expect(showBrokenButton).not.toBeNull()

    act(() => {
      showBrokenButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const statusFilter = view.querySelector<HTMLSelectElement>('#feed-status-filter')
    expect(statusFilter?.value).toBe('broken')
    expect(pageText()).toContain('Showing 1 of 2 feeds')
    expect(pageText()).toContain('Broken URL')

    const deleteBrokenButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Delete Broken (Filtered)'),
    )
    expect(deleteBrokenButton).not.toBeNull()

    act(() => {
      deleteBrokenButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Delete broken feeds?')
    expect(pageText()).toContain('Orange Cyberdefense')

    const confirmButton = Array.from(document.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Delete feeds')
    expect(confirmButton).not.toBeNull()

    act(() => {
      confirmButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(feedsPageDomMocks.bulkDeleteMutate).toHaveBeenCalledWith([
      expect.objectContaining({ id: 'feed-2', name: 'Orange Cyberdefense', has_unreadable_url: true }),
    ])
    expect(pageText()).toContain('Deleted broken 1/1 feed.')
  })

  it('shows an import preflight summary and requires confirmation before overwrite imports', async () => {
    const view = renderPage()
    const fileInput = view.querySelector<HTMLInputElement>('input[type="file"]')
    expect(fileInput).not.toBeNull()

    const importFile = new File(
      [
        JSON.stringify({
          feeds: [
            {
              name: 'Vendor Advisories Updated',
              url: 'https://example.com/vendor.xml',
              enabled: true,
              fetch_mode: 'interval',
              fetch_interval_seconds: 1800,
            },
            {
              name: 'New Feed',
              url: 'https://example.com/new.xml',
              enabled: true,
              fetch_mode: 'interval',
              fetch_interval_seconds: 1800,
            },
          ],
        }),
      ],
      'feeds.json',
      { type: 'application/json' },
    )

    Object.defineProperty(fileInput!, 'files', {
      configurable: true,
      value: [importFile],
    })

    await act(async () => {
      fileInput!.dispatchEvent(new Event('change', { bubbles: true }))
      await Promise.resolve()
    })

    expect(pageText()).toContain('Import preflight: 1 new, 0 overwrite, 1 skip')

    const overwriteCheckbox = Array.from(view.querySelectorAll('label'))
      .find((label) => label.textContent?.includes('Overwrite existing on import'))
      ?.querySelector<HTMLInputElement>('input[type="checkbox"]')
    expect(overwriteCheckbox).not.toBeNull()

    act(() => {
      setCheckboxValue(overwriteCheckbox!, true)
    })

    expect(pageText()).toContain('Import preflight: 1 new, 1 overwrite, 0 skip')

    const runImportButton = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Run Import')
    expect(runImportButton).not.toBeNull()

    act(() => {
      runImportButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Overwrite existing feeds from import?')
    expect(pageText()).toContain('Vendor Advisories')

    const confirmButton = Array.from(document.querySelectorAll('button')).find((button) =>
      button.textContent?.trim() === 'Run overwrite import',
    )
    expect(confirmButton).not.toBeNull()

    act(() => {
      confirmButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(feedsPageDomMocks.importMutate).toHaveBeenCalledTimes(1)
  })

  it('rejects oversized import files before reading them', async () => {
    const view = renderPage()
    const fileInput = view.querySelector<HTMLInputElement>('input[type="file"]')
    expect(fileInput).not.toBeNull()

    const importFile = new File(['{}'], 'large-feeds.json', { type: 'application/json' })
    const readSpy = vi.spyOn(importFile, 'text')
    Object.defineProperty(importFile, 'size', {
      configurable: true,
      value: 2_000_001,
    })
    Object.defineProperty(fileInput!, 'files', {
      configurable: true,
      value: [importFile],
    })

    await act(async () => {
      fileInput!.dispatchEvent(new Event('change', { bubbles: true }))
      await Promise.resolve()
    })

    expect(pageText()).toContain('Import file is too large. Maximum supported size is 2 MB.')
    expect(readSpy).not.toHaveBeenCalled()
    expect(feedsPageDomMocks.importMutate).not.toHaveBeenCalled()
  })
})
