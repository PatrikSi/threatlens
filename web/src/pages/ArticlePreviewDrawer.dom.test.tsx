// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { ArticlePreviewDrawer } from './DashboardPageComponents'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

it('requires resource opt-in for each article and allows returning to blocked resources', () => {
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  const render = (itemId: string) => act(() => root.render(
    <ArticlePreviewDrawer key={itemId} preview={{ itemId, url: `https://example.com/${itemId}`, title: 'Article', sourceLabel: 'Feed' }}
      frameState="loaded" width={700} minWidth={400} maxWidth={1000} isResizing={false}
      onResizeStart={vi.fn()} onResizeBy={vi.fn()} onFrameLoad={vi.fn()} onClose={vi.fn()} />,
  ))
  try {
    render('one')
    const checkbox = () => container.querySelector<HTMLInputElement>('input[type="checkbox"]')!
    const source = () => container.querySelector('iframe')!.getAttribute('src')!
    expect(source()).not.toContain('external_resources')
    act(() => checkbox().click())
    expect(source()).toContain('external_resources=true')
    expect(container.querySelector('iframe')!.getAttribute('sandbox')).not.toContain('allow-scripts')
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
