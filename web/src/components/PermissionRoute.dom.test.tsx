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
})

describe('PermissionRoute', () => {
  it('allows a non-admin principal with the canonical permission', () => {
    const view = renderRoute(['read:users'])

    expect(view.textContent).toContain('Authorized content')
    expect(view.textContent).not.toContain('Permission required')
  })

  it('fails closed when the canonical permission is absent', () => {
    permissionRouteMocks.currentUser.data.access.permissions = ['read:items']

    const view = renderRoute(['read:users'])

    expect(view.textContent).toContain('Permission required')
    expect(view.textContent).not.toContain('Authorized content')
  })
})

function renderRoute(permissions: readonly string[]) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(
      <MemoryRouter>
        <PermissionRoute permissions={permissions}>
          <p>Authorized content</p>
        </PermissionRoute>
      </MemoryRouter>,
    )
  })
  return container
}
