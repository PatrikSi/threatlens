import { useQueryClient } from '@tanstack/react-query'
import { useSyncExternalStore } from 'react'
import { HUNT_DRAFT_PREFIX, huntDraftEntries } from '../pages/huntReviewDrafts'

export function useHuntDraftScopes(): { key: readonly unknown[]; count: number }[] {
  const client = useQueryClient()
  const snapshot = useSyncExternalStore(
    (notify) => client.getQueryCache().subscribe((event) => {
      if (event.query.queryKey[0] === HUNT_DRAFT_PREFIX) notify()
    }),
    () => JSON.stringify(huntDraftEntries(client).map(([key, drafts]) => ({ key, count: Object.keys(drafts ?? {}).length }))),
    () => '[]',
  )
  return JSON.parse(snapshot)
}

export function useHuntDraftCount() {
  return useHuntDraftScopes().reduce((count, entry) => count + entry.count, 0)
}
