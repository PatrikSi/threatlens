// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const landingMocks = vi.hoisted(() => ({
  isLoading: false,
  landingPath: '/feeds',
}))

vi.mock('../workspace/useWorkspace', () => ({
  useWorkspace: () => ({
    isLoading: landingMocks.isLoading,
    model: { landingPath: landingMocks.landingPath },
  }),
}))

import { WorkspaceLandingRedirect } from './WorkspaceLandingRedirect'

let root: Root | null = null
let container: HTMLDivElement | null = null

afterEach(() => {
  act(() => root?.unmount())
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  landingMocks.isLoading = false
  landingMocks.landingPath = '/feeds'
})

describe('WorkspaceLandingRedirect', () => {
  it('navigates to the resolved trusted fallback route', () => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => {
      root?.render(
        <MemoryRouter initialEntries={['/start']}>
          <Routes>
            <Route path="start" element={<WorkspaceLandingRedirect />} />
            <Route path="feeds" element={<p>Feeds landing</p>} />
          </Routes>
        </MemoryRouter>,
      )
    })

    expect(container.textContent).toContain('Feeds landing')
  })

  it('does not redirect before the effective workspace is resolved', () => {
    landingMocks.isLoading = true
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => {
      root?.render(
        <MemoryRouter initialEntries={['/start']}>
          <Routes>
            <Route path="start" element={<WorkspaceLandingRedirect />} />
            <Route path="feeds" element={<p>Feeds landing</p>} />
          </Routes>
        </MemoryRouter>,
      )
    })

    expect(container.textContent).toContain('Resolving your workspace')
    expect(container.textContent).not.toContain('Feeds landing')
  })
})
