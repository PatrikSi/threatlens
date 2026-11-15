import { useRef, type Dispatch, type SetStateAction } from 'react'
import { type QueryClient, useMutation } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { usePendingEntityActions } from '../hooks/usePendingEntityActions'
import {
  clearItemFeedback,
  resolveItemActionError,
  syncItemStateInCache,
} from './dashboardPageUtils'

type ActionFeedback = { tone: 'success' | 'error'; message: string }
type FeedbackByItemId = Record<string, ActionFeedback>
type ReadAction = { itemId: string; isRead: boolean }
export type DashboardItemReadAdmission = {
  available: () => boolean
  capture: () => () => boolean
}

export function useDashboardItemActions({
  canManage,
  captureReadAdmission,
  queryClient,
  savedNoteValuesByItemIdRef,
  setArticleRetryFeedbackByItemId,
  setItemActionFeedbackByItemId,
  setNoteDraftsByItemId,
}: {
  canManage: boolean
  captureReadAdmission: (itemId: string) => () => boolean
  queryClient: QueryClient
  savedNoteValuesByItemIdRef: { current: Record<string, string> }
  setArticleRetryFeedbackByItemId: Dispatch<SetStateAction<FeedbackByItemId>>
  setItemActionFeedbackByItemId: Dispatch<SetStateAction<FeedbackByItemId>>
  setNoteDraftsByItemId: Dispatch<SetStateAction<Record<string, string>>>
}) {
  const pending = usePendingEntityActions()
  // Opening an inspector can start a detail GET before the row's automatic
  // mark-read RPC. Retain that action's original live admission until settlement.
  const automaticReadAdmissions = useRef(new WeakMap<ReadAction, () => boolean>())

  function readActionAdmission(action: ReadAction) {
    return automaticReadAdmissions.current.get(action) ?? captureReadAdmission(action.itemId)
  }

  function requireReadableItem(itemId: string) {
    if (!canManage || !captureReadAdmission(itemId)()) {
      throw new Error('Refresh this article before making changes.')
    }
  }

  async function refreshDashboardItem(itemId: string) {
    // Legacy consumers retain their raw cache contract. Dashboard reads obtain
    // their own fresh authorization instead of admitting a mutation response.
    await Promise.allSettled([
      queryClient.invalidateQueries({ queryKey: ['dashboard', 'items'] }),
      queryClient.invalidateQueries({ queryKey: ['dashboard', 'item', itemId] }),
    ])
  }

  const updateRead = useMutation({
    mutationKey: ['items', 'read'],
    mutationFn: (payload: ReadAction) => {
      if (!canManage || !readActionAdmission(payload)()) throw new Error('Refresh this article before making changes.')
      return apiFetch(`/items/${payload.itemId}/read`, {
        method: 'POST',
        body: JSON.stringify({ is_read: payload.isRead }),
      })
    },
    onMutate: (payload) => {
      const { itemId } = payload
      pending.begin('read', itemId)
      clearItemFeedback(setItemActionFeedbackByItemId, itemId)
      return readActionAdmission(payload)
    },
    onSuccess: async (_data, variables, admitted) => {
      if (admitted()) {
        syncItemStateInCache(queryClient, variables.itemId, { isRead: variables.isRead })
        setItemActionFeedbackByItemId((current) => ({
          ...current,
          [variables.itemId]: {
            tone: 'success',
            message: variables.isRead ? 'Marked article as read.' : 'Marked article as unread.',
          },
        }))
      }
      await refreshDashboardItem(variables.itemId)
    },
    onError: (error, variables) => {
      setItemActionFeedbackByItemId((current) => ({
        ...current,
        [variables.itemId]: {
          tone: 'error',
          message: resolveItemActionError(error, 'Unable to update read status right now.'),
        },
      }))
    },
    onSettled: (_data, _error, variables) => {
      automaticReadAdmissions.current.delete(variables)
      pending.finish('read', variables.itemId)
    },
  })

  const updateStar = useMutation({
    mutationKey: ['items', 'star'],
    mutationFn: (payload: { itemId: string; isStarred: boolean }) => {
      requireReadableItem(payload.itemId)
      return apiFetch(`/items/${payload.itemId}/star`, {
        method: 'POST',
        body: JSON.stringify({ is_starred: payload.isStarred }),
      })
    },
    onMutate: ({ itemId }) => {
      pending.begin('star', itemId)
      clearItemFeedback(setItemActionFeedbackByItemId, itemId)
      return captureReadAdmission(itemId)
    },
    onSuccess: async (_data, variables, admitted) => {
      if (admitted()) {
        syncItemStateInCache(queryClient, variables.itemId, { isStarred: variables.isStarred })
        setItemActionFeedbackByItemId((current) => ({
          ...current,
          [variables.itemId]: {
            tone: 'success',
            message: variables.isStarred ? 'Starred article.' : 'Removed star from article.',
          },
        }))
      }
      await refreshDashboardItem(variables.itemId)
    },
    onError: (error, variables) => {
      setItemActionFeedbackByItemId((current) => ({
        ...current,
        [variables.itemId]: {
          tone: 'error',
          message: resolveItemActionError(error, 'Unable to update star status right now.'),
        },
      }))
    },
    onSettled: (_data, _error, variables) => pending.finish('star', variables.itemId),
  })

  const updateNote = useMutation({
    mutationKey: ['items', 'note'],
    mutationFn: (payload: { itemId: string; note: string | null }) => {
      requireReadableItem(payload.itemId)
      return apiFetch(`/items/${payload.itemId}/note`, {
        method: 'POST',
        body: JSON.stringify({ note: payload.note }),
      })
    },
    onMutate: ({ itemId }) => {
      pending.begin('note', itemId)
      clearItemFeedback(setItemActionFeedbackByItemId, itemId)
      return captureReadAdmission(itemId)
    },
    onSuccess: async (_data, variables, admitted) => {
      if (!admitted()) {
        await refreshDashboardItem(variables.itemId)
        return
      }
      const savedNote = variables.note ?? ''
      savedNoteValuesByItemIdRef.current[variables.itemId] = savedNote
      setNoteDraftsByItemId((current) => {
        if ((current[variables.itemId] ?? '') !== savedNote) {
          return current
        }
        return { ...current, [variables.itemId]: savedNote }
      })
      syncItemStateInCache(queryClient, variables.itemId, { note: variables.note })
      setItemActionFeedbackByItemId((current) => ({
        ...current,
        [variables.itemId]: { tone: 'success', message: 'Saved analyst notes.' },
      }))
      await refreshDashboardItem(variables.itemId)
    },
    onError: (error, variables) => {
      setItemActionFeedbackByItemId((current) => ({
        ...current,
        [variables.itemId]: {
          tone: 'error',
          message: resolveItemActionError(error, 'Unable to save notes right now.'),
        },
      }))
    },
    onSettled: (_data, _error, variables) => pending.finish('note', variables.itemId),
  })

  const retryArticleFetch = useMutation({
    mutationKey: ['items', 'retry-article-fetch'],
    mutationFn: (payload: { itemId: string }) => {
      requireReadableItem(payload.itemId)
      return apiFetch<{ status: 'queued' }>(`/items/${payload.itemId}/retry-article-fetch`, { method: 'POST' })
    },
    onMutate: ({ itemId }) => {
      pending.begin('retry', itemId)
      setArticleRetryFeedbackByItemId((current) => {
        const next = { ...current }
        delete next[itemId]
        return next
      })
      return captureReadAdmission(itemId)
    },
    onSuccess: async (_data, variables, admitted) => {
      if (admitted()) setArticleRetryFeedbackByItemId((current) => ({
        ...current,
        [variables.itemId]: {
          tone: 'success',
          message: 'Article fetch queued. Check back in a moment for refreshed content.',
        },
      }))
      await Promise.allSettled([
        queryClient.invalidateQueries({ queryKey: ['item', variables.itemId] }),
        refreshDashboardItem(variables.itemId),
      ])
    },
    onError: (error, variables) => {
      setArticleRetryFeedbackByItemId((current) => ({
        ...current,
        [variables.itemId]: {
          tone: 'error',
          message: resolveApiErrorMessage(error, 'Article fetch could not be queued'),
        },
      }))
    },
    onSettled: (_data, _error, variables) => pending.finish('retry', variables.itemId),
  })

  const markItemReadIfNeeded = (itemId: string, isRead: boolean) => {
    const admission = captureReadAdmission(itemId)
    if (isRead || !canManage || pending.isPending('read', itemId) || !admission()) {
      return
    }
    const payload = { itemId, isRead: true }
    automaticReadAdmissions.current.set(payload, admission)
    updateRead.mutate(payload)
  }

  return {
    isItemActionPending: pending.isPending,
    markItemReadIfNeeded,
    retryArticleFetch,
    updateNote,
    updateRead,
    updateStar,
  }
}
