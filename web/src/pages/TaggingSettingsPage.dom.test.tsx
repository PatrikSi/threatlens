// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const taggingPageDomMocks = vi.hoisted(() => ({
  queryClient: {
    invalidateQueries: vi.fn(),
  },
  bundleData: {
    settings: {
      id: 'settings-1',
      enabled_categories: ['vulnerability', 'apt_campaign'],
      min_auto_tag_confidence: 0.45,
      secondary_tag_limit: 2,
      created_at: '2026-04-20T10:00:00Z',
      updated_at: '2026-04-21T10:00:00Z',
    },
    rules: [
      {
        id: 'rule-1',
        name: 'VPN disclosures',
        tag_name: 'vpn',
        enabled: true,
        match_type: 'contains',
        pattern: 'vpn',
        case_sensitive: false,
        applies_to: ['title', 'summary'],
        required_categories: [],
        feed_scope: 'all',
        feed_ids: [],
        min_classification_confidence: null,
        created_at: '2026-04-20T10:00:00Z',
        updated_at: '2026-04-21T10:00:00Z',
      },
    ],
  },
  feedsData: [
    {
      id: 'feed-1',
      name: 'Vendor advisories',
      url: 'https://example.com/feed.xml',
      description: null,
      site_url: null,
      language: null,
      enabled: true,
      fetch_mode: 'schedule',
      fetch_interval_seconds: 1800,
      schedule_cron: '0 * * * *',
      etag: null,
      last_modified: null,
      last_fetch_at: null,
      last_success_at: null,
      error_count: 0,
      last_error: null,
      has_unreadable_url: false,
      created_at: '2026-04-20T10:00:00Z',
    },
  ],
  saveSettingsMutate: vi.fn(),
  saveRuleMutate: vi.fn(),
  deleteRuleMutate: vi.fn(),
  previewRuleMutate: vi.fn(),
  reapplyMutate: vi.fn(),
}))

const routerMocks = vi.hoisted(() => ({
  useBlocker: vi.fn(() => ({
    state: 'unblocked' as const,
    proceed: vi.fn(),
    reset: vi.fn(),
  })),
}))

function taggingMutationResult(mutate: ReturnType<typeof vi.fn>) {
  return {
    mutate,
    isPending: false,
    isError: false,
    error: null,
  }
}

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => taggingPageDomMocks.queryClient,
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => {
    const [scope] = queryKey
    const baseResult = {
      isLoading: false,
      isError: false,
      error: null,
    }

    if (scope === 'tagging') {
      return {
        ...baseResult,
        data: taggingPageDomMocks.bundleData,
      }
    }

    if (scope === 'feeds') {
      return {
        ...baseResult,
        data: taggingPageDomMocks.feedsData,
      }
    }

    return {
      ...baseResult,
      data: undefined,
    }
  },
  useMutation: (options: { mutationKey?: unknown; onSuccess?: (result: unknown) => void }) => {
    const mutationKey = Array.isArray(options?.mutationKey) ? options.mutationKey.join(':') : String(options?.mutationKey ?? '')
    if (mutationKey === 'tagging:rules:preview') {
      return taggingMutationResult(
        vi.fn((payload: unknown) => {
          taggingPageDomMocks.previewRuleMutate(payload)
          options.onSuccess?.({
            total: 1,
            items: [
              {
                id: 'item-1',
                feed_id: 'feed-1',
                feed_name: 'Vendor advisories',
                title: 'VPN bulletin',
                summary: 'Recent VPN advisory.',
                url: 'https://example.com/item-1',
                language: 'en',
                source_published_at: null,
                first_seen_at: '2026-04-21T09:00:00Z',
                content_hash: null,
                content_length: null,
                created_at: '2026-04-21T09:00:00Z',
                updated_at: '2026-04-21T09:00:00Z',
                matched_sections: ['title'],
                current_tags: ['existing-tag'],
                classification: 'vulnerability',
              },
            ],
          })
        }),
      )
    }
    if (mutationKey === 'tagging:reapply') {
      return taggingMutationResult(
        vi.fn((payload: unknown) => {
          taggingPageDomMocks.reapplyMutate(payload)
          options.onSuccess?.({ task_id: 'task-retag-1' })
        }),
      )
    }
    if (mutationKey === 'tagging:rules:delete') {
      return taggingMutationResult(taggingPageDomMocks.deleteRuleMutate)
    }
    if (mutationKey === 'tagging:rules:save') {
      return taggingMutationResult(taggingPageDomMocks.saveRuleMutate)
    }
    return taggingMutationResult(
      vi.fn(() => {
        taggingPageDomMocks.saveSettingsMutate()
        options.onSuccess?.({})
      }),
    )
  },
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useBlocker: routerMocks.useBlocker,
  }
})

import { TaggingSettingsPage } from './TaggingSettingsPage'

let root: Root | null = null
let container: HTMLDivElement | null = null

function renderPage() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(<TaggingSettingsPage />)
  })
  return container
}

function pageText() {
  return document.body.textContent ?? ''
}

