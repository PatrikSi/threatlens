// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { ArticlePreviewDrawer } from './DashboardPageComponents'
;(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

it('requires resource opt-in for each article and allows returning to blocked resources', () => {
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  const render = (itemId: string) =>
    act(() =>
      root.render(
        <ArticlePreviewDrawer
          key={itemId}
          preview={{
            itemId,
            url: `https://example.com/${itemId}`,
            title: 'Article',
            sourceLabel: 'Feed',
          }}
          width={700}
          minWidth={400}
          maxWidth={1000}
          isResizing={false}
          onResizeStart={vi.fn()}
          onResizeBy={vi.fn()}
          onClose={vi.fn()}
        />,
      ),
    )
  try {
    render('one')
    const checkbox = () =>
      container.querySelector<HTMLInputElement>('input[type="checkbox"]')!
    const source = () => container.querySelector('iframe')!.getAttribute('src')!
    expect(source()).not.toContain('external_resources')
    act(() => checkbox().click())
    expect(source()).toContain('external_resources=true')
    expect(
      container.querySelector('iframe')!.getAttribute('sandbox'),
    ).not.toContain('allow-scripts')
    act(() => checkbox().click())
    expect(source()).not.toContain('external_resources')
    act(() => checkbox().click())
    render('two')
    expect(checkbox().checked).toBe(false)
    expect(source()).toContain('/items/two/article-preview')
    expect(source()).not.toContain('external_resources')
  } finally {
    act(() => root.unmount())
    container.remove()
  }
})

it('uses saved consent, preserves explicit preview overrides, and restores the default for another article', () => {
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  const render = (itemId: string, enabled: boolean) =>
    act(() =>
      root.render(
        <ArticlePreviewDrawer
          key={itemId}
          defaultExternalResources={enabled}
          preview={{
            itemId,
            url: `https://example.com/${itemId}`,
            title: 'Article',
            sourceLabel: 'Feed',
          }}
          width={700}
          minWidth={400}
          maxWidth={1000}
          isResizing={false}
          onResizeStart={vi.fn()}
          onResizeBy={vi.fn()}
          onClose={vi.fn()}
        />,
      ),
    )
  try {
    render('one', false)
    const checkbox = () =>
      container.querySelector<HTMLInputElement>('input[type="checkbox"]')!
    const source = () => container.querySelector('iframe')!.getAttribute('src')!
    render('one', true)
    expect(checkbox().checked).toBe(true)
    expect(source()).toContain('external_resources=true')
    act(() => checkbox().click())
    render('one', true)
    expect(checkbox().checked).toBe(false)
    expect(source()).not.toContain('external_resources')
    render('two', true)
    expect(checkbox().checked).toBe(true)
    expect(source()).toContain(
      '/items/two/article-preview?external_resources=true',
    )
  } finally {
    act(() => root.unmount())
    container.remove()
  }
})

it('restarts loading and recovery after each resource toggle, including returning to the original URL', () => {
  vi.useFakeTimers()
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  try {
    act(() =>
      root.render(
        <ArticlePreviewDrawer
          preview={{
            itemId: 'one',
            url: 'https://example.com/one',
            title: 'Article',
            sourceLabel: 'Feed',
          }}
          width={700}
          minWidth={400}
          maxWidth={1000}
          isResizing={false}
          onResizeStart={vi.fn()}
          onResizeBy={vi.fn()}
          onClose={vi.fn()}
        />,
      ),
    )
    const checkbox = () =>
      container.querySelector<HTMLInputElement>('input[type="checkbox"]')!
    const load = () =>
      act(() =>
        container.querySelector('iframe')!.dispatchEvent(new Event('load')),
      )
    load()
    expect(container.textContent).not.toContain('Loading original site...')
    act(() => checkbox().click())
    expect(container.textContent).toContain('Loading original site...')
    act(() => vi.advanceTimersByTime(5000))
    expect(container.textContent).toContain('Preview is still loading.')
    load()
    act(() => checkbox().click())
    expect(container.textContent).toContain('Loading original site...')
    act(() => vi.advanceTimersByTime(5000))
    expect(container.textContent).toContain('Preview is still loading.')
  } finally {
    act(() => root.unmount())
    container.remove()
    vi.useRealTimers()
  }
})
