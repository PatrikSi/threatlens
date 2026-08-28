import { createElement, type ReactNode, useCallback, useEffect, useMemo, useState } from 'react'
import { useBlocker } from 'react-router-dom'

import { ConfirmDialog } from '../components/ConfirmDialog'

type ConfirmUnsavedChangesFn = (message: string) => boolean

type UnsavedChangesBlocker = Pick<ReturnType<typeof useBlocker>, 'proceed' | 'reset' | 'state'>

type PendingUnsavedChangesAction = {
  onConfirm?: () => void
  onCancel?: () => void
}

type UnsavedChangesWarningOptions = {
  ignoreSearchChanges?: boolean
}

export type ConfirmDiscardChanges = {
  (onDiscard?: () => void): boolean
  discardDialog: ReactNode
  discardDialogOpen: boolean
}

function defaultConfirmUnsavedChanges(message: string) {
  return window.confirm(message)
}

export function confirmUnsavedChanges(
  message: string,
  confirmWith: ConfirmUnsavedChangesFn = defaultConfirmUnsavedChanges,
) {
  return confirmWith(message)
}

export function createBeforeUnloadHandler(message: string) {
  return (event: BeforeUnloadEvent) => {
    event.preventDefault()
    event.returnValue = message
    return message
  }
}

export function handleBlockedUnsavedChangesNavigation(
  blocker: UnsavedChangesBlocker,
  message: string,
  confirmWith: ConfirmUnsavedChangesFn = defaultConfirmUnsavedChanges,
) {
  if (blocker.state !== 'blocked') {
    return false
  }

  if (confirmUnsavedChanges(message, confirmWith)) {
    blocker.proceed?.()
    return true
  }

  blocker.reset?.()
  return false
}

export function useUnsavedChangesWarning(
  isDirty: boolean,
  message = 'You have unsaved changes. Leave without saving?',
  options: UnsavedChangesWarningOptions = {},
): ConfirmDiscardChanges {
  const blocker = useBlocker(
    options.ignoreSearchChanges
      ? ({ currentLocation, nextLocation }) =>
          isDirty && currentLocation.pathname !== nextLocation.pathname
      : isDirty,
  )
  const [pendingAction, setPendingAction] = useState<PendingUnsavedChangesAction | null>(null)

  const clearPendingAction = useCallback(
    (run: keyof PendingUnsavedChangesAction) => {
      const current = pendingAction
      setPendingAction(null)
      current?.[run]?.()
    },
    [pendingAction],
  )

  const requestDiscard = useCallback(
    (onDiscard?: () => void) => {
      if (!isDirty) {
        onDiscard?.()
        return true
      }

      setPendingAction((current) => current ?? { onConfirm: onDiscard })
      return false
    },
    [isDirty],
  )

  useEffect(() => {
    if (!isDirty) {
      return
    }

    const onBeforeUnload = createBeforeUnloadHandler(message)
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [isDirty, message])

  useEffect(() => {
    if (!isDirty) {
      return
    }
    if (blocker.state !== 'blocked') {
      return
    }

    setPendingAction((current) => current ?? { onConfirm: blocker.proceed, onCancel: blocker.reset })
  }, [blocker, isDirty])

  useEffect(() => {
    if (!isDirty) {
      setPendingAction(null)
    }
  }, [isDirty])

  const discardDialog = createElement(ConfirmDialog, {
    open: pendingAction !== null,
    title: 'Discard unsaved changes?',
    description: message,
    confirmLabel: 'Discard changes',
    onCancel: () => clearPendingAction('onCancel'),
    onConfirm: () => clearPendingAction('onConfirm'),
  })

  return useMemo(
    () =>
      Object.assign(requestDiscard, {
        discardDialog,
        discardDialogOpen: pendingAction !== null,
      }),
    [discardDialog, pendingAction, requestDiscard],
  )
}
