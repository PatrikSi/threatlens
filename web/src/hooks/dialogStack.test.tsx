// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ConfirmDialog, DialogSurface } from '../components/ConfirmDialog'
import { registerDialogLayer } from './dialogStack'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => { document.body.innerHTML = ''; document.body.style.overflow = '' })

describe('dialog stack', () => {
  it('keeps the opener and nested dialog order through confirmation pending and failure transitions', () => {
    const opener = document.createElement('button')
    opener.textContent = 'Recover selected work'
    const container = document.createElement('div')
    document.body.append(opener, container)
    const root = createRoot(container)
    const close = vi.fn()
    const verificationClose = vi.fn()
    const render = (open: boolean, pending: boolean, verification = false) => act(() => root.render(<>
      <ConfirmDialog open={open} title="Recover work?" confirmLabel="Recover" isConfirming={pending} onConfirm={() => undefined} onCancel={close} />
      <DialogSurface open={verification} title="Session verification unavailable" dismissDisabled onClose={verificationClose} />
    </>))
    try {
      opener.focus()
      render(true, false)
      document.querySelector<HTMLButtonElement>('[role=alertdialog] button')!.focus()
      render(true, true, true)
      render(true, false, true)
      act(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })))
      expect(close).not.toHaveBeenCalled()
      expect(verificationClose).not.toHaveBeenCalled()
      expect(document.querySelector('[role=alertdialog]')!.closest('[inert]')).not.toBeNull()
      expect(document.querySelector('[role=dialog]')!.closest('[inert]')).toBeNull()
      render(true, false)
      render(false, false)
      expect(document.activeElement).toBe(opener)
      expect(opener.hasAttribute('inert')).toBe(false)
    } finally {
      act(() => root.unmount())
    }
  })

  it.each(['parent-first', 'child-first'])('restores the baseline after %s removal', (order) => {
    document.body.innerHTML = '<main aria-hidden="false"><button>Open</button></main><aside inert aria-hidden="true"></aside><div id="outer"><button>Nested</button></div><div id="inner"></div>'
    const main = document.querySelector('main')!
    const opener = main.querySelector('button')!
    const outer = document.querySelector<HTMLElement>('#outer')!
    const inner = document.querySelector<HTMLElement>('#inner')!
    opener.focus()
    const first = registerDialogLayer(outer)
    outer.querySelector('button')!.focus()
    const second = registerDialogLayer(inner)
    expect(outer.hasAttribute('inert')).toBe(true)
    expect(inner.hasAttribute('inert')).toBe(false)
    if (order === 'parent-first') { first.release(); outer.remove(); second.release() }
    else { second.release(); expect(outer.hasAttribute('inert')).toBe(false); first.release() }
    expect(main.getAttribute('aria-hidden')).toBe('false')
    expect(main.hasAttribute('inert')).toBe(false)
    expect(document.querySelector('aside')!.hasAttribute('inert')).toBe(true)
    expect(document.activeElement).toBe(opener)
    expect(document.body.style.overflow).toBe('')
  })

  it('isolates body portals created while a dialog is open', async () => {
    const dialog = document.createElement('div')
    document.body.append(dialog)
    const layer = registerDialogLayer(dialog)
    const portal = document.createElement('div')
    document.body.append(portal)
    await Promise.resolve()
    expect(portal.hasAttribute('inert')).toBe(true)
    layer.release()
    expect(portal.hasAttribute('inert')).toBe(false)
  })

  it('delivers Escape only to the top mounted dialog and restores the app after simultaneous close', () => {
    const container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    const outerClose = vi.fn()
    const innerClose = vi.fn()
    act(() => root.render(<><DialogSurface open title="Editor" onClose={outerClose} /><DialogSurface open title="Discard" onClose={innerClose} /></>))
    act(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })))
    expect(innerClose).toHaveBeenCalledTimes(1)
    expect(outerClose).not.toHaveBeenCalled()
    act(() => root.unmount())
    expect(container.hasAttribute('inert')).toBe(false)
    expect(container.hasAttribute('aria-hidden')).toBe(false)
  })
})
