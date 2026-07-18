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
  currentUser: {
    data: {
      id: 'admin-1',
      email: 'admin@example.com',
      role: 'admin',
      is_active: true,
      is_approved: true,
      approved_at: '2026-04-20T10:00:00Z',
      created_at: '2026-04-19T10:00:00Z',
      features: {
        ai_enabled: false,
        ai_configured: false,
        ai_summary_enabled: false,
        ai_relevance_enabled: false,
        ai_daily_brief_enabled: false,
      },
    },
    isLoading: false,
    isError: false,
    error: null,
  },
  usersData: [
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

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => usersPageDomMocks.queryClient,
  useQuery: () => ({
    data: usersPageDomMocks.usersData,
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

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useBlocker: routerMocks.useBlocker,
  }
})

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => usersPageDomMocks.currentUser,
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

function pageText() {
  return document.body.textContent ?? ''
}

function rerenderPage() {
  act(() => {
    root?.render(<UsersPage />)
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

afterEach(() => {
  act(() => {
    root?.unmount()
  })
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  usersPageDomMocks.usersData = [
    {
      id: 'user-1',
      email: 'analyst@example.com',
      role: 'analyst',
      is_active: true,
      is_approved: true,
      approved_at: '2026-04-20T10:00:00Z',
      created_at: '2026-04-19T10:00:00Z',
    },
  ]
  usersPageDomMocks.currentUser = {
    data: {
      id: 'admin-1',
      email: 'admin@example.com',
      role: 'admin',
      is_active: true,
      is_approved: true,
      approved_at: '2026-04-20T10:00:00Z',
      created_at: '2026-04-19T10:00:00Z',
      features: {
        ai_enabled: false,
        ai_configured: false,
        ai_summary_enabled: false,
        ai_relevance_enabled: false,
        ai_daily_brief_enabled: false,
      },
    },
    isLoading: false,
    isError: false,
    error: null,
  }
  usersPageDomMocks.mutate.mockReset()
  routerMocks.blocker.state = 'unblocked'
  routerMocks.blocker.proceed.mockReset()
  routerMocks.blocker.reset.mockReset()
})

describe('UsersPage DOM workflows', () => {
  it('progressively discloses mobile create and user management controls', () => {
    const view = renderPage()
    const createForm = view.querySelector<HTMLElement>('#create-user-form')
    const createToggle = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'New user')
    const userSettings = view.querySelector<HTMLElement>('#user-settings-user-1')
    const userManagement = view.querySelector<HTMLElement>('#user-management-user-1')
    const manageToggle = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Manage')

    expect(createForm?.className).toContain('hidden')
    expect(createToggle?.getAttribute('aria-expanded')).toBe('false')
    expect(userSettings?.className).toContain('hidden')
    expect(userManagement?.className).toContain('hidden')

    act(() => {
      createToggle?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      manageToggle?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(createForm?.className).toContain('block')
    expect(createToggle?.getAttribute('aria-expanded')).toBe('true')
    expect(userSettings?.className).toContain('flex')
    expect(userManagement?.className).toContain('block')
    expect(manageToggle?.getAttribute('aria-expanded')).toBe('true')
  })

  it('reviews the create-user request before posting it', () => {
    const view = renderPage()
    const createSection = view.querySelector('section')
    const createForm = createSection?.querySelector('form')

    const emailInput = createSection?.querySelector<HTMLInputElement>('#create-user-email')
    const passwordInput = createSection?.querySelector<HTMLInputElement>('#create-user-password')
    const roleSelect = createSection?.querySelector<HTMLSelectElement>('#create-user-role')

    expect(emailInput).not.toBeNull()
    expect(passwordInput).not.toBeNull()
    expect(roleSelect).not.toBeNull()
    expect(createForm).not.toBeNull()

    act(() => {
      setInputValue(emailInput!, ' admin@example.com ')
      setInputValue(passwordInput!, 'temporary-password')
      setSelectValue(roleSelect!, 'admin')
    })

    act(() => {
      createForm!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    })

    expect(pageText()).toContain('Create user account?')
    expect(pageText()).toContain('admin@example.com')
    expect(pageText()).toContain('This account will have full administrative access on first sign-in.')
    expect(pageText()).toContain('The account will skip the pending-approval state.')

    const confirmButton = Array.from(document.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Create user')
    expect(confirmButton).not.toBeNull()

    act(() => {
      confirmButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(usersPageDomMocks.mutate).toHaveBeenCalledWith({
      email: 'admin@example.com',
      password: 'temporary-password',
      role: 'admin',
      is_active: true,
      is_approved: true,
    })
  })

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

    expect(pageText()).toContain('Apply privileged user changes?')
    expect(pageText()).toContain('Role will change from analyst to admin.')

    const confirmButton = Array.from(document.querySelectorAll('button')).find((button) =>
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

  it('warns admins before they lock themselves out by changing their own role, active state, or approval', () => {
    usersPageDomMocks.currentUser = {
      data: {
        id: 'admin-1',
        email: 'admin@example.com',
        role: 'admin',
        is_active: true,
        is_approved: true,
        approved_at: '2026-04-20T10:00:00Z',
        created_at: '2026-04-19T10:00:00Z',
        features: {
          ai_enabled: false,
          ai_configured: false,
          ai_summary_enabled: false,
          ai_relevance_enabled: false,
          ai_daily_brief_enabled: false,
        },
      },
      isLoading: false,
      isError: false,
      error: null,
    }
    usersPageDomMocks.usersData = [
      {
        id: 'admin-1',
        email: 'admin@example.com',
        role: 'admin',
        is_active: true,
        is_approved: true,
        approved_at: '2026-04-20T10:00:00Z',
        created_at: '2026-04-19T10:00:00Z',
      },
    ]

    const view = renderPage()
    const roleSelect = view.querySelector<HTMLSelectElement>('#user-role-admin-1')
    const controls = roleSelect?.parentElement
    const rowCheckboxes = Array.from(controls?.querySelectorAll<HTMLInputElement>('input[type="checkbox"]') ?? [])
    const activeCheckbox = rowCheckboxes[0]
    const approvedCheckbox = rowCheckboxes[1]
    const reviewButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Review user changes'),
    )

    expect(roleSelect).not.toBeNull()
    expect(activeCheckbox).not.toBeNull()
    expect(approvedCheckbox).not.toBeNull()
    expect(reviewButton).not.toBeNull()

    act(() => {
      setSelectValue(roleSelect!, 'viewer')
      activeCheckbox!.click()
      approvedCheckbox!.click()
    })

    expect(pageText()).toContain('Self-access warning')
    expect(pageText()).toContain('You are removing your own admin access.')
    expect(pageText()).toContain('You are disabling your own account.')
    expect(pageText()).toContain('You are sending your own account back to pending approval.')

    act(() => {
      reviewButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Apply self-access changes?')
    expect(pageText()).toContain('Lockout risk')

    const confirmButton = Array.from(document.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Apply self-access changes'),
    )
    expect(confirmButton).not.toBeNull()

    act(() => {
      confirmButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(usersPageDomMocks.mutate).toHaveBeenCalledWith({
      id: 'admin-1',
      body: {
        role: 'viewer',
        is_active: false,
        is_approved: false,
      },
    })
  })

  it('keeps unsaved row settings when search filtering temporarily unmounts the user row', () => {
    const view = renderPage()

    const roleSelect = view.querySelector<HTMLSelectElement>('#user-role-user-1')
    const searchInput = view.querySelector<HTMLInputElement>('#user-directory-search')

    expect(roleSelect).not.toBeNull()
    expect(searchInput).not.toBeNull()

    act(() => {
      setSelectValue(roleSelect!, 'admin')
      setInputValue(searchInput!, 'missing-user')
    })

    expect(view.querySelector('#user-role-user-1')).toBeNull()

    act(() => {
      setInputValue(searchInput!, '')
    })

    expect(view.querySelector<HTMLSelectElement>('#user-role-user-1')?.value).toBe('admin')
  })

  it('warns before blocked navigation when user settings drafts are still dirty', () => {
    const view = renderPage()

    const roleSelect = view.querySelector<HTMLSelectElement>('#user-role-user-1')
    expect(roleSelect).not.toBeNull()

    act(() => {
      setSelectValue(roleSelect!, 'admin')
    })

    routerMocks.blocker.state = 'blocked'
    rerenderPage()

    expect(pageText()).toContain('Discard unsaved changes?')
    expect(pageText()).toContain('Discard unsaved user changes?')

    const cancelButton = Array.from(document.querySelectorAll('button'))
      .filter((button) => button.textContent?.trim() === 'Cancel')
      .at(-1)
    expect(cancelButton).not.toBeNull()

    act(() => {
      cancelButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(routerMocks.blocker.reset).toHaveBeenCalledTimes(1)

    routerMocks.blocker.state = 'blocked'
    rerenderPage()

    const discardButton = Array.from(document.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Discard changes'),
    )
    expect(discardButton).not.toBeNull()

    act(() => {
      discardButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(routerMocks.blocker.proceed).toHaveBeenCalledTimes(1)
  })

  it('treats a dirty create-user form as unsaved work before navigation', () => {
    const view = renderPage()

    const emailInput = view.querySelector<HTMLInputElement>('#create-user-email')
    expect(emailInput).not.toBeNull()

    act(() => {
      setInputValue(emailInput!, 'new-analyst@example.com')
    })

    routerMocks.blocker.state = 'blocked'
    rerenderPage()

    expect(pageText()).toContain('Discard unsaved changes?')
    expect(pageText()).toContain('Discard unsaved user changes?')
  })

  it('keeps password-reset drafts when filtering rows and treats them as unsaved changes', () => {
    const view = renderPage()

    const passwordInput = view.querySelector<HTMLInputElement>('#user-reset-password-user-1')
    const searchInput = view.querySelector<HTMLInputElement>('#user-directory-search')
    expect(passwordInput).not.toBeNull()
    expect(searchInput).not.toBeNull()

    act(() => {
      setInputValue(passwordInput!, 'temporary-password')
      setInputValue(searchInput!, 'missing-user')
    })

    expect(view.querySelector('#user-reset-password-user-1')).toBeNull()

    act(() => {
      setInputValue(searchInput!, '')
    })

    expect(view.querySelector<HTMLInputElement>('#user-reset-password-user-1')?.value).toBe('temporary-password')

    routerMocks.blocker.state = 'blocked'
    rerenderPage()

    expect(pageText()).toContain('Discard unsaved user changes?')
  })
})
