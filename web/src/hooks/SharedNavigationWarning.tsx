import { useEffect } from 'react'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type { NavigationWarnings } from './useSharedNavigationWarning'

export function SharedNavigationWarning({ warnings }: { warnings: NavigationWarnings }) {
  const { blocker } = warnings
  const messages = [...new Set([...warnings.forms.values()].filter((form) => form.dirty).map((form) => form.message))]
  useEffect(() => {
    if (blocker?.state === 'blocked' && !messages.length) blocker.proceed()
  }, [blocker, messages.length])
  return <ConfirmDialog
    open={blocker?.state === 'blocked'}
    title="Discard unsaved changes?"
    description={messages.join(' ')}
    confirmLabel="Discard changes"
    onCancel={() => blocker?.reset?.()}
    onConfirm={() => blocker?.proceed?.()}
  />
}
