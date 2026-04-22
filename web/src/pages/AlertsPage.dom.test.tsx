// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const alertsPageDomMocks = vi.hoisted(() => ({
  confirmDiscard: vi.fn(() => true),
  queryClient: {
    invalidateQueries: vi.fn(),
  },
  saveMutate: vi.fn(),
  updateMutate: vi.fn(),
  deleteMutate: vi.fn(),
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
  useMutation: (options: { mutationFn?: unknown }) => {
    const source = String(options?.mutationFn ?? '')
    if (source.includes('/alerts/${id}') && source.includes("DELETE")) {
      return alertMutationResult(alertsPageDomMocks.deleteMutate)
    }
    if (source.includes('/alerts/${payload.id}') && source.includes("PATCH")) {
      return alertMutationResult(alertsPageDomMocks.updateMutate)
    }
    return alertMutationResult(alertsPageDomMocks.saveMutate)
  },
}))

vi.mock('../hooks/useUnsavedChangesWarning', () => ({
  useUnsavedChangesWarning: () => alertsPageDomMocks.confirmDiscard,
}))

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
})

describe('AlertsPage DOM workflows', () => {
  it('loads alert edits, toggles alert state, and confirms deletion', () => {
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

    expect(view.textContent).toContain('Delete alert interest?')
    expect(view.textContent).toContain('VPN advisories')

    const confirmDeleteButton = Array.from(view.querySelectorAll('button'))
      .filter((button) => button.textContent?.includes('Delete alert'))
      .at(-1)
    expect(confirmDeleteButton).not.toBeNull()

    act(() => {
      confirmDeleteButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(alertsPageDomMocks.deleteMutate).toHaveBeenCalledWith('alert-1')
  })
})
