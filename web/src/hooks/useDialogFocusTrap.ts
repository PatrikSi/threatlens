import { RefObject, useEffect, useRef } from 'react'

const DIALOG_FOCUSABLE_SELECTOR =
  'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'

type FocusableElement = Pick<HTMLElement, 'focus' | 'hasAttribute' | 'getAttribute'>

type DialogContainer = Pick<HTMLElement, 'contains' | 'focus'> & {
  querySelectorAll(selectors: string): ArrayLike<HTMLElement>
}

type InertCapableElement = HTMLElement & { inert?: boolean }

type DialogIsolationTarget = Pick<HTMLElement, 'children'>

type DialogIsolationSnapshot = {
  element: InertCapableElement
  ariaHidden: string | null
  hadInertAttribute: boolean
  inertValue: boolean
}

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

  const snapshots: DialogIsolationSnapshot[] = []
  for (const child of Array.from(isolationTarget.children)) {
    if (!(child instanceof HTMLElement) || child === dialogRoot) {
      continue
    }

    const element = child as InertCapableElement
    snapshots.push({
      element,
      ariaHidden: element.getAttribute('aria-hidden'),
      hadInertAttribute: element.hasAttribute('inert'),
      inertValue: Boolean(element.inert),
    })
    element.setAttribute('aria-hidden', 'true')
    element.setAttribute('inert', '')
    element.inert = true
  }

  return () => {
    for (const snapshot of snapshots) {
      if (snapshot.ariaHidden === null) {
        snapshot.element.removeAttribute('aria-hidden')
      } else {
        snapshot.element.setAttribute('aria-hidden', snapshot.ariaHidden)
      }
      if (snapshot.hadInertAttribute) {
        snapshot.element.setAttribute('inert', '')
      } else {
        snapshot.element.removeAttribute('inert')
      }
      snapshot.element.inert = snapshot.inertValue
    }
  }
}

export function useDialogFocusTrap({
  open,
  dialogRef,
  closeButtonRef,
  initialFocusRef,
  dismissDisabled,
  onClose,
}: UseDialogFocusTrapArgs) {
  const previousFocusRef = useRef<HTMLElement | null>(null)
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

    previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const restoreIsolation = applyDialogDocumentIsolation(dialogRef.current?.parentElement ?? null)
    window.requestAnimationFrame(() => {
      const focusTarget = resolveDialogInitialFocusTarget({
        dialog: dialogRef.current,
        closeButton: closeButtonRef.current,
        initialFocus: initialFocusRef?.current ?? null,
        dismissDisabled: dismissDisabledRef.current,
      })
      focusTarget?.focus()
    })

    const onKeyDown = (event: KeyboardEvent) => {
      if (!dialogRef.current) {
        return
      }

      handleDialogSurfaceKeyDown({
        event,
        dialog: dialogRef.current,
        activeElement: document.activeElement instanceof HTMLElement ? document.activeElement : null,
        dismissDisabled: dismissDisabledRef.current,
        onClose: onCloseRef.current,
      })
    }

    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      restoreIsolation()
      if (previousFocusRef.current?.isConnected) {
        previousFocusRef.current.focus()
      }
      previousFocusRef.current = null
    }
  }, [closeButtonRef, dialogRef, initialFocusRef, open])
}
