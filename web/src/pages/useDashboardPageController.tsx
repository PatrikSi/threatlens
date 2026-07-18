import {
  ChangeEvent,
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
  useDeferredValue,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from 'react'
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { ApiError, apiFetch } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { type ArticlePreviewState } from './DashboardPageComponents'
import {
  applyDragMagnetSnap,
  buildDashboardItemsQueryKey,
  clamp,
  clampArticlePreviewWidth,
  clearItemFeedback,
  deriveTimeWindow,
  formatSavedViewImportFailure,
  formatSavedViewImportResult,
  getRelativeTimeAnchorMs,
  getWindowContainerDimensions,
  isRelativeTimeRange,
  loadArticlePreviewWidth,
  loadStoredTimestamp,
  loadWindowSeenState,
  persistArticlePreviewWidth,
  resolveItemActionError,
  resolveDashboardViewSaveError,
  resolveWindowTimeFilter,
  syncItemStateInCache,
} from './dashboardPageUtils'
import { DASHBOARD_TIME_INHERIT_VALUE, MOBILE_DASHBOARD_PAGE_SIZE } from './dashboardPanelPresentation'
import {
  getDashboardStorageKeys,
  migrateLegacyDashboardStorage,
} from './dashboardStorage'
import {
  summarizeGlobalSearchAcrossWindows,
} from './dashboardState'
import {
  buildSavedViewPreview,
  buildDashboardSavedViewState,
  createDefaultAlertWindowFilters,
  createDefaultRssWindowFilters,
  createWindowLayout,
  DEFAULT_ROLLING_DAYS,
  getSnapRect,
  HIDDEN_TAGS,
  isTimeRangeFilter,
  loadDashboardWindows,
  normalizeDashboardWindows,
  normalizeRollingDaysInput,
  parseDashboardSavedView,
  parseImportedSavedViews,
  resolveFloatingPanelRect,
  resolveWindowRect,
  serializeDashboardWindowLayouts,
  withPanelRectPercentages,
  WINDOW_MIN_HEIGHT,
  WINDOW_MIN_WIDTH,
  type DashboardSavedViewPreview,
  type DashboardAlertWindowFilters,
  type DashboardRssWindowFilters,
  type DashboardSavedViewState,
  type DashboardWindow,
  type DashboardWindowSnap,
  type DashboardWindowType,
  type TimeRangeFilter,
  type WindowTimeFilter,
} from './dashboardSavedViews'
import {
  AIDailyBrief,
  AlertInterest,
  AlertMatchListResponse,
  Feed,
  ItemDetail,
  ItemListResponse,
  SavedView,
  Tag,
} from '../types/api'

type DashboardEditSessionSnapshot = {
  activeSavedViewId: string | null
  savedViewName: string
  state: DashboardSavedViewState
}

const MAX_VIEWS_IMPORT_FILE_BYTES = 2_000_000
const FEED_BOOTSTRAP_REFETCH_INTERVAL_MS = 5_000
const RSS_WINDOW_REFETCH_INTERVAL_MS = 60_000

export function useDashboardPageController() {
  const queryClient = useQueryClient()
  const meQuery = useCurrentUser()
  const rootRef = useRef<HTMLDivElement | null>(null)
  const addWindowMenuId = useId()
  const aiFeatures = meQuery.data?.features

  const [dashboardTimeRange, setDashboardTimeRange] = useState<TimeRangeFilter>('all')
  const [dashboardCustomSinceDate, setDashboardCustomSinceDate] = useState('')
  const [dashboardCustomUntilDate, setDashboardCustomUntilDate] = useState('')
  const [dashboardRollingDays, setDashboardRollingDays] = useState(DEFAULT_ROLLING_DAYS)

  const [savedViewName, setSavedViewName] = useState('')
  const [activeSavedViewId, setActiveSavedViewId] = useState<string | null>(null)
  const [pendingViewDelete, setPendingViewDelete] = useState<DashboardSavedViewPreview | null>(null)
  const [pendingSavedViewLoad, setPendingSavedViewLoad] = useState<{ id: string; name: string } | null>(null)
  const [showManageViewsModal, setShowManageViewsModal] = useState(false)
  const [isImportingViews, setIsImportingViews] = useState(false)
  const [importViewsError, setImportViewsError] = useState('')
  const [importViewsResult, setImportViewsResult] = useState('')
  const [mobileDashboardViewsOpen, setMobileDashboardViewsOpen] = useState(false)
  const [mobileActiveWindowId, setMobileActiveWindowId] = useState<string | null>(null)
  const [mobileWindowControlsOpenById, setMobileWindowControlsOpenById] = useState<Record<string, boolean>>({})
  const [isEditMode, setIsEditMode] = useState(false)
  const [viewSaveError, setViewSaveError] = useState('')
  const [viewDeleteError, setViewDeleteError] = useState('')

  const [showAddWindowMenu, setShowAddWindowMenu] = useState(false)
  const [showSaveAsNew, setShowSaveAsNew] = useState(false)
  const [editSessionSnapshot, setEditSessionSnapshot] = useState<DashboardEditSessionSnapshot | null>(null)
  const [, setOpenWindowMenuId] = useState<string | null>(null)
  const [renamingWindowId, setRenamingWindowId] = useState<string | null>(null)
  const [renameWindowDraft, setRenameWindowDraft] = useState('')
  const [relativeTimeAnchorMs, setRelativeTimeAnchorMs] = useState(() => getRelativeTimeAnchorMs())

  const [expandedItemIdsByWindowId, setExpandedItemIdsByWindowId] = useState<Record<string, string>>({})
  const [noteDraftsByItemId, setNoteDraftsByItemId] = useState<Record<string, string>>({})
  const [itemActionFeedbackByItemId, setItemActionFeedbackByItemId] = useState<
    Record<string, { tone: 'success' | 'error'; message: string }>
  >({})
  const [articleRetryFeedbackByItemId, setArticleRetryFeedbackByItemId] = useState<
    Record<string, { tone: 'success' | 'error'; message: string }>
  >({})
  const [articlePreview, setArticlePreview] = useState<ArticlePreviewState | null>(null)
  const [articlePreviewFrameState, setArticlePreviewFrameState] = useState<'loading' | 'loaded' | 'possibly_blocked'>(
    'loading',
  )
  const [articlePreviewWidth, setArticlePreviewWidth] = useState(() => loadArticlePreviewWidth())
  const [isArticlePreviewResizing, setIsArticlePreviewResizing] = useState(false)
  const [isPhoneLayout, setIsPhoneLayout] = useState<boolean>(typeof window !== 'undefined' ? window.innerWidth < 640 : false)

  const [windows, setWindows] = useState<DashboardWindow[]>(() => [createWindowLayout('rss', 1, 1380, 760, 'full')])
  const [windowSeenAt, setWindowSeenAt] = useState<Record<string, string>>({})
  const [rssLastOpenedAt, setRssLastOpenedAt] = useState('')
  const [isWideLayout, setIsWideLayout] = useState<boolean>(typeof window !== 'undefined' ? window.innerWidth >= 1024 : true)
  const initializedDashboardUserRef = useRef<string | null>(null)
  const windowPersistenceTimeoutRef = useRef<number | null>(null)
  const pendingWindowPersistenceRef = useRef<{ userId: string; serialized: string } | null>(null)
  const persistedWindowUserIdRef = useRef<string | null>(null)
  const renameWindowInputRef = useRef<HTMLInputElement | null>(null)
  const addWindowTriggerRef = useRef<HTMLButtonElement | null>(null)
  const addWindowMenuRef = useRef<HTMLDivElement | null>(null)
  const addWindowActionRefs = useRef<Array<HTMLButtonElement | null>>([])
  const pendingAddWindowFocusIndexRef = useRef<number | null>(null)
  const importViewsInputRef = useRef<HTMLInputElement | null>(null)
  const savedNoteValuesByItemIdRef = useRef<Record<string, string>>({})
  const articlePreviewResizeCleanupRef = useRef<(() => void) | null>(null)

  const canManage = meQuery.data?.role === 'admin' || meQuery.data?.role === 'analyst'
  const aiSummaryEnabled = Boolean(aiFeatures?.ai_summary_enabled)
  const aiRelevanceEnabled = Boolean(aiFeatures?.ai_relevance_enabled)
  const aiDailyBriefEnabled = Boolean(aiFeatures?.ai_daily_brief_enabled)
  const hasProtectedEditSession = isEditMode && editSessionSnapshot !== null

  const openArticlePreview = (preview: ArticlePreviewState) => {
    setArticlePreviewFrameState('loading')
    setArticlePreview(preview)
  }

  const closeArticlePreview = () => {
    setArticlePreview(null)
  }

  const updateArticlePreviewWidth = (width: number) => {
    const nextWidth = clampArticlePreviewWidth(width)
    setArticlePreviewWidth(nextWidth)
    persistArticlePreviewWidth(nextWidth)
  }

  const adjustArticlePreviewWidth = (delta: number) => {
    updateArticlePreviewWidth(articlePreviewWidth + delta)
  }

  const startArticlePreviewResize = (event: ReactPointerEvent<HTMLElement>) => {
    event.preventDefault()
    event.stopPropagation()

    articlePreviewResizeCleanupRef.current?.()

    const resizeHandle = event.currentTarget
    const pointerId = event.pointerId
    const startX = event.clientX
    const startWidth = articlePreviewWidth
    const previousCursor = document.body.style.cursor
    const previousUserSelect = document.body.style.userSelect

    try {
      resizeHandle.setPointerCapture(pointerId)
    } catch {
      // Pointer capture is best-effort in tests and older browser engines.
    }

    document.body.style.cursor = 'ew-resize'
    document.body.style.userSelect = 'none'
    setIsArticlePreviewResizing(true)

    const handlePointerMove = (moveEvent: PointerEvent) => {
      if (moveEvent.pointerId !== pointerId) return
      moveEvent.preventDefault()
      updateArticlePreviewWidth(startWidth + startX - moveEvent.clientX)
    }

    const cleanup = () => {
      document.removeEventListener('pointermove', handlePointerMove, true)
      document.removeEventListener('pointerup', handlePointerEnd, true)
      document.removeEventListener('pointercancel', handlePointerEnd, true)
      resizeHandle.removeEventListener('lostpointercapture', cleanup)
      window.removeEventListener('blur', cleanup)
      try {
        if (resizeHandle.hasPointerCapture(pointerId)) {
          resizeHandle.releasePointerCapture(pointerId)
        }
      } catch {
        // The browser may already have released capture by this point.
      }
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousUserSelect
      setIsArticlePreviewResizing(false)
      articlePreviewResizeCleanupRef.current = null
    }

    const handlePointerEnd = (endEvent: PointerEvent) => {
      if (endEvent.pointerId === pointerId) {
        cleanup()
      }
    }

    articlePreviewResizeCleanupRef.current = cleanup
    document.addEventListener('pointermove', handlePointerMove, true)
    document.addEventListener('pointerup', handlePointerEnd, true)
    document.addEventListener('pointercancel', handlePointerEnd, true)
    resizeHandle.addEventListener('lostpointercapture', cleanup)
    window.addEventListener('blur', cleanup)
  }

  const hasUnsavedNoteDrafts = useMemo(
    () =>
      Object.entries(noteDraftsByItemId).some(
        ([itemId, noteDraft]) => noteDraft !== (savedNoteValuesByItemIdRef.current[itemId] ?? ''),
      ),
    [noteDraftsByItemId],
  )
  const hasUnsavedDashboardChanges = hasProtectedEditSession || hasUnsavedNoteDrafts
  const confirmDiscardUnsavedDashboardChanges = useUnsavedChangesWarning(
    hasUnsavedDashboardChanges,
    hasProtectedEditSession
      ? 'You have an unsaved dashboard layout edit session. Leave without saving?'
      : 'You have unsaved dashboard note drafts. Leave without saving?',
  )

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
    window.localStorage.setItem(storageKeys.windows, pending.serialized)
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
  }, [])

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
  }, [isWideLayout, windows])

  useEffect(() => () => articlePreviewResizeCleanupRef.current?.(), [])

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
    if (!articlePreview) {
      return
    }

    const blockedNoticeTimeout = window.setTimeout(() => {
      setArticlePreviewFrameState((current) => (current === 'loading' ? 'possibly_blocked' : current))
    }, 5000)
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setArticlePreview(null)
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.clearTimeout(blockedNoticeTimeout)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [articlePreview])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }
    const userId = meQuery.data?.id
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
      window.localStorage.setItem(storageKeys.windows, serialized)
      pendingWindowPersistenceRef.current = null
      windowPersistenceTimeoutRef.current = null
    }, 200)

    return () => {
      if (windowPersistenceTimeoutRef.current !== null) {
        window.clearTimeout(windowPersistenceTimeoutRef.current)
        windowPersistenceTimeoutRef.current = null
      }
    }
  }, [meQuery.data?.id, windows])

  useEffect(() => {
    const userId = meQuery.data?.id ?? null
    const previousUserId = persistedWindowUserIdRef.current
    if (previousUserId && previousUserId !== userId) {
      flushPendingWindowPersistence(previousUserId)
    }
    persistedWindowUserIdRef.current = userId
  }, [meQuery.data?.id])

  useEffect(() => () => flushPendingWindowPersistence(), [])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }
    const userId = meQuery.data?.id
    if (!userId || initializedDashboardUserRef.current !== userId) {
      return
    }
    const storageKeys = getDashboardStorageKeys(userId)
    window.localStorage.setItem(storageKeys.windowSeenAt, JSON.stringify(windowSeenAt))
  }, [meQuery.data?.id, windowSeenAt])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }

    const userId = meQuery.data?.id
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
    setWindows(loadDashboardWindows(storageKeys.windows, width, height))
    setWindowSeenAt(loadWindowSeenState(storageKeys.windowSeenAt))
    setRssLastOpenedAt(loadStoredTimestamp(storageKeys.lastOpenedAt))
    window.localStorage.setItem(storageKeys.lastOpenedAt, new Date().toISOString())
    initializedDashboardUserRef.current = userId
  }, [meQuery.data?.id])

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
  }, [windows])

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
  }, [aiDailyBriefEnabled])

  const deferredWindows = useDeferredValue(windows)

  const dashboardTimeFilter = useMemo<WindowTimeFilter>(
    () => ({
      time_range: dashboardTimeRange,
      custom_since_date: dashboardCustomSinceDate,
      custom_until_date: dashboardCustomUntilDate,
      rolling_days: dashboardRollingDays,
    }),
    [dashboardTimeRange, dashboardCustomSinceDate, dashboardCustomUntilDate, dashboardRollingDays],
  )
  const hasRelativeTimeScope = useMemo(
    () =>
      isRelativeTimeRange(dashboardTimeFilter.time_range) ||
      windows.some(
        (windowLayout) =>
          (windowLayout.type === 'rss' || windowLayout.type === 'alerts') &&
          windowLayout.time_override !== null &&
          isRelativeTimeRange(windowLayout.time_override.time_range),
      ),
    [dashboardTimeFilter.time_range, windows],
  )

  const rssWindows = useMemo(
    () => deferredWindows.filter((window): window is DashboardWindow & { type: 'rss' } => window.type === 'rss'),
    [deferredWindows],
  )
  const alertWindows = useMemo(
    () => deferredWindows.filter((window): window is DashboardWindow & { type: 'alerts' } => window.type === 'alerts'),
    [deferredWindows],
  )
  const rssDeferredSearchTermsByWindowId = useDeferredValue(
    useMemo(
      () =>
        Object.fromEntries(
          rssWindows.map((windowLayout) => [windowLayout.id, (windowLayout.rss_filters ?? createDefaultRssWindowFilters()).q]),
        ) as Record<string, string>,
      [rssWindows],
    ),
  )
  const alertDeferredSearchTermsByWindowId = useDeferredValue(
    useMemo(
      () =>
        Object.fromEntries(
          alertWindows.map((windowLayout) => [windowLayout.id, (windowLayout.alert_filters ?? createDefaultAlertWindowFilters()).q]),
        ) as Record<string, string>,
      [alertWindows],
    ),
  )

  useEffect(() => {
    if (!hasRelativeTimeScope) {
      return
    }

    const syncAnchor = () => setRelativeTimeAnchorMs(getRelativeTimeAnchorMs())
    syncAnchor()
    const intervalId = window.setInterval(syncAnchor, 60_000)

    return () => window.clearInterval(intervalId)
  }, [hasRelativeTimeScope])

  const feedsQuery = useQuery({
    queryKey: ['feeds'],
    queryFn: () => apiFetch<Feed[]>('/feeds'),
    staleTime: 300_000,
    refetchInterval: (query) => {
      const feeds = query.state.data as Feed[] | undefined
      const hasPendingFirstFetch = feeds?.some((feed) => feed.enabled && !feed.last_success_at && !feed.has_unreadable_url) ?? false
      return hasPendingFirstFetch ? FEED_BOOTSTRAP_REFETCH_INTERVAL_MS : false
    },
  })

  const feedStatusSignature = useMemo(() => {
    if (!feedsQuery.data) {
      return null
    }
    return feedsQuery.data
      .map((feed) => `${feed.id}:${feed.last_success_at ?? ''}:${feed.error_count}:${feed.last_error ?? ''}`)
      .join('|')
  }, [feedsQuery.data])

  useEffect(() => {
    if (feedStatusSignature === null) {
      return
    }
    void queryClient.invalidateQueries({ queryKey: ['items'] })
  }, [feedStatusSignature, queryClient])

  const viewsQuery = useQuery({
    queryKey: ['views'],
    queryFn: () => apiFetch<SavedView[]>('/views'),
    staleTime: 300_000,
  })

  const tagsQuery = useQuery({
    queryKey: ['tags'],
    queryFn: () => apiFetch<Tag[]>('/tags'),
    staleTime: 300_000,
  })

  const alertInterestsQuery = useQuery({
    queryKey: ['alerts', 'enabled'],
    queryFn: () => apiFetch<AlertInterest[]>('/alerts?include_disabled=false'),
    staleTime: 300_000,
  })

  const saveView = useMutation({
    mutationKey: ['dashboard-saved-views', 'create'],
    mutationFn: (payload: { name: string; query: DashboardSavedViewState }) =>
      apiFetch<SavedView>('/views', {
        method: 'POST',
        body: JSON.stringify({
          name: payload.name,
          query_json: payload.query,
        }),
      }),
    onMutate: () => {
      setViewSaveError('')
    },
    onSuccess: (view) => {
      setSavedViewName('')
      setActiveSavedViewId(view.id)
      setEditSessionSnapshot(null)
      setIsEditMode(false)
      setShowAddWindowMenu(false)
      setOpenWindowMenuId(null)
      setShowSaveAsNew(false)
      queryClient.invalidateQueries({ queryKey: ['views'] })
    },
    onError: (error) => {
      setViewSaveError(resolveDashboardViewSaveError(error))
    },
  })

  const deleteView = useMutation({
    mutationKey: ['dashboard-saved-views', 'delete'],
    mutationFn: (viewId: string) =>
      apiFetch(`/views/${viewId}`, {
        method: 'DELETE',
      }),
    onSuccess: (_data, deletedViewId) => {
      setActiveSavedViewId((current) => (current === deletedViewId ? null : current))
      setEditSessionSnapshot((current) => {
        if (!current || current.activeSavedViewId !== deletedViewId) {
          return current
        }
        return {
          ...current,
          activeSavedViewId: null,
        }
      })
      setPendingSavedViewLoad((current) => (current?.id === deletedViewId ? null : current))
      setPendingViewDelete((current) => (current?.id === deletedViewId ? null : current))
      setViewDeleteError('')
      queryClient.invalidateQueries({ queryKey: ['views'] })
    },
    onError: (error) => {
      setViewDeleteError(resolveItemActionError(error, 'Unable to delete the saved view.'))
    },
  })

  const onConfirmDeleteView = () => {
    if (!pendingViewDelete) {
      return
    }

    const viewId = pendingViewDelete.id
    setViewDeleteError('')
    deleteView.mutate(viewId)
  }

  const updateExistingView = useMutation({
    mutationKey: ['dashboard-saved-views', 'update'],
    mutationFn: (payload: { viewId: string; name?: string; query?: DashboardSavedViewState }) =>
      apiFetch<SavedView>(`/views/${payload.viewId}`, {
        method: 'PATCH',
        body: JSON.stringify({
          ...(payload.name !== undefined ? { name: payload.name } : {}),
          ...(payload.query !== undefined ? { query_json: payload.query } : {}),
        }),
      }),
    onMutate: () => {
      setViewSaveError('')
    },
    onSuccess: (view) => {
      setActiveSavedViewId(view.id)
      setEditSessionSnapshot(null)
      setIsEditMode(false)
      setShowAddWindowMenu(false)
      setOpenWindowMenuId(null)
      setShowSaveAsNew(false)
      queryClient.invalidateQueries({ queryKey: ['views'] })
    },
    onError: (error) => {
      setViewSaveError(resolveDashboardViewSaveError(error))
    },
  })

  const updateRead = useMutation({
    mutationKey: ['items', 'read'],
    mutationFn: (payload: { itemId: string; isRead: boolean }) =>
      apiFetch(`/items/${payload.itemId}/read`, {
        method: 'POST',
        body: JSON.stringify({ is_read: payload.isRead }),
      }),
    onMutate: ({ itemId }) => {
      clearItemFeedback(setItemActionFeedbackByItemId, itemId)
    },
    onSuccess: (_data, variables) => {
      syncItemStateInCache(queryClient, variables.itemId, {
        isRead: variables.isRead,
      })
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
  })

  const markItemReadIfNeeded = (itemId: string, isRead: boolean) => {
    if (isRead || !canManage || (updateRead.isPending && updateRead.variables?.itemId === itemId)) {
      return
    }

    updateRead.mutate({
      itemId,
      isRead: true,
    })
  }

  const handleOpenArticlePreview = (preview: ArticlePreviewState, isRead: boolean) => {
    openArticlePreview(preview)
    markItemReadIfNeeded(preview.itemId, isRead)
  }

  const updateStar = useMutation({
    mutationKey: ['items', 'star'],
    mutationFn: (payload: { itemId: string; isStarred: boolean }) =>
      apiFetch(`/items/${payload.itemId}/star`, {
        method: 'POST',
        body: JSON.stringify({ is_starred: payload.isStarred }),
      }),
    onMutate: ({ itemId }) => {
      clearItemFeedback(setItemActionFeedbackByItemId, itemId)
    },
    onSuccess: (_data, variables) => {
      syncItemStateInCache(queryClient, variables.itemId, {
        isStarred: variables.isStarred,
      })
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
  })

  const updateNote = useMutation({
    mutationKey: ['items', 'note'],
    mutationFn: (payload: { itemId: string; note: string | null }) =>
      apiFetch(`/items/${payload.itemId}/note`, {
        method: 'POST',
        body: JSON.stringify({ note: payload.note }),
      }),
    onMutate: ({ itemId }) => {
      clearItemFeedback(setItemActionFeedbackByItemId, itemId)
    },
    onSuccess: (_data, variables) => {
      savedNoteValuesByItemIdRef.current[variables.itemId] = variables.note ?? ''
      setNoteDraftsByItemId((current) => {
        const savedNote = variables.note ?? ''
        if ((current[variables.itemId] ?? '') !== savedNote) {
          return current
        }
        return {
          ...current,
          [variables.itemId]: savedNote,
        }
      })
      syncItemStateInCache(queryClient, variables.itemId, {
        note: variables.note,
      })
      setItemActionFeedbackByItemId((current) => ({
        ...current,
        [variables.itemId]: {
          tone: 'success',
          message: 'Saved analyst notes.',
        },
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
  })

  const retryArticleFetch = useMutation({
    mutationKey: ['items', 'retry-article-fetch'],
    mutationFn: (payload: { itemId: string }) =>
      apiFetch<{ status: 'queued' }>(`/items/${payload.itemId}/retry-article-fetch`, {
        method: 'POST',
      }),
    onMutate: ({ itemId }) => {
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
      const message =
        error instanceof ApiError && error.message.trim()
          ? error.message
          : error instanceof Error && error.message.trim()
            ? error.message
            : 'Unable to queue article fetch right now.'
      setArticleRetryFeedbackByItemId((current) => ({
        ...current,
        [variables.itemId]: {
          tone: 'error',
          message,
        },
      }))
    },
  })
  const viewSavePending = saveView.isPending || updateExistingView.isPending

  const captureCurrentDashboardViewState = () => {
    const { width, height } = getWindowContainerDimensions(rootRef.current)
    return buildDashboardSavedViewState(
      windows,
      {
        time_range: dashboardTimeRange,
        custom_since_date: dashboardCustomSinceDate,
        custom_until_date: dashboardCustomUntilDate,
        rolling_days: dashboardRollingDays,
      },
      { width, height },
    )
  }

  const applyDashboardSavedViewState = (state: DashboardSavedViewState, nextActiveSavedViewId: string | null) => {
    const nextDashboardTimeRange =
      state.rss_filters.time_range !== 'all' ||
      state.rss_filters.custom_since_date ||
      state.rss_filters.custom_until_date ||
      state.rss_filters.rolling_days !== DEFAULT_ROLLING_DAYS
        ? state.rss_filters.time_range
        : state.alert_filters.time_range
    const nextDashboardCustomSinceDate =
      state.rss_filters.custom_since_date || state.alert_filters.custom_since_date || ''
    const nextDashboardCustomUntilDate =
      state.rss_filters.custom_until_date || state.alert_filters.custom_until_date || ''
    const nextDashboardRollingDays =
      state.rss_filters.rolling_days !== DEFAULT_ROLLING_DAYS
        ? state.rss_filters.rolling_days
        : state.alert_filters.rolling_days || DEFAULT_ROLLING_DAYS

    setDashboardTimeRange(nextDashboardTimeRange)
    setDashboardCustomSinceDate(nextDashboardCustomSinceDate)
    setDashboardCustomUntilDate(nextDashboardCustomUntilDate)
    setDashboardRollingDays(nextDashboardRollingDays)
    setExpandedItemIdsByWindowId({})
    setWindows(state.windows)
    setActiveSavedViewId(nextActiveSavedViewId)
  }

  const rssWindowQueries = useQueries({
    queries: rssWindows.map((windowLayout) => {
      const rssFilters = windowLayout.rss_filters ?? createDefaultRssWindowFilters()
      const effectivePageSize = isPhoneLayout ? MOBILE_DASHBOARD_PAGE_SIZE : rssFilters.page_size
      const deferredSearchQuery = rssDeferredSearchTermsByWindowId[windowLayout.id] ?? rssFilters.q
      const selectedFeedIdsParam = rssFilters.selected_feed_ids.slice().sort().join(',')
      const selectedTagsParam = rssFilters.selected_tags
        .filter((tagName) => !HIDDEN_TAGS.has(tagName))
        .slice()
        .sort()
        .join(',')
      const effectiveAiRelevance = aiRelevanceEnabled ? rssFilters.ai_relevance : 'all'
      const effectiveWindowTimeFilter = resolveWindowTimeFilter(windowLayout, dashboardTimeFilter)
      const timeWindow = deriveTimeWindow(
        effectiveWindowTimeFilter.time_range,
        effectiveWindowTimeFilter.custom_since_date,
        effectiveWindowTimeFilter.custom_until_date,
        effectiveWindowTimeFilter.rolling_days,
        relativeTimeAnchorMs,
      )

      return {
        queryKey: buildDashboardItemsQueryKey({
          selected_feed_ids: selectedFeedIdsParam,
          selected_tags: selectedTagsParam,
          q: deferredSearchQuery,
          read_status: rssFilters.read_status,
          star_status: rssFilters.star_status,
          ai_relevance: effectiveAiRelevance,
          since: timeWindow.sinceIso,
          until: timeWindow.untilIso,
          sort: rssFilters.sort,
          page: rssFilters.page,
          page_size: effectivePageSize,
        }),
        retry: 1,
        staleTime: 60_000,
        refetchInterval: rssFilters.page === 1 ? RSS_WINDOW_REFETCH_INTERVAL_MS : false,
        placeholderData: (previousData: ItemListResponse | undefined) => previousData,
        queryFn: () => {
          const params = new URLSearchParams()
          params.set('page', String(rssFilters.page))
          params.set('page_size', String(effectivePageSize))
          params.set('sort', rssFilters.sort)

          if (selectedFeedIdsParam) params.set('feed_ids', selectedFeedIdsParam)
          if (selectedTagsParam) params.set('tags', selectedTagsParam)
          if (deferredSearchQuery) params.set('q', deferredSearchQuery)
          if (timeWindow.sinceIso) params.set('since', timeWindow.sinceIso)
          if (timeWindow.untilIso) params.set('until', timeWindow.untilIso)

          if (rssFilters.read_status === 'read') params.set('is_read', 'true')
          if (rssFilters.read_status === 'unread') params.set('is_read', 'false')
          if (rssFilters.star_status === 'starred') params.set('is_starred', 'true')
          if (rssFilters.star_status === 'unstarred') params.set('is_starred', 'false')
          if (effectiveAiRelevance !== 'all') params.set('ai_relevance', effectiveAiRelevance)

          return apiFetch<ItemListResponse>(`/items?${params.toString()}`)
        },
      }
    }),
  })

  const alertWindowQueries = useQueries({
    queries: alertWindows.map((windowLayout) => {
      const alertFilters = windowLayout.alert_filters ?? createDefaultAlertWindowFilters()
      const effectivePageSize = isPhoneLayout ? MOBILE_DASHBOARD_PAGE_SIZE : alertFilters.page_size
      const deferredSearchQuery = alertDeferredSearchTermsByWindowId[windowLayout.id] ?? alertFilters.q
      const selectedAlertIdsParam = alertFilters.selected_alert_ids.slice().sort().join(',')
      const selectedAlertCategoriesParam = alertFilters.selected_categories.slice().sort().join(',')
      const effectiveWindowTimeFilter = resolveWindowTimeFilter(windowLayout, dashboardTimeFilter)
      const timeWindow = deriveTimeWindow(
        effectiveWindowTimeFilter.time_range,
        effectiveWindowTimeFilter.custom_since_date,
        effectiveWindowTimeFilter.custom_until_date,
        effectiveWindowTimeFilter.rolling_days,
        relativeTimeAnchorMs,
      )

      return {
        queryKey: [
          'alert-matches',
          selectedAlertIdsParam,
          selectedAlertCategoriesParam,
          deferredSearchQuery,
          timeWindow.sinceIso,
          timeWindow.untilIso,
          alertFilters.sort,
          alertFilters.page,
          effectivePageSize,
        ],
        staleTime: 60_000,
        placeholderData: (previousData: AlertMatchListResponse | undefined) => previousData,
        queryFn: () => {
          const params = new URLSearchParams()
          params.set('page', String(alertFilters.page))
          params.set('page_size', String(effectivePageSize))
          params.set('sort', alertFilters.sort)

          if (selectedAlertIdsParam) params.set('alert_ids', selectedAlertIdsParam)
          if (selectedAlertCategoriesParam) params.set('categories', selectedAlertCategoriesParam)
          if (deferredSearchQuery) params.set('q', deferredSearchQuery)
          if (timeWindow.sinceIso) params.set('since', timeWindow.sinceIso)
          if (timeWindow.untilIso) params.set('until', timeWindow.untilIso)

          return apiFetch<AlertMatchListResponse>(`/alerts/matches?${params.toString()}`)
        },
      }
    }),
  })

  const rssQueriesByWindowId = useMemo(
    () =>
      Object.fromEntries(rssWindows.map((windowLayout, index) => [windowLayout.id, rssWindowQueries[index] ?? null])) as Record<
        string,
        (typeof rssWindowQueries)[number] | null
      >,
    [rssWindowQueries, rssWindows],
  )

  const alertQueriesByWindowId = useMemo(
    () =>
      Object.fromEntries(alertWindows.map((windowLayout, index) => [windowLayout.id, alertWindowQueries[index] ?? null])) as Record<
        string,
        (typeof alertWindowQueries)[number] | null
      >,
    [alertWindowQueries, alertWindows],
  )

  useEffect(() => {
    setWindows((current) => {
      let changed = false
      const next = current.map((windowLayout) => {
        if (windowLayout.type === 'rss') {
          const filters = windowLayout.rss_filters ?? createDefaultRssWindowFilters()
          const query = rssQueriesByWindowId[windowLayout.id]
          if (!query?.data || query.isPlaceholderData || query.data.page !== filters.page) {
            return windowLayout
          }
          const totalPages = Math.max(1, Math.ceil(query.data.total / Math.max(1, query.data.page_size)))
          if (filters.page <= totalPages) {
            return windowLayout
          }
          changed = true
          return {
            ...windowLayout,
            rss_filters: {
              ...filters,
              page: totalPages,
            },
          }
        }

        if (windowLayout.type === 'alerts') {
          const filters = windowLayout.alert_filters ?? createDefaultAlertWindowFilters()
          const query = alertQueriesByWindowId[windowLayout.id]
          if (!query?.data || query.isPlaceholderData || query.data.page !== filters.page) {
            return windowLayout
          }
          const totalPages = Math.max(1, Math.ceil(query.data.total / Math.max(1, query.data.page_size)))
          if (filters.page <= totalPages) {
            return windowLayout
          }
          changed = true
          return {
            ...windowLayout,
            alert_filters: {
              ...filters,
              page: totalPages,
            },
          }
        }

        return windowLayout
      })
      return changed ? next : current
    })
  }, [alertQueriesByWindowId, rssQueriesByWindowId])

  useEffect(() => {
    const rssWindowIds = new Set(rssWindows.map((windowLayout) => windowLayout.id))
    setExpandedItemIdsByWindowId((current) => {
      const next = { ...current }
      let changed = false

      for (const windowId of Object.keys(next)) {
        if (rssWindowIds.has(windowId)) {
          continue
        }
        delete next[windowId]
        changed = true
      }

      for (const windowLayout of rssWindows) {
        const selectedItemId = current[windowLayout.id]
        if (!selectedItemId) {
          continue
        }
        const windowItems = rssQueriesByWindowId[windowLayout.id]?.data?.items
        if (!windowItems) {
          continue
        }
        if (windowItems.some((item) => item.id === selectedItemId)) {
          continue
        }
        delete next[windowLayout.id]
        changed = true
      }

      return changed ? next : current
    })
  }, [rssQueriesByWindowId, rssWindows])

  const detailQueries = useQueries({
    queries: rssWindows.map((windowLayout) => {
      const expandedItemId = expandedItemIdsByWindowId[windowLayout.id] ?? ''
      return {
        queryKey: ['item', expandedItemId],
        enabled: Boolean(expandedItemId),
        queryFn: () => apiFetch<ItemDetail>(`/items/${expandedItemId}`),
      }
    }),
  })

  const detailQueriesByWindowId = useMemo(
    () =>
      Object.fromEntries(rssWindows.map((windowLayout, index) => [windowLayout.id, detailQueries[index] ?? null])) as Record<
        string,
        (typeof detailQueries)[number] | null
      >,
    [detailQueries, rssWindows],
  )

  useEffect(() => {
    setNoteDraftsByItemId((current) => {
      let changed = false
      const next = { ...current }

      for (const windowLayout of rssWindows) {
        const detail = detailQueriesByWindowId[windowLayout.id]?.data
        if (!detail) {
          continue
        }

        const savedNote = detail.state.note ?? ''
        const previousSavedNote = savedNoteValuesByItemIdRef.current[detail.id]
        if (previousSavedNote !== savedNote) {
          savedNoteValuesByItemIdRef.current[detail.id] = savedNote
        }
        if ((next[detail.id] === undefined || next[detail.id] === previousSavedNote) && next[detail.id] !== savedNote) {
          next[detail.id] = savedNote
          changed = true
        }
      }

      return changed ? next : current
    })
  }, [detailQueriesByWindowId, rssWindows])

  const dailyBriefHistoryQuery = useQuery({
    queryKey: ['ai', 'daily-briefs'],
    enabled: aiDailyBriefEnabled,
    retry: false,
    queryFn: async () => {
      try {
        return await apiFetch<AIDailyBrief[]>('/ai/daily-briefs')
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) {
          return []
        }
        throw error
      }
    },
  })

  const availableAlertCategories = useMemo(() => {
    const categories = new Set<string>()
    for (const alert of alertInterestsQuery.data ?? []) {
      categories.add(alert.category)
    }
    return Array.from(categories).sort()
  }, [alertInterestsQuery.data])

  const closeAddWindowMenu = (restoreFocus = false) => {
    setShowAddWindowMenu(false)
    if (!restoreFocus) {
      return
    }
    addWindowTriggerRef.current?.focus()
  }

  const focusAddWindowAction = (index: number) => {
    const actions = addWindowActionRefs.current.filter((button): button is HTMLButtonElement => button !== null)
    if (!actions.length) {
      return
    }

    const normalizedIndex = ((index % actions.length) + actions.length) % actions.length
    actions[normalizedIndex]?.focus()
  }

  const openAddWindowMenu = (focusIndex = 0) => {
    pendingAddWindowFocusIndexRef.current = focusIndex
    setShowAddWindowMenu(true)
  }

  const handleAddWindowTriggerKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') {
      return
    }

    event.preventDefault()
    if (showAddWindowMenu) {
      focusAddWindowAction(event.key === 'ArrowUp' ? -1 : 0)
      return
    }

    openAddWindowMenu(event.key === 'ArrowUp' ? -1 : 0)
  }

  const handleAddWindowMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const actions = addWindowActionRefs.current.filter((button): button is HTMLButtonElement => button !== null)
    if (!actions.length) {
      return
    }

    const currentIndex = actions.findIndex((button) => button === document.activeElement)
    let nextIndex: number | null = null

    if (event.key === 'ArrowDown') {
      nextIndex = currentIndex < 0 ? 0 : currentIndex + 1
    } else if (event.key === 'ArrowUp') {
      nextIndex = currentIndex < 0 ? actions.length - 1 : currentIndex - 1
    } else if (event.key === 'Home') {
      nextIndex = 0
    } else if (event.key === 'End') {
      nextIndex = actions.length - 1
    }

    if (nextIndex === null) {
      return
    }

    event.preventDefault()
    focusAddWindowAction(nextIndex)
  }

  useEffect(() => {
    if (!showAddWindowMenu) {
      return
    }

    const requestedFocusIndex = pendingAddWindowFocusIndexRef.current ?? 0
    pendingAddWindowFocusIndexRef.current = null
    focusAddWindowAction(requestedFocusIndex)

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') {
        return
      }
      event.preventDefault()
      closeAddWindowMenu(true)
    }

    const onPointerDown = (event: MouseEvent) => {
      const target = event.target
      if (!(target instanceof Node)) {
        return
      }
      if (addWindowMenuRef.current?.contains(target) || addWindowTriggerRef.current?.contains(target)) {
        return
      }
      closeAddWindowMenu()
    }

    document.addEventListener('keydown', onKeyDown)
    document.addEventListener('mousedown', onPointerDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.removeEventListener('mousedown', onPointerDown)
    }
  }, [showAddWindowMenu])

  const handleToggleItem = (windowId: string, itemId: string, isRead: boolean) => {
    const isOpening = expandedItemIdsByWindowId[windowId] !== itemId
    clearItemFeedback(setItemActionFeedbackByItemId, itemId)
    clearItemFeedback(setArticleRetryFeedbackByItemId, itemId)
    setExpandedItemIdsByWindowId((current) => {
      if (current[windowId] === itemId) {
        const next = { ...current }
        delete next[windowId]
        return next
      }
      return {
        ...current,
        [windowId]: itemId,
      }
    })
    if (isOpening) {
      markItemReadIfNeeded(itemId, isRead)
    }
  }

  const setWindowSnap = (windowId: string, snap: DashboardWindowSnap) => {
    if (!isWideLayout) return
    const { width, height } = getWindowContainerDimensions(rootRef.current)

    setWindows((current) =>
      current.map((window) => {
        if (window.id !== windowId) return window
        if (snap === 'free') {
          const resolvedRect = resolveWindowRect(window, width, height)
          return {
            ...window,
            snap,
            rect: withPanelRectPercentages(resolvedRect, width, height),
          }
        }

        return {
          ...window,
          snap,
          rect: getSnapRect(snap, width, height),
        }
      }),
    )
  }

  const addWindow = (type: DashboardWindowType) => {
    const { width, height } = getWindowContainerDimensions(rootRef.current)
    setWindows((current) => {
      const nextIndex = current.filter((window) => window.type === type).length + 1
      return [...current, createWindowLayout(type, nextIndex, width, height)]
    })
    closeAddWindowMenu(true)
  }

  const removeWindow = (windowId: string) => {
    setWindows((current) => {
      if (current.length <= 1) {
        return current
      }
      return current.filter((window) => window.id !== windowId)
    })
  }

  const openRenameWindow = (windowId: string) => {
    const target = windows.find((window) => window.id === windowId)
    if (!target) return

    setOpenWindowMenuId(null)
    setRenamingWindowId(windowId)
    setRenameWindowDraft(target.title)
  }

  const closeRenameWindow = () => {
    setRenamingWindowId(null)
    setRenameWindowDraft('')
  }

  const saveRenamedWindow = () => {
    if (!renamingWindowId) {
      return
    }

    const normalized = renameWindowDraft.trim().slice(0, 80)
    if (!normalized) {
      return
    }

    setWindows((current) =>
      current.map((window) => (window.id === renamingWindowId ? { ...window, title: normalized } : window)),
    )
    closeRenameWindow()
  }

  const toggleWindowControls = (windowId: string) => {
    setWindows((current) =>
      current.map((window) =>
        window.id === windowId ? { ...window, controls_collapsed: !window.controls_collapsed } : window,
      ),
    )
  }

  const toggleMobileWindowControls = (windowId: string) => {
    setMobileWindowControlsOpenById((current) => ({
      ...current,
      [windowId]: !current[windowId],
    }))
  }

  const updateWindowScratchNote = (windowId: string, scratchNote: string) => {
    setWindows((current) =>
      current.map((window) => (window.id === windowId ? { ...window, scratch_note: scratchNote } : window)),
    )
  }

  const bringWindowToFront = (windowId: string) => {
    setWindows((current) => {
      const target = current.find((entry) => entry.id === windowId)
      if (!target) return current
      const rest = current.filter((entry) => entry.id !== windowId)
      return [...rest, target]
    })
  }

  const startWindowDrag = (event: React.MouseEvent<HTMLDivElement>, windowId: string) => {
    if (!isWideLayout) return

    const rootBounds = rootRef.current?.getBoundingClientRect()
    if (!rootBounds) return

    const targetWindow = windows.find((entry) => entry.id === windowId)
    if (!targetWindow || targetWindow.snap !== 'free') {
      return
    }

    event.preventDefault()

    const startMouseX = event.clientX
    const startMouseY = event.clientY
    const startRect = resolveFloatingPanelRect(targetWindow.rect, rootBounds.width, rootBounds.height)

    const onMove = (moveEvent: MouseEvent) => {
      const deltaX = moveEvent.clientX - startMouseX
      const deltaY = moveEvent.clientY - startMouseY

      const maxX = Math.max(0, rootBounds.width - startRect.width)
      const maxY = Math.max(0, rootBounds.height - startRect.height)
      const candidateX = clamp(startRect.x + deltaX, 0, maxX)
      const candidateY = clamp(startRect.y + deltaY, 0, maxY)
      setWindows((current) => {
        const otherRects = current
          .filter((layout) => layout.id !== windowId)
          .map((layout) => resolveWindowRect(layout, rootBounds.width, rootBounds.height))

        const snapped = applyDragMagnetSnap(
          {
            x: candidateX,
            y: candidateY,
            width: startRect.width,
            height: startRect.height,
          },
          otherRects,
          rootBounds.width,
          rootBounds.height,
          maxX,
          maxY,
        )

        return current.map((window) => {
          if (window.id !== windowId) return window
          return {
            ...window,
            rect: withPanelRectPercentages(
              {
                ...startRect,
                x: snapped.x,
                y: snapped.y,
              },
              rootBounds.width,
              rootBounds.height,
            ),
          }
        })
      })
    }

    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }

    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  const startWindowResize = (event: React.MouseEvent<HTMLButtonElement>, windowId: string) => {
    if (!isWideLayout) return

    const rootBounds = rootRef.current?.getBoundingClientRect()
    if (!rootBounds) return

    const targetWindow = windows.find((entry) => entry.id === windowId)
    if (!targetWindow || targetWindow.snap !== 'free') {
      return
    }

    event.preventDefault()
    event.stopPropagation()

    const startMouseX = event.clientX
    const startMouseY = event.clientY
    const startRect = resolveFloatingPanelRect(targetWindow.rect, rootBounds.width, rootBounds.height)

    const onMove = (moveEvent: MouseEvent) => {
      const deltaX = moveEvent.clientX - startMouseX
      const deltaY = moveEvent.clientY - startMouseY

      const maxWidth = rootBounds.width - startRect.x
      const maxHeight = rootBounds.height - startRect.y
      setWindows((current) =>
        current.map((window) => {
          if (window.id !== windowId) return window
          return {
            ...window,
            rect: withPanelRectPercentages(
              {
                ...startRect,
                width: clamp(startRect.width + deltaX, WINDOW_MIN_WIDTH, maxWidth),
                height: clamp(startRect.height + deltaY, WINDOW_MIN_HEIGHT, maxHeight),
              },
              rootBounds.width,
              rootBounds.height,
            ),
          }
        }),
      )
    }

    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }

    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  const saveCurrentView = () => {
    const name = savedViewName.trim()
    if (!name) return

    saveView.mutate({
      name,
      query: captureCurrentDashboardViewState(),
    })
  }

  const updateActiveView = () => {
    if (!activeSavedViewId) return

    updateExistingView.mutate({
      viewId: activeSavedViewId,
      query: captureCurrentDashboardViewState(),
    })
  }

  const findSavedViewById = (viewId: string) => viewsQuery.data?.find((view) => view.id === viewId) ?? null

  const clearActiveSavedViewSelection = () => {
    setActiveSavedViewId(null)
    setShowSaveAsNew(false)
    setViewSaveError('')
    if (!isEditMode) {
      setEditSessionSnapshot(null)
    }
  }

  const applySavedView = (view: SavedView) => {
    const { width, height } = getWindowContainerDimensions(rootRef.current)
    const parsed = parseDashboardSavedView(view.query_json, width, height)
    setPendingSavedViewLoad(null)
    setEditSessionSnapshot(null)
    setIsEditMode(false)
    setShowAddWindowMenu(false)
    setShowSaveAsNew(false)
    setViewSaveError('')
    applyDashboardSavedViewState(parsed, view.id)
  }

  const requestSavedViewLoad = (viewId: string) => {
    const selected = findSavedViewById(viewId)
    if (!selected) {
      return
    }

    if (hasUnsavedDashboardChanges) {
      setPendingSavedViewLoad({ id: selected.id, name: selected.name })
      return
    }

    applySavedView(selected)
  }

  const onConfirmPendingSavedViewLoad = () => {
    if (!pendingSavedViewLoad) {
      return
    }

    const selected = findSavedViewById(pendingSavedViewLoad.id)
    setPendingSavedViewLoad(null)
    if (selected) {
      applySavedView(selected)
    }
  }

  const exportAllViews = () => {
    const payload = {
      version: 1,
      exported_at: new Date().toISOString(),
      views: (viewsQuery.data ?? []).map((view) => ({
        name: view.name,
        query_json: view.query_json,
      })),
    }

    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
    const objectUrl = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = objectUrl
    anchor.download = `threatlens-dashboard-views-${new Date().toISOString().slice(0, 10)}.json`
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(objectUrl)
  }

  const importViewsFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) {
      return
    }

    if (file.size > MAX_VIEWS_IMPORT_FILE_BYTES) {
      setImportViewsError('Import file is too large. Maximum supported size is 2 MB.')
      event.target.value = ''
      return
    }

    setImportViewsError('')
    setImportViewsResult('')
    setIsImportingViews(true)

    try {
      const text = await file.text()
      const parsed = JSON.parse(text) as unknown
      const entries = parseImportedSavedViews(parsed)
      if (!entries.length) {
        throw new Error('No saved views found in file')
      }

      const importedNames: string[] = []
      for (const entry of entries) {
        try {
          await apiFetch('/views', {
            method: 'POST',
            body: JSON.stringify(entry),
          })
          importedNames.push(entry.name)
        } catch (error) {
          if (importedNames.length) {
            setImportViewsResult(formatSavedViewImportResult(importedNames, true))
            await queryClient.invalidateQueries({ queryKey: ['views'] })
          }
          setImportViewsError(formatSavedViewImportFailure(entry.name, error))
          return
        }
      }

      setImportViewsResult(formatSavedViewImportResult(importedNames))
      await queryClient.invalidateQueries({ queryKey: ['views'] })
    } catch (error) {
      setImportViewsError((error as Error).message || 'Failed to import saved views')
    } finally {
      setIsImportingViews(false)
      event.target.value = ''
    }
  }

  const openImportViewsPicker = () => {
    if (isImportingViews) {
      return
    }
    importViewsInputRef.current?.click()
  }

  const updateWindowRssFilters = (
    windowId: string,
    updater: (current: DashboardRssWindowFilters) => DashboardRssWindowFilters,
    resetPage = true,
  ) => {
    setWindows((current) =>
      current.map((window) => {
        if (window.id !== windowId || window.type !== 'rss') {
          return window
        }

        const nextFilters = updater(window.rss_filters ?? createDefaultRssWindowFilters())
        return {
          ...window,
          rss_filters: {
            ...nextFilters,
            page: resetPage ? 1 : nextFilters.page,
          },
        }
      }),
    )
  }

  const updateWindowAlertFilters = (
    windowId: string,
    updater: (current: DashboardAlertWindowFilters) => DashboardAlertWindowFilters,
    resetPage = true,
  ) => {
    setWindows((current) =>
      current.map((window) => {
        if (window.id !== windowId || window.type !== 'alerts') {
          return window
        }

        const nextFilters = updater(window.alert_filters ?? createDefaultAlertWindowFilters())
        return {
          ...window,
          alert_filters: {
            ...nextFilters,
            page: resetPage ? 1 : nextFilters.page,
          },
        }
      }),
    )
  }

  const updateWindowDailyBriefSelection = (windowId: string, selectedDailyBriefId: string) => {
    setWindows((current) =>
      current.map((window) =>
        window.id === windowId && window.type === 'daily_brief'
          ? { ...window, selected_daily_brief_id: selectedDailyBriefId || null }
          : window,
      ),
    )
  }

  const markWindowSeen = (windowId: string) => {
    setWindowSeenAt((current) => ({
      ...current,
      [windowId]: new Date().toISOString(),
    }))
  }

  const resetAllWindowPages = () => {
    setWindows((current) =>
      current.map((window) => {
        if (window.type === 'rss') {
          return {
            ...window,
            rss_filters: {
              ...(window.rss_filters ?? createDefaultRssWindowFilters()),
              page: 1,
            },
          }
        }

        if (window.type === 'alerts') {
          return {
            ...window,
            alert_filters: {
              ...(window.alert_filters ?? createDefaultAlertWindowFilters()),
              page: 1,
            },
          }
        }

        return window
      }),
    )
  }

  const updateDashboardTimeRange = (nextRange: TimeRangeFilter) => {
    resetAllWindowPages()
    setDashboardTimeRange(nextRange)
  }

  const updateDashboardCustomSinceDate = (nextDate: string) => {
    resetAllWindowPages()
    setDashboardCustomSinceDate(nextDate)
  }

  const updateDashboardCustomUntilDate = (nextDate: string) => {
    resetAllWindowPages()
    setDashboardCustomUntilDate(nextDate)
  }

  const updateDashboardRollingDaysValue = (nextValue: string) => {
    resetAllWindowPages()
    setDashboardTimeRange('days')
    setDashboardRollingDays(normalizeRollingDaysInput(nextValue))
  }

  const updateWindowTimeRange = (windowId: string, nextValue: string) => {
    setWindows((current) =>
      current.map((window) => {
        if (window.id !== windowId || window.type === 'notes' || window.type === 'daily_brief') {
          return window
        }

        if (nextValue === DASHBOARD_TIME_INHERIT_VALUE) {
          return {
            ...window,
            time_override: null,
          }
        }

        if (!isTimeRangeFilter(nextValue)) {
          return window
        }

        const base = window.time_override ?? dashboardTimeFilter
        if (window.type === 'rss') {
          return {
            ...window,
            rss_filters: {
              ...(window.rss_filters ?? createDefaultRssWindowFilters()),
              page: 1,
            },
            time_override: {
              ...base,
              time_range: nextValue,
            },
          }
        }

        return {
          ...window,
          alert_filters: {
            ...(window.alert_filters ?? createDefaultAlertWindowFilters()),
            page: 1,
          },
          time_override: {
            ...base,
            time_range: nextValue,
          },
        }
      }),
    )
  }

  const updateWindowCustomTimeDate = (windowId: string, key: 'custom_since_date' | 'custom_until_date', value: string) => {
    setWindows((current) =>
      current.map((window) => {
        if (window.id !== windowId || window.type === 'notes' || window.type === 'daily_brief') {
          return window
        }

        const base = window.time_override ?? dashboardTimeFilter
        if (window.type === 'rss') {
          return {
            ...window,
            rss_filters: {
              ...(window.rss_filters ?? createDefaultRssWindowFilters()),
              page: 1,
            },
            time_override: {
              ...base,
              time_range: 'custom',
              [key]: value,
            },
          }
        }

        return {
          ...window,
          alert_filters: {
            ...(window.alert_filters ?? createDefaultAlertWindowFilters()),
            page: 1,
          },
          time_override: {
            ...base,
            time_range: 'custom',
            [key]: value,
          },
        }
      }),
    )
  }

  const updateWindowRollingDays = (windowId: string, value: string) => {
    const normalized = normalizeRollingDaysInput(value)
    setWindows((current) =>
      current.map((window) => {
        if (window.id !== windowId || window.type === 'notes' || window.type === 'daily_brief') {
          return window
        }

        const base = window.time_override ?? dashboardTimeFilter
        if (window.type === 'rss') {
          return {
            ...window,
            rss_filters: {
              ...(window.rss_filters ?? createDefaultRssWindowFilters()),
              page: 1,
            },
            time_override: {
              ...base,
              time_range: 'days',
              rolling_days: normalized,
            },
          }
        }

        return {
          ...window,
          alert_filters: {
            ...(window.alert_filters ?? createDefaultAlertWindowFilters()),
            page: 1,
          },
          time_override: {
            ...base,
            time_range: 'days',
            rolling_days: normalized,
          },
        }
      }),
    )
  }

  const applyGlobalSearch = (query: string) => {
    setWindows((current) =>
      current.map((window) => {
        if (window.type === 'rss') {
          return {
            ...window,
            rss_filters: {
              ...(window.rss_filters ?? createDefaultRssWindowFilters()),
              q: query,
              page: 1,
            },
          }
        }
        if (window.type === 'alerts') {
          return {
            ...window,
            alert_filters: {
              ...(window.alert_filters ?? createDefaultAlertWindowFilters()),
              q: query,
              page: 1,
            },
          }
        }
        return window
      }),
    )
  }

  const globalSearchState = useMemo(() => summarizeGlobalSearchAcrossWindows(windows), [windows])

  const rssWindowCount = windows.filter((window) => window.type === 'rss').length
  const alertWindowCount = windows.filter((window) => window.type === 'alerts').length
  const notesWindowCount = windows.filter((window) => window.type === 'notes').length
  const dailyBriefWindowCount = windows.filter((window) => window.type === 'daily_brief').length
  const resolvedMobileWindowId =
    mobileActiveWindowId && windows.some((windowLayout) => windowLayout.id === mobileActiveWindowId)
      ? mobileActiveWindowId
      : windows[0]?.id ?? null
  const renderedWindows = isWideLayout
    ? windows
    : windows.filter((windowLayout) => windowLayout.id === resolvedMobileWindowId)
  const mobileActiveWindowIndex = Math.max(
    0,
    windows.findIndex((windowLayout) => windowLayout.id === resolvedMobileWindowId),
  )
  const containerDimensions = getWindowContainerDimensions(rootRef.current)
  const savedViewPreviews = useMemo(
    () =>
      (viewsQuery.data ?? []).map((view) =>
        buildSavedViewPreview(view, Math.max(containerDimensions.width, 1120), Math.max(containerDimensions.height, 680)),
      ),
    [containerDimensions.height, containerDimensions.width, viewsQuery.data],
  )
  return {
    activeSavedViewId, addWindow, addWindowActionRefs, addWindowMenuId, addWindowMenuRef,
    addWindowTriggerRef,
    adjustArticlePreviewWidth, aiDailyBriefEnabled, aiRelevanceEnabled, aiSummaryEnabled, alertInterestsQuery,
    alertQueriesByWindowId, alertWindowCount, applyDashboardSavedViewState, applyGlobalSearch, articlePreview,
    articlePreviewFrameState, articlePreviewWidth, articleRetryFeedbackByItemId, availableAlertCategories, bringWindowToFront,
    canManage, captureCurrentDashboardViewState, clearActiveSavedViewSelection, closeAddWindowMenu, closeArticlePreview,
    closeRenameWindow, confirmDiscardUnsavedDashboardChanges, containerDimensions, dailyBriefHistoryQuery, dailyBriefWindowCount,
    dashboardCustomSinceDate, dashboardCustomUntilDate, dashboardRollingDays, dashboardTimeFilter, dashboardTimeRange,
    deleteView, detailQueriesByWindowId, editSessionSnapshot, expandedItemIdsByWindowId, exportAllViews,
    feedsQuery, globalSearchState, handleAddWindowMenuKeyDown, handleAddWindowTriggerKeyDown, handleOpenArticlePreview,
    handleToggleItem, hasProtectedEditSession, hasUnsavedDashboardChanges, importViewsError, importViewsFile,
    importViewsInputRef, importViewsResult, isArticlePreviewResizing, isEditMode, isImportingViews,
    isWideLayout, itemActionFeedbackByItemId, markWindowSeen, mobileActiveWindowIndex, mobileDashboardViewsOpen,
    mobileWindowControlsOpenById, noteDraftsByItemId, notesWindowCount, onConfirmDeleteView, onConfirmPendingSavedViewLoad,
    openAddWindowMenu, openImportViewsPicker, openRenameWindow, pendingSavedViewLoad, pendingViewDelete,
    removeWindow, renameWindowDraft, renameWindowInputRef, renamingWindowId, renderedWindows,
    requestSavedViewLoad, resolvedMobileWindowId, retryArticleFetch, rootRef, rssLastOpenedAt,
    rssQueriesByWindowId, rssWindowCount, saveCurrentView, saveRenamedWindow, saveView,
    savedViewName, savedViewPreviews, setArticlePreviewFrameState, setEditSessionSnapshot, setIsEditMode,
    setMobileActiveWindowId, setMobileDashboardViewsOpen, setNoteDraftsByItemId, setOpenWindowMenuId, setPendingSavedViewLoad,
    setPendingViewDelete, setRenameWindowDraft, setSavedViewName, setShowManageViewsModal, setShowSaveAsNew,
    setViewDeleteError, setViewSaveError, setWindowSnap, showAddWindowMenu, showManageViewsModal,
    showSaveAsNew, startArticlePreviewResize, startWindowDrag, startWindowResize, tagsQuery,
    toggleMobileWindowControls, toggleWindowControls, updateActiveView, updateDashboardCustomSinceDate, updateDashboardCustomUntilDate,
    updateDashboardRollingDaysValue, updateDashboardTimeRange, updateExistingView, updateNote, updateRead,
    updateStar, updateWindowAlertFilters, updateWindowCustomTimeDate, updateWindowDailyBriefSelection, updateWindowRollingDays,
    updateWindowRssFilters, updateWindowScratchNote, updateWindowTimeRange, viewDeleteError, viewSaveError,
    viewSavePending, viewsQuery, windowSeenAt, windows,
  }
}

export type DashboardPageController = ReturnType<typeof useDashboardPageController>
