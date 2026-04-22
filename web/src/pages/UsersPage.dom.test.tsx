// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const usersPageDomMocks = vi.hoisted(() => ({
  queryClient: {
    invalidateQueries: vi.fn(),
  },
  mutate: vi.fn(),
}))

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => usersPageDomMocks.queryClient,
  useQuery: () => ({
    data: [
      {
        id: 'user-1',
        email: 'analyst@example.com',
        role: 'analyst',
        is_active: true,
        is_approved: true,
        approved_at: '2026-04-20T10:00:00Z',
        created_at: '2026-04-19T10:00:00Z',
      },
    ],
    isLoading: false,
    isError: false,
    error: null,
  }),
  useMutation: () => ({
      mutate: usersPageDomMocks.mutate,
      isPending: false,
      isError: false,
      error: null,
      variables: null,
    }),
}))

import { UsersPage } from './UsersPage'

let root: Root | null = null
let container: HTMLDivElement | null = null

function renderPage() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(<UsersPage />)
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
})

describe('UsersPage DOM workflows', () => {
  it('renders accessible admin controls and confirms a role change through the review dialog', () => {
    const view = renderPage()

    expect(view.querySelector('label[for="create-user-email"]')?.textContent).toContain('Email')
    expect(view.querySelector('label[for="create-user-password"]')?.textContent).toContain('Password')
    expect(view.querySelector('label[for="create-user-role"]')?.textContent).toContain('Role')
    expect(view.querySelector('label[for="user-directory-search"]')?.textContent).toContain('Search users')
    expect(view.querySelector('label[for="user-role-user-1"]')?.textContent).toContain('Role for analyst@example.com')
    expect(view.querySelector('label[for="user-reset-password-user-1"]')?.textContent).toContain(
      'New password for analyst@example.com',
    )

    const roleSelect = view.querySelector<HTMLSelectElement>('#user-role-user-1')
    const reviewButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Review user changes'),
    )

    expect(roleSelect).not.toBeNull()
    expect(reviewButton).not.toBeNull()

    act(() => {
      roleSelect!.value = 'admin'
      roleSelect!.dispatchEvent(new Event('change', { bubbles: true }))
    })

    act(() => {
      reviewButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(view.textContent).toContain('Apply privileged user changes?')
    expect(view.textContent).toContain('Role will change from analyst to admin.')

    const confirmButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Apply user changes'),
    )
    expect(confirmButton).not.toBeNull()

    act(() => {
      confirmButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(usersPageDomMocks.mutate).toHaveBeenCalledWith({
      id: 'user-1',
      body: { role: 'admin' },
    })
  })
})
