import {
  type Dispatch,
  type RefObject,
  type SetStateAction,
  useEffect,
  useRef,
} from 'react'

import { safeLocalStorage } from '../utils/safeStorage'
import {
  clampArticlePreviewWidth,
  getWindowContainerDimensions,
  loadStoredTimestamp,
  loadWindowSeenState,
} from './dashboardPageUtils'
import {
  createWindowLayout,
  normalizeDashboardWindows,
  serializeDashboardWindowLayouts,
  type DashboardWindow,
} from './dashboardSavedViews'
import { loadDashboardWindows, loadStoredDashboardWindows } from './dashboardWindowStorage'
import { getDashboardStorageKeys, migrateLegacyDashboardStorage } from './dashboardStorage'

type FeedbackByItemId = Record<string, { tone: 'success' | 'error'; message: string }>

type WorkspacePersistenceOptions = {
  aiDailyBriefEnabled: boolean
  defaultPanelIds: readonly DashboardWindow['type'][]
  expandedItemIdsByWindowId: Record<string, string>
  isWideLayout: boolean
  rootRef: RefObject<HTMLDivElement | null>
  savedNoteValuesByItemIdRef: { current: Record<string, string> }
  setArticlePreviewWidth: Dispatch<SetStateAction<number>>
  setExpandedItemIdsByWindowId: Dispatch<SetStateAction<Record<string, string>>>
  setIsPhoneLayout: Dispatch<SetStateAction<boolean>>
  setIsWideLayout: Dispatch<SetStateAction<boolean>>
  setItemActionFeedbackByItemId: Dispatch<SetStateAction<FeedbackByItemId>>
  setMobileActiveWindowId: Dispatch<SetStateAction<string | null>>
  setNoteDraftsByItemId: Dispatch<SetStateAction<Record<string, string>>>
  setRssLastOpenedAt: Dispatch<SetStateAction<string>>
  setWindowSeenAt: Dispatch<SetStateAction<Record<string, string>>>
  setWindows: Dispatch<SetStateAction<DashboardWindow[]>>
  userId: string | null
  windowSeenAt: Record<string, string>
  windows: DashboardWindow[]
  workspaceDefaultsSettled: boolean
}

