// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const protectedRouteMocks = vi.hoisted(() => ({
  currentUser: {
    data: undefined,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  },
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => protectedRouteMocks.currentUser,
}))

import { ProtectedRoute } from './ProtectedRoute'

let root: Root | null = null
let container: HTMLDivElement | null = null

afterEach(() => {
  act(() => root?.unmount())
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
})

describe('ProtectedRoute return routing', () => {
  it('does not preserve the workspace root as an explicit post-login deep link', () => {
    const view = renderRoute('/')

    expect(readLocation(view)).toEqual({
      pathname: '/login',
      search: '',
      hash: '',
      state: { authMessage: 'Sign in to continue.' },
    })
  })

  it('preserves a complete explicit deep link for post-login restoration', () => {
    const view = renderRoute('/investigations/case-1?tab=evidence#ioc')

    expect(readLocation(view)).toEqual({
      pathname: '/login',
      search: '',
      hash: '',
      state: {
        authMessage: 'Sign in to continue.',
        from: {
          pathname: '/investigations/case-1',
          search: '?tab=evidence',
          hash: '#ioc',
        },
      },
    })
  })
})

function renderRoute(initialEntry: string) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/login" element={<LocationProbe />} />
          <Route
            path="*"
            element={(
              <ProtectedRoute>
                <p>Protected content</p>
              </ProtectedRoute>
            )}
          />
        </Routes>
      </MemoryRouter>,
    )
  })
  return container
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{JSON.stringify(location)}</output>
}

function readLocation(view: HTMLElement) {
  const location = JSON.parse(
    view.querySelector('[data-testid="location"]')?.textContent ?? 'null',
  ) as { pathname: string; search: string; hash: string; state: unknown }
  return {
    pathname: location.pathname,
    search: location.search,
    hash: location.hash,
    state: location.state,
  }
}
