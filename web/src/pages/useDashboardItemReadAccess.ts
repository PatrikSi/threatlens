import { useCallback, useRef, type Dispatch, type SetStateAction } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { isQueryAccessDenied, useQueryAccessScope } from '../hooks/useQueryAccessScope'
import type { ItemDetail, ItemListResponse } from '../types/api'
import type { DashboardItemReadAdmission } from './useDashboardItemActions'

export function useDashboardItemReadAccess(
  savedNotes: { current: Record<string, string> },
  setNoteDrafts: Dispatch<SetStateAction<Record<string, string>>>,
) {
  const client = useQueryClient()
  const lists = useQueryAccessScope(['dashboard', 'items'])
  const detailAdmissions = useRef(new Map<string, DashboardItemReadAdmission>())
  const registerItemReadAdmission = useCallback((itemId: string, admission: DashboardItemReadAdmission) => {
    detailAdmissions.current.set(itemId, admission)
    return () => {
      if (detailAdmissions.current.get(itemId) === admission) detailAdmissions.current.delete(itemId)
    }
  }, [])

  function findListItem(itemId: string) {
    for (const [, cached] of client.getQueriesData<Awaited<ReturnType<typeof lists.read<ItemListResponse>>>>({ queryKey: ['dashboard', 'items'] })) {
      const item = lists.currentData(cached)?.items.find((candidate) => candidate.id === itemId)
      if (item) return item
    }
    return undefined
  }

  function captureReadAdmission(itemId: string) {
    const detail = detailAdmissions.current.get(itemId)
    if (detail) return detail.capture()
    const admission = lists.captureResult()(true)
    // A row-only automatic action can use its list read, but closing an
    // inspector cannot erase a known withdrawal for that exact article.
    return () => !isQueryAccessDenied(client, ['dashboard', 'item', itemId])
      && lists.currentData(admission) === true && client
      .getQueriesData<Awaited<ReturnType<typeof lists.read<ItemListResponse>>>>({ queryKey: ['dashboard', 'items'] })
      .some(([key, cached]) => {
        const state = client.getQueryState(key)
        return state?.status === 'success' && state.fetchStatus === 'idle'
          && Boolean(lists.currentData(cached)?.items.some((item) => item.id === itemId))
      })
  }

  const syncItemNote = useCallback((detail: ItemDetail) => {
    const savedNote = detail.state.note ?? ''
    const previousSavedNote = savedNotes.current[detail.id]
    savedNotes.current[detail.id] = savedNote
    setNoteDrafts((current) => {
      const draft = current[detail.id]
      if (draft !== undefined && draft !== previousSavedNote) return current
      return draft === savedNote ? current : { ...current, [detail.id]: savedNote }
    })
  }, [savedNotes, setNoteDrafts])

  return { captureReadAdmission, findListItem, registerItemReadAdmission, syncItemNote }
}
