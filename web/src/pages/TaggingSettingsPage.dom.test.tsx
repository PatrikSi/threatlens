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
  useMutation: (options: { mutationFn?: unknown }) => {
    const source = String(options?.mutationFn ?? '')
    if (source.includes('/tagging/rules/preview')) {
      return taggingMutationResult(taggingPageDomMocks.previewRuleMutate)
    }
    if (source.includes('/tagging/reapply')) {
      return taggingMutationResult(taggingPageDomMocks.reapplyMutate)
    }
    if (source.includes('/tagging/rules/${') && source.includes("DELETE")) {
      return taggingMutationResult(taggingPageDomMocks.deleteRuleMutate)
    }
    if (source.includes('/tagging/rules')) {
      return taggingMutationResult(taggingPageDomMocks.saveRuleMutate)
    }
    return taggingMutationResult(taggingPageDomMocks.saveSettingsMutate)
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
})

describe('TaggingSettingsPage DOM workflows', () => {
  it('loads an existing rule, preserves toggle semantics, protects unsaved changes, and confirms deletion', () => {
    const view = renderPage()

    const savedRuleButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('VPN disclosures'),
    )
    expect(savedRuleButton).not.toBeNull()

    act(() => {
      savedRuleButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

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

    const deleteButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Delete rule'),
    )
    expect(deleteButton).not.toBeNull()

    act(() => {
      deleteButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(view.textContent).toContain('Delete tagging rule?')
    expect(view.textContent).toContain('VPN disclosures')

    const confirmDeleteButton = Array.from(view.querySelectorAll('button'))
      .filter((button) => button.textContent?.includes('Delete rule'))
      .at(-1)
    expect(confirmDeleteButton).not.toBeNull()

    act(() => {
      confirmDeleteButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(taggingPageDomMocks.deleteRuleMutate).toHaveBeenCalledWith('rule-1')

    const nameInput = view.querySelector<HTMLInputElement>('#tagging-rule-name')
    expect(nameInput).not.toBeNull()

    act(() => {
      setInputValue(nameInput!, 'Changed rule name')
    })

    const newRuleButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('New rule'),
    )
    expect(newRuleButton).not.toBeNull()

    act(() => {
      newRuleButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(view.textContent).toContain('Discard unsaved changes?')
    expect(view.textContent).toContain('Discard unsaved tagging changes?')

    const discardChangesButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Discard changes'),
    )
    expect(discardChangesButton).not.toBeNull()

    act(() => {
      discardChangesButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(view.querySelector<HTMLInputElement>('#tagging-rule-name')?.value).toBe('')
  })
})
