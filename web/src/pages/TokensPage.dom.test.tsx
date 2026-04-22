// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const tokensPageDomMocks = vi.hoisted(() => ({
  currentUser: {
    data: {
      id: 'admin-1',
      email: 'admin@example.com',
      role: 'admin',
      is_active: true,
      is_approved: true,
      approved_at: '2026-04-21T10:00:00Z',
      created_at: '2026-04-20T10:00:00Z',
      features: {
        ai_enabled: true,
        ai_configured: true,
        ai_summary_enabled: true,
        ai_relevance_enabled: true,
        ai_daily_brief_enabled: true,
      },
    },
    isLoading: false,
    isError: false,
    error: null,
  },
  queryClient: {
    invalidateQueries: vi.fn(),
  },
  createMutate: vi.fn(),
  revokeMutate: vi.fn(),
}))

function tokenMutationResult(mutate: ReturnType<typeof vi.fn>) {
  return {
    mutate,
    isPending: false,
    isError: false,
    error: null,
  }
}

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => tokensPageDomMocks.queryClient,
  useQuery: () => ({
    data: [
      {
        id: 'token-1',
        user_id: 'admin-1',
        name: 'Legacy automation',
        token_prefix: 'tl_legacy',
        scopes: [],
        last_used_at: null,
        expires_at: null,
        revoked_at: null,
        created_at: '2026-04-20T10:00:00Z',
      },
    ],
    isLoading: false,
    isError: false,
    error: null,
  }),
  useMutation: (options: { mutationFn?: unknown }) => {
    const source = String(options?.mutationFn ?? '')
    if (source.includes('/tokens/${tokenId}') && source.includes("DELETE")) {
      return tokenMutationResult(tokensPageDomMocks.revokeMutate)
    }
    return tokenMutationResult(tokensPageDomMocks.createMutate)
  },
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => tokensPageDomMocks.currentUser,
}))

import { TokensPage } from './TokensPage'

let root: Root | null = null
let container: HTMLDivElement | null = null

function renderPage() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(<TokensPage />)
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
  tokensPageDomMocks.createMutate.mockReset()
  tokensPageDomMocks.revokeMutate.mockReset()
})

describe('TokensPage DOM workflows', () => {
  it('renders explicit token controls and confirms revocation through the dialog', () => {
    const view = renderPage()

    expect(view.querySelector('label[for="token-name"]')?.textContent).toContain('Name')
    expect(view.querySelector('label[for="token-current-password"]')?.textContent).toContain('Current Password')
    expect(view.textContent).toContain('Scoped API routes now reject unscoped tokens')

    const revokeButton = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.includes('Revoke'))
    expect(revokeButton).not.toBeNull()

    act(() => {
      revokeButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(view.textContent).toContain('Revoke API token?')
    expect(view.textContent).toContain('Legacy automation')

    const confirmRevokeButton = Array.from(view.querySelectorAll('button'))
      .filter((button) => button.textContent?.includes('Revoke token'))
      .at(-1)
    expect(confirmRevokeButton).not.toBeNull()

    act(() => {
      confirmRevokeButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(tokensPageDomMocks.revokeMutate).toHaveBeenCalledWith('token-1')
  })
})