export function useDashboardWorkspacePersistence({
  aiDailyBriefEnabled,
  defaultPanelIds,
  expandedItemIdsByWindowId,
  isWideLayout,
  rootRef,
  savedNoteValuesByItemIdRef,
  setArticlePreviewWidth,
  setExpandedItemIdsByWindowId,
  setIsPhoneLayout,
  setIsWideLayout,
  setItemActionFeedbackByItemId,
  setMobileActiveWindowId,
  setNoteDraftsByItemId,
  setRssLastOpenedAt,
  setWindowSeenAt,
  setWindows,
  userId,
  windowSeenAt,
  windows,
  workspaceDefaultsSettled,
}: WorkspacePersistenceOptions) {
  const initializedDashboardUserRef = useRef<string | null>(null)
  const windowPersistenceTimeoutRef = useRef<number | null>(null)
  const pendingWindowPersistenceRef = useRef<{ userId: string; serialized: string } | null>(null)
  const persistedWindowUserIdRef = useRef<string | null>(null)

  const flushPendingWindowPersistence = (targetUserId?: string | null) => {
    if (typeof window === 'undefined') {
      return
    }

    const pending = pendingWindowPersistenceRef.current
    if (!pending) {
      return
    }

    if (targetUserId !== undefined && pending.userId !== targetUserId) {
      return
    }

    if (windowPersistenceTimeoutRef.current !== null) {
      window.clearTimeout(windowPersistenceTimeoutRef.current)
      windowPersistenceTimeoutRef.current = null
    }

    const storageKeys = getDashboardStorageKeys(pending.userId)
    safeLocalStorage.setItem(storageKeys.windows, pending.serialized)
    pendingWindowPersistenceRef.current = null
  }

  useEffect(() => {
    const syncLayout = () => {
      const nextWide = window.innerWidth >= 1024
      setIsWideLayout(nextWide)
      setIsPhoneLayout(window.innerWidth < 640)
      setArticlePreviewWidth((current) => clampArticlePreviewWidth(current))

      if (!nextWide) {
        return
      }

      const { width, height } = getWindowContainerDimensions(rootRef.current)
      setWindows((current) => normalizeDashboardWindows(current, width, height))
    }

    syncLayout()
    window.addEventListener('resize', syncLayout)
    return () => window.removeEventListener('resize', syncLayout)
  }, [rootRef, setArticlePreviewWidth, setIsPhoneLayout, setIsWideLayout, setWindows])

  useEffect(() => {
    if (isWideLayout) {
      return
    }

    setMobileActiveWindowId((current) => {
      if (current && windows.some((windowLayout) => windowLayout.id === current)) {
        return current
      }
      return windows[0]?.id ?? null
    })
  }, [isWideLayout, setMobileActiveWindowId, windows])

  useEffect(() => {
    if (isWideLayout || Object.keys(expandedItemIdsByWindowId).length === 0) {
      return
    }

    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previousOverflow
    }
  }, [expandedItemIdsByWindowId, isWideLayout])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }
    if (!userId || initializedDashboardUserRef.current !== userId) {
      return
    }
    const storageKeys = getDashboardStorageKeys(userId)
    const { width, height } = getWindowContainerDimensions(rootRef.current)
    const serialized = JSON.stringify(serializeDashboardWindowLayouts(windows, { width, height }))
    if (windowPersistenceTimeoutRef.current !== null) {
      window.clearTimeout(windowPersistenceTimeoutRef.current)
    }
    pendingWindowPersistenceRef.current = { userId, serialized }

    windowPersistenceTimeoutRef.current = window.setTimeout(() => {
      safeLocalStorage.setItem(storageKeys.windows, serialized)
      pendingWindowPersistenceRef.current = null
      windowPersistenceTimeoutRef.current = null
    }, 200)

    return () => {
      if (windowPersistenceTimeoutRef.current !== null) {
        window.clearTimeout(windowPersistenceTimeoutRef.current)
        windowPersistenceTimeoutRef.current = null
      }
    }
  }, [rootRef, userId, windows])

  useEffect(() => {
    const previousUserId = persistedWindowUserIdRef.current
    if (previousUserId && previousUserId !== userId) {
      flushPendingWindowPersistence(previousUserId)
    }
    persistedWindowUserIdRef.current = userId
  }, [userId])

  useEffect(() => () => flushPendingWindowPersistence(), [])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }
    if (!userId || initializedDashboardUserRef.current !== userId) {
      return
    }
    const storageKeys = getDashboardStorageKeys(userId)
    safeLocalStorage.setItem(storageKeys.windowSeenAt, JSON.stringify(windowSeenAt))
  }, [userId, windowSeenAt])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }

    if (!userId) {
      initializedDashboardUserRef.current = null
      setWindows([createWindowLayout('rss', 1, 1380, 760, 'full')])
      setWindowSeenAt({})
      setRssLastOpenedAt('')
      setExpandedItemIdsByWindowId({})
      setNoteDraftsByItemId({})
      savedNoteValuesByItemIdRef.current = {}
      setItemActionFeedbackByItemId({})
      return
    }

    migrateLegacyDashboardStorage(userId)

    if (initializedDashboardUserRef.current === userId) {
      return
    }

    const storageKeys = getDashboardStorageKeys(userId)
    const { width, height } = getWindowContainerDimensions(rootRef.current)
    const storedWindows = loadStoredDashboardWindows(storageKeys.windows, width, height)
    // Organization panel choices seed only a missing local layout; they never replace an established workspace.
    if (!workspaceDefaultsSettled && !storedWindows) {
      return
    }
    setWindows(storedWindows ?? loadDashboardWindows(storageKeys.windows, width, height, defaultPanelIds))
    setWindowSeenAt(loadWindowSeenState(storageKeys.windowSeenAt))
    setRssLastOpenedAt(loadStoredTimestamp(storageKeys.lastOpenedAt))
    safeLocalStorage.setItem(storageKeys.lastOpenedAt, new Date().toISOString())
    initializedDashboardUserRef.current = userId
  }, [
    rootRef,
    defaultPanelIds,
    savedNoteValuesByItemIdRef,
    setExpandedItemIdsByWindowId,
    setItemActionFeedbackByItemId,
    setNoteDraftsByItemId,
    setRssLastOpenedAt,
    setWindowSeenAt,
    setWindows,
    userId,
    workspaceDefaultsSettled,
  ])

  useEffect(() => {
    setWindowSeenAt((current) => {
      const next: Record<string, string> = {}
      let changed = false
      const seed = new Date().toISOString()
      for (const layout of windows) {
        if (layout.type !== 'alerts') {
          continue
        }
        if (current[layout.id]) {
          next[layout.id] = current[layout.id]
          continue
        }
        next[layout.id] = seed
        changed = true
      }
      if (!changed && Object.keys(next).length === Object.keys(current).length) {
        return current
      }
      return next
    })
  }, [setWindowSeenAt, windows])

  useEffect(() => {
    if (aiDailyBriefEnabled) {
      return
    }

    const { width, height } = getWindowContainerDimensions(rootRef.current)
    setWindows((current) => {
      const filtered = current.filter((window) => window.type !== 'daily_brief')
      if (filtered.length === current.length) {
        return current
      }
      if (!filtered.length) {
        return [createWindowLayout('rss', 1, width, height, 'full')]
      }
      return normalizeDashboardWindows(filtered, width, height)
    })
  }, [aiDailyBriefEnabled, rootRef, setWindows])
}
