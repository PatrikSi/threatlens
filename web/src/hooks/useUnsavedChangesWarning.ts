import { useEffect } from 'react'

export function useUnsavedChangesWarning(
  isDirty: boolean,
  message = 'You have unsaved changes. Leave without saving?',
) {
  useEffect(() => {
    if (!isDirty) {
      return
    }

    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = message
      return message
    }

    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [isDirty, message])

  return () => {
    if (!isDirty) {
      return true
    }
    return window.confirm(message)
  }
}