function setInputValue(input: HTMLInputElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

afterEach(() => {
  act(() => {
    root?.unmount()
  })
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  taggingPageDomMocks.previewRuleMutate.mockReset()
  taggingPageDomMocks.deleteRuleMutate.mockReset()
  taggingPageDomMocks.reapplyMutate.mockReset()
  taggingPageDomMocks.saveSettingsMutate.mockReset()
})

describe('TaggingSettingsPage DOM workflows', () => {
  it('loads an existing rule, exposes selection semantics, and preserves toggle state', () => {
    const view = renderPage()

    const savedRuleButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('VPN disclosures'),
    )
    expect(savedRuleButton).not.toBeNull()
    expect(savedRuleButton?.getAttribute('aria-pressed')).toBe('false')

    act(() => {
      savedRuleButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(savedRuleButton?.getAttribute('aria-pressed')).toBe('true')
    expect(view.querySelector<HTMLInputElement>('#tagging-rule-name')?.value).toBe('VPN disclosures')
    expect(view.querySelector<HTMLInputElement>('#tagging-rule-tag-name')?.value).toBe('vpn')
    expect(
      Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Any feed')?.getAttribute('aria-pressed'),
    ).toBe('true')
    expect(
      Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Title')?.getAttribute('aria-pressed'),
    ).toBe('true')
    expect(
      Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Vulnerability')?.getAttribute('aria-pressed'),
    ).toBe('true')

    const previewButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Preview rule'),
    )
    expect(previewButton).not.toBeNull()

    act(() => {
      previewButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(taggingPageDomMocks.previewRuleMutate).toHaveBeenCalledWith({
      name: 'VPN disclosures',
      tag_name: 'vpn',
      enabled: true,
      match_type: 'contains',
      pattern: 'vpn',
      case_sensitive: false,
      applies_to: ['title', 'summary'],
      required_categories: [],
      feed_scope: 'all',
      feed_ids: [],
      min_classification_confidence: null,
    })
  })

  it('protects unsaved rule changes before opening the delete confirmation', () => {
    const view = renderPage()

    const savedRuleButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('VPN disclosures'),
    )
    expect(savedRuleButton).not.toBeNull()

    act(() => {
      savedRuleButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const nameInput = view.querySelector<HTMLInputElement>('#tagging-rule-name')
    expect(nameInput).not.toBeNull()

    act(() => {
      setInputValue(nameInput!, 'Changed rule name')
    })

    const deleteButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Delete rule'),
    )
    expect(deleteButton).not.toBeNull()

    act(() => {
      deleteButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Discard unsaved changes?')
    expect(pageText()).toContain('Discard unsaved tagging changes?')
    expect(pageText()).not.toContain('Delete tagging rule?')

    const discardChangesButton = Array.from(document.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Discard changes'),
    )
    expect(discardChangesButton).not.toBeNull()

    act(() => {
      discardChangesButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Delete tagging rule?')
    expect(pageText()).toContain('VPN disclosures')

    const confirmDeleteButton = Array.from(document.querySelectorAll('button'))
      .filter((button) => button.textContent?.includes('Delete rule'))
      .at(-1)
    expect(confirmDeleteButton).not.toBeNull()

    act(() => {
      confirmDeleteButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(taggingPageDomMocks.deleteRuleMutate).toHaveBeenCalledWith('rule-1')
  })

  it('requires an explicit confirmation before queueing a full retagging pass', () => {
    const view = renderPage()

    const daysInput = view.querySelector<HTMLInputElement>('#tagging-reapply-days')
    const limitInput = view.querySelector<HTMLInputElement>('#tagging-reapply-limit')
    expect(daysInput).not.toBeNull()
    expect(limitInput).not.toBeNull()

    act(() => {
      setInputValue(daysInput!, '14')
      setInputValue(limitInput!, '0')
    })

    const queueButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Queue retagging'),
    )
    expect(queueButton).not.toBeNull()

    act(() => {
      queueButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Queue full retagging pass?')
    expect(pageText()).toContain('all items in the selected time window')

    const confirmButton = Array.from(document.querySelectorAll('button')).find((button) =>
      button.textContent?.trim() === 'Queue full retagging',
    )
    expect(confirmButton).not.toBeNull()

    act(() => {
      confirmButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(taggingPageDomMocks.reapplyMutate).toHaveBeenCalledWith({ days: 14, limit: 0 })
  })

  it('announces tagging save, preview, and reapply feedback through a polite live region', () => {
    const view = renderPage()

    const saveDefaultsButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Save defaults'),
    )
    expect(saveDefaultsButton).not.toBeNull()

    act(() => {
      saveDefaultsButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    let notice = view.querySelector('[role="status"][aria-live="polite"][aria-atomic="true"]')
    expect(notice).not.toBeNull()
    expect(notice?.textContent).toContain('Tagging settings updated.')

    const savedRuleButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('VPN disclosures'),
    )
    expect(savedRuleButton).not.toBeNull()

    act(() => {
      savedRuleButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const previewButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Preview rule'),
    )
    expect(previewButton).not.toBeNull()

    act(() => {
      previewButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    notice = view.querySelector('[role="status"][aria-live="polite"][aria-atomic="true"]')
    expect(notice?.textContent).toContain('Preview loaded.')

    const queueButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Queue retagging'),
    )
    expect(queueButton).not.toBeNull()

    act(() => {
      queueButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const confirmButton = Array.from(document.querySelectorAll('button')).find((button) =>
      button.textContent?.trim() === 'Queue full retagging',
    )
    expect(confirmButton).not.toBeNull()

    act(() => {
      confirmButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    notice = view.querySelector('[role="status"][aria-live="polite"][aria-atomic="true"]')
    expect(notice?.textContent).toContain('Retagging queued. Task ID: task-retag-1')
  })
})
