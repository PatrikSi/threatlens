import { useEffect } from 'react'
import { createBeforeUnloadHandler } from '../hooks/useUnsavedChangesWarning'
import { useHuntDraftCount } from '../hooks/useHuntDraftCount'

/** Lives with the session cache, including while the article or team is closed. */
export function HuntDraftUnloadGuard() {
  const dirty = useHuntDraftCount() > 0
  useEffect(() => {
    if (!dirty) return
    const handler = createBeforeUnloadHandler('You have unsaved hunt review notes.')
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])
  return null
}
