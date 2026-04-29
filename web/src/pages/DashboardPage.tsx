import {
  ChangeEvent,
  Dispatch,
  KeyboardEvent as ReactKeyboardEvent,
  ReactNode,
  SetStateAction,
  useDeferredValue,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import { ConfirmDialog, DialogSurface } from '../components/ConfirmDialog'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { feedHealthBadgeClass, resolveFeedHealth } from '../utils/feedHealth'
import { formatDateOnly, formatDateTime } from '../utils/datetime'
import { looksLikeHtml, parseArticleBlocks, sanitizeHref, sanitizeHtmlFragment, stripHtml } from './dashboardContent'
import { getDashboardStorageKeys, migrateLegacyDashboardStorage } from './dashboardStorage'
import { summarizeGlobalSearchAcrossWindows } from './dashboardState'
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
  normalizePanelRect,
  normalizeRollingDaysInput,
  PAGE_SIZE_OPTIONS,
  parseDashboardSavedView,
  parseImportedSavedViews,
  resolveWindowRect,
  resolveSavedViewSelectionChange,
  serializeDashboardWindowLayouts,
  WINDOW_MIN_HEIGHT,
  WINDOW_MIN_WIDTH,
  type DashboardSavedViewPreview,
  type DashboardAlertWindowFilters,
  type DashboardRssWindowFilters,
  type DashboardSavedViewState,
  type DashboardWindow,
  type DashboardWindowSnap,
  type DashboardWindowType,
  type PanelRect,
  type ReadStatusFilter,
  type StarStatusFilter,
  type TimeRangeFilter,
  type TimeSort,
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

interface DashboardEditSessionSnapshot {
  activeSavedViewId: string | null
  savedViewName: string
  state: DashboardSavedViewState
}

const DRAG_EDGE_SNAP_THRESHOLD = 12
const DRAG_MIDLINE_SNAP_THRESHOLD = 8
const DASHBOARD_TIME_INHERIT_VALUE = '__dashboard_time__'
const MAX_VIEWS_IMPORT_FILE_BYTES = 2_000_000
const SAVED_VIEW_THUMBNAIL_WIDTH = 148
const SAVED_VIEW_THUMBNAIL_HEIGHT = 96
const KEYBOARD_PANEL_MOVE_STEP = 24
const KEYBOARD_PANEL_RESIZE_STEP = 32
const ROLLING_WINDOW_FIELD_CLASS =
  'flex w-full items-center rounded border border-slate/20 bg-white px-2 py-1.5 text-sm focus-within:border-cyan/60 focus-within:ring-2 focus-within:ring-cyan/60 focus-within:ring-offset-1 dark:border-cyan-900/40 dark:bg-[#072019] dark:focus-within:border-cyan-400/60 dark:focus-within:ring-cyan-300/60 dark:focus-within:ring-offset-[var(--tl-input-bg)]'

const WINDOW_SNAP_OPTIONS: Array<{ value: DashboardWindowSnap; label: string }> = [
  { value: 'free', label: 'Floating (Advanced)' },
  { value: 'full', label: 'Full' },
  { value: 'left', label: 'Left Half' },
  { value: 'right', label: 'Right Half' },
  { value: 'top_left', label: 'Top Left' },
  { value: 'top_right', label: 'Top Right' },
  { value: 'bottom_left', label: 'Bottom Left' },
  { value: 'bottom_right', label: 'Bottom Right' },
]

const WINDOW_TYPE_META: Record<
  DashboardWindowType,
  {
    label: string
    description: string
    badgeClassName: string
    headerClassName: string
    shellClassName: string
    panelClassName: string
  }
> = {
  rss: {
    label: 'RSS Triage',
    description: 'Track feeds, pivot by tags, and expand into article detail.',
    badgeClassName: 'tl-chip-info',
    headerClassName: 'bg-white/92 dark:bg-[#041612]/90',
    shellClassName: 'border-slate/20 bg-white/95 dark:border-cyan-900/45 dark:bg-[#041612]/96',
    panelClassName: 'bg-white/90 dark:bg-[#03130f]/84',
  },
  alerts: {
    label: 'Alert Matches',
    description: 'Watch keyword-driven matches across your configured interests.',
    badgeClassName: 'tl-chip-neutral',
    headerClassName: 'bg-white/92 dark:bg-[#041612]/90',
    shellClassName: 'border-slate/20 bg-white/95 dark:border-cyan-900/45 dark:bg-[#041612]/96',
    panelClassName: 'bg-white/90 dark:bg-[#03130f]/84',
  },
  notes: {
    label: 'Notes',
    description: 'Keep scratch notes, pivots, and hypotheses attached to this view.',
    badgeClassName: 'tl-chip-neutral',
    headerClassName: 'bg-white/92 dark:bg-[#041612]/90',
    shellClassName: 'border-slate/20 bg-white/95 dark:border-cyan-900/45 dark:bg-[#041612]/96',
    panelClassName: 'bg-white/90 dark:bg-[#03130f]/84',
  },
  daily_brief: {
    label: 'Daily Brief',
    description: 'Review retained AI briefings and the items that shaped them.',
    badgeClassName: 'tl-chip-neutral',
    headerClassName: 'bg-white/92 dark:bg-[#041612]/90',
    shellClassName: 'border-slate/20 bg-white/95 dark:border-cyan-900/45 dark:bg-[#041612]/96',
    panelClassName: 'bg-white/90 dark:bg-[#03130f]/84',
  },
}

function getRelativeTimeAnchorMs() {
  return Math.floor(Date.now() / 60_000) * 60_000
}

function isRelativeTimeRange(value: TimeRangeFilter) {
  return value === '24h' || value === '7d' || value === '30d' || value === 'days'
}

function resolveDashboardViewSaveError(error: unknown) {
  if (error instanceof ApiError && error.message.trim()) {
    return error.message
  }
  if (error instanceof Error && error.message.trim()) {
    return error.message
  }
  return 'Failed to save the dashboard view. Your edits are still open.'
}

function resolveSavedViewImportError(error: unknown) {
  if (error instanceof ApiError && error.message.trim()) {
    return error.message
  }
  if (error instanceof Error && error.message.trim()) {
    return error.message
  }
  return 'Unable to import this saved view.'
}

function summarizeSavedViewNames(names: string[]) {
  const quotedNames = names.map((name) => `"${name}"`)
  if (quotedNames.length <= 3) {
    return quotedNames.join(', ')
  }
  return `${quotedNames.slice(0, 3).join(', ')}, and ${quotedNames.length - 3} more`
}

function formatSavedViewImportResult(importedNames: string[], partial = false) {
  const count = importedNames.length
  const label = `saved view${count === 1 ? '' : 's'}`
  const namesSummary = summarizeSavedViewNames(importedNames)
  if (partial) {
    return `Imported ${count} ${label} before the import stopped: ${namesSummary}.`
  }
  return `Imported ${count} ${label}: ${namesSummary}.`
}

function formatSavedViewImportFailure(viewName: string, error: unknown) {
  return `Failed to import "${viewName}": ${resolveSavedViewImportError(error)}`
}

export function DashboardPage() {
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
  const [isEditMode, setIsEditMode] = useState(false)
  const [viewSaveError, setViewSaveError] = useState('')

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

  const canManage = meQuery.data?.role === 'admin' || meQuery.data?.role === 'analyst'
  const aiSummaryEnabled = Boolean(aiFeatures?.ai_summary_enabled)
  const aiRelevanceEnabled = Boolean(aiFeatures?.ai_relevance_enabled)
  const aiDailyBriefEnabled = Boolean(aiFeatures?.ai_daily_brief_enabled)
  const hasProtectedEditSession = isEditMode && editSessionSnapshot !== null
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
    if (typeof window === 'undefined') {
      return
    }
    const userId = meQuery.data?.id
    if (!userId || initializedDashboardUserRef.current !== userId) {
      return
    }
    const storageKeys = getDashboardStorageKeys(userId)
    const serialized = JSON.stringify(serializeDashboardWindowLayouts(windows))
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
  })

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
      queryClient.invalidateQueries({ queryKey: ['views'] })
    },
  })

  const onConfirmDeleteView = () => {
    if (!pendingViewDelete) {
      return
    }

    const viewId = pendingViewDelete.id
    setPendingViewDelete(null)
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
      setNoteDraftsByItemId((current) => ({
        ...current,
        [variables.itemId]: variables.note ?? '',
      }))
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

  const captureCurrentDashboardViewState = () =>
    buildDashboardSavedViewState(windows, {
      time_range: dashboardTimeRange,
      custom_since_date: dashboardCustomSinceDate,
      custom_until_date: dashboardCustomUntilDate,
      rolling_days: dashboardRollingDays,
    })

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
      const deferredSearchQuery = rssDeferredSearchTermsByWindowId[windowLayout.id] ?? rssFilters.q
      const selectedFeedIdsParam = rssFilters.selected_feed_ids.slice().sort().join(',')
      const selectedTagsParam = rssFilters.selected_tags
        .filter((tagName) => !HIDDEN_TAGS.has(tagName))
        .slice()
        .sort()
        .join(',')
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
          since: timeWindow.sinceIso,
          until: timeWindow.untilIso,
          sort: rssFilters.sort,
          page: rssFilters.page,
          page_size: rssFilters.page_size,
        }),
        retry: 1,
        staleTime: 60_000,
        refetchInterval: rssFilters.page === 1 ? 60_000 : false,
        placeholderData: (previousData: ItemListResponse | undefined) => previousData,
        queryFn: () => {
          const params = new URLSearchParams()
          params.set('page', String(rssFilters.page))
          params.set('page_size', String(rssFilters.page_size))
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

          return apiFetch<ItemListResponse>(`/items?${params.toString()}`)
        },
      }
    }),
  })

  const alertWindowQueries = useQueries({
    queries: alertWindows.map((windowLayout) => {
      const alertFilters = windowLayout.alert_filters ?? createDefaultAlertWindowFilters()
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
          alertFilters.page_size,
        ],
        staleTime: 60_000,
        placeholderData: (previousData: AlertMatchListResponse | undefined) => previousData,
        queryFn: () => {
          const params = new URLSearchParams()
          params.set('page', String(alertFilters.page))
          params.set('page_size', String(alertFilters.page_size))
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

  const handleToggleItem = (windowId: string, itemId: string) => {
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
  }

  const setWindowSnap = (windowId: string, snap: DashboardWindowSnap) => {
    if (!isWideLayout) return
    const { width, height } = getWindowContainerDimensions(rootRef.current)

    setWindows((current) =>
      current.map((window) => {
        if (window.id !== windowId) return window
        if (snap === 'free') {
          return {
            ...window,
            snap,
            rect: normalizePanelRect(window.rect, width, height),
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
    const startRect = targetWindow.rect

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
            rect: {
              ...window.rect,
              x: snapped.x,
              y: snapped.y,
            },
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
    const startRect = targetWindow.rect

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
            rect: {
              ...window.rect,
              width: clamp(startRect.width + deltaX, WINDOW_MIN_WIDTH, maxWidth),
              height: clamp(startRect.height + deltaY, WINDOW_MIN_HEIGHT, maxHeight),
            },
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

  const adjustFreeWindowRect = (
    windowId: string,
    patch: Partial<Pick<PanelRect, 'x' | 'y' | 'width' | 'height'>>,
  ) => {
    if (!isWideLayout) {
      return
    }

    const { width, height } = getWindowContainerDimensions(rootRef.current)
    setWindows((current) =>
      current.map((window) => {
        if (window.id !== windowId || window.snap !== 'free') {
          return window
        }

        return {
          ...window,
          rect: normalizePanelRect(
            {
              x: window.rect.x + (patch.x ?? 0),
              y: window.rect.y + (patch.y ?? 0),
              width: window.rect.width + (patch.width ?? 0),
              height: window.rect.height + (patch.height ?? 0),
            },
            width,
            height,
          ),
        }
      }),
    )
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
  const containerDimensions = getWindowContainerDimensions(rootRef.current)
  const savedViewPreviews = useMemo(
    () =>
      (viewsQuery.data ?? []).map((view) =>
        buildSavedViewPreview(view, Math.max(containerDimensions.width, 1120), Math.max(containerDimensions.height, 680)),
      ),
    [containerDimensions.height, containerDimensions.width, viewsQuery.data],
  )
  return (
    <div className="w-full">
      <div className="border-b border-slate/20 bg-white/85 px-3 py-1.5 shadow-sm dark:border-cyan-900/40 dark:bg-[#041612]/92">
        <div className="mb-1.5 flex items-center justify-between gap-2 sm:hidden">
          <p className="text-xs font-semibold text-slate dark:text-slate-300">Dashboard</p>
          <button
            type="button"
            className="h-8 rounded border border-slate/20 px-2 text-xs dark:border-cyan-900/40"
            onClick={() => setMobileDashboardViewsOpen((current) => !current)}
            aria-expanded={mobileDashboardViewsOpen}
            aria-controls="dashboard-view-toolbar"
          >
            {mobileDashboardViewsOpen ? 'Hide' : 'Show'}
          </button>
        </div>
        <div
          id="dashboard-view-toolbar"
          className={`${mobileDashboardViewsOpen ? 'flex' : 'hidden'} flex-col gap-1.5 sm:flex sm:flex-row sm:flex-wrap sm:items-center lg:flex-nowrap`}
        >
          <input
            value={globalSearchState.value}
            onChange={(event) => applyGlobalSearch(event.target.value)}
            aria-label="Search across all dashboard panels"
            placeholder={
              globalSearchState.isMixed
                ? 'Panels have different searches. Type here to overwrite all panel searches...'
                : 'Search across all panels...'
            }
            className="h-8 w-full min-w-[180px] rounded border border-slate/20 bg-white px-2.5 text-xs sm:flex-1 dark:border-cyan-900/40 dark:bg-[#041612]"
          />
          <div className="flex flex-col gap-1.5 sm:flex-row sm:flex-wrap sm:items-center">
            <select
              className="h-8 w-full rounded border border-slate/20 bg-white px-2 text-xs sm:w-auto dark:border-cyan-900/40 dark:bg-[#041612]"
              value={dashboardTimeRange}
              onChange={(event) => updateDashboardTimeRange(event.target.value as TimeRangeFilter)}
              aria-label="Dashboard time range"
            >
              <option value="all">All time</option>
              <option value="24h">Last 24h</option>
              <option value="7d">Last 7d</option>
              <option value="30d">Last 30d</option>
              <option value="days">Last X days</option>
              <option value="custom">Custom</option>
            </select>
            {dashboardTimeRange === 'days' && (
              <>
                <label className={`${ROLLING_WINDOW_FIELD_CLASS} h-8 text-xs sm:w-[138px] dark:bg-[#041612]`}>
                  <span className="mr-2 text-xs text-slate dark:text-white/60">Last</span>
                  <input
                    type="number"
                    min={1}
                    max={365}
                    value={dashboardRollingDays}
                    onChange={(event) => updateDashboardRollingDaysValue(event.target.value)}
                    aria-label="Dashboard rolling time window in days"
                    className="w-full bg-transparent text-xs focus-visible:outline-none"
                  />
                  <span className="ml-2 text-xs text-slate dark:text-white/60">days</span>
                </label>
                <div className="flex h-8 w-full items-center rounded border border-slate/20 bg-slate/10 px-2 text-xs text-slate sm:w-auto dark:border-cyan-900/40 dark:bg-[#041612] dark:text-white/65">
                  {formatRollingWindowHint(dashboardRollingDays)}
                </div>
              </>
            )}
            {dashboardTimeRange === 'custom' && (
              <>
                <input
                  type="date"
                  className="h-8 w-full rounded border border-slate/20 bg-white px-2 text-xs sm:w-auto dark:border-cyan-900/40 dark:bg-[#041612]"
                  value={dashboardCustomSinceDate}
                  onChange={(event) => updateDashboardCustomSinceDate(event.target.value)}
                  aria-label="Dashboard custom start date"
                />
                <input
                  type="date"
                  className="h-8 w-full rounded border border-slate/20 bg-white px-2 text-xs sm:w-auto dark:border-cyan-900/40 dark:bg-[#041612]"
                  value={dashboardCustomUntilDate}
                  onChange={(event) => updateDashboardCustomUntilDate(event.target.value)}
                  aria-label="Dashboard custom end date"
                />
              </>
            )}
          </div>
          <select
            className="h-8 w-full rounded border border-slate/20 bg-white px-2 text-xs xl:w-auto dark:border-cyan-900/40 dark:bg-[#041612]"
            value={activeSavedViewId ?? ''}
            aria-label="Load saved dashboard view"
            onChange={(event) => {
              const change = resolveSavedViewSelectionChange({
                currentActiveSavedViewId: activeSavedViewId,
                nextValue: event.target.value,
                hasProtectedEditSession: hasUnsavedDashboardChanges,
              })

              if (change.kind === 'clear') {
                if (hasUnsavedDashboardChanges) {
                  confirmDiscardUnsavedDashboardChanges(() => {
                    clearActiveSavedViewSelection()
                  })
                } else {
                  clearActiveSavedViewSelection()
                }
                return
              }

              if (change.kind === 'load' || change.kind === 'confirm_load') {
                requestSavedViewLoad(change.viewId)
              }
            }}
          >
            <option value="">Load View</option>
            {viewsQuery.data?.map((view) => (
              <option key={view.id} value={view.id}>
                {view.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="h-8 w-full rounded border border-slate/20 px-3 text-xs sm:w-auto dark:border-cyan-900/40"
            onClick={() => setShowManageViewsModal(true)}
          >
            Views
          </button>
          {!isEditMode ? (
            <button
              type="button"
              className="h-8 w-full rounded border border-slate/20 px-3 text-xs font-semibold sm:w-auto dark:border-cyan-900/40"
              onClick={() => {
                setEditSessionSnapshot({
                  activeSavedViewId,
                  savedViewName,
                  state: captureCurrentDashboardViewState(),
                })
                setIsEditMode(true)
                setShowSaveAsNew(false)
                setSavedViewName('')
              }}
            >
              Edit Layout
            </button>
          ) : (
            <>
              <div className="relative">
                <button
                  type="button"
                  ref={addWindowTriggerRef}
                  className="h-8 w-full rounded border border-slate/20 px-3 text-xs sm:w-auto dark:border-cyan-900/40"
                  onClick={() => {
                    if (showAddWindowMenu) {
                      closeAddWindowMenu()
                      return
                    }
                    openAddWindowMenu()
                  }}
                  onKeyDown={handleAddWindowTriggerKeyDown}
                  aria-haspopup="menu"
                  aria-expanded={showAddWindowMenu}
                  aria-controls={showAddWindowMenu ? addWindowMenuId : undefined}
                >
                  Add Panel
                </button>
                {showAddWindowMenu && (
                  <div
                    ref={addWindowMenuRef}
                    id={addWindowMenuId}
                    role="menu"
                    aria-label="Add dashboard panel"
                    onKeyDown={handleAddWindowMenuKeyDown}
                    className="absolute right-0 top-[calc(100%+6px)] z-30 w-56 max-w-[calc(100vw-2rem)] rounded border border-slate/20 bg-white p-1 shadow-lg dark:border-cyan-900/40 dark:bg-[#041612]"
                  >
                    <button
                      ref={(node) => {
                        addWindowActionRefs.current[0] = node
                      }}
                      type="button"
                      role="menuitem"
                      className="w-full rounded px-2 py-1.5 text-left text-xs hover:bg-cyan/10"
                      onClick={() => addWindow('rss')}
                    >
                      RSS Panel ({rssWindowCount})
                    </button>
                    <button
                      ref={(node) => {
                        addWindowActionRefs.current[1] = node
                      }}
                      type="button"
                      role="menuitem"
                      className="w-full rounded px-2 py-1.5 text-left text-xs hover:bg-cyan/10"
                      onClick={() => addWindow('alerts')}
                    >
                      Alerts Panel ({alertWindowCount})
                    </button>
                    <button
                      ref={(node) => {
                        addWindowActionRefs.current[2] = node
                      }}
                      type="button"
                      role="menuitem"
                      className="w-full rounded px-2 py-1.5 text-left text-xs hover:bg-cyan/10"
                      onClick={() => addWindow('notes')}
                    >
                      Notes Panel ({notesWindowCount})
                    </button>
                    {aiDailyBriefEnabled && (
                      <button
                        ref={(node) => {
                          addWindowActionRefs.current[3] = node
                        }}
                        type="button"
                        role="menuitem"
                        className="w-full rounded px-2 py-1.5 text-left text-xs hover:bg-cyan/10"
                        onClick={() => addWindow('daily_brief')}
                      >
                        Daily Brief Panel ({dailyBriefWindowCount})
                      </button>
                    )}
                  </div>
                )}
              </div>
              {activeSavedViewId ? (
                <>
                  <span className="hidden items-center rounded border border-cyan/30 bg-cyan/8 px-2.5 text-xs font-semibold text-cyan sm:flex dark:border-cyan-800/40 dark:bg-cyan-950/40 dark:text-cyan-200">
                    Editing &ldquo;{viewsQuery.data?.find((v) => v.id === activeSavedViewId)?.name}&rdquo;
                  </span>
                  <button
                    type="button"
                    className="h-8 w-full rounded bg-ink px-3 text-xs font-semibold text-white disabled:opacity-50 sm:w-auto dark:bg-cyan dark:text-slate-950"
                    onClick={() => {
                      updateActiveView()
                    }}
                    disabled={viewSavePending}
                  >
                    {updateExistingView.isPending ? 'Saving...' : 'Save'}
                  </button>
                  {!showSaveAsNew ? (
                    <button
                      type="button"
                      className="h-8 w-full rounded border border-slate/20 px-3 text-xs sm:w-auto dark:border-cyan-900/40"
                      disabled={viewSavePending}
                      onClick={() => setShowSaveAsNew(true)}
                    >
                      Save as New...
                    </button>
                  ) : (
                    <>
                      <input
                        autoFocus
                        value={savedViewName}
                        onChange={(event) => setSavedViewName(event.target.value)}
                        aria-label="New saved dashboard view name"
                        placeholder="New view name..."
                        disabled={viewSavePending}
                        className="h-8 w-full min-w-[140px] rounded border border-slate/20 bg-white px-2.5 text-xs sm:w-auto sm:min-w-[160px] dark:border-cyan-900/40 dark:bg-[#041612]"
                      />
                      <button
                        type="button"
                        className="h-8 w-full rounded border border-slate/20 px-3 text-xs font-semibold disabled:opacity-50 sm:w-auto dark:border-cyan-900/40"
                        onClick={() => {
                          saveCurrentView()
                        }}
                        disabled={viewSavePending || !savedViewName.trim()}
                      >
                        {saveView.isPending ? 'Creating...' : 'Create'}
                      </button>
                    </>
                  )}
                </>
              ) : (
                <>
                  <input
                    value={savedViewName}
                    onChange={(event) => setSavedViewName(event.target.value)}
                    aria-label="Saved dashboard view name"
                    placeholder="View name..."
                    disabled={viewSavePending}
                    className="h-8 w-full min-w-[140px] rounded border border-slate/20 bg-white px-2.5 text-xs sm:w-auto sm:min-w-[160px] dark:border-cyan-900/40 dark:bg-[#041612]"
                  />
                  <button
                    type="button"
                    className="h-8 w-full rounded bg-ink px-3 text-xs font-semibold text-white disabled:opacity-50 sm:w-auto dark:bg-cyan dark:text-slate-950"
                    onClick={() => {
                      saveCurrentView()
                    }}
                    disabled={viewSavePending || !savedViewName.trim()}
                  >
                    {saveView.isPending ? 'Saving...' : 'Save New View'}
                  </button>
                </>
              )}
              <button
                type="button"
                className="h-8 w-full rounded border border-slate/20 px-3 text-xs sm:w-auto dark:border-cyan-900/40"
                disabled={viewSavePending}
                onClick={() => {
                  if (editSessionSnapshot) {
                    applyDashboardSavedViewState(editSessionSnapshot.state, editSessionSnapshot.activeSavedViewId)
                    setSavedViewName(editSessionSnapshot.savedViewName)
                  }
                  setIsEditMode(false)
                  closeAddWindowMenu()
                  setOpenWindowMenuId(null)
                  setShowSaveAsNew(false)
                  setEditSessionSnapshot(null)
                  setViewSaveError('')
                }}
              >
                Cancel
              </button>
            </>
          )}
        </div>
        {viewSaveError && (
          <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-sm text-red-600 dark:text-red-300">
            {viewSaveError}
          </p>
        )}
        {viewSavePending && (
          <p role="status" aria-live="polite" aria-atomic="true" className="mt-2 text-sm text-cyan-700 dark:text-cyan-300">
            Saving the current layout. Editing is temporarily locked until the request finishes.
          </p>
        )}
      </div>

      <div
        ref={rootRef}
        className={`relative ${isWideLayout ? 'h-[calc(100vh-126px)] min-h-[620px] w-full overflow-hidden bg-slate-100/70 dark:bg-[#02100c]' : 'space-y-3 p-3'}`}
      >
        {viewSavePending && (
          <div className="absolute inset-0 z-30 flex items-start justify-center bg-white/55 px-4 py-6 backdrop-blur-[1px] dark:bg-slate-950/45">
            <div className="rounded-full border border-cyan/30 bg-white/95 px-4 py-2 text-sm font-semibold text-cyan shadow-sm dark:border-cyan-500/35 dark:bg-[#041612]/95 dark:text-cyan-100">
              Saving view changes...
            </div>
          </div>
        )}
        {windows.map((windowLayout) => {
          const resolvedRect =
            windowLayout.snap === 'free'
              ? normalizePanelRect(windowLayout.rect, containerDimensions.width, containerDimensions.height)
              : getSnapRect(windowLayout.snap, containerDimensions.width, containerDimensions.height)
          const effectiveWindowTimeFilter = resolveWindowTimeFilter(windowLayout, dashboardTimeFilter)
          const rssFilters = windowLayout.rss_filters ?? createDefaultRssWindowFilters()
          const alertFilters = windowLayout.alert_filters ?? createDefaultAlertWindowFilters()
          const rssQuery = rssQueriesByWindowId[windowLayout.id]
          const alertQuery = alertQueriesByWindowId[windowLayout.id]
          const rssWindowItems =
            windowLayout.type === 'rss'
              ? rssQuery?.data?.items ?? []
              : []
          const alertWindowItems =
            windowLayout.type === 'alerts'
              ? alertQuery?.data?.items ?? []
              : []
          const lastSeenAtIso = windowSeenAt[windowLayout.id] ?? ''
          const rssChangedCount = windowLayout.type === 'rss' ? countNewEntriesSince(rssWindowItems, rssLastOpenedAt) : 0
          const alertChangedCount = windowLayout.type === 'alerts' ? countNewEntriesSince(alertWindowItems, lastSeenAtIso) : 0
          const rssTotalPages =
            windowLayout.type === 'rss'
              ? Math.max(1, Math.ceil((rssQuery?.data?.total ?? 0) / Math.max(1, rssFilters.page_size)))
              : 1
          const alertTotalPages =
            windowLayout.type === 'alerts'
              ? Math.max(1, Math.ceil((alertQuery?.data?.total ?? 0) / Math.max(1, alertFilters.page_size)))
              : 1
          const windowMeta = WINDOW_TYPE_META[windowLayout.type]
          const windowTimeSummary = formatWindowTimeSummary(windowLayout, dashboardTimeFilter)
          const activeLocalFilterCount = countActiveWindowFilters(windowLayout, rssFilters, alertFilters)
          const isPanelRefreshing =
            windowLayout.type === 'rss'
              ? Boolean(rssQuery?.isFetching && !rssQuery.isLoading)
              : windowLayout.type === 'alerts'
                ? Boolean(alertQuery?.isFetching && !alertQuery.isLoading)
                : windowLayout.type === 'daily_brief'
                  ? Boolean(dailyBriefHistoryQuery.isFetching && !dailyBriefHistoryQuery.isLoading)
                  : false

          const snapped = isWideLayout && windowLayout.snap !== 'free'
          const sectionClass = `${isWideLayout ? 'absolute' : 'relative'} flex flex-col overflow-hidden border text-[13px] ${windowMeta.shellClassName} ${
            snapped ? 'rounded-none shadow-none' : 'rounded-xl shadow-lg shadow-slate-400/15 dark:shadow-cyan-950/40'
          }`

          return (
            <section
              key={windowLayout.id}
              className={sectionClass}
              style={
                isWideLayout
                  ? {
                      left: resolvedRect.x,
                      top: resolvedRect.y,
                      width: resolvedRect.width,
                      height: resolvedRect.height,
                    }
                  : undefined
              }
              onMouseDown={() => bringWindowToFront(windowLayout.id)}
            >
              <div
                className={`flex flex-col gap-3 border-b border-slate/20 px-3 py-2.5 dark:border-cyan-900/40 ${windowMeta.headerClassName} sm:flex-row sm:items-start sm:justify-between`}
                onMouseDown={isEditMode ? (event) => startWindowDrag(event, windowLayout.id) : undefined}
                style={isEditMode ? { cursor: 'grab' } : undefined}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`rounded border px-2 py-0.5 text-[10px] font-semibold ${windowMeta.badgeClassName}`}>
                      {windowMeta.label}
                    </span>
                    <h2 className="text-base font-semibold leading-tight text-ink dark:text-white">{windowLayout.title}</h2>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    {isEditMode && (
                      <span className="rounded border border-slate/20 bg-white/70 px-2 py-0.5 text-[10px] font-semibold text-slate-700 dark:border-cyan-900/40 dark:bg-[#041612]/80 dark:text-white/65">
                        {formatWindowSnapLabel(windowLayout.snap)}
                      </span>
                    )}
                    <span className="rounded border border-slate/20 bg-white/70 px-2 py-0.5 text-[10px] font-semibold text-slate-700 dark:border-cyan-900/40 dark:bg-[#041612]/80 dark:text-white/65">
                      {windowTimeSummary}
                    </span>
                    {(windowLayout.type === 'rss' || windowLayout.type === 'alerts') && activeLocalFilterCount > 0 && (
                      <span className="rounded border border-slate/20 bg-white/70 px-2 py-0.5 text-[10px] font-semibold text-slate-700 dark:border-cyan-900/40 dark:bg-[#041612]/80 dark:text-white/65">
                        {activeLocalFilterCount} local filters
                      </span>
                    )}
                    {windowLayout.type === 'alerts' && (
                      <span className="rounded border border-slate/20 px-2 py-0.5 text-[10px] font-semibold text-slate dark:border-cyan-900/40 dark:text-slate-300">
                        {alertWindowItems.length} shown
                      </span>
                    )}
                    {windowLayout.type === 'notes' && (
                      <span className="rounded border border-slate/20 px-2 py-0.5 text-[10px] font-semibold text-slate dark:border-cyan-900/40 dark:text-slate-300">
                        Scratch pad
                      </span>
                    )}
                    {windowLayout.type === 'rss' && rssChangedCount > 0 && (
                      <span className="rounded border border-cyan/40 bg-cyan/20 px-2 py-0.5 text-[10px] font-semibold text-cyan">
                        +{rssChangedCount} new
                      </span>
                    )}
                    {windowLayout.type === 'alerts' && alertChangedCount > 0 && (
                      <span className="rounded border border-cyan/40 bg-cyan/20 px-2 py-0.5 text-[10px] font-semibold text-cyan">
                        +{alertChangedCount} new
                      </span>
                    )}
                    {isPanelRefreshing && (
                      <span className="rounded border border-slate/20 bg-white/70 px-2 py-0.5 text-[10px] font-semibold text-slate-700 dark:border-cyan-900/40 dark:bg-[#041612]/80 dark:text-white/65">
                        Updating...
                      </span>
                    )}
                  </div>
                </div>
                <div
                  className="flex flex-wrap items-center gap-2 sm:shrink-0 sm:border-l sm:border-slate/15 sm:pl-3 dark:sm:border-cyan-900/30"
                  onMouseDown={(event) => event.stopPropagation()}
                >
                    {windowLayout.type === 'alerts' && (
                      <button
                        type="button"
                        className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40"
                        onClick={() => markWindowSeen(windowLayout.id)}
                      >
                        Mark Seen
                      </button>
                    )}
                    {(windowLayout.type === 'rss' || windowLayout.type === 'alerts') && (
                      <button
                        type="button"
                        className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40"
                        onClick={() => toggleWindowControls(windowLayout.id)}
                      >
                        {windowLayout.controls_collapsed ? 'Show Filters' : 'Hide Filters'}
                      </button>
                    )}
                    {isEditMode && (
                      <>
                        <select
                          className="rounded border border-slate/20 bg-white px-2 py-1 text-xs dark:border-cyan-900/40 dark:bg-[#072019]"
                          value={windowLayout.snap}
                          onChange={(event) => setWindowSnap(windowLayout.id, event.target.value as DashboardWindowSnap)}
                          onMouseDown={(event) => event.stopPropagation()}
                          aria-label={`${windowLayout.title} panel layout`}
                        >
                          {WINDOW_SNAP_OPTIONS.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                        <button
                          type="button"
                          className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40"
                          onClick={() => openRenameWindow(windowLayout.id)}
                        >
                          Rename
                        </button>
                        <button
                          type="button"
                          className="rounded border border-slate/20 px-2 py-1 text-xs text-red-600 disabled:opacity-40 dark:border-cyan-900/40"
                          disabled={windows.length <= 1}
                          onClick={() => removeWindow(windowLayout.id)}
                        >
                          Close
                        </button>
                      </>
                    )}
                    {isEditMode && isWideLayout && windowLayout.snap === 'free' && (
                      <div
                        role="group"
                        aria-label={`${windowLayout.title} keyboard layout controls`}
                        className="w-full rounded border border-slate/20 bg-white/80 p-2 text-[11px] dark:border-cyan-900/40 dark:bg-[#041612]/85"
                      >
                        <p className="font-semibold text-slate-800 dark:text-white/80">Keyboard panel controls</p>
                        <p className="mt-1 text-slate dark:text-white/60">
                          Position {resolvedRect.x}, {resolvedRect.y} · Size {resolvedRect.width} x {resolvedRect.height}
                        </p>
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          <button
                            type="button"
                            className="rounded border border-slate/20 px-2 py-1 text-[11px] dark:border-cyan-900/40"
                            onClick={() => adjustFreeWindowRect(windowLayout.id, { x: -KEYBOARD_PANEL_MOVE_STEP })}
                            aria-label={`Move ${windowLayout.title} left`}
                          >
                            Left
                          </button>
                          <button
                            type="button"
                            className="rounded border border-slate/20 px-2 py-1 text-[11px] dark:border-cyan-900/40"
                            onClick={() => adjustFreeWindowRect(windowLayout.id, { x: KEYBOARD_PANEL_MOVE_STEP })}
                            aria-label={`Move ${windowLayout.title} right`}
                          >
                            Right
                          </button>
                          <button
                            type="button"
                            className="rounded border border-slate/20 px-2 py-1 text-[11px] dark:border-cyan-900/40"
                            onClick={() => adjustFreeWindowRect(windowLayout.id, { y: -KEYBOARD_PANEL_MOVE_STEP })}
                            aria-label={`Move ${windowLayout.title} up`}
                          >
                            Up
                          </button>
                          <button
                            type="button"
                            className="rounded border border-slate/20 px-2 py-1 text-[11px] dark:border-cyan-900/40"
                            onClick={() => adjustFreeWindowRect(windowLayout.id, { y: KEYBOARD_PANEL_MOVE_STEP })}
                            aria-label={`Move ${windowLayout.title} down`}
                          >
                            Down
                          </button>
                          <button
                            type="button"
                            className="rounded border border-slate/20 px-2 py-1 text-[11px] dark:border-cyan-900/40"
                            onClick={() => adjustFreeWindowRect(windowLayout.id, { width: -KEYBOARD_PANEL_RESIZE_STEP })}
                            aria-label={`Make ${windowLayout.title} narrower`}
                          >
                            Narrower
                          </button>
                          <button
                            type="button"
                            className="rounded border border-slate/20 px-2 py-1 text-[11px] dark:border-cyan-900/40"
                            onClick={() => adjustFreeWindowRect(windowLayout.id, { width: KEYBOARD_PANEL_RESIZE_STEP })}
                            aria-label={`Make ${windowLayout.title} wider`}
                          >
                            Wider
                          </button>
                          <button
                            type="button"
                            className="rounded border border-slate/20 px-2 py-1 text-[11px] dark:border-cyan-900/40"
                            onClick={() => adjustFreeWindowRect(windowLayout.id, { height: -KEYBOARD_PANEL_RESIZE_STEP })}
                            aria-label={`Make ${windowLayout.title} shorter`}
                          >
                            Shorter
                          </button>
                          <button
                            type="button"
                            className="rounded border border-slate/20 px-2 py-1 text-[11px] dark:border-cyan-900/40"
                            onClick={() => adjustFreeWindowRect(windowLayout.id, { height: KEYBOARD_PANEL_RESIZE_STEP })}
                            aria-label={`Make ${windowLayout.title} taller`}
                          >
                            Taller
                          </button>
                        </div>
                      </div>
                    )}
                </div>
              </div>

              {windowLayout.type === 'rss' ? (
                <>
                  {!windowLayout.controls_collapsed && (
                    <div className={`border-b border-slate/20 px-3 py-2 dark:border-cyan-900/40 ${windowMeta.panelClassName}`}>
                      <div className="flex items-center gap-1.5 overflow-x-auto pb-0.5" role="group" aria-label={`${windowLayout.title} feed filters`}>
                      <button
                        type="button"
                        aria-pressed={rssFilters.selected_feed_ids.length === 0}
                        aria-label={`${windowLayout.title} all feeds`}
                        className={`rounded border px-3 py-1 text-xs font-semibold ${
                          rssFilters.selected_feed_ids.length === 0
                            ? 'tl-chip-filter-active'
                            : 'tl-chip-neutral'
                        }`}
                        onClick={() => updateWindowRssFilters(windowLayout.id, (current) => ({ ...current, selected_feed_ids: [] }))}
                      >
                        All
                      </button>
                      {feedsQuery.data?.map((feed) => {
                        const active = rssFilters.selected_feed_ids.includes(feed.id)
                        const health = resolveFeedHealth(feed)
                        return (
                          <button
                            key={feed.id}
                            type="button"
                            aria-pressed={active}
                            className={`whitespace-nowrap rounded border px-3 py-1 text-xs font-semibold ${
                              active ? 'tl-chip-filter-active' : 'tl-chip-neutral'
                            }`}
                            aria-label={`${windowLayout.title} ${feed.name} feed, ${health.label}`}
                            onClick={() =>
                              updateWindowRssFilters(windowLayout.id, (current) => ({
                                ...current,
                                selected_feed_ids: current.selected_feed_ids.includes(feed.id)
                                  ? current.selected_feed_ids.filter((id) => id !== feed.id)
                                  : [...current.selected_feed_ids, feed.id],
                              }))
                            }
                          >
                            {feed.name}
                            <span className={`tl-chip ml-1.5 ${feedHealthBadgeClass(health.status)}`}>{health.label}</span>
                          </button>
                        )
                      })}
                      </div>

                      <div className="mt-1 flex items-center gap-1.5 overflow-x-auto pb-0.5" role="group" aria-label={`${windowLayout.title} tag filters`}>
                      <button
                        type="button"
                        aria-pressed={rssFilters.selected_tags.length === 0}
                        aria-label={`${windowLayout.title} all tags`}
                        className={`rounded border px-3 py-1 text-xs font-semibold ${
                          rssFilters.selected_tags.length === 0
                            ? 'tl-chip-filter-active'
                            : 'tl-chip-neutral'
                        }`}
                        onClick={() => updateWindowRssFilters(windowLayout.id, (current) => ({ ...current, selected_tags: [] }))}
                      >
                        All
                      </button>
                      {tagsQuery.data
                        ?.filter((tag) => !HIDDEN_TAGS.has(tag.name))
                        .map((tag) => {
                          const active = rssFilters.selected_tags.includes(tag.name)
                          return (
                          <button
                            key={tag.id}
                            type="button"
                            aria-pressed={active}
                            className={`whitespace-nowrap rounded border px-3 py-1 text-xs font-semibold ${
                              active
                                ? 'tl-chip-filter-active'
                                : 'tl-chip-neutral'
                              }`}
                              onClick={() =>
                                updateWindowRssFilters(windowLayout.id, (current) => ({
                                  ...current,
                                  selected_tags: current.selected_tags.includes(tag.name)
                                    ? current.selected_tags.filter((entry) => entry !== tag.name)
                                    : [...current.selected_tags, tag.name],
                                }))
                              }
                            >
                              #{tag.name}
                            </button>
                          )
                        })}
                      </div>
                      {tagsQuery.isError && <p className="mt-0.5 text-xs text-red-600">Failed to load tags.</p>}

                      <div className="mt-1 flex flex-wrap items-center gap-1.5">
                      <input
                        value={rssFilters.q}
                        onChange={(event) => updateWindowRssFilters(windowLayout.id, (current) => ({ ...current, q: event.target.value }))}
                        aria-label={`${windowLayout.title} search query`}
                        placeholder="Search title, summary, URL"
                        className="w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm sm:min-w-64 sm:flex-1 dark:border-cyan-900/40 dark:bg-[#072019]"
                      />
                      <select
                        className="w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm sm:w-auto dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={windowLayout.time_override?.time_range ?? DASHBOARD_TIME_INHERIT_VALUE}
                        onChange={(event) => updateWindowTimeRange(windowLayout.id, event.target.value)}
                        aria-label={`${windowLayout.title} time range`}
                      >
                        <option value={DASHBOARD_TIME_INHERIT_VALUE}>Dashboard Time</option>
                        <option value="all">All time</option>
                        <option value="24h">24h</option>
                        <option value="7d">7d</option>
                        <option value="30d">30d</option>
                        <option value="days">Last X days</option>
                        <option value="custom">Custom</option>
                      </select>
                      {effectiveWindowTimeFilter.time_range === 'days' && (
                        <label className={`${ROLLING_WINDOW_FIELD_CLASS} sm:w-[150px] dark:bg-[#072019]`}>
                          <span className="mr-2 text-xs text-slate dark:text-white/60">Last</span>
                          <input
                            type="number"
                            min={1}
                            max={365}
                            value={effectiveWindowTimeFilter.rolling_days}
                            onChange={(event) => updateWindowRollingDays(windowLayout.id, event.target.value)}
                            aria-label={`${windowLayout.title} rolling time window in days`}
                            className="w-full bg-transparent focus-visible:outline-none"
                          />
                          <span className="ml-2 text-xs text-slate dark:text-white/60">days</span>
                        </label>
                      )}
                      <select
                        className="w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm sm:w-auto dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={rssFilters.sort}
                        onChange={(event) =>
                          updateWindowRssFilters(windowLayout.id, (current) => ({ ...current, sort: event.target.value as TimeSort }))
                        }
                        aria-label={`${windowLayout.title} sort order`}
                      >
                        <option value="published_at_desc">Newest</option>
                        <option value="published_at_asc">Oldest</option>
                        <option value="first_seen_desc">Seen newest</option>
                        <option value="first_seen_asc">Seen oldest</option>
                      </select>
                      <div
                        className="flex w-full rounded border border-slate/20 p-0.5 sm:w-auto dark:border-cyan-900/40"
                        role="group"
                        aria-label={`${windowLayout.title} view mode`}
                      >
                        <button
                          type="button"
                          aria-pressed={rssFilters.view_mode === 'expanded'}
                          className={`flex-1 rounded px-2 py-1 text-xs font-semibold sm:flex-none ${rssFilters.view_mode === 'expanded' ? 'bg-cyan/15 text-cyan' : ''}`}
                          onClick={() => updateWindowRssFilters(windowLayout.id, (current) => ({ ...current, view_mode: 'expanded' }), false)}
                        >
                          Expanded
                        </button>
                        <button
                          type="button"
                          aria-pressed={rssFilters.view_mode === 'compact'}
                          className={`flex-1 rounded px-2 py-1 text-xs font-semibold sm:flex-none ${rssFilters.view_mode === 'compact' ? 'bg-cyan/15 text-cyan' : ''}`}
                          onClick={() => updateWindowRssFilters(windowLayout.id, (current) => ({ ...current, view_mode: 'compact' }), false)}
                        >
                          Compact
                        </button>
                      </div>
                      <button
                        type="button"
                        className="w-full rounded border border-slate/20 px-3 py-1.5 text-xs font-semibold sm:w-auto dark:border-cyan-900/40"
                        onClick={() =>
                          updateWindowRssFilters(
                            windowLayout.id,
                            (current) => ({ ...current, show_advanced_filters: !current.show_advanced_filters }),
                            false,
                          )
                        }
                      >
                        {rssFilters.show_advanced_filters ? 'Hide Filters' : 'More Filters'}
                      </button>
                      </div>

                      {rssFilters.show_advanced_filters && (
                        <div className="mt-1 grid gap-2 rounded border border-slate/20 bg-white/90 p-2 dark:border-cyan-900/40 dark:bg-[#072019]/70 md:grid-cols-2 lg:grid-cols-3">
                        <select
                          className="rounded border border-slate/20 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#041612]"
                          value={rssFilters.read_status}
                          onChange={(event) =>
                            updateWindowRssFilters(windowLayout.id, (current) => ({
                              ...current,
                              read_status: event.target.value as ReadStatusFilter,
                            }))
                          }
                          aria-label={`${windowLayout.title} read status filter`}
                        >
                          <option value="all">Read: All</option>
                          <option value="unread">Read: Unread</option>
                          <option value="read">Read: Read</option>
                        </select>
                        <select
                          className="rounded border border-slate/20 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#041612]"
                          value={rssFilters.star_status}
                          onChange={(event) =>
                            updateWindowRssFilters(windowLayout.id, (current) => ({
                              ...current,
                              star_status: event.target.value as StarStatusFilter,
                            }))
                          }
                          aria-label={`${windowLayout.title} star filter`}
                        >
                          <option value="all">Stars: All</option>
                          <option value="starred">Stars: Starred</option>
                          <option value="unstarred">Stars: Unstarred</option>
                        </select>
                        <div className="flex flex-col gap-2 sm:flex-row">
                          <input
                            type="date"
                            className="w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm disabled:opacity-50 dark:border-cyan-900/40 dark:bg-[#041612]"
                            value={effectiveWindowTimeFilter.custom_since_date}
                            onChange={(event) => updateWindowCustomTimeDate(windowLayout.id, 'custom_since_date', event.target.value)}
                            disabled={effectiveWindowTimeFilter.time_range !== 'custom'}
                            aria-label={`${windowLayout.title} custom start date`}
                          />
                          <input
                            type="date"
                            className="w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm disabled:opacity-50 dark:border-cyan-900/40 dark:bg-[#041612]"
                            value={effectiveWindowTimeFilter.custom_until_date}
                            onChange={(event) => updateWindowCustomTimeDate(windowLayout.id, 'custom_until_date', event.target.value)}
                            disabled={effectiveWindowTimeFilter.time_range !== 'custom'}
                            aria-label={`${windowLayout.title} custom end date`}
                          />
                        </div>
                        </div>
                      )}
                    </div>
                  )}

                  <div className="flex-1 overflow-auto p-3">
                    <div className="space-y-2">
                      {rssWindowItems.map((item) => {
                        const expanded = expandedItemIdsByWindowId[windowLayout.id] === item.id
                        const detailQuery = detailQueriesByWindowId[windowLayout.id]
                        const detail = expanded ? detailQuery?.data : null
                        const compact = rssFilters.view_mode === 'compact'
                        const itemHref = sanitizeHref(item.canonical_url || item.url)
                        const detailHref = sanitizeHref(detail?.article?.final_url || detail?.url || null)

                        return (
                          <article
                            key={item.id}
                            className={`rounded border text-slate-900 dark:text-slate-100 ${compact ? 'p-2' : 'p-3'} transition ${
                              expanded ? 'tl-row-selected' : 'border-slate/20 bg-white/65 dark:border-cyan-900/40 dark:bg-white/[0.02]'
                            } ${item.is_read ? 'opacity-85' : ''}`}
                          >
                            <div className="w-full text-left">
                              <div className="flex items-start justify-between gap-3">
                                <h3 className={`${compact ? 'text-[14px]' : 'text-[15px]'} font-semibold leading-snug`}>
                                  {itemHref ? (
                                    <a
                                      href={itemHref}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="hover:text-cyan hover:underline"
                                      onClick={(event) => event.stopPropagation()}
                                    >
                                      {item.title}
                                    </a>
                                  ) : (
                                    <span>{item.title}</span>
                                  )}
                                </h3>
                                <div className="flex shrink-0 items-center gap-2">
                                  <span className="tl-source-text text-xs font-semibold dark:text-slate-300">{item.feed_name}</span>
                                </div>
                              </div>
                              <button
                                type="button"
                                className="mt-1 w-full text-left text-slate-900 dark:text-slate-100"
                                onClick={() => handleToggleItem(windowLayout.id, item.id)}
                                aria-expanded={expanded}
                                aria-controls={`rss-item-detail-${item.id}`}
                              >
                                <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate dark:text-slate-300">
                                  <span>Published {formatPublishedAt(item.published_at)}</span>
                                  {item.status !== 'content_fetched' && (
                                    <span className={`tl-chip ${itemStatusTone(item.status)}`}>{formatItemStatusLabel(item.status)}</span>
                                  )}
                                  {!item.is_read && <span className="tl-chip tl-chip-info">Unread</span>}
                                  {item.is_starred && <span className="tl-chip tl-chip-neutral">Starred</span>}
                                  {aiRelevanceEnabled && item.ai_relevance_label && (
                                    <span className={`tl-chip ${aiRelevanceTone(item.ai_relevance_label)}`}>
                                      AI {formatAiRelevanceLabel(item.ai_relevance_label)}
                                    </span>
                                  )}
                                  {item.tags
                                    .filter((tagName) => !HIDDEN_TAGS.has(tagName))
                                    .slice(0, 3)
                                    .map((tagName) => (
                                      <span
                                        key={`${item.id}-${tagName}`}
                                        className="tl-chip tl-chip-neutral"
                                      >
                                        #{tagName}
                                      </span>
                                    ))}
                                </div>
                                {!compact && (
                                  <p className="mt-2 line-clamp-2 text-[13px] leading-5 text-slate dark:text-slate-300">
                                    {item.summary || 'No summary available.'}
                                  </p>
                                )}
                              </button>
                            </div>

                            {expanded && (
                              <div
                                id={`rss-item-detail-${item.id}`}
                                className="mt-3 border-t border-slate/20 pt-3 dark:border-cyan-900/40"
                              >
                                {detailQuery?.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading article content...</p>}
                                {detailQuery?.isError && <p className="text-sm text-red-600">Failed to load item details.</p>}

                                {detail && detail.id === item.id && (
                                  <>
                                    <div className="flex flex-wrap items-center gap-2">
                                      {detailHref ? (
                                        <a
                                          className="rounded border border-slate/20 px-2 py-1 text-xs hover:border-cyan hover:text-cyan dark:border-cyan-900/40"
                                          href={detailHref}
                                          target="_blank"
                                          rel="noreferrer"
                                        >
                                          Open Source Link
                                        </a>
                                      ) : (
                                        <span className="rounded border border-slate/20 px-2 py-1 text-xs text-slate dark:border-cyan-900/40 dark:text-slate-400">
                                          Source link unavailable
                                        </span>
                                      )}
                                      <button
                                        className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40"
                                        disabled={!canManage || (updateRead.isPending && updateRead.variables?.itemId === detail.id)}
                                        onClick={() =>
                                          updateRead.mutate({
                                            itemId: detail.id,
                                            isRead: !detail.state.is_read,
                                          })
                                        }
                                      >
                                        {updateRead.isPending && updateRead.variables?.itemId === detail.id
                                          ? 'Saving...'
                                          : detail.state.is_read
                                            ? 'Mark Unread'
                                            : 'Mark Read'}
                                      </button>
                                      <button
                                        className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40"
                                        disabled={!canManage || (updateStar.isPending && updateStar.variables?.itemId === detail.id)}
                                        onClick={() =>
                                          updateStar.mutate({
                                            itemId: detail.id,
                                            isStarred: !detail.state.is_starred,
                                          })
                                        }
                                      >
                                        {updateStar.isPending && updateStar.variables?.itemId === detail.id
                                          ? 'Saving...'
                                          : detail.state.is_starred
                                            ? 'Unstar'
                                            : 'Star'}
                                      </button>
                                      {!canManage && <span className="text-xs text-amber-600 dark:text-amber-300">Viewer role is read-only.</span>}
                                    </div>
                                    {itemActionFeedbackByItemId[detail.id] && (
                                      <p
                                        role={itemActionFeedbackByItemId[detail.id]?.tone === 'error' ? 'alert' : 'status'}
                                        aria-live={itemActionFeedbackByItemId[detail.id]?.tone === 'error' ? 'assertive' : 'polite'}
                                        aria-atomic="true"
                                        className={`mt-2 text-xs ${
                                          itemActionFeedbackByItemId[detail.id]?.tone === 'success'
                                            ? 'text-emerald-700 dark:text-emerald-300'
                                            : 'text-red-600 dark:text-red-300'
                                        }`}
                                      >
                                        {itemActionFeedbackByItemId[detail.id]?.message}
                                      </p>
                                    )}

                                    <div className="tl-surface-muted mt-3 rounded p-3">
                                      <p className="text-xs font-semibold text-slate dark:text-slate-300">RSS summary</p>
                                      {detail.classification && (
                                        <p className="mt-1 text-xs text-slate dark:text-slate-300">
                                          Classification:{' '}
                                          <span className="font-semibold">
                                            {formatClassificationLabel(detail.classification.primary_category)}
                                          </span>{' '}
                                          ({Math.round(detail.classification.confidence * 100)}% confidence)
                                        </p>
                                      )}
                                      <div className="rss-reader tl-reader-surface mt-2 rounded p-3">
                                        {renderRichContent(detail.summary || 'No summary.', detail.id, 'summary')}
                                      </div>
                                    </div>

                                    {(aiSummaryEnabled || aiRelevanceEnabled) && detail.ai_insight?.status === 'ready' && (
                                      <div className="tl-surface-muted mt-3 rounded p-3">
                                        <p className="text-xs font-semibold text-slate dark:text-slate-300">AI insight</p>
                                        {aiRelevanceEnabled && detail.ai_insight.relevance_label && (
                                          <div className="mt-2 flex flex-wrap items-center gap-2">
                                            <span className={`tl-chip tl-chip-md ${aiRelevanceTone(detail.ai_insight.relevance_label)}`}>
                                              {formatAiRelevanceLabel(detail.ai_insight.relevance_label)} Relevance
                                            </span>
                                            {typeof detail.ai_insight.relevance_score === 'number' && (
                                              <span className="text-xs text-slate dark:text-white/65">
                                                Score {Math.round(detail.ai_insight.relevance_score * 100)}%
                                              </span>
                                            )}
                                          </div>
                                        )}
                                        {aiRelevanceEnabled && detail.ai_insight.relevance_reasons.length > 0 && (
                                          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate dark:text-white/75">
                                            {detail.ai_insight.relevance_reasons.map((reason, index) => (
                                              <li key={`${detail.id}-ai-reason-${index}`}>{reason}</li>
                                            ))}
                                          </ul>
                                        )}
                                        {aiSummaryEnabled && detail.ai_insight.summary_text && (
                                          <div className="tl-reader-surface mt-3 rounded p-3">
                                            {renderArticleBlocks(detail.ai_insight.summary_text, `${detail.id}-ai-summary`)}
                                          </div>
                                        )}
                                        <p className="mt-2 text-xs text-slate dark:text-white/60">
                                          Generated {formatPublishedAt(detail.ai_insight.generated_at)}{detail.ai_insight.model ? ` via ${detail.ai_insight.model}` : ''}.
                                        </p>
                                      </div>
                                    )}
                                    {(aiSummaryEnabled || aiRelevanceEnabled) && detail.ai_insight?.status === 'error' && detail.ai_insight.error && (
                                      <div className="mt-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/20 dark:text-red-200">
                                        AI enrichment failed: {detail.ai_insight.error}
                                      </div>
                                    )}

                                    <div className="tl-surface-muted mt-3 rounded p-3">
                                      <p className="text-xs font-semibold text-slate dark:text-slate-300">Full article</p>
                                      {detail.article?.text && detail.article.extraction_method === 'rss_summary_fallback' && (
                                        <p className="mt-2 text-xs text-amber-700 dark:text-amber-200">
                                          Showing RSS summary because full article extraction returned {detail.article.error ?? 'an error'}.
                                        </p>
                                      )}
                                      {detail.article?.text ? (
                                        <div className="rss-reader tl-reader-surface mt-2 rounded p-3">
                                          {renderRichContent(detail.article.text, detail.id, 'article')}
                                        </div>
                                      ) : (
                                        <p className="mt-2 text-sm text-slate dark:text-slate-300">No extracted article text available yet.</p>
                                      )}
                                      {detail.article?.error && detail.article.extraction_method !== 'rss_summary_fallback' && (
                                        <p className="mt-2 text-sm text-red-600">Extraction error: {detail.article.error}</p>
                                      )}
                                      {(!detail.article?.text || detail.article?.error) && (
                                        <div className="mt-3 flex flex-wrap items-center gap-2">
                                          <button
                                            className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40 disabled:opacity-50"
                                            disabled={
                                              !canManage ||
                                              (retryArticleFetch.isPending && retryArticleFetch.variables?.itemId === detail.id)
                                            }
                                            onClick={() => retryArticleFetch.mutate({ itemId: detail.id })}
                                          >
                                            {retryArticleFetch.isPending && retryArticleFetch.variables?.itemId === detail.id
                                              ? 'Queueing...'
                                              : detail.article?.error
                                                ? 'Retry Article Fetch'
                                                : 'Queue Article Fetch'}
                                          </button>
                                          {articleRetryFeedbackByItemId[detail.id] && (
                                            <span
                                              role={articleRetryFeedbackByItemId[detail.id]?.tone === 'error' ? 'alert' : 'status'}
                                              aria-live={articleRetryFeedbackByItemId[detail.id]?.tone === 'error' ? 'assertive' : 'polite'}
                                              aria-atomic="true"
                                              className={`text-xs ${
                                                articleRetryFeedbackByItemId[detail.id]?.tone === 'success'
                                                  ? 'text-emerald-700 dark:text-emerald-300'
                                                  : 'text-red-600 dark:text-red-300'
                                              }`}
                                            >
                                              {articleRetryFeedbackByItemId[detail.id]?.message}
                                            </span>
                                          )}
                                          {!canManage && (
                                            <span className="text-xs text-slate dark:text-slate-300">Read-only for viewer role.</span>
                                          )}
                                        </div>
                                      )}
                                    </div>

                                    <div className="tl-surface-muted mt-3 rounded p-3">
                                      <label className="text-xs font-semibold text-slate dark:text-slate-300">Notes</label>
                                      <textarea
                                        className="mt-1 h-20 w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                                        value={noteDraftsByItemId[detail.id] ?? detail.state.note ?? ''}
                                        onChange={(event) =>
                                          setNoteDraftsByItemId((current) => ({
                                            ...current,
                                            [detail.id]: event.target.value,
                                          }))
                                        }
                                        disabled={!canManage}
                                        aria-label={`Analyst notes for ${detail.title}`}
                                      />
                                      <div className="mt-2 flex items-center gap-2">
                                        <button
                                          className="rounded bg-ink px-2.5 py-1 text-xs font-semibold text-white disabled:opacity-50 dark:border dark:border-cyan-500/35 dark:bg-[var(--tl-accent-bg-strong)] dark:text-[var(--tl-accent-soft)]"
                                          onClick={() =>
                                            updateNote.mutate({
                                              itemId: detail.id,
                                              note: (noteDraftsByItemId[detail.id] ?? detail.state.note ?? '') || null,
                                            })
                                          }
                                          disabled={!canManage || (updateNote.isPending && updateNote.variables?.itemId === detail.id)}
                                        >
                                          {updateNote.isPending && updateNote.variables?.itemId === detail.id ? 'Saving...' : 'Save Notes'}
                                        </button>
                                        {!canManage && <span className="text-xs text-slate dark:text-slate-300">Read-only for viewer role.</span>}
                                      </div>
                                    </div>
                                  </>
                                )}
                              </div>
                            )}
                          </article>
                        )
                      })}

                      {rssQuery?.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading items...</p>}
                      {rssQuery?.isFetching && !rssQuery.isLoading && (
                        <p className="text-xs text-slate dark:text-white/60">Refreshing items...</p>
                      )}
                      {rssQuery?.isError && (
                        <p className="text-sm text-red-600">
                          Failed to load items. {(rssQuery.error as Error | undefined)?.message ?? ''}
                        </p>
                      )}
                      {!rssQuery?.isLoading && !rssWindowItems.length && (
                        <p className="text-sm text-slate dark:text-slate-300">No items match current filters.</p>
                      )}
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate/20 px-3 py-2 text-xs dark:border-cyan-900/40">
                    <button
                      className="rounded border border-slate/20 px-2 py-1 disabled:opacity-50 dark:border-cyan-900/40"
                      disabled={rssFilters.page <= 1}
                      onClick={() =>
                        updateWindowRssFilters(windowLayout.id, (current) => ({ ...current, page: current.page - 1 }), false)
                      }
                    >
                      Prev
                    </button>
                    <span className="w-full text-center sm:w-auto">
                      Page {rssFilters.page} / {rssTotalPages}
                    </span>
                    <div className="ml-auto flex items-center gap-2">
                      <label className="text-xs text-slate dark:text-slate-300">Per page</label>
                      <select
                        className="rounded border border-slate/20 bg-white px-2 py-1 text-xs dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={rssFilters.page_size}
                        onChange={(event) =>
                          updateWindowRssFilters(windowLayout.id, (current) => ({
                            ...current,
                            page_size: Number(event.target.value) as DashboardRssWindowFilters['page_size'],
                          }))
                        }
                        aria-label={`${windowLayout.title} results per page`}
                      >
                        {PAGE_SIZE_OPTIONS.map((option) => (
                          <option key={option} value={option}>
                            {option}
                          </option>
                        ))}
                      </select>
                      <button
                        className="rounded border border-slate/20 px-2 py-1 disabled:opacity-50 dark:border-cyan-900/40"
                        disabled={rssFilters.page >= rssTotalPages}
                        onClick={() =>
                          updateWindowRssFilters(windowLayout.id, (current) => ({ ...current, page: current.page + 1 }), false)
                        }
                      >
                        Next
                      </button>
                    </div>
                  </div>
                </>
              ) : windowLayout.type === 'alerts' ? (
                <>
                  {!windowLayout.controls_collapsed && (
                    <div className={`border-b border-slate/20 px-3 py-2 dark:border-cyan-900/40 ${windowMeta.panelClassName}`}>
                    <div
                      className="flex items-center gap-2 overflow-x-auto pb-1"
                      role="group"
                      aria-label={`${windowLayout.title} alert category filters`}
                    >
                      <button
                        type="button"
                        aria-pressed={alertFilters.selected_categories.length === 0}
                        className={`rounded border px-3 py-1 text-xs font-semibold ${
                          alertFilters.selected_categories.length === 0
                            ? 'tl-chip-filter-active'
                            : 'tl-chip-neutral'
                        }`}
                        onClick={() => updateWindowAlertFilters(windowLayout.id, (current) => ({ ...current, selected_categories: [] }))}
                      >
                        All Categories
                      </button>
                      {availableAlertCategories.map((category) => {
                        const active = alertFilters.selected_categories.includes(category)
                        return (
                          <button
                            key={category}
                            type="button"
                            aria-pressed={active}
                            className={`whitespace-nowrap rounded border px-3 py-1 text-xs font-semibold ${
                              active ? 'tl-chip-filter-active' : 'tl-chip-neutral'
                            }`}
                            onClick={() =>
                              updateWindowAlertFilters(windowLayout.id, (current) => ({
                                ...current,
                                selected_categories: current.selected_categories.includes(category)
                                  ? current.selected_categories.filter((entry) => entry !== category)
                                  : [...current.selected_categories, category],
                              }))
                            }
                          >
                            {formatClassificationLabel(category)}
                          </button>
                        )
                      })}
                    </div>

                    <div
                      className="mt-2 flex items-center gap-2 overflow-x-auto pb-1"
                      role="group"
                      aria-label={`${windowLayout.title} alert interest filters`}
                    >
                      <button
                        type="button"
                        aria-pressed={alertFilters.selected_alert_ids.length === 0}
                        className={`rounded border px-3 py-1 text-xs font-semibold ${
                          alertFilters.selected_alert_ids.length === 0
                            ? 'tl-chip-filter-active'
                            : 'tl-chip-neutral'
                        }`}
                        onClick={() => updateWindowAlertFilters(windowLayout.id, (current) => ({ ...current, selected_alert_ids: [] }))}
                      >
                        All Interests
                      </button>
                      {alertInterestsQuery.data?.map((interest) => {
                        const active = alertFilters.selected_alert_ids.includes(interest.id)
                        return (
                          <button
                            key={interest.id}
                            type="button"
                            aria-pressed={active}
                            className={`whitespace-nowrap rounded border px-3 py-1 text-xs font-semibold ${
                              active
                                ? 'tl-chip-filter-active'
                                : 'tl-chip-neutral'
                            }`}
                            onClick={() =>
                              updateWindowAlertFilters(windowLayout.id, (current) => ({
                                ...current,
                                selected_alert_ids: current.selected_alert_ids.includes(interest.id)
                                  ? current.selected_alert_ids.filter((entry) => entry !== interest.id)
                                  : [...current.selected_alert_ids, interest.id],
                              }))
                            }
                          >
                            {interest.name}
                          </button>
                        )
                      })}
                    </div>

                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <input
                        value={alertFilters.q}
                        onChange={(event) => updateWindowAlertFilters(windowLayout.id, (current) => ({ ...current, q: event.target.value }))}
                        aria-label={`${windowLayout.title} search query`}
                        placeholder="Search matched alert items"
                        className="w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm sm:min-w-64 sm:flex-1 dark:border-cyan-900/40 dark:bg-[#072019]"
                      />
                      <select
                        className="w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm sm:w-auto dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={windowLayout.time_override?.time_range ?? DASHBOARD_TIME_INHERIT_VALUE}
                        onChange={(event) => updateWindowTimeRange(windowLayout.id, event.target.value)}
                        aria-label={`${windowLayout.title} time range`}
                      >
                        <option value={DASHBOARD_TIME_INHERIT_VALUE}>Dashboard Time</option>
                        <option value="all">All time</option>
                        <option value="24h">24h</option>
                        <option value="7d">7d</option>
                        <option value="30d">30d</option>
                        <option value="days">Last X days</option>
                        <option value="custom">Custom</option>
                      </select>
                      {effectiveWindowTimeFilter.time_range === 'days' && (
                        <label className={`${ROLLING_WINDOW_FIELD_CLASS} sm:w-[150px] dark:bg-[#072019]`}>
                          <span className="mr-2 text-xs text-slate dark:text-white/60">Last</span>
                          <input
                            type="number"
                            min={1}
                            max={365}
                            value={effectiveWindowTimeFilter.rolling_days}
                            onChange={(event) => updateWindowRollingDays(windowLayout.id, event.target.value)}
                            aria-label={`${windowLayout.title} rolling time window in days`}
                            className="w-full bg-transparent focus-visible:outline-none"
                          />
                          <span className="ml-2 text-xs text-slate dark:text-white/60">days</span>
                        </label>
                      )}
                      <select
                        className="w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm sm:w-auto dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={alertFilters.sort}
                        onChange={(event) =>
                          updateWindowAlertFilters(windowLayout.id, (current) => ({ ...current, sort: event.target.value as TimeSort }))
                        }
                        aria-label={`${windowLayout.title} sort order`}
                      >
                        <option value="published_at_desc">Newest</option>
                        <option value="published_at_asc">Oldest</option>
                        <option value="first_seen_desc">Seen newest</option>
                        <option value="first_seen_asc">Seen oldest</option>
                      </select>
                      <div
                        className="flex w-full rounded border border-slate/20 p-0.5 sm:w-auto dark:border-cyan-900/40"
                        role="group"
                        aria-label={`${windowLayout.title} alert view mode`}
                      >
                        <button
                          type="button"
                          aria-pressed={alertFilters.view_mode === 'expanded'}
                          className={`flex-1 rounded px-2 py-1 text-xs font-semibold sm:flex-none ${alertFilters.view_mode === 'expanded' ? 'bg-cyan/15 text-cyan' : ''}`}
                          onClick={() => updateWindowAlertFilters(windowLayout.id, (current) => ({ ...current, view_mode: 'expanded' }), false)}
                        >
                          Expanded
                        </button>
                        <button
                          type="button"
                          aria-pressed={alertFilters.view_mode === 'compact'}
                          className={`flex-1 rounded px-2 py-1 text-xs font-semibold sm:flex-none ${alertFilters.view_mode === 'compact' ? 'bg-cyan/15 text-cyan' : ''}`}
                          onClick={() => updateWindowAlertFilters(windowLayout.id, (current) => ({ ...current, view_mode: 'compact' }), false)}
                        >
                          Compact
                        </button>
                      </div>
                      <input
                        type="date"
                        className="w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm disabled:opacity-50 sm:w-auto dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={effectiveWindowTimeFilter.custom_since_date}
                        onChange={(event) => updateWindowCustomTimeDate(windowLayout.id, 'custom_since_date', event.target.value)}
                        disabled={effectiveWindowTimeFilter.time_range !== 'custom'}
                        aria-label={`${windowLayout.title} custom start date`}
                      />
                      <input
                        type="date"
                        className="w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm disabled:opacity-50 sm:w-auto dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={effectiveWindowTimeFilter.custom_until_date}
                        onChange={(event) => updateWindowCustomTimeDate(windowLayout.id, 'custom_until_date', event.target.value)}
                        disabled={effectiveWindowTimeFilter.time_range !== 'custom'}
                        aria-label={`${windowLayout.title} custom end date`}
                      />
                    </div>
                    </div>
                  )}

                  <div className="flex-1 overflow-auto p-3">
                    <div className="space-y-2">
                      {alertWindowItems.map((item) => {
                        const compactAlerts = alertFilters.view_mode === 'compact'
                        const itemHref = sanitizeHref(item.canonical_url || item.url)
                        return (
                        <article key={item.id} className={`rounded border border-slate/20 ${compactAlerts ? 'p-2' : 'p-3'} dark:border-cyan-900/40`}>
                          <div className="flex items-start justify-between gap-2">
                            <div>
                              <h3 className={`font-semibold leading-snug ${compactAlerts ? 'text-[13px]' : ''}`}>
                                {itemHref ? (
                                  <a
                                    href={itemHref}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="hover:text-cyan hover:underline"
                                  >
                                    {item.title}
                                  </a>
                                ) : (
                                  <span>{item.title}</span>
                                )}
                              </h3>
                              <p className={`text-xs text-slate dark:text-slate-300 ${compactAlerts ? 'mt-0.5' : 'mt-1'}`}>
                                {item.feed_name} • Published {formatPublishedAt(item.published_at)}
                              </p>
                            </div>
                            <span className="rounded border border-slate/20 px-2 py-0.5 text-[11px] dark:border-cyan-900/40">
                              {item.matches.length} alerts
                            </span>
                          </div>
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {item.matches.map((match) => (
                              <span
                                key={`${item.id}-${match.alert_id}`}
                                className="tl-chip tl-chip-neutral"
                              >
                                {match.alert_name} ({formatClassificationLabel(match.category)})
                              </span>
                            ))}
                          </div>
                          {!compactAlerts && (
                            <p className="mt-2 line-clamp-2 text-[13px] leading-5 text-slate dark:text-slate-300">
                              {item.summary || 'No summary available.'}
                            </p>
                          )}
                        </article>
                      )})}

                      {alertQuery?.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading alert matches...</p>}
                      {alertQuery?.isFetching && !alertQuery.isLoading && (
                        <p className="text-xs text-slate dark:text-white/60">Refreshing matches...</p>
                      )}
                      {alertQuery?.isError && (
                        <p className="text-sm text-red-600">
                          Failed to load alert matches. {(alertQuery.error as Error | undefined)?.message ?? ''}
                        </p>
                      )}
                      {!alertQuery?.isLoading && !alertWindowItems.length && (
                        <p className="text-sm text-slate dark:text-slate-300">No items matched current alert filters.</p>
                      )}
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate/20 px-3 py-2 text-xs dark:border-cyan-900/40">
                    <button
                      className="rounded border border-slate/20 px-2 py-1 disabled:opacity-50 dark:border-cyan-900/40"
                      disabled={alertFilters.page <= 1}
                      onClick={() =>
                        updateWindowAlertFilters(windowLayout.id, (current) => ({ ...current, page: current.page - 1 }), false)
                      }
                    >
                      Prev
                    </button>
                    <span className="w-full text-center sm:w-auto">
                      Page {alertFilters.page} / {alertTotalPages}
                    </span>
                    <div className="ml-auto flex items-center gap-2">
                      <label className="text-xs text-slate dark:text-slate-300">Per page</label>
                      <select
                        className="rounded border border-slate/20 bg-white px-2 py-1 text-xs dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={alertFilters.page_size}
                        onChange={(event) =>
                          updateWindowAlertFilters(windowLayout.id, (current) => ({
                            ...current,
                            page_size: Number(event.target.value) as DashboardAlertWindowFilters['page_size'],
                          }))
                        }
                        aria-label={`${windowLayout.title} results per page`}
                      >
                        {PAGE_SIZE_OPTIONS.map((option) => (
                          <option key={option} value={option}>
                            {option}
                          </option>
                        ))}
                      </select>
                      <button
                        className="rounded border border-slate/20 px-2 py-1 disabled:opacity-50 dark:border-cyan-900/40"
                        disabled={alertFilters.page >= alertTotalPages}
                        onClick={() =>
                          updateWindowAlertFilters(windowLayout.id, (current) => ({ ...current, page: current.page + 1 }), false)
                        }
                      >
                        Next
                      </button>
                    </div>
                  </div>
                </>
              ) : windowLayout.type === 'daily_brief' ? (
                <div className={`flex min-h-0 flex-1 flex-col p-3 ${windowMeta.panelClassName}`}>
                  {dailyBriefHistoryQuery.isLoading && <p className="text-sm text-slate dark:text-white/75">Loading daily brief...</p>}
                  {dailyBriefHistoryQuery.isError && (
                    <p className="text-sm text-red-600">
                      Failed to load the daily brief. {(dailyBriefHistoryQuery.error as Error | undefined)?.message ?? ''}
                    </p>
                  )}
                  {!dailyBriefHistoryQuery.isLoading && !(dailyBriefHistoryQuery.data?.length ?? 0) && (
                    <p className="text-sm text-slate dark:text-white/75">
                      No AI daily brief is available yet. An admin can generate one from the AI page after the endpoint is configured.
                    </p>
                  )}
                  {(dailyBriefHistoryQuery.data?.length ?? 0) > 0 && (() => {
                    const availableBriefs = dailyBriefHistoryQuery.data ?? []
                    const selectedBrief =
                      availableBriefs.find((brief) => brief.id === windowLayout.selected_daily_brief_id) ?? availableBriefs[0]

                    if (!selectedBrief) {
                      return null
                    }

                    return (
                    <div className="min-h-0 flex-1 space-y-3 overflow-auto">
                      <div className="rounded border border-slate/20 bg-white/92 p-3 dark:border-cyan-900/40 dark:bg-[#041612]/92">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                          <label className="flex min-w-[220px] flex-1 items-center gap-2 text-sm">
                            <span className="text-xs font-semibold text-slate dark:text-white/55">Briefing</span>
                            <select
                              className="w-full rounded border border-slate/20 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#041612]"
                              value={selectedBrief.id}
                              onChange={(event) => updateWindowDailyBriefSelection(windowLayout.id, event.target.value)}
                              aria-label={`${windowLayout.title} briefing selection`}
                            >
                              {availableBriefs.map((brief) => (
                                <option key={brief.id} value={brief.id}>
                                  {formatDailyBriefOptionLabel(brief)}
                                </option>
                              ))}
                            </select>
                          </label>
                        </div>
                        <p className="mt-3 text-xs text-slate dark:text-white/60">
                          Generated {formatPublishedAt(selectedBrief.generated_at)} for {selectedBrief.item_count} items covering{' '}
                          {formatPublishedAt(selectedBrief.window_start)} to {formatPublishedAt(selectedBrief.window_end)}.
                        </p>
                      </div>

                      {selectedBrief.key_points.length > 0 && (
                        <div className="rounded border border-slate/20 bg-white/90 p-3 dark:border-cyan-900/40 dark:bg-[#041612]/92">
                          <p className="text-xs font-semibold text-slate dark:text-slate-300">Key points</p>
                          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate dark:text-white/75">
                            {selectedBrief.key_points.map((point, index) => (
                              <li key={`${windowLayout.id}-brief-point-${index}`}>{point}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {selectedBrief.recommended_actions.length > 0 && (
                        <div className="rounded border border-slate/20 bg-white/90 p-3 dark:border-cyan-900/40 dark:bg-[#041612]/92">
                          <p className="text-xs font-semibold text-slate dark:text-slate-300">Recommended actions</p>
                          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate dark:text-white/75">
                            {selectedBrief.recommended_actions.map((action, index) => (
                              <li key={`${windowLayout.id}-brief-action-${index}`}>{action}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {selectedBrief.items.length > 0 && (
                        <div className="rounded border border-slate/20 bg-white/90 p-3 dark:border-cyan-900/40 dark:bg-[#041612]/92">
                          <p className="text-xs font-semibold text-slate dark:text-slate-300">Referenced items</p>
                          <div className="mt-2 space-y-2">
                            {selectedBrief.items.map((item) => (
                              <article key={item.id} className="rounded border border-slate/20 p-2 dark:border-cyan-900/40">
                                <div className="flex items-start justify-between gap-2">
                                  {sanitizeHref(item.url) ? (
                                    <a
                                      href={sanitizeHref(item.url) ?? undefined}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="text-sm font-semibold hover:text-cyan hover:underline dark:hover:text-cyan-200"
                                    >
                                      {item.title}
                                    </a>
                                  ) : (
                                    <span className="text-sm font-semibold">{item.title}</span>
                                  )}
                                  {item.relevance_label && (
                                    <span className={`tl-chip shrink-0 ${aiRelevanceTone(item.relevance_label)}`}>
                                      {formatAiRelevanceLabel(item.relevance_label)}
                                    </span>
                                  )}
                                </div>
                                <p className="mt-1 text-xs text-slate dark:text-white/65">
                                  {item.feed_name} • Published {formatPublishedAt(item.published_at)}
                                </p>
                              </article>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                    )
                  })()}
                </div>
              ) : (
                <div className={`flex flex-1 flex-col p-3 ${windowMeta.panelClassName}`}>
                  <label className="text-xs font-semibold text-slate dark:text-slate-300">Scratch notes</label>
                  <textarea
                    className="mt-2 h-full min-h-[180px] w-full flex-1 rounded border border-slate/20 bg-white px-3 py-2 text-sm leading-6 dark:border-cyan-900/40 dark:bg-[#072019]"
                    placeholder="Use this space for quick notes, pivots, and hypotheses..."
                    value={windowLayout.scratch_note}
                    onChange={(event) => updateWindowScratchNote(windowLayout.id, event.target.value)}
                    aria-label={`${windowLayout.title} scratch notes`}
                  />
                  <p className="mt-2 text-xs text-slate dark:text-slate-300">Saved in this panel and in saved views.</p>
                </div>
              )}

              {isEditMode && isWideLayout && windowLayout.snap === 'free' && (
                <button
                  type="button"
                  className="absolute bottom-1 right-1 h-4 w-4 cursor-se-resize rounded border border-slate/20 bg-white/85 dark:border-cyan-900/40 dark:bg-[#0b2a23]"
                  aria-label="Resize panel"
                  onMouseDown={(event) => startWindowResize(event, windowLayout.id)}
                />
              )}
            </section>
          )
        })}
      </div>

      {renamingWindowId && (
        <DialogSurface
          open
          title="Rename panel"
          description="Rename this panel without leaving the dashboard."
          eyebrow="Panel settings"
          onClose={closeRenameWindow}
          initialFocusRef={renameWindowInputRef}
          panelClassName="max-w-md"
          footer={
            <>
              <button
                type="button"
                className="rounded border border-slate/20 px-3 py-2 text-xs font-semibold dark:border-cyan-900/40"
                onClick={closeRenameWindow}
              >
                Cancel
              </button>
              <button
                type="button"
                className="rounded bg-ink px-3 py-2 text-xs font-semibold text-white dark:bg-cyan dark:text-slate-950"
                onClick={saveRenamedWindow}
              >
                Save Panel Title
              </button>
            </>
          }
        >
          <div className="space-y-3">
            <div className="space-y-1">
              <label
                htmlFor="dashboard-panel-title-input"
                className="text-xs font-semibold uppercase text-slate dark:text-white/60"
              >
                Panel title
              </label>
              <input
                id="dashboard-panel-title-input"
                ref={renameWindowInputRef}
                value={renameWindowDraft}
                onChange={(event) => setRenameWindowDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault()
                    saveRenamedWindow()
                  }
                }}
                maxLength={80}
                className="w-full rounded border border-slate/20 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
              />
            </div>
            <p className="text-xs text-slate dark:text-white/60">Up to 80 characters. Saved with this view.</p>
          </div>
        </DialogSurface>
      )}

      {showManageViewsModal && (
        <DialogSurface
          open
          title="Manage Saved Views"
          description="Load, import, export, or delete saved dashboard layouts without leaving your current workspace."
          onClose={() => setShowManageViewsModal(false)}
          panelClassName="max-h-[92vh] max-w-3xl overflow-auto"
          bodyClassName="mt-4 space-y-4 text-sm text-slate dark:text-white/75"
        >
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="rounded border border-slate/20 px-3 py-1.5 text-xs dark:border-cyan-900/40"
              onClick={exportAllViews}
            >
              Export JSON
            </button>
            <button
              type="button"
              className="rounded border border-slate/20 px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-60 dark:border-cyan-900/40"
              onClick={openImportViewsPicker}
              disabled={isImportingViews}
            >
              Import JSON
            </button>
            <input
              ref={importViewsInputRef}
              type="file"
              accept="application/json"
              className="hidden"
              tabIndex={-1}
              aria-label="Import saved dashboard views JSON"
              onChange={(event) => {
                void importViewsFile(event)
              }}
              disabled={isImportingViews}
            />
            {isImportingViews && <span className="text-xs text-slate dark:text-slate-300">Importing...</span>}
            {importViewsError && <span className="text-xs text-red-600">{importViewsError}</span>}
            {importViewsResult && <span className="text-xs text-emerald-600">{importViewsResult}</span>}
          </div>

          <div className="grid gap-2 md:grid-cols-2">
            {savedViewPreviews.map((view) => (
              <div
                key={view.id}
                className="rounded border border-slate/20 p-2 dark:border-cyan-900/40"
              >
                <div className="flex items-start gap-3">
                  <SavedViewThumbnail windows={view.windows} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-semibold">{view.name}</p>
                    <p className="text-xs text-slate dark:text-slate-300">{formatDateTime(view.created_at)}</p>
                    <div className="mt-1 flex flex-wrap gap-1.5 text-[11px]">
                      <span className="rounded border border-slate/20 px-1.5 py-0.5 dark:border-cyan-900/40">
                        RSS {view.window_type_counts.rss}
                      </span>
                      <span className="rounded border border-slate/20 px-1.5 py-0.5 dark:border-cyan-900/40">
                        Alerts {view.window_type_counts.alerts}
                      </span>
                      <span className="rounded border border-slate/20 px-1.5 py-0.5 dark:border-cyan-900/40">
                        Notes {view.window_type_counts.notes}
                      </span>
                      <span className="rounded border border-slate/20 px-1.5 py-0.5 dark:border-cyan-900/40">
                        Daily Brief {view.window_type_counts.daily_brief}
                      </span>
                    </div>
                  </div>
                </div>
                <div className="mt-2 flex items-center gap-2">
                  <button
                    type="button"
                    className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40"
                    onClick={() => requestSavedViewLoad(view.id)}
                    aria-label={`Load saved view ${view.name}`}
                  >
                    Load
                  </button>
                  <button
                    type="button"
                    className="rounded border border-slate/20 px-2 py-1 text-xs text-red-600 dark:border-cyan-900/40"
                    onClick={() => setPendingViewDelete(view)}
                    disabled={deleteView.isPending || Boolean(pendingViewDelete)}
                    aria-label={`Delete saved view ${view.name}`}
                  >
                    Delete
                  </button>
                </div>
              </div>
            ))}

            {viewsQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading saved views...</p>}
            {viewsQuery.isError && <p className="text-sm text-red-600">Failed to load saved views.</p>}
            {!viewsQuery.isLoading && !viewsQuery.data?.length && (
              <p className="text-sm text-slate dark:text-slate-300">No saved views available.</p>
            )}
          </div>
        </DialogSurface>
      )}

      <ConfirmDialog
        open={Boolean(pendingSavedViewLoad)}
        title={hasProtectedEditSession ? 'Discard the current edit session?' : 'Discard unsaved note drafts?'}
        description={
          hasProtectedEditSession
            ? 'Loading another saved view will replace the layout you are editing and clear the current cancel checkpoint.'
            : 'Loading another saved view can hide your in-progress note drafts before you save them.'
        }
        confirmLabel="Load saved view"
        onCancel={() => setPendingSavedViewLoad(null)}
        onConfirm={onConfirmPendingSavedViewLoad}
      >
        {pendingSavedViewLoad && (
          <div className="space-y-2">
            <p className="font-semibold text-ink dark:text-white">{pendingSavedViewLoad.name}</p>
            <p className="text-xs text-slate dark:text-white/70">
              {hasProtectedEditSession
                ? 'Save or cancel the current edit session first if you want to keep those unsaved layout changes.'
                : 'Save your item notes first if you want the current note drafts to remain safely persisted.'}
            </p>
          </div>
        )}
      </ConfirmDialog>

      <ConfirmDialog
        open={Boolean(pendingViewDelete)}
        title="Delete saved view?"
        description="This permanently removes the saved dashboard layout and filters."
        confirmLabel="Delete view"
        onCancel={() => setPendingViewDelete(null)}
        onConfirm={onConfirmDeleteView}
        confirmDisabled={deleteView.isPending}
        isConfirming={deleteView.isPending}
      >
        {pendingViewDelete && (
          <div className="space-y-2">
            <p className="font-semibold text-ink dark:text-white">{pendingViewDelete.name}</p>
            <p className="text-xs text-slate dark:text-white/70">
              Saved on {formatDateTime(pendingViewDelete.created_at)}
            </p>
          </div>
        )}
      </ConfirmDialog>
      {confirmDiscardUnsavedDashboardChanges.discardDialog}
    </div>
  )
}

function deriveTimeWindow(
  timeRange: TimeRangeFilter,
  customSinceDate: string,
  customUntilDate: string,
  rollingDays = DEFAULT_ROLLING_DAYS,
  relativeTimeAnchorMs = getRelativeTimeAnchorMs(),
) {
  if (timeRange === 'all') {
    return { sinceIso: '', untilIso: '' }
  }

  if (timeRange === 'days') {
    const dayCount = clamp(Number(rollingDays) || Number(DEFAULT_ROLLING_DAYS), 1, 365)
    const now = new Date(relativeTimeAnchorMs)
    const since = new Date(now)
    since.setTime(now.getTime() - dayCount * 24 * 60 * 60 * 1000)
    return { sinceIso: since.toISOString(), untilIso: now.toISOString() }
  }

  if (timeRange === 'custom') {
    const since = parseStartOfDay(customSinceDate)
    const until = parseEndOfDay(customUntilDate)

    if (since && until && since > until) {
      return { sinceIso: until.toISOString(), untilIso: since.toISOString() }
    }

    return {
      sinceIso: since ? since.toISOString() : '',
      untilIso: until ? until.toISOString() : '',
    }
  }

  const now = new Date(relativeTimeAnchorMs)
  const since = new Date(now)

  if (timeRange === '24h') {
    since.setTime(now.getTime() - 24 * 60 * 60 * 1000)
  }

  if (timeRange === '7d') {
    since.setTime(now.getTime() - 7 * 24 * 60 * 60 * 1000)
  }

  if (timeRange === '30d') {
    since.setTime(now.getTime() - 30 * 24 * 60 * 60 * 1000)
  }

  return { sinceIso: since.toISOString(), untilIso: now.toISOString() }
}

function parseStartOfDay(date: string): Date | null {
  if (!date) return null
  const parsed = new Date(`${date}T00:00:00`)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

function parseEndOfDay(date: string): Date | null {
  if (!date) return null
  const parsed = new Date(`${date}T23:59:59.999`)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

function resolveWindowTimeFilter(windowLayout: DashboardWindow, dashboardTimeFilter: WindowTimeFilter): WindowTimeFilter {
  if (windowLayout.type === 'notes' || windowLayout.type === 'daily_brief') {
    return dashboardTimeFilter
  }
  return windowLayout.time_override ?? dashboardTimeFilter
}

type ItemCachePatch = {
  isRead?: boolean
  isStarred?: boolean
  note?: string | null
}

type DashboardItemsQueryKeyParams = {
  selected_feed_ids: string
  selected_tags: string
  q: string
  read_status: ReadStatusFilter
  star_status: StarStatusFilter
  since: string | null
  until: string | null
  sort: TimeSort
  page: number
  page_size: number
}

function buildDashboardItemsQueryKey(params: DashboardItemsQueryKeyParams) {
  return ['items', params] as const
}

function resolveItemActionError(error: unknown, fallback: string) {
  if (error instanceof ApiError && error.message.trim()) {
    return error.message
  }
  if (error instanceof Error && error.message.trim()) {
    return error.message
  }
  return fallback
}

function clearItemFeedback(
  setter: Dispatch<SetStateAction<Record<string, { tone: 'success' | 'error'; message: string }>>>,
  itemId: string,
) {
  setter((current) => {
    if (!(itemId in current)) {
      return current
    }
    const next = { ...current }
    delete next[itemId]
    return next
  })
}

function syncItemStateInCache(queryClient: ReturnType<typeof useQueryClient>, itemId: string, patch: ItemCachePatch) {
  queryClient.setQueriesData<ItemListResponse>({ queryKey: ['items'] }, (current) => {
    if (!current) {
      return current
    }

    let changed = false
    const items = current.items.map((item) => {
      if (item.id !== itemId) {
        return item
      }

      changed = true
      return {
        ...item,
        is_read: patch.isRead ?? item.is_read,
        is_starred: patch.isStarred ?? item.is_starred,
      }
    })

    return changed ? { ...current, items } : current
  })

  queryClient.setQueryData<ItemDetail>(['item', itemId], (current) => {
    if (!current) {
      return current
    }

    return {
      ...current,
      state: {
        ...current.state,
        is_read: patch.isRead ?? current.state.is_read,
        is_starred: patch.isStarred ?? current.state.is_starred,
        note: patch.note !== undefined ? patch.note : current.state.note,
        updated_at: new Date().toISOString(),
      },
    }
  })

  if (patch.isRead === undefined && patch.isStarred === undefined) {
    return
  }

  queryClient.invalidateQueries({
    predicate: (query) => shouldRefreshFilteredItemList(query.queryKey, patch),
  })
}

function shouldRefreshFilteredItemList(queryKey: readonly unknown[], patch: ItemCachePatch) {
  if (queryKey[0] !== 'items') {
    return false
  }

  const params = queryKey[1]
  if (!isDashboardItemsQueryKeyParams(params)) {
    return false
  }

  return (
    (patch.isRead !== undefined && params.read_status !== 'all') ||
    (patch.isStarred !== undefined && params.star_status !== 'all')
  )
}

function isDashboardItemsQueryKeyParams(value: unknown): value is DashboardItemsQueryKeyParams {
  if (typeof value !== 'object' || value === null) {
    return false
  }

  const params = value as Record<string, unknown>
  const readStatus = params.read_status
  const starStatus = params.star_status
  const isDashboardReadStatus = readStatus === 'all' || readStatus === 'read' || readStatus === 'unread'
  const isDashboardStarStatus = starStatus === 'all' || starStatus === 'starred' || starStatus === 'unstarred'

  return (
    typeof params.selected_feed_ids === 'string' &&
    typeof params.selected_tags === 'string' &&
    typeof params.q === 'string' &&
    isDashboardReadStatus &&
    isDashboardStarStatus &&
    (params.since === null || typeof params.since === 'string') &&
    (params.until === null || typeof params.until === 'string') &&
    typeof params.sort === 'string' &&
    typeof params.page === 'number' &&
    typeof params.page_size === 'number'
  )
}

function formatPublishedAt(value: string | null) {
  return formatDateTime(value)
}

function formatDailyBriefOptionLabel(brief: AIDailyBrief) {
  return `${formatDateOnly(brief.brief_date)} · ${brief.item_count} items`
}

function formatClassificationLabel(value: string): string {
  return value
    .split('_')
    .map((part) => (part ? part[0].toUpperCase() + part.slice(1) : part))
    .join(' ')
}

function formatAiRelevanceLabel(value: 'low' | 'medium' | 'high'): string {
  if (value === 'high') return 'High'
  if (value === 'medium') return 'Medium'
  return 'Low'
}

function aiRelevanceTone(value: 'low' | 'medium' | 'high'): string {
  if (value === 'high') {
    return 'tl-chip-warning'
  }
  if (value === 'medium') {
    return 'tl-chip-info'
  }
  return 'tl-chip-neutral'
}

function formatItemStatusLabel(value: string): string {
  const normalized = value.trim().toLowerCase()
  if (normalized === 'error') {
    return 'content error'
  }
  if (normalized === 'new') {
    return 'new item'
  }
  return normalized.replace(/_/g, ' ')
}

function itemStatusTone(value: string): string {
  const normalized = value.trim().toLowerCase()
  if (normalized.includes('error') || normalized.includes('failed')) {
    return 'tl-chip-warning'
  }
  return 'tl-chip-neutral'
}

function SavedViewThumbnail({ windows }: { windows: DashboardWindow[] }) {
  const previewContainerWidth = 1120
  const previewContainerHeight = 680

  return (
    <div
      className="relative shrink-0 overflow-hidden rounded border border-slate/20 bg-white/90 dark:border-cyan-900/40 dark:bg-[#041612]"
      style={{ width: SAVED_VIEW_THUMBNAIL_WIDTH, height: SAVED_VIEW_THUMBNAIL_HEIGHT }}
    >
      {windows.slice(0, 14).map((windowLayout) => {
        const rect = resolveWindowRect(windowLayout, previewContainerWidth, previewContainerHeight)
        const left = Math.max(0, (rect.x / previewContainerWidth) * SAVED_VIEW_THUMBNAIL_WIDTH)
        const top = Math.max(0, (rect.y / previewContainerHeight) * SAVED_VIEW_THUMBNAIL_HEIGHT)
        const width = Math.max(6, (rect.width / previewContainerWidth) * SAVED_VIEW_THUMBNAIL_WIDTH)
        const height = Math.max(6, (rect.height / previewContainerHeight) * SAVED_VIEW_THUMBNAIL_HEIGHT)

        return (
          <div
            key={windowLayout.id}
            className={`absolute overflow-hidden rounded-[3px] border ${thumbnailWindowTone(windowLayout.type)}`}
            style={{ left, top, width, height }}
            title={windowLayout.title}
          />
        )
      })}
      {windows.length > 14 && (
        <div className="absolute bottom-1 right-1 rounded border border-slate/40 bg-white/85 px-1 text-[10px] font-semibold text-slate-700 dark:border-cyan-900/40 dark:bg-[#041612]/90 dark:text-slate-200">
          +{windows.length - 14}
        </div>
      )}
    </div>
  )
}

function thumbnailWindowTone(type: DashboardWindowType): string {
  if (type === 'rss') return 'border-cyan-500/40 bg-cyan-400/30 dark:bg-cyan-500/35'
  if (type === 'alerts') return 'border-amber-500/40 bg-amber-300/35 dark:bg-amber-500/35'
  if (type === 'daily_brief') return 'border-slate-400/45 bg-slate-200/70 dark:border-cyan-900/40 dark:bg-cyan-500/20'
  return 'border-slate-400/40 bg-slate-300/45 dark:border-slate-600/45 dark:bg-slate-500/30'
}

function formatRollingWindowHint(rollingDays: string) {
  const dayCount = clamp(Number(rollingDays) || Number(DEFAULT_ROLLING_DAYS), 1, 365)
  const now = new Date()
  const since = new Date(now)
  since.setTime(now.getTime() - dayCount * 24 * 60 * 60 * 1000)
  return `Since ${formatDateOnly(since)}`
}

function formatDashboardTimeRangeSummary(timeRange: TimeRangeFilter, customSinceDate: string, customUntilDate: string, rollingDays = DEFAULT_ROLLING_DAYS) {
  if (timeRange === 'all') return 'All time'
  if (timeRange === '24h') return 'Last 24h'
  if (timeRange === '7d') return 'Last 7 days'
  if (timeRange === '30d') return 'Last 30 days'
  if (timeRange === 'days') return `Last ${clamp(Number(rollingDays) || Number(DEFAULT_ROLLING_DAYS), 1, 365)} days`
  if (customSinceDate && customUntilDate) return `Custom ${formatDateOnly(customSinceDate)} to ${formatDateOnly(customUntilDate)}`
  if (customSinceDate) return `Custom from ${formatDateOnly(customSinceDate)}`
  if (customUntilDate) return `Custom until ${formatDateOnly(customUntilDate)}`
  return 'Custom window'
}

function countActiveWindowFilters(
  windowLayout: DashboardWindow,
  rssFilters: DashboardRssWindowFilters,
  alertFilters: DashboardAlertWindowFilters,
) {
  if (windowLayout.type === 'rss') {
    return (
      rssFilters.selected_feed_ids.length +
      rssFilters.selected_tags.length +
      (rssFilters.q.trim() ? 1 : 0) +
      (rssFilters.read_status !== 'all' ? 1 : 0) +
      (rssFilters.star_status !== 'all' ? 1 : 0)
    )
  }

  if (windowLayout.type === 'alerts') {
    return alertFilters.selected_alert_ids.length + alertFilters.selected_categories.length + (alertFilters.q.trim() ? 1 : 0)
  }

  return 0
}

function formatWindowSnapLabel(snap: DashboardWindowSnap) {
  return WINDOW_SNAP_OPTIONS.find((option) => option.value === snap)?.label ?? 'Placement'
}

function formatWindowTimeSummary(windowLayout: DashboardWindow, dashboardTimeFilter: WindowTimeFilter) {
  if (windowLayout.type === 'notes') {
    return 'Saved with view'
  }

  if (windowLayout.type === 'daily_brief') {
    return 'Brief snapshots'
  }

  if (!windowLayout.time_override) {
    return `Scope: ${formatDashboardTimeRangeSummary(
      dashboardTimeFilter.time_range,
      dashboardTimeFilter.custom_since_date,
      dashboardTimeFilter.custom_until_date,
      dashboardTimeFilter.rolling_days,
    )}`
  }

  return `Scope: ${formatDashboardTimeRangeSummary(
    windowLayout.time_override.time_range,
    windowLayout.time_override.custom_since_date,
    windowLayout.time_override.custom_until_date,
    windowLayout.time_override.rolling_days,
  )}`
}

function loadWindowSeenState(storageKey: string): Record<string, string> {
  return loadStoredTimestampMap(storageKey)
}

function loadStoredTimestampMap(storageKey: string): Record<string, string> {
  if (typeof window === 'undefined') {
    return {}
  }

  const raw = window.localStorage.getItem(storageKey)
  if (!raw) {
    return {}
  }

  try {
    const parsed = JSON.parse(raw) as unknown
    if (!isRecord(parsed)) return {}
    const next: Record<string, string> = {}
    for (const [key, value] of Object.entries(parsed)) {
      if (typeof value === 'string' && value.trim()) {
        next[key] = value
      }
    }
    return next
  } catch {
    return {}
  }
}

function loadStoredTimestamp(storageKey: string): string {
  if (typeof window === 'undefined') {
    return ''
  }

  try {
    const value = window.localStorage.getItem(storageKey)
    return typeof value === 'string' ? value : ''
  } catch {
    return ''
  }
}

function countNewEntriesSince<T extends { first_seen_at: string }>(entries: T[], lastSeenAtIso: string): number {
  if (!lastSeenAtIso) {
    return 0
  }

  const marker = Date.parse(lastSeenAtIso)
  if (Number.isNaN(marker)) {
    return 0
  }

  let count = 0
  for (const entry of entries) {
    const candidate = Date.parse(entry.first_seen_at)
    if (!Number.isNaN(candidate) && candidate > marker) {
      count += 1
    }
  }
  return count
}

function renderRichContent(content: string, itemId: string, section: 'summary' | 'article'): ReactNode {
  const trimmed = content.trim()
  if (!trimmed) {
    return <p>No content.</p>
  }

  if (!looksLikeHtml(trimmed)) {
    return renderArticleBlocks(trimmed, `${itemId}-${section}`)
  }

  const sanitized = sanitizeHtmlFragment(trimmed)
  if (!sanitized) {
    return renderArticleBlocks(stripHtml(trimmed), `${itemId}-${section}`)
  }

  return <div className="rss-rich" dangerouslySetInnerHTML={{ __html: sanitized }} />
}

function renderArticleBlocks(text: string, itemId: string) {
  const blocks = parseArticleBlocks(text)

  return blocks.map((block, index) => {
    if (block.kind === 'heading') {
      return (
        <h4 key={`${itemId}-heading-${index}`} className="rss-heading">
          {block.text}
        </h4>
      )
    }

    if (block.kind === 'bullet-list') {
      return (
        <ul key={`${itemId}-ul-${index}`} className="rss-list">
          {block.items.map((entry, entryIndex) => (
            <li key={`${itemId}-ul-${index}-${entryIndex}`}>{entry}</li>
          ))}
        </ul>
      )
    }

    if (block.kind === 'numbered-list') {
      return (
        <ol key={`${itemId}-ol-${index}`} className="rss-list rss-list-ordered">
          {block.items.map((entry, entryIndex) => (
            <li key={`${itemId}-ol-${index}-${entryIndex}`}>{entry}</li>
          ))}
        </ol>
      )
    }

    if (block.kind === 'quote') {
      return (
        <blockquote key={`${itemId}-quote-${index}`} className="rss-quote">
          {block.text}
        </blockquote>
      )
    }

    return <p key={`${itemId}-paragraph-${index}`}>{block.text}</p>
  })
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function applyDragMagnetSnap(
  movingRect: PanelRect,
  otherRects: PanelRect[],
  containerWidth: number,
  containerHeight: number,
  maxX: number,
  maxY: number,
): Pick<PanelRect, 'x' | 'y'> {
  let snappedX = movingRect.x
  let snappedY = movingRect.y
  let bestXDistance = Number.POSITIVE_INFINITY
  let bestYDistance = Number.POSITIVE_INFINITY

  const maybeSnapX = (candidate: number, threshold: number) => {
    const normalized = clamp(candidate, 0, maxX)
    const distance = Math.abs(movingRect.x - normalized)
    if (distance <= threshold && distance < bestXDistance) {
      snappedX = normalized
      bestXDistance = distance
    }
  }

  const maybeSnapY = (candidate: number, threshold: number) => {
    const normalized = clamp(candidate, 0, maxY)
    const distance = Math.abs(movingRect.y - normalized)
    if (distance <= threshold && distance < bestYDistance) {
      snappedY = normalized
      bestYDistance = distance
    }
  }

  for (const rect of otherRects) {
    const left = rect.x
    const right = rect.x + rect.width
    const top = rect.y
    const bottom = rect.y + rect.height

    maybeSnapX(left, DRAG_EDGE_SNAP_THRESHOLD)
    maybeSnapX(right, DRAG_EDGE_SNAP_THRESHOLD)
    maybeSnapX(left - movingRect.width, DRAG_EDGE_SNAP_THRESHOLD)
    maybeSnapX(right - movingRect.width, DRAG_EDGE_SNAP_THRESHOLD)

    maybeSnapY(top, DRAG_EDGE_SNAP_THRESHOLD)
    maybeSnapY(bottom, DRAG_EDGE_SNAP_THRESHOLD)
    maybeSnapY(top - movingRect.height, DRAG_EDGE_SNAP_THRESHOLD)
    maybeSnapY(bottom - movingRect.height, DRAG_EDGE_SNAP_THRESHOLD)
  }

  const midX = containerWidth / 2
  const midY = containerHeight / 2

  maybeSnapX(midX, DRAG_MIDLINE_SNAP_THRESHOLD)
  maybeSnapX(midX - movingRect.width, DRAG_MIDLINE_SNAP_THRESHOLD)
  maybeSnapY(midY, DRAG_MIDLINE_SNAP_THRESHOLD)
  maybeSnapY(midY - movingRect.height, DRAG_MIDLINE_SNAP_THRESHOLD)

  return { x: snappedX, y: snappedY }
}

function getWindowContainerDimensions(rootElement: HTMLDivElement | null): { width: number; height: number } {
  if (typeof window === 'undefined') {
    return { width: 1380, height: 760 }
  }

  const rootBounds = rootElement?.getBoundingClientRect()
  const width = Math.max(WINDOW_MIN_WIDTH, Math.floor(rootBounds?.width ?? window.innerWidth))
  const height = Math.max(WINDOW_MIN_HEIGHT, Math.floor(rootBounds?.height ?? window.innerHeight - 140))
  return { width, height }
}

function clamp(value: number, min: number, max: number) {
  if (value < min) return min
  if (value > max) return max
  return value
}
