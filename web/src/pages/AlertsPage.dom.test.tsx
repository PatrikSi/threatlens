// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const alertsPageDomMocks = vi.hoisted(() => ({
  queryClient: {
    invalidateQueries: vi.fn(),
  },
  saveMutate: vi.fn(),
  updateMutate: vi.fn(),
  deleteMutate: vi.fn(),
  deleteShouldFail: false,
  previewItems: [] as unknown[],
}))

const routerMocks = vi.hoisted(() => ({
  useBlocker: vi.fn(() => ({
    state: 'unblocked' as const,
    proceed: vi.fn(),
    reset: vi.fn(),
  })),
}))

function alertMutationResult(mutate: ReturnType<typeof vi.fn>) {
  return {
    mutate,
    isPending: false,
    isError: false,
    error: null,
  }
}

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => alertsPageDomMocks.queryClient,
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
              items: alertsPageDomMocks.previewItems,
              total: alertsPageDomMocks.previewItems.length,
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
  useMutation: (options: {
    mutationKey?: unknown
    onSuccess?: (result: unknown, variables: unknown) => void
    onError?: (error: unknown, variables: unknown) => void
  }) => {
    const mutationKey = Array.isArray(options?.mutationKey) ? options.mutationKey.join(':') : String(options?.mutationKey ?? '')
    if (mutationKey === 'alerts:delete') {
      return alertMutationResult(
        vi.fn((alertId: string) => {
          alertsPageDomMocks.deleteMutate(alertId)
          if (alertsPageDomMocks.deleteShouldFail) {
            options.onError?.(new Error('Alert deletion failed.'), alertId)
            return
          }
          options.onSuccess?.(undefined, alertId)
        }),
      )
    }
    if (mutationKey === 'alerts:update') {
      return alertMutationResult(alertsPageDomMocks.updateMutate)
    }
    return alertMutationResult(alertsPageDomMocks.saveMutate)
  },
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useBlocker: routerMocks.useBlocker,
  }
})

import { AlertsPage } from './AlertsPage'

let root: Root | null = null
let container: HTMLDivElement | null = null

function renderPage() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(<AlertsPage />)
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

function setTextAreaValue(input: HTMLTextAreaElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')
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
  alertsPageDomMocks.saveMutate.mockReset()
  alertsPageDomMocks.updateMutate.mockReset()
  alertsPageDomMocks.deleteMutate.mockReset()
  alertsPageDomMocks.deleteShouldFail = false
  alertsPageDomMocks.previewItems = []
})

describe('AlertsPage DOM workflows', () => {
  it('keeps the delete target open and renders destructive mutation failures', () => {
    alertsPageDomMocks.deleteShouldFail = true
    renderPage()
    const deleteButton = Array.from(document.querySelectorAll('button')).find((button) =>
      button.textContent?.trim() === 'Delete',
    )

    act(() => {
      deleteButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    const confirmDeleteButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Delete alert',
    )
    act(() => {
      confirmDeleteButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(document.body.textContent).toContain('Delete alert interest?')
    expect(document.body.textContent).toContain('VPN advisories')
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('Alert deletion failed.')
  })

  it('disables empty alert submissions and renders preview summaries as text', () => {
    alertsPageDomMocks.previewItems = [
      {
        id: 'item-1',
        title: 'Ransomware report',
        feed_name: 'Security Feed',
        first_seen_at: '2026-04-21T10:00:00Z',
        summary: '<p><em><strong>Introduction</strong></em></p>&#xd;<a href="https://example.com">Read more</a>',
        matches: [{ category: 'malware', matched_keywords: ['ransomware'] }],
      },
    ]
    const view = renderPage()
    const addButton = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.includes('Add Interest'))
    const nameInput = view.querySelector<HTMLInputElement>('#alert-interest-name')
    const keywordsInput = view.querySelector<HTMLTextAreaElement>('#alert-interest-keywords')

    expect(addButton).not.toBeNull()
    expect(addButton?.hasAttribute('disabled')).toBe(true)
    expect(pageText()).not.toContain('Enter an interest name.')

    act(() => {
      setInputValue(nameInput!, 'Agent2 Preview Only')
    })

    expect(pageText()).toContain('Enter at least one keyword.')

    act(() => {
      setTextAreaValue(keywordsInput!, 'ransomware')
    })

    expect(pageText()).toContain('Introduction Read more')
    expect(pageText()).not.toContain('<p>')
    expect(pageText()).not.toContain('&#xd;')
  })

  it('loads alert edits, protects unsaved changes, toggles alert state, and confirms deletion', () => {
    const view = renderPage()

    const editButton = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.includes('Edit'))
    const disableButton = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.includes('Disable'))
    const deleteButton = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.includes('Delete'))

    expect(editButton).not.toBeNull()
    expect(disableButton).not.toBeNull()
    expect(deleteButton).not.toBeNull()

    act(() => {
      editButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(view.querySelector<HTMLInputElement>('#alert-interest-name')?.value).toBe('VPN advisories')
    expect(view.querySelector<HTMLTextAreaElement>('#alert-interest-keywords')?.value).toBe('vpn, gateway')

    act(() => {
      disableButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(alertsPageDomMocks.updateMutate).toHaveBeenCalledWith({
      id: 'alert-1',
      body: { enabled: false },
    })

    act(() => {
      deleteButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Delete alert interest?')
    expect(pageText()).toContain('VPN advisories')

    const confirmDeleteButton = Array.from(document.querySelectorAll('button'))
      .filter((button) => button.textContent?.includes('Delete alert'))
      .at(-1)
    expect(confirmDeleteButton).not.toBeNull()

    act(() => {
      confirmDeleteButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(alertsPageDomMocks.deleteMutate).toHaveBeenCalledWith('alert-1')
    expect(view.querySelector<HTMLInputElement>('#alert-interest-name')?.value).toBe('')
  })

  it('discards unsaved alert edits before opening the delete confirmation', () => {
    const view = renderPage()

    const editButton = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.includes('Edit'))
    expect(editButton).not.toBeNull()

    act(() => {
      editButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const nameInput = view.querySelector<HTMLInputElement>('#alert-interest-name')
    expect(nameInput).not.toBeNull()

    act(() => {
      setInputValue(nameInput!, 'Changed alert name')
    })

    const deleteButton = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.includes('Delete'))
    expect(deleteButton).not.toBeNull()

    act(() => {
      deleteButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Discard unsaved changes?')
    expect(pageText()).toContain('Discard unsaved alert changes?')
    expect(pageText()).not.toContain('Delete alert interest?')

    const discardChangesButton = Array.from(document.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Discard changes'),
    )
    expect(discardChangesButton).not.toBeNull()

    act(() => {
      discardChangesButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Delete alert interest?')
    expect(pageText()).toContain('VPN advisories')

    const confirmDeleteButton = Array.from(document.querySelectorAll('button'))
      .filter((button) => button.textContent?.includes('Delete alert'))
      .at(-1)
    expect(confirmDeleteButton).not.toBeNull()

    act(() => {
      confirmDeleteButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(alertsPageDomMocks.deleteMutate).toHaveBeenCalledWith('alert-1')
  })
})
