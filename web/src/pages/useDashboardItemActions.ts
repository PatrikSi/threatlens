import { type Dispatch, type SetStateAction } from 'react'
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

export function useDashboardItemActions({
  canManage,
  queryClient,
  savedNoteValuesByItemIdRef,
  setArticleRetryFeedbackByItemId,
  setItemActionFeedbackByItemId,
  setNoteDraftsByItemId,
}: {
  canManage: boolean
  queryClient: QueryClient
  savedNoteValuesByItemIdRef: { current: Record<string, string> }
  setArticleRetryFeedbackByItemId: Dispatch<SetStateAction<FeedbackByItemId>>
  setItemActionFeedbackByItemId: Dispatch<SetStateAction<FeedbackByItemId>>
  setNoteDraftsByItemId: Dispatch<SetStateAction<Record<string, string>>>
}) {
  const pending = usePendingEntityActions()

  const updateRead = useMutation({
    mutationKey: ['items', 'read'],
    mutationFn: (payload: { itemId: string; isRead: boolean }) =>
      apiFetch(`/items/${payload.itemId}/read`, {
        method: 'POST',
        body: JSON.stringify({ is_read: payload.isRead }),
      }),
    onMutate: ({ itemId }) => {
      pending.begin('read', itemId)
      clearItemFeedback(setItemActionFeedbackByItemId, itemId)
    },
    onSuccess: (_data, variables) => {
      syncItemStateInCache(queryClient, variables.itemId, { isRead: variables.isRead })
      setItemActionFeedbackByItemId((current) => ({
        ...current,
        [variables.itemId]: {
          tone: 'success',
          message: variables.isRead ? 'Marked article as read.' : 'Marked article as unread.',
        },
      }))
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
    onSettled: (_data, _error, variables) => pending.finish('read', variables.itemId),
  })

  const updateStar = useMutation({
    mutationKey: ['items', 'star'],
    mutationFn: (payload: { itemId: string; isStarred: boolean }) =>
      apiFetch(`/items/${payload.itemId}/star`, {
        method: 'POST',
        body: JSON.stringify({ is_starred: payload.isStarred }),
      }),
    onMutate: ({ itemId }) => {
      pending.begin('star', itemId)
      clearItemFeedback(setItemActionFeedbackByItemId, itemId)
    },
    onSuccess: (_data, variables) => {
      syncItemStateInCache(queryClient, variables.itemId, { isStarred: variables.isStarred })
      setItemActionFeedbackByItemId((current) => ({
        ...current,
        [variables.itemId]: {
          tone: 'success',
          message: variables.isStarred ? 'Starred article.' : 'Removed star from article.',
        },
      }))
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
    mutationFn: (payload: { itemId: string; note: string | null }) =>
      apiFetch(`/items/${payload.itemId}/note`, {
        method: 'POST',
        body: JSON.stringify({ note: payload.note }),
      }),
    onMutate: ({ itemId }) => {
      pending.begin('note', itemId)
      clearItemFeedback(setItemActionFeedbackByItemId, itemId)
    },
    onSuccess: (_data, variables) => {
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
    mutationFn: (payload: { itemId: string }) =>
      apiFetch<{ status: 'queued' }>(`/items/${payload.itemId}/retry-article-fetch`, { method: 'POST' }),
    onMutate: ({ itemId }) => {
      pending.begin('retry', itemId)
      setArticleRetryFeedbackByItemId((current) => {
        const next = { ...current }
        delete next[itemId]
        return next
      })
    },
    onSuccess: async (_data, variables) => {
      setArticleRetryFeedbackByItemId((current) => ({
        ...current,
        [variables.itemId]: {
          tone: 'success',
          message: 'Article fetch queued. Check back in a moment for refreshed content.',
        },
      }))
      await queryClient.invalidateQueries({ queryKey: ['item', variables.itemId] })
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
    if (isRead || !canManage || pending.isPending('read', itemId)) {
      return
    }
    updateRead.mutate({ itemId, isRead: true })
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
