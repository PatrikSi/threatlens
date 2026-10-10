// @vitest-environment jsdom
import { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { DialogSurface } from '../components/ConfirmDialog'
import { ArticlePreviewDrawer } from './DashboardPageComponents'
import { useArticlePreview } from './useArticlePreview'
;(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true
let root: Root
let container: HTMLDivElement

function Harness({ showFirst = true }: { showFirst?: boolean }) {
  const preview = useArticlePreview()
  const [modal, setModal] = useState(false)
  return (
    <>
      {['one', 'two']
        .filter((id) => showFirst || id !== 'one')
        .map((id) => (
          <button
            key={id}
            onClick={(event) => {
              event.currentTarget.focus()
              preview.openArticlePreview({
                itemId: id,
                title: id,
                url: `https://example.com/${id}`,
                sourceLabel: 'Feed',
              })
            }}
          >
            Preview {id}
          </button>
        ))}
      <button onClick={() => setModal(true)}>Open modal</button>
      {preview.articlePreview && (
        <ArticlePreviewDrawer
          key={preview.articlePreview.itemId}
          preview={preview.articlePreview}
          width={700}
          minWidth={400}
          maxWidth={1000}
          isResizing={false}
          onResizeStart={vi.fn()}
          onResizeBy={vi.fn()}
          onClose={preview.closeArticlePreview}
        />
      )}
      <DialogSurface
        open={modal}
        title="Overlaid modal"
        onClose={() => setModal(false)}
      >
        <button onClick={preview.closeArticlePreview}>
          Remove background preview
        </button>
      </DialogSurface>
    </>
  )
}
function button(label: string) {
  const value = [
    ...document.querySelectorAll<HTMLButtonElement>('button'),
  ].find(
    (node) => (node.getAttribute('aria-label') ?? node.textContent) === label,
  )
  if (!value) throw new Error(`Missing button: ${label}`)
  return value
}
function activate(label: string) {
  act(() => {
    button(label).focus()
    button(label).click()
    vi.advanceTimersByTime(20)
  })
  act(() => vi.advanceTimersByTime(20))
}
beforeEach(() => {
  vi.useFakeTimers()
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  act(() => root.render(<Harness />))
})
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.useRealTimers()
})

it('focuses Close once, retains focus on resource changes, and restores the connected opener', () => {
  const opener = button('Preview one')
  activate('Preview one')
  expect(document.activeElement).toBe(button('Close original article preview'))
  expect(container.hasAttribute('inert')).toBe(false)
  expect(document.querySelector('aside')!.hasAttribute('aria-modal')).toBe(
    false,
  )
  const resources = container.querySelector<HTMLInputElement>(
    'input[type="checkbox"]',
  )!
  act(() => {
    resources.focus()
    resources.click()
    vi.advanceTimersByTime(20)
  })
  expect(document.activeElement).toBe(resources)
  activate('Close original article preview')
  expect(document.activeElement).toBe(opener)
})

it('remembers the newly selected article opener and returns focus on Escape', () => {
  activate('Preview one')
  activate('Preview two')
  expect(document.activeElement).toBe(button('Close original article preview'))
  act(() =>
    document.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
    ),
  )
  expect(document.querySelector('aside')).toBeNull()
  expect(document.activeElement).toBe(button('Preview two'))
})

it('does not restore focus to a removed opener', () => {
  activate('Preview one')
  const opener = button('Preview one')
  const restore = vi.spyOn(opener, 'focus')
  act(() => root.render(<Harness showFirst={false} />))
  activate('Close original article preview')
  expect(opener.isConnected).toBe(false)
  expect(restore).not.toHaveBeenCalled()
})

it('leaves modal focus and Escape handling to the existing dialog coordinator', () => {
  activate('Preview one')
  activate('Open modal')
  expect(container.hasAttribute('inert')).toBe(true)
  expect(document.activeElement).toBe(button('Close dialog'))
  act(() =>
    document.dispatchEvent(
      new KeyboardEvent('keydown', {
        key: 'Escape',
        bubbles: true,
        cancelable: true,
      }),
    ),
  )
  expect(document.querySelector('[aria-modal="true"]')).toBeNull()
  expect(document.querySelector('aside')).not.toBeNull()
  expect(document.activeElement).toBe(button('Open modal'))
  activate('Open modal')
  activate('Remove background preview')
  expect(document.querySelector('aside')).toBeNull()
  expect(document.activeElement).toBe(button('Remove background preview'))
  expect(container.hasAttribute('inert')).toBe(true)
  activate('Close dialog')
  expect(container.hasAttribute('inert')).toBe(false)
})
