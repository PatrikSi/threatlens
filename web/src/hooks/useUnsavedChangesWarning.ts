import { useEffect } from 'react'
import { useBlocker } from 'react-router-dom'

type ConfirmUnsavedChangesFn = (message: string) => boolean

type UnsavedChangesBlocker = Pick<ReturnType<typeof useBlocker>, 'proceed' | 'reset' | 'state'>

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
) {
  const blocker = useBlocker(isDirty)

  useEffect(() => {
    if (!isDirty) {
      return
    }

    const onBeforeUnload = createBeforeUnloadHandler(message)
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [isDirty, message])

  useEffect(() => {
    handleBlockedUnsavedChangesNavigation(blocker, message)
  }, [blocker, message])

  return () => {
    if (!isDirty) {
      return true
    }
    return confirmUnsavedChanges(message)
  }
}
