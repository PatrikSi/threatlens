// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const permissionRouteMocks = vi.hoisted(() => ({
  currentUser: {
    data: {
      id: 'user-1',
      role: 'analyst',
      access: { permissions: ['read:users'] as string[] },
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  },
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => permissionRouteMocks.currentUser,
}))

import { PermissionRoute } from './PermissionRoute'

let root: Root | null = null
let container: HTMLDivElement | null = null

afterEach(() => {
  act(() => root?.unmount())
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  permissionRouteMocks.currentUser.data.access.permissions = ['read:users']
  permissionRouteMocks.currentUser.data.role = 'analyst'
})

describe('PermissionRoute', () => {
  it('allows a non-admin principal with the canonical permission', () => {
    const view = renderRoute(['read:users'])

    expect(view.textContent).toContain('Authorized content')
    expect(view.textContent).not.toContain('Permission required')
  })

  it('fails closed when the canonical permission is absent', () => {
    permissionRouteMocks.currentUser.data.access.permissions = ['read:items']

    const view = renderRoute(['read:users'], undefined, '/feeds')

    expect(view.textContent).toContain('Permission required')
    expect(view.textContent).not.toContain('Authorized content')
    expect(view.querySelector('a[href="/settings"]')?.textContent).toContain('Open settings')
    expect(view.querySelector('a[href="/start"]')?.textContent).toContain('Open workspace start')
    expect(view.querySelector('a[href="/"]')).toBeNull()
  })

  it('explains sealed base-role requirements separately from permissions', () => {
    const view = renderRoute(['read:users'], ['admin'], '/settings/identity')

    expect(view.textContent).toContain('Base role required')
    expect(view.textContent).toContain('Administrator base role')
    expect(view.textContent).toContain('Additive custom roles do not unlock')
    expect(view.querySelector('a[href="/settings"]')?.textContent).toContain('Back to settings')
    expect(view.querySelector('a[href="/start"]')?.textContent).toContain('Open workspace start')
    expect(view.textContent).not.toContain('Authorized content')
  })
})

function renderRoute(
  permissions: readonly string[],
  roles?: ReadonlyArray<'admin' | 'analyst' | 'viewer'>,
  pathname = '/',
) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(
      <MemoryRouter initialEntries={[pathname]}>
        <PermissionRoute permissions={permissions} roles={roles}>
          <p>Authorized content</p>
        </PermissionRoute>
      </MemoryRouter>,
    )
  })
  return container
}
