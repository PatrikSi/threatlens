// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const statsPageDomMocks = vi.hoisted(() => ({
  feeds: [
    { id: 'feed-1', name: 'Feed One' },
    { id: 'feed-2', name: 'Feed Two' },
  ],
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => ({
    data: queryKey[0] === 'feeds' ? statsPageDomMocks.feeds : undefined,
    isLoading: false,
    isError: false,
    error: null,
  }),
}))

import { StatsPage } from './StatsPage'

let root: Root | null = null
let container: HTMLDivElement | null = null

function renderPage() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(<StatsPage />)
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
  statsPageDomMocks.feeds = [
    { id: 'feed-1', name: 'Feed One' },
    { id: 'feed-2', name: 'Feed Two' },
  ]
})

describe('StatsPage filters', () => {
  it('labels the time window and removes deleted feeds from the selection', () => {
    const view = renderPage()
    const feedTwoCheckbox = Array.from(view.querySelectorAll('label'))
      .find((label) => label.textContent?.includes('Feed Two'))
      ?.querySelector<HTMLInputElement>('input')

    expect(view.querySelector('label[for="stats-time-window"]')?.textContent).toContain('Statistics time window')

    act(() => {
      feedTwoCheckbox?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(view.textContent).toContain('1 selected')

    statsPageDomMocks.feeds = [{ id: 'feed-1', name: 'Feed One' }]
    act(() => {
      root?.render(<StatsPage />)
    })

    expect(view.textContent).toContain('All feeds selected')
  })
})
