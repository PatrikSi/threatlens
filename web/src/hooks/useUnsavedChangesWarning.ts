import { useEffect } from 'react'
import { useBlocker } from 'react-router-dom'

export function useUnsavedChangesWarning(
  isDirty: boolean,
  message = 'You have unsaved changes. Leave without saving?',
) {
  const blocker = useBlocker(isDirty)

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

  useEffect(() => {
    if (blocker.state !== 'blocked') {
      return
    }

    if (window.confirm(message)) {
      blocker.proceed()
      return
    }

    blocker.reset()
  }, [blocker, message])

  return () => {
    if (!isDirty) {
      return true
    }
    return window.confirm(message)
  }
}
