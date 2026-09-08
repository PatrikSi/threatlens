import { RefObject, useEffect, useRef } from 'react'

import { registerDialogLayer } from './dialogStack'

const DIALOG_FOCUSABLE_SELECTOR =
  'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'

type FocusableElement = Pick<HTMLElement, 'focus' | 'hasAttribute' | 'getAttribute'>

type DialogContainer = Pick<HTMLElement, 'contains' | 'focus'> & {
  querySelectorAll(selectors: string): ArrayLike<HTMLElement>
}

type DialogIsolationTarget = Pick<HTMLElement, 'children'>

type DialogKeyDownEvent = Pick<KeyboardEvent, 'key' | 'shiftKey' | 'preventDefault'>

type ResolveDialogInitialFocusTargetArgs = {
  dialog: HTMLElement | null
  closeButton: HTMLElement | null
  initialFocus: HTMLElement | null
  dismissDisabled: boolean
}

type HandleDialogSurfaceKeyDownArgs = {
  event: DialogKeyDownEvent
  dialog: DialogContainer
  activeElement: HTMLElement | null
  dismissDisabled: boolean
  onClose: () => void
}

type UseDialogFocusTrapArgs = {
  open: boolean
  dialogRef: RefObject<HTMLDivElement | null>
  closeButtonRef: RefObject<HTMLElement | null>
  initialFocusRef?: RefObject<HTMLElement | null>
  dismissDisabled: boolean
  onClose: () => void
}

export function getFocusableDialogElements(container: DialogContainer): FocusableElement[] {
  return Array.from(container.querySelectorAll(DIALOG_FOCUSABLE_SELECTOR)).filter(
    (element) => !element.hasAttribute('disabled') && element.getAttribute('aria-hidden') !== 'true',
  )
}

export function resolveDialogInitialFocusTarget({
  dialog,
  closeButton,
  initialFocus,
  dismissDisabled,
}: ResolveDialogInitialFocusTargetArgs): FocusableElement | null {
  return initialFocus ?? (dismissDisabled ? dialog : closeButton) ?? dialog
}

export function handleDialogSurfaceKeyDown({
  event,
  dialog,
  activeElement,
  dismissDisabled,
  onClose,
}: HandleDialogSurfaceKeyDownArgs) {
  if (event.key === 'Escape' && !dismissDisabled) {
    event.preventDefault()
    onClose()
    return
  }

  if (event.key !== 'Tab') {
    return
  }

  const focusable = getFocusableDialogElements(dialog)
  if (!focusable.length) {
    event.preventDefault()
    dialog.focus()
    return
  }

  const first = focusable[0]
  const last = focusable[focusable.length - 1]

  if (!activeElement || !dialog.contains(activeElement)) {
    event.preventDefault()
    ;(event.shiftKey ? last : first).focus()
    return
  }

  if (event.shiftKey && activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

export function applyDialogDocumentIsolation(
  dialogRoot: HTMLElement | null,
  isolationTarget: DialogIsolationTarget | null = typeof document !== 'undefined' ? document.body : null,
) {
  if (!dialogRoot || !isolationTarget) {
    return () => undefined
  }

  const layer = registerDialogLayer(dialogRoot, isolationTarget)
  return () => layer.release(false)
}

export function useDialogFocusTrap({
  open,
  dialogRef,
  closeButtonRef,
  initialFocusRef,
  dismissDisabled,
  onClose,
}: UseDialogFocusTrapArgs) {
  const dismissDisabledRef = useRef(dismissDisabled)
  const onCloseRef = useRef(onClose)

  useEffect(() => {
    dismissDisabledRef.current = dismissDisabled
  }, [dismissDisabled])

  useEffect(() => {
    onCloseRef.current = onClose
  }, [onClose])

  useEffect(() => {
    if (!open) {
      return
    }

    const dialogRoot = dialogRef.current?.parentElement
    if (!dialogRoot) return
    const layer = registerDialogLayer(dialogRoot)
    const frame = window.requestAnimationFrame(() => {
      if (!layer.isTop()) return
      const focusTarget = resolveDialogInitialFocusTarget({
        dialog: dialogRef.current,
        closeButton: closeButtonRef.current,
        initialFocus: initialFocusRef?.current ?? null,
        dismissDisabled: dismissDisabledRef.current,
      })
      focusTarget?.focus()
    })

    const onKeyDown = (event: KeyboardEvent) => {
      if (!layer.isTop() || event.defaultPrevented || !dialogRef.current) {
        return
      }

      if (event.key === 'Escape') {
        // Background page listeners must not dismiss previews or editors beneath a modal.
        event.stopImmediatePropagation()
        event.preventDefault()
      }

      handleDialogSurfaceKeyDown({
        event,
        dialog: dialogRef.current,
        activeElement: document.activeElement instanceof HTMLElement ? document.activeElement : null,
        dismissDisabled: dismissDisabledRef.current,
        onClose: onCloseRef.current,
      })
    }

    document.addEventListener('keydown', onKeyDown, true)
    return () => {
      document.removeEventListener('keydown', onKeyDown, true)
      window.cancelAnimationFrame(frame)
      layer.release()
    }
  }, [closeButtonRef, dialogRef, initialFocusRef, open])
}
