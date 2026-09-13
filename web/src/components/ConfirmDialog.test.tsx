// @vitest-environment jsdom

import type { ReactElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  applyDialogDocumentIsolation,
  getFocusableDialogElements,
  handleDialogSurfaceKeyDown,
  resolveDialogInitialFocusTarget,
} from '../hooks/useDialogFocusTrap'
import { ConfirmDialog, DialogSurface } from './ConfirmDialog'

function renderStaticDialog(element: ReactElement) {
  const originalDocument = globalThis.document
  vi.stubGlobal('document', undefined)
  try {
    return renderToStaticMarkup(element)
  } finally {
    vi.stubGlobal('document', originalDocument)
  }
}

afterEach(() => { document.body.innerHTML = ''; vi.restoreAllMocks() })

function createFocusableElement(options?: { disabled?: boolean; ariaHidden?: boolean }) {
  const element = document.createElement('button')
  element.disabled = options?.disabled ?? false
  if (options?.ariaHidden) element.setAttribute('aria-hidden', 'true')
  // JSDOM has no layout. Only these explicitly visible controls get a rectangle;
  // browser tests exercise real CSS visibility and geometry.
  vi.spyOn(element, 'getClientRects').mockReturnValue([new DOMRect(0, 0, 10, 10)] as unknown as DOMRectList)
  vi.spyOn(element, 'focus')
  return element
}

function createDialogContainer(controls: HTMLElement[]) {
  const container = document.createElement('div')
  container.append(...controls)
  document.body.append(container)
  return container
}

describe('ConfirmDialog', () => {
  it('renders reusable dialog semantics for non-destructive overlays', () => {
    const markup = renderStaticDialog(
      <DialogSurface
        open
        title="Manage Saved Views"
        description="Load, import, export, or delete saved dashboard layouts."
        eyebrow="Dashboard"
        onClose={() => undefined}
      >
        <p>Saved view content</p>
      </DialogSurface>,
    )

    expect(markup).toContain('role="dialog"')
    expect(markup).toContain('aria-modal="true"')
    expect(markup).toContain('Manage Saved Views')
    expect(markup).toContain('Saved view content')
    expect(markup).toContain('max-h-[calc(100dvh-1.5rem)]')
    expect(markup).toContain('sm:max-h-[calc(100dvh-3rem)]')
    expect(markup).not.toContain('sm:max-h-none')
    expect(markup).not.toContain('sm:overflow-visible')
  })

  it('renders alertdialog semantics when open', () => {
    const markup = renderStaticDialog(
      <ConfirmDialog
        open
        title="Delete alert?"
        description="This removes the alert."
        confirmLabel="Delete alert"
        onCancel={() => undefined}
        onConfirm={() => undefined}
      />,
    )

    expect(markup).toContain('role="alertdialog"')
    expect(markup).toContain('aria-modal="true"')
    expect(markup).toContain('Delete alert?')
    expect(markup).toContain('Delete alert')
  })

  it('marks the dialog busy and disables dismiss controls while confirming', () => {
    const markup = renderStaticDialog(
      <ConfirmDialog
        open
        title="Apply changes?"
        description="Review the changes below."
        confirmLabel="Apply"
        confirmTone="primary"
        isConfirming
        onCancel={() => undefined}
        onConfirm={() => undefined}
      />,
    )

    expect(markup).toContain('aria-busy="true"')
    expect(markup).toContain('Working...')
    expect(markup.match(/disabled=""/g)?.length ?? 0).toBeGreaterThanOrEqual(3)
  })

  it('prefers an explicit initial focus target for destructive confirmations', () => {
    const dialog = createFocusableElement()
    const closeButton = createFocusableElement()
    const cancelButton = createFocusableElement()

    expect(
      resolveDialogInitialFocusTarget({
        dialog,
        closeButton,
        initialFocus: cancelButton,
        dismissDisabled: false,
      }),
    ).toBe(cancelButton)
  })

  it('filters disabled and aria-hidden controls out of the focus order', () => {
    const first = createFocusableElement()
    const disabled = createFocusableElement({ disabled: true })
    const hidden = createFocusableElement({ ariaHidden: true })

    expect(getFocusableDialogElements(createDialogContainer([first, disabled, hidden]))).toEqual([first])
  })

  it('excludes negative tabindex and inherited disabled, inert or aria-hidden state', () => {
    const first = createFocusableElement()
    const last = createFocusableElement()
    const negative = createFocusableElement()
    negative.tabIndex = -1
    const disabledParent = document.createElement('fieldset')
    disabledParent.disabled = true
    disabledParent.append(createFocusableElement())
    const inertParent = document.createElement('div')
    inertParent.setAttribute('inert', '')
    inertParent.append(createFocusableElement())
    const hiddenParent = document.createElement('div')
    hiddenParent.setAttribute('aria-hidden', 'true')
    hiddenParent.append(createFocusableElement())
    const container = createDialogContainer([first, negative, disabledParent, inertParent, hiddenParent, last])
    expect(getFocusableDialogElements(container)).toEqual([first, last])
  })

  it('uses positive tabindex order before ordinary controls', () => {
    const ordinary = createFocusableElement()
    const second = createFocusableElement()
    second.tabIndex = 2
    const first = createFocusableElement()
    first.tabIndex = 1
    expect(getFocusableDialogElements(createDialogContainer([ordinary, second, first]))).toEqual([first, second, ordinary])
  })

  it('closes on Escape when dismissal is allowed', () => {
    const event = {
      key: 'Escape',
      shiftKey: false,
      preventDefault: vi.fn(),
    }
    const onClose = vi.fn()

    handleDialogSurfaceKeyDown({
      event,
      dialog: createDialogContainer([]),
      activeElement: null,
      dismissDisabled: false,
      onClose,
    })

    expect(event.preventDefault).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('wraps keyboard focus when tabbing past the last focusable control', () => {
    const first = createFocusableElement()
    const last = createFocusableElement()
    const event = {
      key: 'Tab',
      shiftKey: false,
      preventDefault: vi.fn(),
    }

    handleDialogSurfaceKeyDown({
      event,
      dialog: createDialogContainer([first, last]),
      activeElement: last,
      dismissDisabled: false,
      onClose: () => undefined,
    })

    expect(event.preventDefault).toHaveBeenCalledTimes(1)
    expect(first.focus).toHaveBeenCalledTimes(1)
  })

  it('hides sibling body content from assistive tech while the dialog is isolated', () => {
    const appRoot = document.createElement('div')
    const dialogRoot = document.createElement('div')
    document.body.append(appRoot, dialogRoot)

    const restore = applyDialogDocumentIsolation(dialogRoot)

    expect(appRoot.getAttribute('aria-hidden')).toBe('true')
    expect(appRoot.hasAttribute('inert')).toBe(true)
    expect(dialogRoot.getAttribute('aria-hidden')).toBeNull()

    restore()

    expect(appRoot.getAttribute('aria-hidden')).toBeNull()
    expect(appRoot.hasAttribute('inert')).toBe(false)
    appRoot.remove()
    dialogRoot.remove()
  })
})
