import { ChangeEvent, ReactNode, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { feedHealthDotClass, resolveFeedHealth } from '../utils/feedHealth'
import { formatDateOnly, formatDateTime } from '../utils/datetime'
import { summarizeGlobalSearchAcrossWindows } from './dashboardState'
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

type TimeRangeFilter = 'all' | '24h' | '7d' | '30d' | 'days' | 'custom'
type ReadStatusFilter = 'all' | 'read' | 'unread'
type StarStatusFilter = 'all' | 'starred' | 'unstarred'
type TimeSort = 'published_at_desc' | 'published_at_asc' | 'first_seen_desc' | 'first_seen_asc'
type DashboardViewMode = 'expanded' | 'compact'
type AlertViewMode = 'expanded' | 'compact'
type DashboardWindowType = 'rss' | 'alerts' | 'notes' | 'daily_brief'
type DashboardWindowSnap = 'free' | 'full' | 'left' | 'right' | 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right'
type WindowTimeFilter = {
  time_range: TimeRangeFilter
  custom_since_date: string
  custom_until_date: string
  rolling_days: string
}

type PanelRect = {
  x: number
  y: number
  width: number
  height: number
}

interface DashboardWindow {
  id: string
  type: DashboardWindowType
  title: string
  snap: DashboardWindowSnap
  rect: PanelRect
  controls_collapsed: boolean
  scratch_note: string
  time_override: WindowTimeFilter | null
  rss_filters: DashboardRssWindowFilters | null
  alert_filters: DashboardAlertWindowFilters | null
  selected_daily_brief_id: string | null
}

interface DashboardRssWindowFilters {
  selected_feed_ids: string[]
  selected_tags: string[]
  q: string
  read_status: ReadStatusFilter
  star_status: StarStatusFilter
  view_mode: DashboardViewMode
  page: number
  page_size: number
  sort: TimeSort
  show_advanced_filters: boolean
}

interface DashboardAlertWindowFilters {
  selected_alert_ids: string[]
  selected_categories: string[]
  q: string
  view_mode: AlertViewMode
  page: number
  page_size: number
  sort: TimeSort
}

interface DashboardSavedViewQuery {
  selected_feed_ids: string[]
  selected_tags: string[]
  q: string
  read_status: ReadStatusFilter
  star_status: StarStatusFilter
  view_mode: DashboardViewMode
  page_size: number
  time_range: TimeRangeFilter
  custom_since_date: string
  custom_until_date: string
  rolling_days: string
  sort: TimeSort
}

interface DashboardAlertViewQuery {
  selected_alert_ids: string[]
  selected_categories: string[]
  q: string
  view_mode: AlertViewMode
  page_size: number
  time_range: TimeRangeFilter
  custom_since_date: string
  custom_until_date: string
  rolling_days: string
  sort: TimeSort
}

interface DashboardSavedViewState {
  version: number
  rss_filters: DashboardSavedViewQuery
  alert_filters: DashboardAlertViewQuery
  windows: DashboardWindow[]
  ui: {
    show_advanced_filters: boolean
  }
}

interface ImportedSavedViewEntry {
  name: string
  query_json: Record<string, unknown>
}

interface SavedViewPreview {
  id: string
  name: string
  created_at: string
  windows: DashboardWindow[]
  window_type_counts: {
    rss: number
    alerts: number
    notes: number
    daily_brief: number
  }
}

const WINDOW_STORAGE_KEY = 'threatlens.dashboard.windows.v2'
const WINDOW_SEEN_STORAGE_KEY = 'threatlens.dashboard.window-seen.v1'
const USER_LAST_OPEN_STORAGE_KEY = 'threatlens.user-last-open.v1'
const DASHBOARD_VIEW_VERSION = 6
const WINDOW_MIN_WIDTH = 460
const WINDOW_MIN_HEIGHT = 320
const DRAG_EDGE_SNAP_THRESHOLD = 12
const DRAG_MIDLINE_SNAP_THRESHOLD = 8
const DASHBOARD_TIME_INHERIT_VALUE = '__dashboard_time__'
const DEFAULT_ROLLING_DAYS = '7'
const HIDDEN_TAGS = new Set(['content_fetched', 'priority'])
const PAGE_SIZE_OPTIONS = [10, 25, 50, 100]
const MAX_VIEWS_IMPORT_FILE_BYTES = 2_000_000
const MAX_IMPORTED_VIEWS = 250
const SAVED_VIEW_THUMBNAIL_WIDTH = 148
const SAVED_VIEW_THUMBNAIL_HEIGHT = 96

export function getDashboardStorageKeys(userId: string) {
  return {
    windows: `${WINDOW_STORAGE_KEY}:${userId}`,
    windowSeenAt: `${WINDOW_SEEN_STORAGE_KEY}:${userId}`,
    lastOpenedAt: `${USER_LAST_OPEN_STORAGE_KEY}:${userId}`,
  } as const
}

export function migrateLegacyDashboardStorage(userId: string) {
  if (typeof window === 'undefined' || !userId) {
    return false
  }

  const storageKeys = getDashboardStorageKeys(userId)
  const migrations: Array<{ legacyKey: string; scopedKey: string }> = [
    { legacyKey: WINDOW_STORAGE_KEY, scopedKey: storageKeys.windows },
    { legacyKey: WINDOW_SEEN_STORAGE_KEY, scopedKey: storageKeys.windowSeenAt },
    { legacyKey: USER_LAST_OPEN_STORAGE_KEY, scopedKey: storageKeys.lastOpenedAt },
  ]

  let migrated = false
  for (const { legacyKey, scopedKey } of migrations) {
    if (window.localStorage.getItem(scopedKey) !== null) {
      continue
    }

    const legacyValue = window.localStorage.getItem(legacyKey)
    if (legacyValue === null) {
      continue
    }

    window.localStorage.setItem(scopedKey, legacyValue)
    migrated = true
  }

  return migrated
}

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
    badgeClassName:
      'border-cyan/40 bg-cyan/15 text-cyan dark:border-cyan-800/50 dark:bg-cyan-950/55 dark:text-cyan-200',
    headerClassName: 'bg-white/92 dark:bg-[#041612]/90',
    shellClassName: 'border-slate/20 bg-white/95 dark:border-cyan-900/45 dark:bg-[#041612]/96',
    panelClassName: 'bg-white/90 dark:bg-[#03130f]/84',
  },
  alerts: {
    label: 'Alert Matches',
    description: 'Watch keyword-driven matches across your configured interests.',
    badgeClassName:
      'border-amber-300/55 bg-amber-50/90 text-amber-800 dark:border-amber-800/45 dark:bg-amber-950/25 dark:text-amber-200',
    headerClassName: 'bg-white/92 dark:bg-[#041612]/90',
    shellClassName: 'border-slate/20 bg-white/95 dark:border-cyan-900/45 dark:bg-[#041612]/96',
    panelClassName: 'bg-white/90 dark:bg-[#03130f]/84',
  },
  notes: {
    label: 'Notes',
    description: 'Keep scratch notes, pivots, and hypotheses attached to this view.',
    badgeClassName:
      'border-slate/20 bg-slate/10 text-slate-700 dark:border-slate-600/45 dark:bg-slate-800/40 dark:text-slate-200',
    headerClassName: 'bg-white/92 dark:bg-[#041612]/90',
    shellClassName: 'border-slate/20 bg-white/95 dark:border-cyan-900/45 dark:bg-[#041612]/96',
    panelClassName: 'bg-white/90 dark:bg-[#03130f]/84',
  },
  daily_brief: {
    label: 'Daily Brief',
    description: 'Review retained AI briefings and the items that shaped them.',
    badgeClassName:
      'border-slate/20 bg-white/80 text-slate-700 dark:border-cyan-900/40 dark:bg-[#041612] dark:text-white/70',
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

function createDefaultRssWindowFilters(showAdvancedFilters = false): DashboardRssWindowFilters {
  return {
    selected_feed_ids: [],
    selected_tags: [],
    q: '',
    read_status: 'all',
    star_status: 'all',
    view_mode: 'compact',
    page: 1,
    page_size: 25,
    sort: 'published_at_desc',
    show_advanced_filters: showAdvancedFilters,
  }
}

function createDefaultAlertWindowFilters(): DashboardAlertWindowFilters {
  return {
    selected_alert_ids: [],
    selected_categories: [],
    q: '',
    view_mode: 'expanded',
    page: 1,
    page_size: 25,
    sort: 'published_at_desc',
  }
}

function parseRssWindowFiltersCandidate(
  raw: unknown,
  fallback?: Partial<DashboardRssWindowFilters>,
  showAdvancedFallback = false,
): DashboardRssWindowFilters {
  const source = isRecord(raw) ? raw : {}
  const base = {
    ...createDefaultRssWindowFilters(showAdvancedFallback),
    ...fallback,
  }

  const selectedFeedIds = Array.isArray(source.selected_feed_ids)
    ? source.selected_feed_ids.filter((entry): entry is string => typeof entry === 'string')
    : (base.selected_feed_ids ?? [])
  const selectedTags = Array.isArray(source.selected_tags)
    ? source.selected_tags.filter((entry): entry is string => typeof entry === 'string' && !HIDDEN_TAGS.has(entry))
    : (base.selected_tags ?? [])

  return {
    selected_feed_ids: [...selectedFeedIds],
    selected_tags: [...selectedTags],
    q: typeof source.q === 'string' ? source.q : (base.q ?? ''),
    read_status:
      source.read_status === 'read' || source.read_status === 'unread'
        ? source.read_status
        : (base.read_status ?? 'all'),
    star_status:
      source.star_status === 'starred' || source.star_status === 'unstarred'
        ? source.star_status
        : (base.star_status ?? 'all'),
    view_mode:
      source.view_mode === 'expanded' || source.view_mode === 'compact'
        ? source.view_mode
        : (base.view_mode ?? 'compact'),
    page: typeof source.page === 'number' && source.page >= 1 ? Math.floor(source.page) : (base.page ?? 1),
    page_size:
      typeof source.page_size === 'number' && PAGE_SIZE_OPTIONS.includes(source.page_size)
        ? source.page_size
        : (base.page_size ?? 25),
    sort:
      typeof source.sort === 'string' && isTimeSort(source.sort)
        ? source.sort
        : (base.sort ?? 'published_at_desc'),
    show_advanced_filters:
      typeof source.show_advanced_filters === 'boolean'
        ? source.show_advanced_filters
        : (base.show_advanced_filters ?? showAdvancedFallback),
  }
}

function parseAlertWindowFiltersCandidate(
  raw: unknown,
  fallback?: Partial<DashboardAlertWindowFilters>,
): DashboardAlertWindowFilters {
  const source = isRecord(raw) ? raw : {}
  const base = {
    ...createDefaultAlertWindowFilters(),
    ...fallback,
  }

  const selectedAlertIds = Array.isArray(source.selected_alert_ids)
    ? source.selected_alert_ids.filter((entry): entry is string => typeof entry === 'string')
    : (base.selected_alert_ids ?? [])
  const selectedCategories = Array.isArray(source.selected_categories)
    ? source.selected_categories.filter((entry): entry is string => typeof entry === 'string')
    : (base.selected_categories ?? [])

  return {
    selected_alert_ids: [...selectedAlertIds],
    selected_categories: [...selectedCategories],
    q: typeof source.q === 'string' ? source.q : (base.q ?? ''),
    view_mode:
      source.view_mode === 'compact' || source.view_mode === 'expanded'
        ? source.view_mode
        : (base.view_mode ?? 'expanded'),
    page: typeof source.page === 'number' && source.page >= 1 ? Math.floor(source.page) : (base.page ?? 1),
    page_size:
      typeof source.page_size === 'number' && PAGE_SIZE_OPTIONS.includes(source.page_size)
        ? source.page_size
        : (base.page_size ?? 25),
    sort:
      typeof source.sort === 'string' && isTimeSort(source.sort)
        ? source.sort
        : (base.sort ?? 'published_at_desc'),
  }
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

export function DashboardPage() {
  const queryClient = useQueryClient()
  const meQuery = useCurrentUser()
  const rootRef = useRef<HTMLDivElement | null>(null)
  const aiFeatures = meQuery.data?.features

  const [dashboardTimeRange, setDashboardTimeRange] = useState<TimeRangeFilter>('all')
  const [dashboardCustomSinceDate, setDashboardCustomSinceDate] = useState('')
  const [dashboardCustomUntilDate, setDashboardCustomUntilDate] = useState('')
  const [dashboardRollingDays, setDashboardRollingDays] = useState(DEFAULT_ROLLING_DAYS)

  const [savedViewName, setSavedViewName] = useState('')
  const [activeSavedViewId, setActiveSavedViewId] = useState<string | null>(null)
  const [pendingViewDelete, setPendingViewDelete] = useState<SavedViewPreview | null>(null)
  const [showManageViewsModal, setShowManageViewsModal] = useState(false)
  const [isImportingViews, setIsImportingViews] = useState(false)
  const [importViewsError, setImportViewsError] = useState('')
  const [importViewsResult, setImportViewsResult] = useState('')
  const [mobileDashboardViewsOpen, setMobileDashboardViewsOpen] = useState(false)
  const [isEditMode, setIsEditMode] = useState(false)
  const [viewSaveError, setViewSaveError] = useState('')

  const [showAddWindowMenu, setShowAddWindowMenu] = useState(false)
  const [showSaveAsNew, setShowSaveAsNew] = useState(false)
  const [, setOpenWindowMenuId] = useState<string | null>(null)
  const [renamingWindowId, setRenamingWindowId] = useState<string | null>(null)
  const [renameWindowDraft, setRenameWindowDraft] = useState('')
  const [relativeTimeAnchorMs, setRelativeTimeAnchorMs] = useState(() => getRelativeTimeAnchorMs())

  const [expandedItemIdsByWindowId, setExpandedItemIdsByWindowId] = useState<Record<string, string>>({})
  const [noteDraftsByItemId, setNoteDraftsByItemId] = useState<Record<string, string>>({})

  const [windows, setWindows] = useState<DashboardWindow[]>(() => [createWindowLayout('rss', 1, 1380, 760, 'full')])
  const [windowSeenAt, setWindowSeenAt] = useState<Record<string, string>>({})
  const [rssLastOpenedAt, setRssLastOpenedAt] = useState('')
  const [isWideLayout, setIsWideLayout] = useState<boolean>(typeof window !== 'undefined' ? window.innerWidth >= 1024 : true)
  const initializedDashboardUserRef = useRef<string | null>(null)
  const windowPersistenceTimeoutRef = useRef<number | null>(null)
  const pendingWindowPersistenceRef = useRef<{ userId: string; serialized: string } | null>(null)
  const persistedWindowUserIdRef = useRef<string | null>(null)

  const canManage = meQuery.data?.role === 'admin' || meQuery.data?.role === 'analyst'
  const aiSummaryEnabled = Boolean(aiFeatures?.ai_summary_enabled)
  const aiRelevanceEnabled = Boolean(aiFeatures?.ai_relevance_enabled)
  const aiDailyBriefEnabled = Boolean(aiFeatures?.ai_daily_brief_enabled)

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
    mutationFn: (viewId: string) =>
      apiFetch(`/views/${viewId}`, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      setActiveSavedViewId(null)
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
    mutationFn: (payload: { itemId: string; isRead: boolean }) =>
      apiFetch(`/items/${payload.itemId}/read`, {
        method: 'POST',
        body: JSON.stringify({ is_read: payload.isRead }),
      }),
    onSuccess: (_data, variables) =>
      syncItemStateInCache(queryClient, variables.itemId, {
        isRead: variables.isRead,
      }),
  })

  const updateStar = useMutation({
    mutationFn: (payload: { itemId: string; isStarred: boolean }) =>
      apiFetch(`/items/${payload.itemId}/star`, {
        method: 'POST',
        body: JSON.stringify({ is_starred: payload.isStarred }),
      }),
    onSuccess: (_data, variables) =>
      syncItemStateInCache(queryClient, variables.itemId, {
        isStarred: variables.isStarred,
      }),
  })

  const updateNote = useMutation({
    mutationFn: (payload: { itemId: string; note: string | null }) =>
      apiFetch(`/items/${payload.itemId}/note`, {
        method: 'POST',
        body: JSON.stringify({ note: payload.note }),
      }),
    onSuccess: (_data, variables) => {
      setNoteDraftsByItemId((current) => ({
        ...current,
        [variables.itemId]: variables.note ?? '',
      }))
      syncItemStateInCache(queryClient, variables.itemId, {
        note: variables.note,
      })
    },
  })
  const viewSavePending = saveView.isPending || updateExistingView.isPending

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
        queryKey: [
          'items',
          selectedFeedIdsParam,
          selectedTagsParam,
          deferredSearchQuery,
          rssFilters.read_status,
          rssFilters.star_status,
          timeWindow.sinceIso,
          timeWindow.untilIso,
          rssFilters.sort,
          rssFilters.page,
          rssFilters.page_size,
        ],
        retry: 1,
        staleTime: 60_000,
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

      for (const query of detailQueries) {
        const detail = query.data
        if (!detail) {
          continue
        }
        if (next[detail.id] !== undefined) {
          continue
        }
        next[detail.id] = detail.state.note ?? ''
        changed = true
      }

      return changed ? next : current
    })
  }, [detailQueries])

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

  const handleToggleItem = (windowId: string, itemId: string, isRead: boolean) => {
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
    if (!isRead && canManage) {
      updateRead.mutate({ itemId, isRead: true })
    }
  }

  const setWindowSnap = (windowId: string, snap: DashboardWindowSnap) => {
    if (!isWideLayout) return
    const { width, height } = getWindowContainerDimensions(rootRef.current)

    setActiveSavedViewId(null)
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
    setActiveSavedViewId(null)
    setWindows((current) => {
      const nextIndex = current.filter((window) => window.type === type).length + 1
      return [...current, createWindowLayout(type, nextIndex, width, height)]
    })
    setShowAddWindowMenu(false)
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

  const saveRenamedWindow = () => {
    if (!renamingWindowId) {
      return
    }

    const normalized = renameWindowDraft.trim().slice(0, 80)
    if (!normalized) {
      return
    }

    setActiveSavedViewId(null)
    setWindows((current) =>
      current.map((window) => (window.id === renamingWindowId ? { ...window, title: normalized } : window)),
    )
    setRenamingWindowId(null)
    setRenameWindowDraft('')
  }

  const toggleWindowControls = (windowId: string) => {
    setActiveSavedViewId(null)
    setWindows((current) =>
      current.map((window) =>
        window.id === windowId ? { ...window, controls_collapsed: !window.controls_collapsed } : window,
      ),
    )
  }

  const updateWindowScratchNote = (windowId: string, scratchNote: string) => {
    setActiveSavedViewId(null)
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
      setActiveSavedViewId(null)

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
      setActiveSavedViewId(null)

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

  const saveCurrentView = () => {
    const name = savedViewName.trim()
    if (!name) return

    saveView.mutate({
      name,
      query: buildDashboardSavedViewState(windows, {
        time_range: dashboardTimeRange,
        custom_since_date: dashboardCustomSinceDate,
        custom_until_date: dashboardCustomUntilDate,
        rolling_days: dashboardRollingDays,
      }),
    })
  }

  const updateActiveView = () => {
    if (!activeSavedViewId) return

    updateExistingView.mutate({
      viewId: activeSavedViewId,
      query: buildDashboardSavedViewState(windows, {
        time_range: dashboardTimeRange,
        custom_since_date: dashboardCustomSinceDate,
        custom_until_date: dashboardCustomUntilDate,
        rolling_days: dashboardRollingDays,
      }),
    })
  }

  const applySavedView = (view: SavedView) => {
    const { width, height } = getWindowContainerDimensions(rootRef.current)
    const parsed = parseDashboardSavedView(view.query_json, width, height)
    const nextDashboardTimeRange =
      parsed.rss_filters.time_range !== 'all' ||
      parsed.rss_filters.custom_since_date ||
      parsed.rss_filters.custom_until_date ||
      parsed.rss_filters.rolling_days !== DEFAULT_ROLLING_DAYS
        ? parsed.rss_filters.time_range
        : parsed.alert_filters.time_range
    const nextDashboardCustomSinceDate =
      parsed.rss_filters.custom_since_date || parsed.alert_filters.custom_since_date || ''
    const nextDashboardCustomUntilDate =
      parsed.rss_filters.custom_until_date || parsed.alert_filters.custom_until_date || ''
    const nextDashboardRollingDays =
      parsed.rss_filters.rolling_days !== DEFAULT_ROLLING_DAYS
        ? parsed.rss_filters.rolling_days
        : parsed.alert_filters.rolling_days || DEFAULT_ROLLING_DAYS

    setDashboardTimeRange(nextDashboardTimeRange)
    setDashboardCustomSinceDate(nextDashboardCustomSinceDate)
    setDashboardCustomUntilDate(nextDashboardCustomUntilDate)
    setDashboardRollingDays(nextDashboardRollingDays)
    setExpandedItemIdsByWindowId({})
    setWindows(parsed.windows)
    setActiveSavedViewId(view.id)
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

      let created = 0
      for (const entry of entries) {
        await apiFetch('/views', {
          method: 'POST',
          body: JSON.stringify(entry),
        })
        created += 1
      }

      setImportViewsResult(`Imported ${created} saved view${created === 1 ? '' : 's'}.`)
      await queryClient.invalidateQueries({ queryKey: ['views'] })
    } catch (error) {
      setImportViewsError((error as Error).message || 'Failed to import saved views')
    } finally {
      setIsImportingViews(false)
      event.target.value = ''
    }
  }

  const updateWindowRssFilters = (
    windowId: string,
    updater: (current: DashboardRssWindowFilters) => DashboardRssWindowFilters,
    resetPage = true,
  ) => {
    setActiveSavedViewId(null)
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
    setActiveSavedViewId(null)
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
    setActiveSavedViewId(null)
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
    setActiveSavedViewId(null)
    resetAllWindowPages()
    setDashboardTimeRange(nextRange)
  }

  const updateDashboardCustomSinceDate = (nextDate: string) => {
    setActiveSavedViewId(null)
    resetAllWindowPages()
    setDashboardCustomSinceDate(nextDate)
  }

  const updateDashboardCustomUntilDate = (nextDate: string) => {
    setActiveSavedViewId(null)
    resetAllWindowPages()
    setDashboardCustomUntilDate(nextDate)
  }

  const updateDashboardRollingDaysValue = (nextValue: string) => {
    setActiveSavedViewId(null)
    resetAllWindowPages()
    setDashboardTimeRange('days')
    setDashboardRollingDays(normalizeRollingDaysInput(nextValue))
  }

  const updateWindowTimeRange = (windowId: string, nextValue: string) => {
    setActiveSavedViewId(null)
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
        return {
          ...window,
          ...(window.type === 'rss'
            ? {
                rss_filters: {
                  ...(window.rss_filters ?? createDefaultRssWindowFilters()),
                  page: 1,
                },
              }
            : {
                alert_filters: {
                  ...(window.alert_filters ?? createDefaultAlertWindowFilters()),
                  page: 1,
                },
              }),
          time_override: {
            ...base,
            time_range: nextValue,
          },
        }
      }),
    )
  }

  const updateWindowCustomTimeDate = (windowId: string, key: 'custom_since_date' | 'custom_until_date', value: string) => {
    setActiveSavedViewId(null)
    setWindows((current) =>
      current.map((window) => {
        if (window.id !== windowId || window.type === 'notes' || window.type === 'daily_brief') {
          return window
        }

        const base = window.time_override ?? dashboardTimeFilter
        return {
          ...window,
          ...(window.type === 'rss'
            ? {
                rss_filters: {
                  ...(window.rss_filters ?? createDefaultRssWindowFilters()),
                  page: 1,
                },
              }
            : {
                alert_filters: {
                  ...(window.alert_filters ?? createDefaultAlertWindowFilters()),
                  page: 1,
                },
              }),
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
    setActiveSavedViewId(null)
    setWindows((current) =>
      current.map((window) => {
        if (window.id !== windowId || window.type === 'notes' || window.type === 'daily_brief') {
          return window
        }

        const base = window.time_override ?? dashboardTimeFilter
        return {
          ...window,
          ...(window.type === 'rss'
            ? {
                rss_filters: {
                  ...(window.rss_filters ?? createDefaultRssWindowFilters()),
                  page: 1,
                },
              }
            : {
                alert_filters: {
                  ...(window.alert_filters ?? createDefaultAlertWindowFilters()),
                  page: 1,
                },
              }),
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
    setActiveSavedViewId(null)
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
          <p className="text-xs font-medium text-slate dark:text-slate-300">Dashboard</p>
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
                <label className="flex h-8 w-full items-center rounded border border-slate/20 bg-white px-2 text-xs sm:w-[138px] dark:border-cyan-900/40 dark:bg-[#041612]">
                  <span className="mr-2 text-xs text-slate dark:text-white/60">Last</span>
                  <input
                    type="number"
                    min={1}
                    max={365}
                    value={dashboardRollingDays}
                    onChange={(event) => updateDashboardRollingDaysValue(event.target.value)}
                    className="w-full bg-transparent text-xs outline-none"
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
                />
                <input
                  type="date"
                  className="h-8 w-full rounded border border-slate/20 bg-white px-2 text-xs sm:w-auto dark:border-cyan-900/40 dark:bg-[#041612]"
                  value={dashboardCustomUntilDate}
                  onChange={(event) => updateDashboardCustomUntilDate(event.target.value)}
                />
              </>
            )}
          </div>
          <select
            className="h-8 w-full rounded border border-slate/20 bg-white px-2 text-xs xl:w-auto dark:border-cyan-900/40 dark:bg-[#041612]"
            value={activeSavedViewId ?? ''}
            onChange={(event) => {
              const value = event.target.value
              if (!value) {
                setActiveSavedViewId(null)
                return
              }
              const selected = viewsQuery.data?.find((view) => view.id === value)
              if (selected) {
                applySavedView(selected)
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
                  className="h-8 w-full rounded border border-slate/20 px-3 text-xs sm:w-auto dark:border-cyan-900/40"
                  onClick={() => setShowAddWindowMenu((current) => !current)}
                >
                  Add Panel
                </button>
                {showAddWindowMenu && (
                  <div className="absolute right-0 top-[calc(100%+6px)] z-30 w-56 max-w-[calc(100vw-2rem)] rounded border border-slate/20 bg-white p-1 shadow-lg dark:border-cyan-900/40 dark:bg-[#041612]">
                    <button
                      type="button"
                      className="w-full rounded px-2 py-1.5 text-left text-xs hover:bg-cyan/10"
                      onClick={() => addWindow('rss')}
                    >
                      RSS Panel ({rssWindowCount})
                    </button>
                    <button
                      type="button"
                      className="w-full rounded px-2 py-1.5 text-left text-xs hover:bg-cyan/10"
                      onClick={() => addWindow('alerts')}
                    >
                      Alerts Panel ({alertWindowCount})
                    </button>
                    <button
                      type="button"
                      className="w-full rounded px-2 py-1.5 text-left text-xs hover:bg-cyan/10"
                      onClick={() => addWindow('notes')}
                    >
                      Notes Panel ({notesWindowCount})
                    </button>
                    {aiDailyBriefEnabled && (
                      <button
                        type="button"
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
                  <span className="hidden items-center rounded border border-cyan/30 bg-cyan/8 px-2.5 text-xs font-medium text-cyan sm:flex dark:border-cyan-800/40 dark:bg-cyan-950/40 dark:text-cyan-200">
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
                  setIsEditMode(false)
                  setShowAddWindowMenu(false)
                  setOpenWindowMenuId(null)
                  setShowSaveAsNew(false)
                  setViewSaveError('')
                }}
              >
                Cancel
              </button>
            </>
          )}
        </div>
        {viewSaveError && <p className="mt-2 text-sm text-red-600 dark:text-red-300">{viewSaveError}</p>}
        {viewSavePending && <p className="mt-2 text-sm text-cyan-700 dark:text-cyan-300">Saving the current layout. Editing is temporarily locked until the request finishes.</p>}
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
                    <h2 className="font-display text-lg leading-tight text-ink dark:text-white">{windowLayout.title}</h2>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    {isEditMode && (
                      <span className="rounded border border-slate/20 bg-white/70 px-2 py-0.5 text-[10px] font-medium text-slate-700 dark:border-cyan-900/40 dark:bg-[#041612]/80 dark:text-white/65">
                        {formatWindowSnapLabel(windowLayout.snap)}
                      </span>
                    )}
                    <span className="rounded border border-slate/20 bg-white/70 px-2 py-0.5 text-[10px] font-medium text-slate-700 dark:border-cyan-900/40 dark:bg-[#041612]/80 dark:text-white/65">
                      {windowTimeSummary}
                    </span>
                    {(windowLayout.type === 'rss' || windowLayout.type === 'alerts') && activeLocalFilterCount > 0 && (
                      <span className="rounded border border-slate/20 bg-white/70 px-2 py-0.5 text-[10px] font-medium text-slate-700 dark:border-cyan-900/40 dark:bg-[#041612]/80 dark:text-white/65">
                        {activeLocalFilterCount} local filters
                      </span>
                    )}
                    {windowLayout.type === 'alerts' && (
                      <span className="rounded border border-slate/20 px-2 py-0.5 text-[10px] font-medium text-slate dark:border-cyan-900/40 dark:text-slate-300">
                        {alertWindowItems.length} shown
                      </span>
                    )}
                    {windowLayout.type === 'notes' && (
                      <span className="rounded border border-slate/20 px-2 py-0.5 text-[10px] font-medium text-slate dark:border-cyan-900/40 dark:text-slate-300">
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
                      <span className="rounded border border-slate/20 bg-white/70 px-2 py-0.5 text-[10px] font-medium text-slate-700 dark:border-cyan-900/40 dark:bg-[#041612]/80 dark:text-white/65">
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
                </div>
              </div>

              {windowLayout.type === 'rss' ? (
                <>
                  {!windowLayout.controls_collapsed && (
                    <div className={`border-b border-slate/20 px-3 py-2 dark:border-cyan-900/40 ${windowMeta.panelClassName}`}>
                      <div className="flex items-center gap-1.5 overflow-x-auto pb-0.5">
                      <button
                        type="button"
                        className={`rounded border px-3 py-1 text-xs font-semibold ${
                          rssFilters.selected_feed_ids.length === 0
                            ? 'border-cyan bg-cyan/15 text-cyan dark:bg-cyan-950/55'
                            : 'border-slate/20 dark:border-cyan-900/40'
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
                            className={`whitespace-nowrap rounded border px-3 py-1 text-xs font-semibold ${
                              active ? 'border-cyan bg-cyan/15 text-cyan dark:bg-cyan-950/55' : 'border-slate/20 dark:border-cyan-900/40'
                            }`}
                            onClick={() =>
                              updateWindowRssFilters(windowLayout.id, (current) => ({
                                ...current,
                                selected_feed_ids: current.selected_feed_ids.includes(feed.id)
                                  ? current.selected_feed_ids.filter((id) => id !== feed.id)
                                  : [...current.selected_feed_ids, feed.id],
                              }))
                            }
                          >
                            <span className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full ${feedHealthDotClass(health.status)}`} />
                            {feed.name}
                          </button>
                        )
                      })}
                      </div>

                      <div className="mt-1 flex items-center gap-1.5 overflow-x-auto pb-0.5">
                      <button
                        type="button"
                        className={`rounded border px-3 py-1 text-xs font-semibold ${
                          rssFilters.selected_tags.length === 0
                            ? 'border-violet-500 bg-violet-500/15 text-violet-700 dark:bg-violet-950/45 dark:text-violet-300'
                            : 'border-slate/20 dark:border-cyan-900/40'
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
                              className={`whitespace-nowrap rounded border px-3 py-1 text-xs font-semibold ${
                                active
                                  ? 'border-violet-500 bg-violet-500/15 text-violet-700 dark:bg-violet-950/45 dark:text-violet-300'
                                  : 'border-slate/20 dark:border-cyan-900/40'
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
                        placeholder="Search title, summary, URL"
                        className="w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm sm:min-w-64 sm:flex-1 dark:border-cyan-900/40 dark:bg-[#072019]"
                      />
                      <select
                        className="w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm sm:w-auto dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={windowLayout.time_override?.time_range ?? DASHBOARD_TIME_INHERIT_VALUE}
                        onChange={(event) => updateWindowTimeRange(windowLayout.id, event.target.value)}
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
                        <label className="flex w-full items-center rounded border border-slate/20 bg-white px-2 py-1.5 text-sm sm:w-[150px] dark:border-cyan-900/40 dark:bg-[#072019]">
                          <span className="mr-2 text-xs text-slate dark:text-white/60">Last</span>
                          <input
                            type="number"
                            min={1}
                            max={365}
                            value={effectiveWindowTimeFilter.rolling_days}
                            onChange={(event) => updateWindowRollingDays(windowLayout.id, event.target.value)}
                            className="w-full bg-transparent outline-none"
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
                      >
                        <option value="published_at_desc">Newest</option>
                        <option value="published_at_asc">Oldest</option>
                        <option value="first_seen_desc">Seen newest</option>
                        <option value="first_seen_asc">Seen oldest</option>
                      </select>
                      <div className="flex w-full rounded border border-slate/20 p-0.5 sm:w-auto dark:border-cyan-900/40">
                        <button
                          type="button"
                          className={`flex-1 rounded px-2 py-1 text-xs font-semibold sm:flex-none ${rssFilters.view_mode === 'expanded' ? 'bg-cyan/15 text-cyan' : ''}`}
                          onClick={() => updateWindowRssFilters(windowLayout.id, (current) => ({ ...current, view_mode: 'expanded' }), false)}
                        >
                          Expanded
                        </button>
                        <button
                          type="button"
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
                          />
                          <input
                            type="date"
                            className="w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm disabled:opacity-50 dark:border-cyan-900/40 dark:bg-[#041612]"
                            value={effectiveWindowTimeFilter.custom_until_date}
                            onChange={(event) => updateWindowCustomTimeDate(windowLayout.id, 'custom_until_date', event.target.value)}
                            disabled={effectiveWindowTimeFilter.time_range !== 'custom'}
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
                              expanded ? 'border-cyan bg-cyan/5 dark:border-cyan-700/50 dark:bg-cyan-950/25' : 'border-slate/20 dark:border-cyan-900/40'
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
                                  <span className="text-xs text-slate dark:text-slate-300">{item.feed_name}</span>
                                </div>
                              </div>
                              <button
                                type="button"
                                className="mt-1 w-full text-left text-slate-900 dark:text-slate-100"
                                onClick={() => handleToggleItem(windowLayout.id, item.id, item.is_read)}
                              >
                                <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate dark:text-slate-300">
                                  <span>Published {formatPublishedAt(item.published_at)}</span>
                                  {item.status !== 'content_fetched' && (
                                    <span className="rounded bg-slate/15 px-1.5 py-0.5 dark:bg-[#0b1a33]">{item.status}</span>
                                  )}
                                  {!item.is_read && <span className="rounded bg-cyan/20 px-1.5 py-0.5 text-cyan">Unread</span>}
                                  {item.is_starred && <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-700">Starred</span>}
                                  {aiRelevanceEnabled && item.ai_relevance_label && (
                                    <span className={`rounded px-1.5 py-0.5 ${aiRelevanceTone(item.ai_relevance_label)}`}>
                                      AI {formatAiRelevanceLabel(item.ai_relevance_label)}
                                    </span>
                                  )}
                                  {item.tags
                                    .filter((tagName) => !HIDDEN_TAGS.has(tagName))
                                    .slice(0, 3)
                                    .map((tagName) => (
                                      <span
                                        key={`${item.id}-${tagName}`}
                                        className="rounded bg-violet-100 px-1.5 py-0.5 text-violet-800 dark:bg-violet-900/35 dark:text-violet-200"
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
                              <div className="mt-3 border-t border-slate/20 pt-3 dark:border-cyan-900/40">
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
                                        disabled={!canManage}
                                        onClick={() =>
                                          updateRead.mutate({
                                            itemId: detail.id,
                                            isRead: !detail.state.is_read,
                                          })
                                        }
                                      >
                                        {detail.state.is_read ? 'Mark Unread' : 'Mark Read'}
                                      </button>
                                      <button
                                        className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40"
                                        disabled={!canManage}
                                        onClick={() =>
                                          updateStar.mutate({
                                            itemId: detail.id,
                                            isStarred: !detail.state.is_starred,
                                          })
                                        }
                                      >
                                        {detail.state.is_starred ? 'Unstar' : 'Star'}
                                      </button>
                                      {!canManage && <span className="text-xs text-amber-600">Viewer role is read-only.</span>}
                                    </div>

                                    <div className="mt-3 rounded border border-slate/20 bg-white/90 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/90">
                                      <p className="text-xs font-medium text-slate dark:text-slate-300">RSS summary</p>
                                      {detail.classification && (
                                        <p className="mt-1 text-xs text-slate dark:text-slate-300">
                                          Classification:{' '}
                                          <span className="font-semibold">
                                            {formatClassificationLabel(detail.classification.primary_category)}
                                          </span>{' '}
                                          ({Math.round(detail.classification.confidence * 100)}% confidence)
                                        </p>
                                      )}
                                      <div className="rss-reader mt-2 rounded bg-white/95 p-3 text-slate-900 dark:bg-[#041612]/80 dark:text-slate-100">
                                        {renderRichContent(detail.summary || 'No summary.', detail.id, 'summary')}
                                      </div>
                                    </div>

                                    {(aiSummaryEnabled || aiRelevanceEnabled) && detail.ai_insight?.status === 'ready' && (
                                      <div className="mt-3 rounded border border-slate/20 bg-white/90 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/90">
                                        <p className="text-xs font-medium text-slate dark:text-slate-300">AI insight</p>
                                        {aiRelevanceEnabled && detail.ai_insight.relevance_label && (
                                          <div className="mt-2 flex flex-wrap items-center gap-2">
                                            <span className={`rounded px-2 py-1 text-xs font-semibold ${aiRelevanceTone(detail.ai_insight.relevance_label)}`}>
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
                                          <div className="mt-3 rounded bg-white/95 p-3 dark:bg-[#041612]/80">
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

                                    <div className="mt-3 rounded border border-slate/20 bg-white/90 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/90">
                                      <p className="text-xs font-medium text-slate dark:text-slate-300">Full article</p>
                                      {detail.article?.text ? (
                                        <div className="rss-reader mt-2 rounded bg-white/95 p-3 text-slate-900 dark:bg-[#041612]/80 dark:text-slate-100">
                                          {renderRichContent(detail.article.text, detail.id, 'article')}
                                        </div>
                                      ) : (
                                        <p className="mt-2 text-sm text-slate dark:text-slate-300">No extracted article text available yet.</p>
                                      )}
                                      {detail.article?.error && (
                                        <p className="mt-2 text-sm text-red-600">Extraction error: {detail.article.error}</p>
                                      )}
                                    </div>

                                    <div className="mt-3 rounded border border-slate/20 bg-white/90 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/90">
                                      <label className="text-xs font-medium text-slate dark:text-slate-300">Notes</label>
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
                                      />
                                      <div className="mt-2 flex items-center gap-2">
                                        <button
                                          className="rounded bg-ink px-2.5 py-1 text-xs font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-slate-950"
                                          onClick={() =>
                                            updateNote.mutate({
                                              itemId: detail.id,
                                              note: (noteDraftsByItemId[detail.id] ?? detail.state.note ?? '') || null,
                                            })
                                          }
                                          disabled={!canManage}
                                        >
                                          Save Notes
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
                            page_size: Number(event.target.value),
                          }))
                        }
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
                    <div className="flex items-center gap-2 overflow-x-auto pb-1">
                      <button
                        type="button"
                        className={`rounded border px-3 py-1 text-xs font-semibold ${
                          alertFilters.selected_categories.length === 0
                            ? 'border-cyan bg-cyan/15 text-cyan dark:bg-cyan-950/55'
                            : 'border-slate/20 dark:border-cyan-900/40'
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
                            className={`whitespace-nowrap rounded border px-3 py-1 text-xs font-semibold ${
                              active ? 'border-cyan bg-cyan/15 text-cyan dark:bg-cyan-950/55' : 'border-slate/20 dark:border-cyan-900/40'
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

                    <div className="mt-2 flex items-center gap-2 overflow-x-auto pb-1">
                      <button
                        type="button"
                        className={`rounded border px-3 py-1 text-xs font-semibold ${
                          alertFilters.selected_alert_ids.length === 0
                            ? 'border-violet-500 bg-violet-500/15 text-violet-700 dark:bg-violet-950/45 dark:text-violet-300'
                            : 'border-slate/20 dark:border-cyan-900/40'
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
                            className={`whitespace-nowrap rounded border px-3 py-1 text-xs font-semibold ${
                              active
                                ? 'border-violet-500 bg-violet-500/15 text-violet-700 dark:bg-violet-950/45 dark:text-violet-300'
                                : 'border-slate/20 dark:border-cyan-900/40'
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
                        placeholder="Search matched alert items"
                        className="w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm sm:min-w-64 sm:flex-1 dark:border-cyan-900/40 dark:bg-[#072019]"
                      />
                      <select
                        className="w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm sm:w-auto dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={windowLayout.time_override?.time_range ?? DASHBOARD_TIME_INHERIT_VALUE}
                        onChange={(event) => updateWindowTimeRange(windowLayout.id, event.target.value)}
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
                        <label className="flex w-full items-center rounded border border-slate/20 bg-white px-2 py-1.5 text-sm sm:w-[150px] dark:border-cyan-900/40 dark:bg-[#072019]">
                          <span className="mr-2 text-xs text-slate dark:text-white/60">Last</span>
                          <input
                            type="number"
                            min={1}
                            max={365}
                            value={effectiveWindowTimeFilter.rolling_days}
                            onChange={(event) => updateWindowRollingDays(windowLayout.id, event.target.value)}
                            className="w-full bg-transparent outline-none"
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
                      >
                        <option value="published_at_desc">Newest</option>
                        <option value="published_at_asc">Oldest</option>
                        <option value="first_seen_desc">Seen newest</option>
                        <option value="first_seen_asc">Seen oldest</option>
                      </select>
                      <div className="flex w-full rounded border border-slate/20 p-0.5 sm:w-auto dark:border-cyan-900/40">
                        <button
                          type="button"
                          className={`flex-1 rounded px-2 py-1 text-xs font-semibold sm:flex-none ${alertFilters.view_mode === 'expanded' ? 'bg-cyan/15 text-cyan' : ''}`}
                          onClick={() => updateWindowAlertFilters(windowLayout.id, (current) => ({ ...current, view_mode: 'expanded' }), false)}
                        >
                          Expanded
                        </button>
                        <button
                          type="button"
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
                      />
                      <input
                        type="date"
                        className="w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm disabled:opacity-50 sm:w-auto dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={effectiveWindowTimeFilter.custom_until_date}
                        onChange={(event) => updateWindowCustomTimeDate(windowLayout.id, 'custom_until_date', event.target.value)}
                        disabled={effectiveWindowTimeFilter.time_range !== 'custom'}
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
                                className="rounded border border-amber-300/60 bg-amber-100/70 px-2 py-0.5 text-[11px] text-amber-800 dark:border-amber-800/40 dark:bg-amber-950/30 dark:text-amber-200"
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
                            page_size: Number(event.target.value),
                          }))
                        }
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
                            <span className="text-xs font-medium text-slate dark:text-white/55">Briefing</span>
                            <select
                              className="w-full rounded border border-slate/20 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#041612]"
                              value={selectedBrief.id}
                              onChange={(event) => updateWindowDailyBriefSelection(windowLayout.id, event.target.value)}
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
                          <p className="text-xs font-medium text-slate dark:text-slate-300">Key points</p>
                          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate dark:text-white/75">
                            {selectedBrief.key_points.map((point, index) => (
                              <li key={`${windowLayout.id}-brief-point-${index}`}>{point}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {selectedBrief.recommended_actions.length > 0 && (
                        <div className="rounded border border-slate/20 bg-white/90 p-3 dark:border-cyan-900/40 dark:bg-[#041612]/92">
                          <p className="text-xs font-medium text-slate dark:text-slate-300">Recommended actions</p>
                          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate dark:text-white/75">
                            {selectedBrief.recommended_actions.map((action, index) => (
                              <li key={`${windowLayout.id}-brief-action-${index}`}>{action}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {selectedBrief.items.length > 0 && (
                        <div className="rounded border border-slate/20 bg-white/90 p-3 dark:border-cyan-900/40 dark:bg-[#041612]/92">
                          <p className="text-xs font-medium text-slate dark:text-slate-300">Referenced items</p>
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
                                    <span className={`shrink-0 rounded px-2 py-0.5 text-[11px] ${aiRelevanceTone(item.relevance_label)}`}>
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
                  <label className="text-xs font-medium text-slate dark:text-slate-300">Scratch notes</label>
                  <textarea
                    className="mt-2 h-full min-h-[180px] w-full flex-1 rounded border border-slate/20 bg-white px-3 py-2 text-sm leading-6 dark:border-cyan-900/40 dark:bg-[#072019]"
                    placeholder="Use this space for quick notes, pivots, and hypotheses..."
                    value={windowLayout.scratch_note}
                    onChange={(event) => updateWindowScratchNote(windowLayout.id, event.target.value)}
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
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/55 p-3">
          <div className="w-full max-w-md rounded-2xl border border-slate/20 bg-white p-4 shadow-xl dark:border-cyan-900/40 dark:bg-[#041612]">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-xs font-medium text-slate dark:text-white/55">Panel settings</p>
                <h3 className="mt-1 font-display text-xl text-ink dark:text-white">Rename panel</h3>
                <p className="mt-1 text-sm text-slate dark:text-white/70">
                  Rename this panel without leaving the dashboard.
                </p>
              </div>
              <button
                type="button"
                className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40"
                onClick={() => {
                  setRenamingWindowId(null)
                  setRenameWindowDraft('')
                }}
              >
                Close
              </button>
            </div>

            <div className="mt-4 space-y-3">
              <input
                autoFocus
                value={renameWindowDraft}
                onChange={(event) => setRenameWindowDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault()
                    saveRenamedWindow()
                  }
                  if (event.key === 'Escape') {
                    setRenamingWindowId(null)
                    setRenameWindowDraft('')
                  }
                }}
                maxLength={80}
                className="w-full rounded border border-slate/20 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
              />
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs text-slate dark:text-white/60">Up to 80 characters. Saved with this view.</p>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="rounded border border-slate/20 px-3 py-2 text-xs font-semibold dark:border-cyan-900/40"
                    onClick={() => {
                      setRenamingWindowId(null)
                      setRenameWindowDraft('')
                    }}
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
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {showManageViewsModal && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/55 p-3">
          <div className="max-h-[92vh] w-full max-w-3xl overflow-auto rounded-xl border border-slate/20 bg-white p-4 dark:border-cyan-900/40 dark:bg-[#041612]">
            <div className="flex items-center justify-between gap-3">
              <h3 className="font-display text-xl">Manage Saved Views</h3>
              <button
                type="button"
                className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40"
                onClick={() => setShowManageViewsModal(false)}
              >
                Close
              </button>
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button
                type="button"
                className="rounded border border-slate/20 px-3 py-1.5 text-xs dark:border-cyan-900/40"
                onClick={exportAllViews}
              >
                Export JSON
              </button>
              <label className="rounded border border-slate/20 px-3 py-1.5 text-xs dark:border-cyan-900/40">
                Import JSON
                <input
                  type="file"
                  accept="application/json"
                  className="hidden"
                  onChange={(event) => {
                    void importViewsFile(event)
                  }}
                  disabled={isImportingViews}
                />
              </label>
              {isImportingViews && <span className="text-xs text-slate dark:text-slate-300">Importing...</span>}
              {importViewsError && <span className="text-xs text-red-600">{importViewsError}</span>}
              {importViewsResult && <span className="text-xs text-emerald-600">{importViewsResult}</span>}
            </div>

            <div className="mt-4 grid gap-2 md:grid-cols-2">
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
                      onClick={() => {
                        const selected = viewsQuery.data?.find((entry) => entry.id === view.id)
                        if (selected) {
                          applySavedView(selected)
                        }
                      }}
                    >
                      Load
                    </button>
                    <button
                      type="button"
                      className="rounded border border-slate/20 px-2 py-1 text-xs text-red-600 dark:border-cyan-900/40"
                      onClick={() => setPendingViewDelete(view)}
                      disabled={deleteView.isPending || Boolean(pendingViewDelete)}
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
          </div>
        </div>
      )}

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

function isTimeRangeFilter(value: unknown): value is TimeRangeFilter {
  return value === 'all' || value === '24h' || value === '7d' || value === '30d' || value === 'days' || value === 'custom'
}

function isTimeSort(value: unknown): value is TimeSort {
  return (
    value === 'published_at_desc' ||
    value === 'published_at_asc' ||
    value === 'first_seen_desc' ||
    value === 'first_seen_asc'
  )
}

function parseWindowTimeFilterCandidate(value: unknown): WindowTimeFilter | null {
  if (!isRecord(value)) return null
  if (!isTimeRangeFilter(value.time_range)) return null
  return {
    time_range: value.time_range,
    custom_since_date: typeof value.custom_since_date === 'string' ? value.custom_since_date : '',
    custom_until_date: typeof value.custom_until_date === 'string' ? value.custom_until_date : '',
    rolling_days:
      typeof value.rolling_days === 'string' ? normalizeRollingDaysInput(value.rolling_days) : DEFAULT_ROLLING_DAYS,
  }
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

  if (queryKey.length !== 11) {
    return false
  }

  const readStatus = queryKey[4]
  const starStatus = queryKey[5]
  const isDashboardReadStatus = readStatus === 'all' || readStatus === 'read' || readStatus === 'unread'
  const isDashboardStarStatus = starStatus === 'all' || starStatus === 'starred' || starStatus === 'unstarred'

  if (!isDashboardReadStatus || !isDashboardStarStatus) {
    return false
  }

  return (
    (patch.isRead !== undefined && readStatus !== 'all') ||
    (patch.isStarred !== undefined && starStatus !== 'all')
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
    return 'bg-red-100 text-red-800 dark:bg-red-950/35 dark:text-red-200'
  }
  if (value === 'medium') {
    return 'bg-amber-100 text-amber-800 dark:bg-amber-950/35 dark:text-amber-200'
  }
  return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/35 dark:text-emerald-200'
}

function buildSavedViewPreview(view: SavedView, containerWidth: number, containerHeight: number): SavedViewPreview {
  const parsed = parseDashboardSavedView(view.query_json, containerWidth, containerHeight)
  const counts = {
    rss: 0,
    alerts: 0,
    notes: 0,
    daily_brief: 0,
  }

  for (const window of parsed.windows) {
    if (window.type === 'rss') counts.rss += 1
    if (window.type === 'alerts') counts.alerts += 1
    if (window.type === 'notes') counts.notes += 1
    if (window.type === 'daily_brief') counts.daily_brief += 1
  }

  return {
    id: view.id,
    name: view.name,
    created_at: view.created_at,
    windows: parsed.windows,
    window_type_counts: counts,
  }
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

function normalizeRollingDaysInput(value: string) {
  const numeric = value.replace(/[^\d]/g, '')
  if (!numeric) {
    return DEFAULT_ROLLING_DAYS
  }
  return String(clamp(Number(numeric), 1, 365))
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

function serializeDashboardWindowLayouts(windows: DashboardWindow[]) {
  return windows.map((window) => ({
    id: window.id,
    type: window.type,
    title: window.title,
    snap: window.snap,
    rect: { ...window.rect },
    controls_collapsed: window.controls_collapsed,
    scratch_note: window.scratch_note,
    time_override: window.time_override ? { ...window.time_override } : null,
    selected_daily_brief_id: window.selected_daily_brief_id,
  }))
}

function loadDashboardWindows(storageKey: string, containerWidth: number, containerHeight: number): DashboardWindow[] {
  if (typeof window === 'undefined') {
    return [createWindowLayout('rss', 1, containerWidth, containerHeight, 'full')]
  }

  const raw = window.localStorage.getItem(storageKey)
  if (!raw) {
    return [createWindowLayout('rss', 1, containerWidth, containerHeight, 'full')]
  }

  try {
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) {
      return [createWindowLayout('rss', 1, containerWidth, containerHeight, 'full')]
    }

    const windows: DashboardWindow[] = []

    for (const entry of parsed) {
      if (!isRecord(entry)) continue
      if (!isWindowType(entry.type)) continue
      if (!isWindowSnap(entry.snap)) continue

      const rect = parsePanelRectCandidate(entry.rect)
      if (!rect) continue

      windows.push({
        id: typeof entry.id === 'string' && entry.id ? entry.id : crypto.randomUUID(),
        title: typeof entry.title === 'string' && entry.title ? entry.title : defaultWindowTitle(entry.type, windows.length + 1),
        type: entry.type,
        snap: entry.snap,
        rect: normalizePanelRect(rect, containerWidth, containerHeight),
        controls_collapsed: entry.controls_collapsed === true,
        scratch_note: typeof entry.scratch_note === 'string' ? entry.scratch_note : '',
        time_override: parseWindowTimeFilterCandidate(entry.time_override),
        rss_filters: entry.type === 'rss' ? parseRssWindowFiltersCandidate(entry.rss_filters) : null,
        alert_filters: entry.type === 'alerts' ? parseAlertWindowFiltersCandidate(entry.alert_filters) : null,
        selected_daily_brief_id:
          entry.type === 'daily_brief' && typeof entry.selected_daily_brief_id === 'string' && entry.selected_daily_brief_id
            ? entry.selected_daily_brief_id
            : null,
      })
    }

    if (!windows.length) {
      return [createWindowLayout('rss', 1, containerWidth, containerHeight, 'full')]
    }

    return normalizeDashboardWindows(windows, containerWidth, containerHeight)
  } catch {
    return [createWindowLayout('rss', 1, containerWidth, containerHeight, 'full')]
  }
}

function normalizeDashboardWindows(windows: DashboardWindow[], containerWidth: number, containerHeight: number) {
  if (!windows.length) {
    return [createWindowLayout('rss', 1, containerWidth, containerHeight, 'full')]
  }

  return windows.map((window) => {
    if (window.snap === 'free') {
      return {
        ...window,
        rect: normalizePanelRect(window.rect, containerWidth, containerHeight),
      }
    }

    return {
      ...window,
      rect: getSnapRect(window.snap, containerWidth, containerHeight),
    }
  })
}

function defaultWindowTitle(type: DashboardWindowType, index: number): string {
  if (type === 'rss') return `RSS Panel ${index}`
  if (type === 'alerts') return `Alerts Panel ${index}`
  if (type === 'daily_brief') return `Daily Brief Panel ${index}`
  return `Notes Panel ${index}`
}

function createWindowLayout(
  type: DashboardWindowType,
  index: number,
  containerWidth: number,
  containerHeight: number,
  snap: DashboardWindowSnap = 'free',
): DashboardWindow {
  const id = `${type}-${Date.now()}-${Math.random().toString(16).slice(2, 7)}`
  const width = clamp(Math.round(containerWidth * 0.68), WINDOW_MIN_WIDTH, containerWidth)
  const height = clamp(Math.round(containerHeight * 0.74), WINDOW_MIN_HEIGHT, containerHeight)

  const maxX = Math.max(0, containerWidth - width)
  const maxY = Math.max(0, containerHeight - height)

  const rect: PanelRect = {
    x: clamp((index * 28) % Math.max(1, maxX + 1), 0, maxX),
    y: clamp((index * 22) % Math.max(1, maxY + 1), 0, maxY),
    width,
    height,
  }

  return {
    id,
    type,
    title: defaultWindowTitle(type, index),
    snap,
    rect: snap === 'free' ? rect : getSnapRect(snap, containerWidth, containerHeight),
    controls_collapsed: false,
    scratch_note: '',
    time_override: null,
    rss_filters: type === 'rss' ? createDefaultRssWindowFilters() : null,
    alert_filters: type === 'alerts' ? createDefaultAlertWindowFilters() : null,
    selected_daily_brief_id: null,
  }
}

function getSnapRect(snap: DashboardWindowSnap, containerWidth: number, containerHeight: number): PanelRect {
  const width = Math.max(WINDOW_MIN_WIDTH, containerWidth)
  const height = Math.max(WINDOW_MIN_HEIGHT, containerHeight)

  const halfWidth = Math.floor(width / 2)
  const halfHeight = Math.floor(height / 2)

  if (snap === 'full') {
    return { x: 0, y: 0, width, height }
  }

  if (snap === 'left') {
    return { x: 0, y: 0, width: halfWidth, height }
  }

  if (snap === 'right') {
    return { x: halfWidth, y: 0, width: width - halfWidth, height }
  }

  if (snap === 'top_left') {
    return { x: 0, y: 0, width: halfWidth, height: halfHeight }
  }

  if (snap === 'top_right') {
    return { x: halfWidth, y: 0, width: width - halfWidth, height: halfHeight }
  }

  if (snap === 'bottom_left') {
    return { x: 0, y: halfHeight, width: halfWidth, height: height - halfHeight }
  }

  if (snap === 'bottom_right') {
    return { x: halfWidth, y: halfHeight, width: width - halfWidth, height: height - halfHeight }
  }

  return {
    x: 0,
    y: 0,
    width,
    height,
  }
}

function parseSavedViewRssFilters(raw: Record<string, unknown>): DashboardSavedViewQuery {
  const selectedFeedIds = Array.isArray(raw.selected_feed_ids)
    ? raw.selected_feed_ids.filter((entry): entry is string => typeof entry === 'string')
    : []
  const selectedTags = Array.isArray(raw.selected_tags)
    ? raw.selected_tags.filter((entry): entry is string => typeof entry === 'string' && !HIDDEN_TAGS.has(entry))
    : []

  return {
    selected_feed_ids: selectedFeedIds,
    selected_tags: selectedTags,
    q: typeof raw.q === 'string' ? raw.q : '',
    read_status: raw.read_status === 'read' || raw.read_status === 'unread' ? raw.read_status : 'all',
    star_status: raw.star_status === 'starred' || raw.star_status === 'unstarred' ? raw.star_status : 'all',
    view_mode: raw.view_mode === 'expanded' ? 'expanded' : 'compact',
    page_size: typeof raw.page_size === 'number' && PAGE_SIZE_OPTIONS.includes(raw.page_size) ? raw.page_size : 25,
    time_range: isTimeRangeFilter(raw.time_range) ? raw.time_range : 'all',
    custom_since_date: typeof raw.custom_since_date === 'string' ? raw.custom_since_date : '',
    custom_until_date: typeof raw.custom_until_date === 'string' ? raw.custom_until_date : '',
    rolling_days: typeof raw.rolling_days === 'string' && raw.rolling_days.trim() ? raw.rolling_days.trim() : DEFAULT_ROLLING_DAYS,
    sort: typeof raw.sort === 'string' && isTimeSort(raw.sort) ? raw.sort : 'published_at_desc',
  }
}

function parseSavedViewAlertFilters(raw: Record<string, unknown>): DashboardAlertViewQuery {
  const selectedAlertIds = Array.isArray(raw.selected_alert_ids)
    ? raw.selected_alert_ids.filter((entry): entry is string => typeof entry === 'string')
    : []
  const selectedAlertCategories = Array.isArray(raw.selected_categories)
    ? raw.selected_categories.filter((entry): entry is string => typeof entry === 'string')
    : []

  return {
    selected_alert_ids: selectedAlertIds,
    selected_categories: selectedAlertCategories,
    q: typeof raw.q === 'string' ? raw.q : '',
    view_mode: raw.view_mode === 'compact' ? 'compact' : 'expanded',
    page_size: typeof raw.page_size === 'number' && PAGE_SIZE_OPTIONS.includes(raw.page_size) ? raw.page_size : 25,
    time_range: isTimeRangeFilter(raw.time_range) ? raw.time_range : 'all',
    custom_since_date: typeof raw.custom_since_date === 'string' ? raw.custom_since_date : '',
    custom_until_date: typeof raw.custom_until_date === 'string' ? raw.custom_until_date : '',
    rolling_days: typeof raw.rolling_days === 'string' && raw.rolling_days.trim() ? raw.rolling_days.trim() : DEFAULT_ROLLING_DAYS,
    sort: typeof raw.sort === 'string' && isTimeSort(raw.sort) ? raw.sort : 'published_at_desc',
  }
}

function buildSavedViewRssFilters(rssFilters: DashboardRssWindowFilters, dashboardTimeFilter: WindowTimeFilter): DashboardSavedViewQuery {
  return {
    selected_feed_ids: [...rssFilters.selected_feed_ids],
    selected_tags: [...rssFilters.selected_tags],
    q: rssFilters.q,
    read_status: rssFilters.read_status,
    star_status: rssFilters.star_status,
    view_mode: rssFilters.view_mode,
    page_size: rssFilters.page_size,
    time_range: dashboardTimeFilter.time_range,
    custom_since_date: dashboardTimeFilter.custom_since_date,
    custom_until_date: dashboardTimeFilter.custom_until_date,
    rolling_days: dashboardTimeFilter.rolling_days,
    sort: rssFilters.sort,
  }
}

function buildSavedViewAlertFilters(
  alertFilters: DashboardAlertWindowFilters,
  dashboardTimeFilter: WindowTimeFilter,
): DashboardAlertViewQuery {
  return {
    selected_alert_ids: [...alertFilters.selected_alert_ids],
    selected_categories: [...alertFilters.selected_categories],
    q: alertFilters.q,
    view_mode: alertFilters.view_mode,
    page_size: alertFilters.page_size,
    time_range: dashboardTimeFilter.time_range,
    custom_since_date: dashboardTimeFilter.custom_since_date,
    custom_until_date: dashboardTimeFilter.custom_until_date,
    rolling_days: dashboardTimeFilter.rolling_days,
    sort: alertFilters.sort,
  }
}

function parseDashboardSavedView(raw: Record<string, unknown>, containerWidth: number, containerHeight: number): DashboardSavedViewState {
  const fallback = createWindowLayout('rss', 1, containerWidth, containerHeight, 'full')

  const legacyFilters = isRecord(raw.filters) ? raw.filters : raw
  const legacyLayout = isRecord(raw.layout) ? raw.layout : {}
  const legacyWindows = isRecord(legacyLayout.windows) ? legacyLayout.windows : {}
  const legacyFeedRect = parsePanelRectCandidate(legacyWindows.feeds) || parsePanelRectCandidate(raw.panel_rect) || fallback.rect
  const rssSource = isRecord(raw.rss_filters) ? raw.rss_filters : legacyFilters
  const alertSource = isRecord(raw.alert_filters) ? raw.alert_filters : {}
  const uiSource = isRecord(raw.ui) ? raw.ui : {}

  const rssFilters = parseSavedViewRssFilters(rssSource)
  const alertFilters = parseSavedViewAlertFilters(alertSource)
  const showAdvancedFilters = typeof uiSource.show_advanced_filters === 'boolean' ? uiSource.show_advanced_filters : false

  const parsedWindows: DashboardWindow[] = []
  if (Array.isArray(raw.windows)) {
    for (const entry of raw.windows) {
      if (!isRecord(entry)) continue
      if (!isWindowType(entry.type)) continue
      if (!isWindowSnap(entry.snap)) continue
      const rect = parsePanelRectCandidate(entry.rect)
      if (!rect) continue

      parsedWindows.push({
        id: typeof entry.id === 'string' && entry.id ? entry.id : crypto.randomUUID(),
        type: entry.type,
        title:
          typeof entry.title === 'string' && entry.title
            ? entry.title
            : defaultWindowTitle(entry.type, parsedWindows.length + 1),
        snap: entry.snap,
        rect,
        controls_collapsed: entry.controls_collapsed === true,
        scratch_note: typeof entry.scratch_note === 'string' ? entry.scratch_note : '',
        time_override: parseWindowTimeFilterCandidate(entry.time_override),
        rss_filters:
          entry.type === 'rss'
            ? parseRssWindowFiltersCandidate(entry.rss_filters, { ...rssFilters, page: 1 }, showAdvancedFilters)
            : null,
        alert_filters:
          entry.type === 'alerts'
            ? parseAlertWindowFiltersCandidate(entry.alert_filters, { ...alertFilters, page: 1 })
            : null,
        selected_daily_brief_id:
          entry.type === 'daily_brief' && typeof entry.selected_daily_brief_id === 'string' && entry.selected_daily_brief_id
            ? entry.selected_daily_brief_id
            : null,
      })
    }
  }

  if (!parsedWindows.length) {
    parsedWindows.push({
      id: fallback.id,
      type: 'rss',
      title: 'RSS Feed 1',
      snap: 'free',
      rect: legacyFeedRect,
      controls_collapsed: false,
      scratch_note: '',
      time_override: null,
      rss_filters: parseRssWindowFiltersCandidate(null, { ...rssFilters, page: 1 }, showAdvancedFilters),
      alert_filters: null,
      selected_daily_brief_id: null,
    })
  }

  return {
    version: DASHBOARD_VIEW_VERSION,
    rss_filters: rssFilters,
    alert_filters: alertFilters,
    windows: normalizeDashboardWindows(parsedWindows, containerWidth, containerHeight),
    ui: {
      show_advanced_filters: showAdvancedFilters,
    },
  }
}

function buildDashboardSavedViewState(windows: DashboardWindow[], dashboardTimeFilter: WindowTimeFilter): DashboardSavedViewState {
  const firstRssWindow = windows.find((window): window is DashboardWindow & { type: 'rss' } => window.type === 'rss')
  const firstAlertWindow = windows.find((window): window is DashboardWindow & { type: 'alerts' } => window.type === 'alerts')
  const rssWindowFilters = firstRssWindow?.rss_filters ?? createDefaultRssWindowFilters()
  const alertWindowFilters = firstAlertWindow?.alert_filters ?? createDefaultAlertWindowFilters()

  return {
    version: DASHBOARD_VIEW_VERSION,
    rss_filters: buildSavedViewRssFilters(rssWindowFilters, dashboardTimeFilter),
    alert_filters: buildSavedViewAlertFilters(alertWindowFilters, dashboardTimeFilter),
    windows: windows.map((window) => ({
      id: window.id,
      type: window.type,
      title: window.title,
      snap: window.snap,
      rect: { ...window.rect },
      controls_collapsed: window.controls_collapsed,
      scratch_note: window.scratch_note,
      time_override: window.time_override ? { ...window.time_override } : null,
      rss_filters: window.rss_filters
        ? {
            ...window.rss_filters,
            selected_feed_ids: [...window.rss_filters.selected_feed_ids],
            selected_tags: [...window.rss_filters.selected_tags],
            page: 1,
          }
        : null,
      alert_filters: window.alert_filters
        ? {
            ...window.alert_filters,
            selected_alert_ids: [...window.alert_filters.selected_alert_ids],
            selected_categories: [...window.alert_filters.selected_categories],
            page: 1,
          }
        : null,
      selected_daily_brief_id: window.selected_daily_brief_id,
    })),
    ui: {
      show_advanced_filters: rssWindowFilters.show_advanced_filters,
    },
  }
}

function parseImportedSavedViews(raw: unknown): ImportedSavedViewEntry[] {
  const source = isRecord(raw) && Array.isArray(raw.views) ? raw.views : raw
  if (!Array.isArray(source)) {
    throw new Error('Expected a JSON array or an object with a "views" array')
  }

  const entries: ImportedSavedViewEntry[] = []
  for (const entry of source) {
    if (!isRecord(entry)) continue
    const name = typeof entry.name === 'string' ? entry.name.trim() : ''
    const queryJson = isRecord(entry.query_json) ? entry.query_json : null
    if (!name || !queryJson) {
      continue
    }

    entries.push({
      name,
      query_json: queryJson,
    })
  }

  if (entries.length > MAX_IMPORTED_VIEWS) {
    throw new Error(`Import file contains too many views. Maximum allowed is ${MAX_IMPORTED_VIEWS}.`)
  }

  return entries
}

type ArticleBlock =
  | { kind: 'heading'; text: string }
  | { kind: 'paragraph'; text: string }
  | { kind: 'bullet-list'; items: string[] }
  | { kind: 'numbered-list'; items: string[] }
  | { kind: 'quote'; text: string }

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

function parseArticleBlocks(text: string): ArticleBlock[] {
  const lines = text.replace(/\r/g, '').split('\n')
  const blocks: ArticleBlock[] = []
  const nonEmptyCount = lines.filter((line) => line.trim()).length
  const blankCount = lines.length - nonEmptyCount
  const useLineOrientedMode = nonEmptyCount >= 10 && blankCount <= Math.ceil(nonEmptyCount * 0.12)

  let index = 0
  while (index < lines.length) {
    const raw = lines[index]
    const line = raw.trim()

    if (!line) {
      index += 1
      continue
    }

    if (isHeadingLine(line)) {
      blocks.push({ kind: 'heading', text: cleanHeading(line) })
      index += 1
      continue
    }

    if (looksLikeSectionHeading(line)) {
      blocks.push({ kind: 'heading', text: line })
      index += 1
      continue
    }

    if (isBulletLine(line)) {
      const items: string[] = []
      while (index < lines.length && isBulletLine(lines[index].trim())) {
        items.push(cleanBullet(lines[index].trim()))
        index += 1
      }
      if (items.length) {
        blocks.push({ kind: 'bullet-list', items })
      }
      continue
    }

    if (line.includes(' • ')) {
      const items: string[] = []
      while (index < lines.length) {
        const bulletLine = lines[index].trim()
        if (!bulletLine || !bulletLine.includes(' • ')) {
          break
        }
        items.push(bulletLine)
        index += 1
      }
      if (items.length) {
        blocks.push({ kind: 'bullet-list', items })
      }
      continue
    }

    if (isNumberedLine(line)) {
      const items: string[] = []
      while (index < lines.length && isNumberedLine(lines[index].trim())) {
        items.push(cleanNumbered(lines[index].trim()))
        index += 1
      }
      if (items.length) {
        blocks.push({ kind: 'numbered-list', items })
      }
      continue
    }

    if (line.startsWith('>')) {
      const quoteLines: string[] = []
      while (index < lines.length && lines[index].trim().startsWith('>')) {
        quoteLines.push(lines[index].trim().replace(/^>\s*/, ''))
        index += 1
      }
      const quoteText = quoteLines.join(' ').replace(/\s{2,}/g, ' ').trim()
      if (quoteText) {
        blocks.push({ kind: 'quote', text: quoteText })
      }
      continue
    }

    if (useLineOrientedMode) {
      blocks.push({ kind: 'paragraph', text: line })
      index += 1
      continue
    }

    const paragraphLines: string[] = []
    while (index < lines.length) {
      const paragraphLine = lines[index].trim()
      if (!paragraphLine || isHeadingLine(paragraphLine) || isBulletLine(paragraphLine) || isNumberedLine(paragraphLine) || paragraphLine.startsWith('>')) {
        break
      }
      paragraphLines.push(paragraphLine)
      index += 1
    }

    if (paragraphLines.length) {
      blocks.push({
        kind: 'paragraph',
        text: paragraphLines.join(' ').replace(/\s{2,}/g, ' ').trim(),
      })
      continue
    }

    index += 1
  }

  return blocks
}

function isHeadingLine(line: string): boolean {
  if (/^#{1,4}\s+/.test(line)) {
    return true
  }

  return /^[A-Z][A-Z0-9\s\-:]{8,}$/.test(line) && line === line.toUpperCase()
}

function cleanHeading(line: string): string {
  return line.replace(/^#{1,4}\s*/, '').trim()
}

function isBulletLine(line: string): boolean {
  return /^[-*•]\s+/.test(line)
}

function cleanBullet(line: string): string {
  return line.replace(/^[-*•]\s+/, '').trim()
}

function isNumberedLine(line: string): boolean {
  return /^\d+[.)]\s+/.test(line)
}

function cleanNumbered(line: string): string {
  return line.replace(/^\d+[.)]\s+/, '').trim()
}

function looksLikeSectionHeading(line: string): boolean {
  if (line.length < 3 || line.length > 72) return false
  if (/^https?:\/\//i.test(line)) return false
  if (line.includes(' • ')) return false
  if (/[.!?]$/.test(line)) return false

  const words = line.split(/\s+/)
  if (words.length > 10) return false
  if (words.every((word) => word.length <= 2)) return false

  return true
}

const ALLOWED_HTML_TAGS = new Set([
  'a',
  'b',
  'blockquote',
  'br',
  'code',
  'em',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'hr',
  'i',
  'li',
  'ol',
  'p',
  'pre',
  'strong',
  'u',
  'ul',
])

function looksLikeHtml(value: string): boolean {
  return /<([a-z][a-z0-9]*)\b[^>]*>/i.test(value)
}

function sanitizeHtmlFragment(html: string): string {
  if (typeof window === 'undefined') {
    return ''
  }

  const parser = new DOMParser()
  const document = parser.parseFromString(html, 'text/html')
  const sanitized = Array.from(document.body.childNodes)
    .map((node) => sanitizeNode(node))
    .join('')
    .trim()

  return sanitized
}

function sanitizeNode(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) {
    return escapeHtml(node.textContent ?? '')
  }

  if (node.nodeType !== Node.ELEMENT_NODE) {
    return ''
  }

  const element = node as HTMLElement
  const tag = element.tagName.toLowerCase()
  const children = Array.from(element.childNodes)
    .map((child) => sanitizeNode(child))
    .join('')

  if (!ALLOWED_HTML_TAGS.has(tag)) {
    return children
  }

  if (tag === 'br' || tag === 'hr') {
    return `<${tag}>`
  }

  if (tag === 'a') {
    const href = sanitizeHref(element.getAttribute('href'))
    if (!href) {
      return children
    }
    return `<a href="${escapeAttribute(href)}" target="_blank" rel="noopener noreferrer">${children}</a>`
  }

  return `<${tag}>${children}</${tag}>`
}

function sanitizeHref(rawHref: string | null): string | null {
  if (!rawHref) return null
  const href = rawHref.trim()
  if (/^https?:\/\//i.test(href)) return href
  return null
}

function stripHtml(value: string): string {
  if (typeof window === 'undefined') return value
  const parser = new DOMParser()
  const document = parser.parseFromString(value, 'text/html')
  return document.body.textContent?.trim() ?? ''
}

function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

function escapeAttribute(value: string): string {
  return escapeHtml(value)
}

function parsePanelRectCandidate(value: unknown): PanelRect | null {
  if (!isRecord(value)) return null
  if (
    typeof value.x !== 'number' ||
    typeof value.y !== 'number' ||
    typeof value.width !== 'number' ||
    typeof value.height !== 'number'
  ) {
    return null
  }

  if (!Number.isFinite(value.x) || !Number.isFinite(value.y) || !Number.isFinite(value.width) || !Number.isFinite(value.height)) {
    return null
  }

  return {
    x: value.x,
    y: value.y,
    width: value.width,
    height: value.height,
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isWindowType(value: unknown): value is DashboardWindowType {
  return value === 'rss' || value === 'alerts' || value === 'notes' || value === 'daily_brief'
}

function isWindowSnap(value: unknown): value is DashboardWindowSnap {
  return (
    value === 'free' ||
    value === 'full' ||
    value === 'left' ||
    value === 'right' ||
    value === 'top_left' ||
    value === 'top_right' ||
    value === 'bottom_left' ||
    value === 'bottom_right'
  )
}

function resolveWindowRect(windowLayout: DashboardWindow, containerWidth: number, containerHeight: number): PanelRect {
  if (windowLayout.snap === 'free') {
    return normalizePanelRect(windowLayout.rect, containerWidth, containerHeight)
  }
  return getSnapRect(windowLayout.snap, containerWidth, containerHeight)
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

function normalizePanelRect(panel: PanelRect, containerWidth: number, containerHeight: number): PanelRect {
  const maxWidth = Math.max(WINDOW_MIN_WIDTH, containerWidth)
  const maxHeight = Math.max(WINDOW_MIN_HEIGHT, containerHeight)

  const width = clamp(panel.width, WINDOW_MIN_WIDTH, maxWidth)
  const height = clamp(panel.height, WINDOW_MIN_HEIGHT, maxHeight)

  const maxX = Math.max(0, maxWidth - width)
  const maxY = Math.max(0, maxHeight - height)

  return {
    x: clamp(panel.x, 0, maxX),
    y: clamp(panel.y, 0, maxY),
    width,
    height,
  }
}
