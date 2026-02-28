import { ChangeEvent, ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { useDebouncedValue } from '../hooks/useDebouncedValue'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { feedHealthDotClass, resolveFeedHealth } from '../utils/feedHealth'
import {
  AlertInterest,
  AlertMatchListResponse,
  Feed,
  ItemDetail,
  ItemListEntry,
  ItemListResponse,
  SavedView,
  Tag,
} from '../types/api'

type TimeRangeFilter = 'all' | '24h' | '7d' | '30d' | 'custom'
type ReadStatusFilter = 'all' | 'read' | 'unread'
type StarStatusFilter = 'all' | 'starred' | 'unstarred'
type TimeSort = 'published_at_desc' | 'published_at_asc' | 'first_seen_desc' | 'first_seen_asc'
type DashboardViewMode = 'expanded' | 'compact'
type AlertViewMode = 'expanded' | 'compact'
type DashboardWindowType = 'rss' | 'alerts' | 'notes'
type DashboardWindowSnap = 'free' | 'full' | 'left' | 'right' | 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right'
type WindowTimeFilter = {
  time_range: TimeRangeFilter
  custom_since_date: string
  custom_until_date: string
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
  }
}

const WINDOW_STORAGE_KEY = 'threatlens.dashboard.windows.v2'
const WINDOW_SEEN_STORAGE_KEY = 'threatlens.dashboard.window-seen.v1'
const DASHBOARD_VIEW_VERSION = 3
const WINDOW_MIN_WIDTH = 460
const WINDOW_MIN_HEIGHT = 320
const DRAG_EDGE_SNAP_THRESHOLD = 12
const DRAG_MIDLINE_SNAP_THRESHOLD = 8
const DASHBOARD_TIME_INHERIT_VALUE = '__dashboard_time__'
const HIDDEN_TAGS = new Set(['content_fetched', 'priority'])
const PAGE_SIZE_OPTIONS = [10, 25, 50, 100]
const MAX_VIEWS_IMPORT_FILE_BYTES = 2_000_000
const MAX_IMPORTED_VIEWS = 250
const SAVED_VIEW_THUMBNAIL_WIDTH = 148
const SAVED_VIEW_THUMBNAIL_HEIGHT = 96

const WINDOW_SNAP_OPTIONS: Array<{ value: DashboardWindowSnap; label: string }> = [
  { value: 'free', label: 'Free' },
  { value: 'full', label: 'Full' },
  { value: 'left', label: 'Left Half' },
  { value: 'right', label: 'Right Half' },
  { value: 'top_left', label: 'Top Left' },
  { value: 'top_right', label: 'Top Right' },
  { value: 'bottom_left', label: 'Bottom Left' },
  { value: 'bottom_right', label: 'Bottom Right' },
]

export function DashboardPage() {
  const queryClient = useQueryClient()
  const meQuery = useCurrentUser()
  const rootRef = useRef<HTMLDivElement | null>(null)

  const [selectedFeedIds, setSelectedFeedIds] = useState<string[]>([])
  const [selectedTags, setSelectedTags] = useState<string[]>([])
  const [q, setQ] = useState('')
  const [readStatus, setReadStatus] = useState<ReadStatusFilter>('all')
  const [starStatus, setStarStatus] = useState<StarStatusFilter>('all')
  const [viewMode, setViewMode] = useState<DashboardViewMode>('compact')
  const [dashboardTimeRange, setDashboardTimeRange] = useState<TimeRangeFilter>('all')
  const [dashboardCustomSinceDate, setDashboardCustomSinceDate] = useState('')
  const [dashboardCustomUntilDate, setDashboardCustomUntilDate] = useState('')
  const [sort, setSort] = useState<TimeSort>('published_at_desc')
  const [showAdvancedFilters, setShowAdvancedFilters] = useState(false)

  const [alertSelectedIds, setAlertSelectedIds] = useState<string[]>([])
  const [alertSelectedCategories, setAlertSelectedCategories] = useState<string[]>([])
  const [alertQ, setAlertQ] = useState('')
  const [alertViewMode, setAlertViewMode] = useState<AlertViewMode>('expanded')
  const [alertSort, setAlertSort] = useState<TimeSort>('published_at_desc')

  const [savedViewName, setSavedViewName] = useState('')
  const [activeSavedViewId, setActiveSavedViewId] = useState<string | null>(null)
  const [showManageViewsModal, setShowManageViewsModal] = useState(false)
  const [isImportingViews, setIsImportingViews] = useState(false)
  const [importViewsError, setImportViewsError] = useState('')
  const [importViewsResult, setImportViewsResult] = useState('')

  const [showAddWindowMenu, setShowAddWindowMenu] = useState(false)

  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState<number>(25)
  const [expandedItemId, setExpandedItemId] = useState<string>('')
  const [noteDraft, setNoteDraft] = useState('')

  const [alertPage, setAlertPage] = useState(1)
  const [alertPageSize, setAlertPageSize] = useState<number>(25)

  const [windows, setWindows] = useState<DashboardWindow[]>(() => loadDashboardWindows())
  const [windowSeenAt, setWindowSeenAt] = useState<Record<string, string>>(() => loadWindowSeenState())
  const [isWideLayout, setIsWideLayout] = useState<boolean>(typeof window !== 'undefined' ? window.innerWidth >= 1024 : true)

  const canManage = meQuery.data?.role === 'admin' || meQuery.data?.role === 'analyst'

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
    window.localStorage.setItem(WINDOW_STORAGE_KEY, JSON.stringify(windows))
  }, [windows])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }
    window.localStorage.setItem(WINDOW_SEEN_STORAGE_KEY, JSON.stringify(windowSeenAt))
  }, [windowSeenAt])

  useEffect(() => {
    setWindowSeenAt((current) => {
      const next: Record<string, string> = {}
      let changed = false
      const seed = new Date().toISOString()
      for (const layout of windows) {
        if (current[layout.id]) {
          next[layout.id] = current[layout.id]
          continue
        }
        if (layout.type !== 'notes') {
          next[layout.id] = seed
          changed = true
        }
      }
      if (!changed && Object.keys(next).length === Object.keys(current).length) {
        return current
      }
      return next
    })
  }, [windows])

  const debouncedQ = useDebouncedValue(q)
  const debouncedAlertQ = useDebouncedValue(alertQ)

  const feedIdsParam = useMemo(() => selectedFeedIds.slice().sort().join(','), [selectedFeedIds])
  const selectedTagsParam = useMemo(
    () =>
      selectedTags
        .filter((tagName) => !HIDDEN_TAGS.has(tagName))
        .slice()
        .sort()
        .join(','),
    [selectedTags],
  )

  const alertIdsParam = useMemo(() => alertSelectedIds.slice().sort().join(','), [alertSelectedIds])
  const alertCategoriesParam = useMemo(() => alertSelectedCategories.slice().sort().join(','), [alertSelectedCategories])

  const dashboardTimeFilter = useMemo<WindowTimeFilter>(
    () => ({
      time_range: dashboardTimeRange,
      custom_since_date: dashboardCustomSinceDate,
      custom_until_date: dashboardCustomUntilDate,
    }),
    [dashboardTimeRange, dashboardCustomSinceDate, dashboardCustomUntilDate],
  )

  const rssQueryTimeWindow = useMemo(() => {
    const filters = windows
      .filter((window) => window.type === 'rss')
      .map((window) => resolveWindowTimeFilter(window, dashboardTimeFilter))
    return combineTimeWindows(filters)
  }, [dashboardTimeFilter, windows])

  const alertQueryTimeWindow = useMemo(() => {
    const filters = windows
      .filter((window) => window.type === 'alerts')
      .map((window) => resolveWindowTimeFilter(window, dashboardTimeFilter))
    return combineTimeWindows(filters)
  }, [dashboardTimeFilter, windows])

  const feedsQuery = useQuery({
    queryKey: ['feeds'],
    queryFn: () => apiFetch<Feed[]>('/feeds'),
  })

  const viewsQuery = useQuery({
    queryKey: ['views'],
    queryFn: () => apiFetch<SavedView[]>('/views'),
  })

  const tagsQuery = useQuery({
    queryKey: ['tags'],
    queryFn: () => apiFetch<Tag[]>('/tags'),
  })

  const alertInterestsQuery = useQuery({
    queryKey: ['alerts', 'enabled'],
    queryFn: () => apiFetch<AlertInterest[]>('/alerts?include_disabled=false'),
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
    onSuccess: (view) => {
      setSavedViewName('')
      setActiveSavedViewId(view.id)
      queryClient.invalidateQueries({ queryKey: ['views'] })
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

  const updateRead = useMutation({
    mutationFn: (payload: { itemId: string; isRead: boolean }) =>
      apiFetch(`/items/${payload.itemId}/read`, {
        method: 'POST',
        body: JSON.stringify({ is_read: payload.isRead }),
      }),
    onSuccess: (_data, variables) => invalidateLists(queryClient, variables.itemId),
  })

  const updateStar = useMutation({
    mutationFn: (payload: { itemId: string; isStarred: boolean }) =>
      apiFetch(`/items/${payload.itemId}/star`, {
        method: 'POST',
        body: JSON.stringify({ is_starred: payload.isStarred }),
      }),
    onSuccess: (_data, variables) => invalidateLists(queryClient, variables.itemId),
  })

  const updateNote = useMutation({
    mutationFn: (payload: { itemId: string; note: string | null }) =>
      apiFetch(`/items/${payload.itemId}/note`, {
        method: 'POST',
        body: JSON.stringify({ note: payload.note }),
      }),
    onSuccess: (_data, variables) => invalidateLists(queryClient, variables.itemId),
  })

  const itemsQuery = useQuery({
    queryKey: [
      'items',
      feedIdsParam,
      selectedTagsParam,
      debouncedQ,
      readStatus,
      starStatus,
      rssQueryTimeWindow.sinceIso,
      rssQueryTimeWindow.untilIso,
      sort,
      page,
      pageSize,
    ],
    retry: 1,
    queryFn: () => {
      const params = new URLSearchParams()
      params.set('page', String(page))
      params.set('page_size', String(pageSize))
      params.set('sort', sort)

      if (feedIdsParam) params.set('feed_ids', feedIdsParam)
      if (selectedTagsParam) params.set('tags', selectedTagsParam)
      if (debouncedQ) params.set('q', debouncedQ)
      if (rssQueryTimeWindow.sinceIso) params.set('since', rssQueryTimeWindow.sinceIso)
      if (rssQueryTimeWindow.untilIso) params.set('until', rssQueryTimeWindow.untilIso)

      if (readStatus === 'read') params.set('is_read', 'true')
      if (readStatus === 'unread') params.set('is_read', 'false')
      if (starStatus === 'starred') params.set('is_starred', 'true')
      if (starStatus === 'unstarred') params.set('is_starred', 'false')

      return apiFetch<ItemListResponse>(`/items?${params.toString()}`)
    },
  })

  const alertMatchesQuery = useQuery({
    queryKey: [
      'alert-matches',
      alertIdsParam,
      alertCategoriesParam,
      debouncedAlertQ,
      alertQueryTimeWindow.sinceIso,
      alertQueryTimeWindow.untilIso,
      alertSort,
      alertPage,
      alertPageSize,
    ],
    queryFn: () => {
      const params = new URLSearchParams()
      params.set('page', String(alertPage))
      params.set('page_size', String(alertPageSize))
      params.set('sort', alertSort)

      if (alertIdsParam) params.set('alert_ids', alertIdsParam)
      if (alertCategoriesParam) params.set('categories', alertCategoriesParam)
      if (debouncedAlertQ) params.set('q', debouncedAlertQ)
      if (alertQueryTimeWindow.sinceIso) params.set('since', alertQueryTimeWindow.sinceIso)
      if (alertQueryTimeWindow.untilIso) params.set('until', alertQueryTimeWindow.untilIso)

      return apiFetch<AlertMatchListResponse>(`/alerts/matches?${params.toString()}`)
    },
  })

  useEffect(() => {
    const items = itemsQuery.data?.items ?? []
    if (!items.length) {
      setExpandedItemId('')
      return
    }

    if (!expandedItemId) {
      return
    }

    const selectedExists = items.some((item) => item.id === expandedItemId)
    if (!selectedExists) {
      setExpandedItemId('')
    }
  }, [itemsQuery.data?.items, expandedItemId])

  const detailQuery = useQuery({
    queryKey: ['item', expandedItemId],
    enabled: Boolean(expandedItemId),
    queryFn: () => apiFetch<ItemDetail>(`/items/${expandedItemId}`),
  })

  useEffect(() => {
    setNoteDraft(detailQuery.data?.state.note ?? '')
  }, [detailQuery.data?.state.note])

  const totalPages = useMemo(() => {
    const total = itemsQuery.data?.total ?? 0
    return Math.max(1, Math.ceil(total / pageSize))
  }, [itemsQuery.data?.total, pageSize])

  const alertTotalPages = useMemo(() => {
    const total = alertMatchesQuery.data?.total ?? 0
    return Math.max(1, Math.ceil(total / alertPageSize))
  }, [alertMatchesQuery.data?.total, alertPageSize])

  const availableAlertCategories = useMemo(() => {
    const categories = new Set<string>()
    for (const alert of alertInterestsQuery.data ?? []) {
      categories.add(alert.category)
    }
    return Array.from(categories).sort()
  }, [alertInterestsQuery.data])

  const handleToggleItem = (itemId: string, isRead: boolean) => {
    if (expandedItemId === itemId) {
      setExpandedItemId('')
      return
    }

    setExpandedItemId(itemId)
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

  const renameWindow = (windowId: string) => {
    const target = windows.find((window) => window.id === windowId)
    if (!target) return

    const nextTitle = window.prompt('Rename window', target.title)
    if (nextTitle === null) return

    const normalized = nextTitle.trim().slice(0, 80)
    if (!normalized) return

    setActiveSavedViewId(null)
    setWindows((current) =>
      current.map((window) => (window.id === windowId ? { ...window, title: normalized } : window)),
    )
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
      query: buildDashboardSavedViewState(
        {
          selected_feed_ids: selectedFeedIds,
          selected_tags: selectedTags,
          q,
          read_status: readStatus,
          star_status: starStatus,
          view_mode: viewMode,
          page_size: pageSize,
          time_range: dashboardTimeRange,
          custom_since_date: dashboardCustomSinceDate,
          custom_until_date: dashboardCustomUntilDate,
          sort,
        },
        {
          selected_alert_ids: alertSelectedIds,
          selected_categories: alertSelectedCategories,
          q: alertQ,
          view_mode: alertViewMode,
          page_size: alertPageSize,
          time_range: dashboardTimeRange,
          custom_since_date: dashboardCustomSinceDate,
          custom_until_date: dashboardCustomUntilDate,
          sort: alertSort,
        },
        windows,
        showAdvancedFilters,
      ),
    })
  }

  const applySavedView = (view: SavedView) => {
    const { width, height } = getWindowContainerDimensions(rootRef.current)
    const parsed = parseDashboardSavedView(view.query_json, width, height)

    setPage(1)
    setAlertPage(1)

    setSelectedFeedIds(parsed.rss_filters.selected_feed_ids)
    setSelectedTags(parsed.rss_filters.selected_tags)
    setQ(parsed.rss_filters.q)
    setReadStatus(parsed.rss_filters.read_status)
    setStarStatus(parsed.rss_filters.star_status)
    setViewMode(parsed.rss_filters.view_mode)
    setPageSize(parsed.rss_filters.page_size)
    const nextDashboardTimeRange =
      parsed.rss_filters.time_range !== 'all' || parsed.rss_filters.custom_since_date || parsed.rss_filters.custom_until_date
        ? parsed.rss_filters.time_range
        : parsed.alert_filters.time_range
    const nextDashboardCustomSinceDate =
      parsed.rss_filters.custom_since_date || parsed.alert_filters.custom_since_date || ''
    const nextDashboardCustomUntilDate =
      parsed.rss_filters.custom_until_date || parsed.alert_filters.custom_until_date || ''

    setDashboardTimeRange(nextDashboardTimeRange)
    setDashboardCustomSinceDate(nextDashboardCustomSinceDate)
    setDashboardCustomUntilDate(nextDashboardCustomUntilDate)
    setSort(parsed.rss_filters.sort)
    setShowAdvancedFilters(parsed.ui.show_advanced_filters)

    setAlertSelectedIds(parsed.alert_filters.selected_alert_ids)
    setAlertSelectedCategories(parsed.alert_filters.selected_categories)
    setAlertQ(parsed.alert_filters.q)
    setAlertViewMode(parsed.alert_filters.view_mode)
    setAlertPageSize(parsed.alert_filters.page_size)
    setAlertSort(parsed.alert_filters.sort)

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

  const clearRssSelection = () => {
    setPage(1)
    setActiveSavedViewId(null)
  }

  const clearAlertSelection = () => {
    setAlertPage(1)
    setActiveSavedViewId(null)
  }

  const markWindowSeen = (windowId: string) => {
    setWindowSeenAt((current) => ({
      ...current,
      [windowId]: new Date().toISOString(),
    }))
  }

  const updateDashboardTimeRange = (nextRange: TimeRangeFilter) => {
    setActiveSavedViewId(null)
    setPage(1)
    setAlertPage(1)
    setDashboardTimeRange(nextRange)
  }

  const updateDashboardCustomSinceDate = (nextDate: string) => {
    setActiveSavedViewId(null)
    setPage(1)
    setAlertPage(1)
    setDashboardCustomSinceDate(nextDate)
  }

  const updateDashboardCustomUntilDate = (nextDate: string) => {
    setActiveSavedViewId(null)
    setPage(1)
    setAlertPage(1)
    setDashboardCustomUntilDate(nextDate)
  }

  const updateWindowTimeRange = (windowId: string, nextValue: string) => {
    setActiveSavedViewId(null)
    setPage(1)
    setAlertPage(1)
    setWindows((current) =>
      current.map((window) => {
        if (window.id !== windowId || window.type === 'notes') {
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
    setPage(1)
    setAlertPage(1)
    setWindows((current) =>
      current.map((window) => {
        if (window.id !== windowId || window.type === 'notes') {
          return window
        }

        const base = window.time_override ?? dashboardTimeFilter
        return {
          ...window,
          time_override: {
            ...base,
            time_range: 'custom',
            [key]: value,
          },
        }
      }),
    )
  }

  const rssWindowCount = windows.filter((window) => window.type === 'rss').length
  const alertWindowCount = windows.filter((window) => window.type === 'alerts').length
  const notesWindowCount = windows.filter((window) => window.type === 'notes').length
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
      <div className="border-b border-slate/20 bg-white/85 px-3 py-2 text-[13px] shadow-sm dark:border-cyan-900/40 dark:bg-[#041612]/92">
        <div className="grid gap-2 xl:grid-cols-[160px_1fr_auto_auto_auto_auto_auto] xl:items-center">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate dark:text-slate-300">Dashboard Views</p>
          <input
            value={savedViewName}
            onChange={(event) => setSavedViewName(event.target.value)}
            placeholder="Save dashboard view as..."
            className="w-full rounded border border-slate/25 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#041612]"
          />
          <button
            type="button"
            className="w-full rounded bg-ink px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50 sm:w-auto dark:bg-cyan dark:text-slate-950"
            onClick={saveCurrentView}
            disabled={saveView.isPending || !savedViewName.trim()}
          >
            Save View
          </button>
          <button
            type="button"
            className="w-full rounded border border-slate/25 px-3 py-1.5 text-xs sm:w-auto dark:border-cyan-900/40"
            onClick={() => setShowManageViewsModal(true)}
          >
            Manage Views
          </button>
          <div className="flex flex-col gap-1.5 sm:flex-row sm:flex-wrap sm:items-center">
            <select
              className="w-full rounded border border-slate/25 bg-white px-2 py-1.5 text-sm sm:w-auto dark:border-cyan-900/40 dark:bg-[#041612]"
              value={dashboardTimeRange}
              onChange={(event) => updateDashboardTimeRange(event.target.value as TimeRangeFilter)}
            >
              <option value="all">All time</option>
              <option value="24h">Last 24h</option>
              <option value="7d">Last 7d</option>
              <option value="30d">Last 30d</option>
              <option value="custom">Custom</option>
            </select>
            <input
              type="date"
              className="w-full rounded border border-slate/25 bg-white px-2 py-1.5 text-sm disabled:opacity-50 sm:w-auto dark:border-cyan-900/40 dark:bg-[#041612]"
              value={dashboardCustomSinceDate}
              onChange={(event) => updateDashboardCustomSinceDate(event.target.value)}
              disabled={dashboardTimeRange !== 'custom'}
            />
            <input
              type="date"
              className="w-full rounded border border-slate/25 bg-white px-2 py-1.5 text-sm disabled:opacity-50 sm:w-auto dark:border-cyan-900/40 dark:bg-[#041612]"
              value={dashboardCustomUntilDate}
              onChange={(event) => updateDashboardCustomUntilDate(event.target.value)}
              disabled={dashboardTimeRange !== 'custom'}
            />
          </div>
          <div className="relative">
            <button
              type="button"
              className="w-full rounded border border-slate/25 px-3 py-1.5 text-xs sm:w-auto dark:border-cyan-900/40"
              onClick={() => setShowAddWindowMenu((current) => !current)}
            >
              Add Window
            </button>
            {showAddWindowMenu && (
              <div className="absolute right-0 top-[calc(100%+6px)] z-30 w-56 max-w-[calc(100vw-2rem)] rounded border border-slate/20 bg-white p-1 shadow-lg dark:border-cyan-900/40 dark:bg-[#041612]">
                <button
                  type="button"
                  className="w-full rounded px-2 py-1.5 text-left text-xs hover:bg-cyan/10"
                  onClick={() => addWindow('rss')}
                >
                  RSS Feed Window ({rssWindowCount})
                </button>
                <button
                  type="button"
                  className="w-full rounded px-2 py-1.5 text-left text-xs hover:bg-cyan/10"
                  onClick={() => addWindow('alerts')}
                >
                  Alerts Window ({alertWindowCount})
                </button>
                <button
                  type="button"
                  className="w-full rounded px-2 py-1.5 text-left text-xs hover:bg-cyan/10"
                  onClick={() => addWindow('notes')}
                >
                  Notes Window ({notesWindowCount})
                </button>
              </div>
            )}
          </div>
          <select
            className="w-full rounded border border-slate/25 bg-white px-2 py-1.5 text-sm xl:w-auto dark:border-cyan-900/40 dark:bg-[#041612]"
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
            <option value="">Load Dashboard View</option>
            {viewsQuery.data?.map((view) => (
              <option key={view.id} value={view.id}>
                {view.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div
        ref={rootRef}
        className={`${isWideLayout ? 'relative h-[calc(100vh-138px)] min-h-[620px] w-full overflow-hidden' : 'space-y-3 p-3'}`}
      >
        {windows.map((windowLayout) => {
          const resolvedRect =
            windowLayout.snap === 'free'
              ? normalizePanelRect(windowLayout.rect, containerDimensions.width, containerDimensions.height)
              : getSnapRect(windowLayout.snap, containerDimensions.width, containerDimensions.height)
          const effectiveWindowTimeFilter = resolveWindowTimeFilter(windowLayout, dashboardTimeFilter)
          const rssWindowItems =
            windowLayout.type === 'rss'
              ? filterEntriesByTimeWindow(itemsQuery.data?.items ?? [], effectiveWindowTimeFilter)
              : []
          const alertWindowItems =
            windowLayout.type === 'alerts'
              ? filterEntriesByTimeWindow(alertMatchesQuery.data?.items ?? [], effectiveWindowTimeFilter)
              : []
          const lastSeenAtIso = windowSeenAt[windowLayout.id] ?? ''
          const rssChangedCount = windowLayout.type === 'rss' ? countNewEntriesSince(rssWindowItems, lastSeenAtIso) : 0
          const alertChangedCount = windowLayout.type === 'alerts' ? countNewEntriesSince(alertWindowItems, lastSeenAtIso) : 0

          const snapped = isWideLayout && windowLayout.snap !== 'free'
          const sectionClass = `${isWideLayout ? 'absolute' : 'relative'} flex flex-col overflow-hidden border border-slate/20 bg-white/85 text-[13px] dark:border-cyan-900/40 dark:bg-[#041612]/96 ${
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
                className="flex flex-col gap-2 border-b border-slate/20 px-3 py-2 sm:flex-row sm:items-center sm:justify-between dark:border-cyan-900/40"
                onMouseDown={(event) => startWindowDrag(event, windowLayout.id)}
              >
                <div>
                  <h2 className="font-display text-lg leading-tight">{windowLayout.title}</h2>
                  {windowLayout.type === 'rss' ? (
                    <p className="text-xs text-slate dark:text-slate-300">RSS feed triage with saved filters and notes.</p>
                  ) : windowLayout.type === 'alerts' ? (
                    <p className="text-xs text-slate dark:text-slate-300">Keyword-driven matching against your configured alert interests.</p>
                  ) : (
                    <p className="text-xs text-slate dark:text-slate-300">Scratch notes persisted in dashboard layout and saved views.</p>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-2 sm:justify-end" onMouseDown={(event) => event.stopPropagation()}>
                  <span className="rounded border border-slate/25 px-2 py-0.5 text-[11px] text-slate dark:border-cyan-900/40 dark:text-slate-300">
                    {windowLayout.type === 'rss'
                      ? `${rssWindowItems.length} shown`
                      : windowLayout.type === 'alerts'
                        ? `${alertWindowItems.length} shown`
                        : 'Scratch Pad'}
                  </span>
                  {windowLayout.type === 'rss' && rssChangedCount > 0 && (
                    <span className="rounded border border-cyan/40 bg-cyan/20 px-2 py-0.5 text-[11px] font-semibold text-cyan">
                      +{rssChangedCount} new
                    </span>
                  )}
                  {windowLayout.type === 'alerts' && alertChangedCount > 0 && (
                    <span className="rounded border border-cyan/40 bg-cyan/20 px-2 py-0.5 text-[11px] font-semibold text-cyan">
                      +{alertChangedCount} new
                    </span>
                  )}
                  {windowLayout.type !== 'notes' && (
                    <button
                      type="button"
                      className="rounded border border-slate/25 px-2 py-1 text-xs dark:border-cyan-900/40"
                      onClick={() => markWindowSeen(windowLayout.id)}
                    >
                      Mark Seen
                    </button>
                  )}
                  {windowLayout.type !== 'notes' && (
                    <button
                      type="button"
                      className="rounded border border-slate/25 px-2 py-1 text-xs dark:border-cyan-900/40"
                      onClick={() => toggleWindowControls(windowLayout.id)}
                    >
                      {windowLayout.controls_collapsed ? 'Expand Filters' : 'Collapse Filters'}
                    </button>
                  )}
                  <button
                    type="button"
                    className="rounded border border-slate/25 px-2 py-1 text-xs dark:border-cyan-900/40"
                    onClick={() => renameWindow(windowLayout.id)}
                  >
                    Rename
                  </button>
                  <select
                    className="hidden rounded border border-slate/25 bg-white px-2 py-1 text-xs sm:block dark:border-cyan-900/40 dark:bg-[#041612]"
                    value={windowLayout.snap}
                    onChange={(event) => setWindowSnap(windowLayout.id, event.target.value as DashboardWindowSnap)}
                  >
                    {WINDOW_SNAP_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="rounded border border-slate/25 px-2 py-1 text-xs text-red-600 disabled:opacity-40 dark:border-cyan-900/40"
                    disabled={windows.length <= 1}
                    onClick={() => removeWindow(windowLayout.id)}
                  >
                    Close
                  </button>
                </div>
              </div>

              {windowLayout.type === 'rss' ? (
                <>
                  {!windowLayout.controls_collapsed && (
                    <div className="border-b border-slate/20 px-3 py-1.5 dark:border-cyan-900/40">
                      <div className="flex items-center gap-1.5 overflow-x-auto pb-0.5">
                      <button
                        type="button"
                        className={`rounded-full border px-3 py-1 text-xs font-semibold ${
                          selectedFeedIds.length === 0
                            ? 'border-cyan bg-cyan/15 text-cyan dark:bg-cyan-950/55'
                            : 'border-slate/25 dark:border-cyan-900/40'
                        }`}
                        onClick={() => {
                          clearRssSelection()
                          setSelectedFeedIds([])
                        }}
                      >
                        All
                      </button>
                      {feedsQuery.data?.map((feed) => {
                        const active = selectedFeedIds.includes(feed.id)
                        const health = resolveFeedHealth(feed)
                        return (
                          <button
                            key={feed.id}
                            type="button"
                            className={`whitespace-nowrap rounded-full border px-3 py-1 text-xs font-semibold ${
                              active ? 'border-cyan bg-cyan/15 text-cyan dark:bg-cyan-950/55' : 'border-slate/25 dark:border-cyan-900/40'
                            }`}
                            onClick={() => {
                              clearRssSelection()
                              setSelectedFeedIds((current) =>
                                current.includes(feed.id) ? current.filter((id) => id !== feed.id) : [...current, feed.id],
                              )
                            }}
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
                        className={`rounded-full border px-3 py-1 text-xs font-semibold ${
                          selectedTags.length === 0
                            ? 'border-violet-500 bg-violet-500/15 text-violet-700 dark:bg-violet-950/45 dark:text-violet-300'
                            : 'border-slate/25 dark:border-cyan-900/40'
                        }`}
                        onClick={() => {
                          clearRssSelection()
                          setSelectedTags([])
                        }}
                      >
                        All
                      </button>
                      {tagsQuery.data
                        ?.filter((tag) => !HIDDEN_TAGS.has(tag.name))
                        .map((tag) => {
                          const active = selectedTags.includes(tag.name)
                          return (
                            <button
                              key={tag.id}
                              type="button"
                              className={`whitespace-nowrap rounded-full border px-3 py-1 text-xs font-semibold ${
                                active
                                  ? 'border-violet-500 bg-violet-500/15 text-violet-700 dark:bg-violet-950/45 dark:text-violet-300'
                                  : 'border-slate/25 dark:border-cyan-900/40'
                              }`}
                              onClick={() => {
                                clearRssSelection()
                                setSelectedTags((current) =>
                                  current.includes(tag.name)
                                    ? current.filter((entry) => entry !== tag.name)
                                    : [...current, tag.name],
                                )
                              }}
                            >
                              #{tag.name}
                            </button>
                          )
                        })}
                      </div>
                      {tagsQuery.isError && <p className="mt-0.5 text-xs text-red-600">Failed to load tags.</p>}

                      <div className="mt-1 flex flex-wrap items-center gap-1.5">
                      <input
                        value={q}
                        onChange={(event) => {
                          clearRssSelection()
                          setQ(event.target.value)
                        }}
                        placeholder="Search title, summary, URL"
                        className="w-full rounded border border-slate/25 bg-white px-2 py-1.5 text-sm sm:min-w-64 sm:flex-1 dark:border-cyan-900/40 dark:bg-[#072019]"
                      />
                      <select
                        className="w-full rounded border border-slate/25 bg-white px-2 py-1.5 text-sm sm:w-auto dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={windowLayout.time_override?.time_range ?? DASHBOARD_TIME_INHERIT_VALUE}
                        onChange={(event) => updateWindowTimeRange(windowLayout.id, event.target.value)}
                      >
                        <option value={DASHBOARD_TIME_INHERIT_VALUE}>Dashboard Time</option>
                        <option value="all">All time</option>
                        <option value="24h">24h</option>
                        <option value="7d">7d</option>
                        <option value="30d">30d</option>
                        <option value="custom">Custom</option>
                      </select>
                      <select
                        className="w-full rounded border border-slate/25 bg-white px-2 py-1.5 text-sm sm:w-auto dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={sort}
                        onChange={(event) => {
                          clearRssSelection()
                          setSort(event.target.value as TimeSort)
                        }}
                      >
                        <option value="published_at_desc">Newest</option>
                        <option value="published_at_asc">Oldest</option>
                        <option value="first_seen_desc">Seen newest</option>
                        <option value="first_seen_asc">Seen oldest</option>
                      </select>
                      <div className="flex w-full rounded border border-slate/25 p-0.5 sm:w-auto dark:border-cyan-900/40">
                        <button
                          type="button"
                          className={`flex-1 rounded px-2 py-1 text-xs font-semibold sm:flex-none ${viewMode === 'expanded' ? 'bg-cyan/15 text-cyan' : ''}`}
                          onClick={() => {
                            clearRssSelection()
                            setViewMode('expanded')
                          }}
                        >
                          Expanded
                        </button>
                        <button
                          type="button"
                          className={`flex-1 rounded px-2 py-1 text-xs font-semibold sm:flex-none ${viewMode === 'compact' ? 'bg-cyan/15 text-cyan' : ''}`}
                          onClick={() => {
                            clearRssSelection()
                            setViewMode('compact')
                          }}
                        >
                          Compact
                        </button>
                      </div>
                      <button
                        type="button"
                        className="w-full rounded border border-slate/25 px-3 py-1.5 text-xs font-semibold sm:w-auto dark:border-cyan-900/40"
                        onClick={() => {
                          setActiveSavedViewId(null)
                          setShowAdvancedFilters((current) => !current)
                        }}
                      >
                        {showAdvancedFilters ? 'Hide Filters' : 'More Filters'}
                      </button>
                      </div>

                      {showAdvancedFilters && (
                        <div className="mt-1 grid gap-2 rounded border border-slate/20 bg-sand/40 p-2 dark:border-cyan-900/40 dark:bg-[#072019]/70 md:grid-cols-2 lg:grid-cols-3">
                        <select
                          className="rounded border border-slate/25 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#041612]"
                          value={readStatus}
                          onChange={(event) => {
                            clearRssSelection()
                            setReadStatus(event.target.value as ReadStatusFilter)
                          }}
                        >
                          <option value="all">Read: All</option>
                          <option value="unread">Read: Unread</option>
                          <option value="read">Read: Read</option>
                        </select>
                        <select
                          className="rounded border border-slate/25 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#041612]"
                          value={starStatus}
                          onChange={(event) => {
                            clearRssSelection()
                            setStarStatus(event.target.value as StarStatusFilter)
                          }}
                        >
                          <option value="all">Stars: All</option>
                          <option value="starred">Stars: Starred</option>
                          <option value="unstarred">Stars: Unstarred</option>
                        </select>
                        <div className="flex flex-col gap-2 sm:flex-row">
                          <input
                            type="date"
                            className="w-full rounded border border-slate/25 bg-white px-2 py-1.5 text-sm disabled:opacity-50 dark:border-cyan-900/40 dark:bg-[#041612]"
                            value={effectiveWindowTimeFilter.custom_since_date}
                            onChange={(event) => updateWindowCustomTimeDate(windowLayout.id, 'custom_since_date', event.target.value)}
                            disabled={effectiveWindowTimeFilter.time_range !== 'custom'}
                          />
                          <input
                            type="date"
                            className="w-full rounded border border-slate/25 bg-white px-2 py-1.5 text-sm disabled:opacity-50 dark:border-cyan-900/40 dark:bg-[#041612]"
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
                        const expanded = expandedItemId === item.id
                        const detail = expanded ? detailQuery.data : null
                        const compact = viewMode === 'compact'
                        const density = computeInformationDensity(item)

                        return (
                          <article
                            key={item.id}
                            className={`rounded border ${compact ? 'p-2' : 'p-3'} transition ${
                              expanded ? 'border-cyan bg-cyan/5 dark:border-cyan-700/50 dark:bg-cyan-950/25' : 'border-slate/20 dark:border-cyan-900/40'
                            } ${item.is_read ? 'opacity-85' : ''}`}
                          >
                            <div className="w-full text-left">
                              <div className="flex items-start justify-between gap-3">
                                <h3 className={`${compact ? 'text-[14px]' : 'text-[15px]'} font-semibold leading-snug`}>
                                  <a
                                    href={item.canonical_url || item.url}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="hover:text-cyan hover:underline"
                                    onClick={(event) => event.stopPropagation()}
                                  >
                                    {item.title}
                                  </a>
                                </h3>
                                <div className="flex shrink-0 items-center gap-2">
                                  <span className="rounded bg-teal-100 px-1.5 py-0.5 text-[11px] text-teal-800 dark:bg-teal-900/35 dark:text-teal-200">
                                    {density.label} {density.score}
                                  </span>
                                  <span className="text-xs text-slate dark:text-slate-300">{item.feed_name}</span>
                                </div>
                              </div>
                              <button
                                type="button"
                                className="mt-1 w-full text-left"
                                onClick={() => handleToggleItem(item.id, item.is_read)}
                              >
                                <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate dark:text-slate-300">
                                  <span>Published {formatPublishedAt(item.published_at)}</span>
                                  {item.status !== 'content_fetched' && (
                                    <span className="rounded bg-slate/15 px-1.5 py-0.5 dark:bg-[#0b1a33]">{item.status}</span>
                                  )}
                                  {!item.is_read && <span className="rounded bg-cyan/20 px-1.5 py-0.5 text-cyan">Unread</span>}
                                  {item.is_starred && <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-700">Starred</span>}
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
                                {detailQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading article content...</p>}
                                {detailQuery.isError && <p className="text-sm text-red-600">Failed to load item details.</p>}

                                {detail && detail.id === item.id && (
                                  <>
                                    <div className="flex flex-wrap items-center gap-2">
                                      <a
                                        className="rounded border border-slate/30 px-2 py-1 text-xs hover:border-cyan hover:text-cyan dark:border-cyan-900/40"
                                        href={detail.article?.final_url || detail.url}
                                        target="_blank"
                                        rel="noreferrer"
                                      >
                                        Open Source Link
                                      </a>
                                      <button
                                        className="rounded border border-slate/30 px-2 py-1 text-xs dark:border-cyan-900/40"
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
                                        className="rounded border border-slate/30 px-2 py-1 text-xs dark:border-cyan-900/40"
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

                                    <div className="mt-3 rounded border border-slate/20 bg-sand/50 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/90">
                                      <p className="text-xs font-bold uppercase tracking-wide text-slate dark:text-slate-300">RSS Summary</p>
                                      {detail.classification && (
                                        <p className="mt-1 text-xs text-slate dark:text-slate-300">
                                          Classification:{' '}
                                          <span className="font-semibold">
                                            {formatClassificationLabel(detail.classification.primary_category)}
                                          </span>{' '}
                                          ({Math.round(detail.classification.confidence * 100)}% confidence)
                                        </p>
                                      )}
                                      <div className="rss-reader mt-2 rounded bg-white/70 p-3 dark:bg-[#041612]/80">
                                        {renderRichContent(detail.summary || 'No summary.', detail.id, 'summary')}
                                      </div>
                                    </div>

                                    <div className="mt-3 rounded border border-slate/20 bg-white p-3 dark:border-cyan-900/40 dark:bg-[#072019]/90">
                                      <p className="text-xs font-bold uppercase tracking-wide text-slate dark:text-slate-300">Full Article</p>
                                      {detail.article?.text ? (
                                        <div className="rss-reader mt-2 rounded bg-white/70 p-3 dark:bg-[#041612]/80">
                                          {renderRichContent(detail.article.text, detail.id, 'article')}
                                        </div>
                                      ) : (
                                        <p className="mt-2 text-sm text-slate dark:text-slate-300">No extracted article text available yet.</p>
                                      )}
                                      {detail.article?.error && (
                                        <p className="mt-2 text-sm text-red-600">Extraction error: {detail.article.error}</p>
                                      )}
                                    </div>

                                    <div className="mt-3 rounded border border-slate/20 bg-white p-3 dark:border-cyan-900/40 dark:bg-[#072019]/90">
                                      <label className="text-xs font-semibold uppercase tracking-wide text-slate dark:text-slate-300">Notes</label>
                                      <textarea
                                        className="mt-1 h-20 w-full rounded border border-slate/30 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                                        value={noteDraft}
                                        onChange={(event) => setNoteDraft(event.target.value)}
                                        disabled={!canManage}
                                      />
                                      <div className="mt-2 flex items-center gap-2">
                                        <button
                                          className="rounded bg-ink px-2.5 py-1 text-xs font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-slate-950"
                                          onClick={() => updateNote.mutate({ itemId: detail.id, note: noteDraft || null })}
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

                      {itemsQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading items...</p>}
                      {itemsQuery.isError && (
                        <p className="text-sm text-red-600">
                          Failed to load items. {(itemsQuery.error as Error | undefined)?.message ?? ''}
                        </p>
                      )}
                      {!itemsQuery.isLoading && !rssWindowItems.length && (
                        <p className="text-sm text-slate dark:text-slate-300">No items match current filters.</p>
                      )}
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate/20 px-3 py-2 text-xs dark:border-cyan-900/40">
                    <button
                      className="rounded border border-slate/30 px-2 py-1 disabled:opacity-50 dark:border-cyan-900/40"
                      disabled={page <= 1}
                      onClick={() => setPage((current) => current - 1)}
                    >
                      Prev
                    </button>
                    <span className="w-full text-center sm:w-auto">
                      Page {page} / {totalPages}
                    </span>
                    <div className="ml-auto flex items-center gap-2">
                      <label className="text-xs text-slate dark:text-slate-300">Per page</label>
                      <select
                        className="rounded border border-slate/30 bg-white px-2 py-1 text-xs dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={pageSize}
                        onChange={(event) => {
                          setActiveSavedViewId(null)
                          setPage(1)
                          setPageSize(Number(event.target.value))
                        }}
                      >
                        {PAGE_SIZE_OPTIONS.map((option) => (
                          <option key={option} value={option}>
                            {option}
                          </option>
                        ))}
                      </select>
                      <button
                        className="rounded border border-slate/30 px-2 py-1 disabled:opacity-50 dark:border-cyan-900/40"
                        disabled={page >= totalPages}
                        onClick={() => setPage((current) => current + 1)}
                      >
                        Next
                      </button>
                    </div>
                  </div>
                </>
              ) : windowLayout.type === 'alerts' ? (
                <>
                  {!windowLayout.controls_collapsed && (
                    <div className="border-b border-slate/20 px-3 py-1.5 dark:border-cyan-900/40">
                    <div className="flex items-center gap-2 overflow-x-auto pb-1">
                      <button
                        type="button"
                        className={`rounded-full border px-3 py-1 text-xs font-semibold ${
                          alertSelectedCategories.length === 0
                            ? 'border-cyan bg-cyan/15 text-cyan dark:bg-cyan-950/55'
                            : 'border-slate/25 dark:border-cyan-900/40'
                        }`}
                        onClick={() => {
                          clearAlertSelection()
                          setAlertSelectedCategories([])
                        }}
                      >
                        All Categories
                      </button>
                      {availableAlertCategories.map((category) => {
                        const active = alertSelectedCategories.includes(category)
                        return (
                          <button
                            key={category}
                            type="button"
                            className={`whitespace-nowrap rounded-full border px-3 py-1 text-xs font-semibold ${
                              active ? 'border-cyan bg-cyan/15 text-cyan dark:bg-cyan-950/55' : 'border-slate/25 dark:border-cyan-900/40'
                            }`}
                            onClick={() => {
                              clearAlertSelection()
                              setAlertSelectedCategories((current) =>
                                current.includes(category)
                                  ? current.filter((entry) => entry !== category)
                                  : [...current, category],
                              )
                            }}
                          >
                            {formatClassificationLabel(category)}
                          </button>
                        )
                      })}
                    </div>

                    <div className="mt-2 flex items-center gap-2 overflow-x-auto pb-1">
                      <button
                        type="button"
                        className={`rounded-full border px-3 py-1 text-xs font-semibold ${
                          alertSelectedIds.length === 0
                            ? 'border-violet-500 bg-violet-500/15 text-violet-700 dark:bg-violet-950/45 dark:text-violet-300'
                            : 'border-slate/25 dark:border-cyan-900/40'
                        }`}
                        onClick={() => {
                          clearAlertSelection()
                          setAlertSelectedIds([])
                        }}
                      >
                        All Interests
                      </button>
                      {alertInterestsQuery.data?.map((interest) => {
                        const active = alertSelectedIds.includes(interest.id)
                        return (
                          <button
                            key={interest.id}
                            type="button"
                            className={`whitespace-nowrap rounded-full border px-3 py-1 text-xs font-semibold ${
                              active
                                ? 'border-violet-500 bg-violet-500/15 text-violet-700 dark:bg-violet-950/45 dark:text-violet-300'
                                : 'border-slate/25 dark:border-cyan-900/40'
                            }`}
                            onClick={() => {
                              clearAlertSelection()
                              setAlertSelectedIds((current) =>
                                current.includes(interest.id)
                                  ? current.filter((entry) => entry !== interest.id)
                                  : [...current, interest.id],
                              )
                            }}
                          >
                            {interest.name}
                          </button>
                        )
                      })}
                    </div>

                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <input
                        value={alertQ}
                        onChange={(event) => {
                          clearAlertSelection()
                          setAlertQ(event.target.value)
                        }}
                        placeholder="Search matched alert items"
                        className="w-full rounded border border-slate/25 bg-white px-2 py-1.5 text-sm sm:min-w-64 sm:flex-1 dark:border-cyan-900/40 dark:bg-[#072019]"
                      />
                      <select
                        className="w-full rounded border border-slate/25 bg-white px-2 py-1.5 text-sm sm:w-auto dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={windowLayout.time_override?.time_range ?? DASHBOARD_TIME_INHERIT_VALUE}
                        onChange={(event) => updateWindowTimeRange(windowLayout.id, event.target.value)}
                      >
                        <option value={DASHBOARD_TIME_INHERIT_VALUE}>Dashboard Time</option>
                        <option value="all">All time</option>
                        <option value="24h">24h</option>
                        <option value="7d">7d</option>
                        <option value="30d">30d</option>
                        <option value="custom">Custom</option>
                      </select>
                      <select
                        className="w-full rounded border border-slate/25 bg-white px-2 py-1.5 text-sm sm:w-auto dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={alertSort}
                        onChange={(event) => {
                          clearAlertSelection()
                          setAlertSort(event.target.value as TimeSort)
                        }}
                      >
                        <option value="published_at_desc">Newest</option>
                        <option value="published_at_asc">Oldest</option>
                        <option value="first_seen_desc">Seen newest</option>
                        <option value="first_seen_asc">Seen oldest</option>
                      </select>
                      <div className="flex w-full rounded border border-slate/25 p-0.5 sm:w-auto dark:border-cyan-900/40">
                        <button
                          type="button"
                          className={`flex-1 rounded px-2 py-1 text-xs font-semibold sm:flex-none ${alertViewMode === 'expanded' ? 'bg-cyan/15 text-cyan' : ''}`}
                          onClick={() => {
                            clearAlertSelection()
                            setAlertViewMode('expanded')
                          }}
                        >
                          Expanded
                        </button>
                        <button
                          type="button"
                          className={`flex-1 rounded px-2 py-1 text-xs font-semibold sm:flex-none ${alertViewMode === 'compact' ? 'bg-cyan/15 text-cyan' : ''}`}
                          onClick={() => {
                            clearAlertSelection()
                            setAlertViewMode('compact')
                          }}
                        >
                          Compact
                        </button>
                      </div>
                      <input
                        type="date"
                        className="w-full rounded border border-slate/25 bg-white px-2 py-1.5 text-sm disabled:opacity-50 sm:w-auto dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={effectiveWindowTimeFilter.custom_since_date}
                        onChange={(event) => updateWindowCustomTimeDate(windowLayout.id, 'custom_since_date', event.target.value)}
                        disabled={effectiveWindowTimeFilter.time_range !== 'custom'}
                      />
                      <input
                        type="date"
                        className="w-full rounded border border-slate/25 bg-white px-2 py-1.5 text-sm disabled:opacity-50 sm:w-auto dark:border-cyan-900/40 dark:bg-[#072019]"
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
                        const compactAlerts = alertViewMode === 'compact'
                        return (
                        <article key={item.id} className={`rounded border border-slate/20 ${compactAlerts ? 'p-2' : 'p-3'} dark:border-cyan-900/40`}>
                          <div className="flex items-start justify-between gap-2">
                            <div>
                              <h3 className={`font-semibold leading-snug ${compactAlerts ? 'text-[13px]' : ''}`}>
                                <a
                                  href={item.canonical_url || item.url}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="hover:text-cyan hover:underline"
                                >
                                  {item.title}
                                </a>
                              </h3>
                              <p className={`text-xs text-slate dark:text-slate-300 ${compactAlerts ? 'mt-0.5' : 'mt-1'}`}>
                                {item.feed_name} • Published {formatPublishedAt(item.published_at)}
                              </p>
                            </div>
                            <span className="rounded border border-slate/25 px-2 py-0.5 text-[11px] dark:border-cyan-900/40">
                              {item.matches.length} alerts
                            </span>
                          </div>
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {item.matches.map((match) => (
                              <span
                                key={`${item.id}-${match.alert_id}`}
                                className="rounded-full border border-amber-300/60 bg-amber-100/70 px-2 py-0.5 text-[11px] text-amber-800 dark:border-amber-800/40 dark:bg-amber-950/30 dark:text-amber-200"
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

                      {alertMatchesQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading alert matches...</p>}
                      {alertMatchesQuery.isError && (
                        <p className="text-sm text-red-600">
                          Failed to load alert matches. {(alertMatchesQuery.error as Error | undefined)?.message ?? ''}
                        </p>
                      )}
                      {!alertMatchesQuery.isLoading && !alertWindowItems.length && (
                        <p className="text-sm text-slate dark:text-slate-300">No items matched current alert filters.</p>
                      )}
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate/20 px-3 py-2 text-xs dark:border-cyan-900/40">
                    <button
                      className="rounded border border-slate/30 px-2 py-1 disabled:opacity-50 dark:border-cyan-900/40"
                      disabled={alertPage <= 1}
                      onClick={() => setAlertPage((current) => current - 1)}
                    >
                      Prev
                    </button>
                    <span className="w-full text-center sm:w-auto">
                      Page {alertPage} / {alertTotalPages}
                    </span>
                    <div className="ml-auto flex items-center gap-2">
                      <label className="text-xs text-slate dark:text-slate-300">Per page</label>
                      <select
                        className="rounded border border-slate/30 bg-white px-2 py-1 text-xs dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={alertPageSize}
                        onChange={(event) => {
                          setActiveSavedViewId(null)
                          setAlertPage(1)
                          setAlertPageSize(Number(event.target.value))
                        }}
                      >
                        {PAGE_SIZE_OPTIONS.map((option) => (
                          <option key={option} value={option}>
                            {option}
                          </option>
                        ))}
                      </select>
                      <button
                        className="rounded border border-slate/30 px-2 py-1 disabled:opacity-50 dark:border-cyan-900/40"
                        disabled={alertPage >= alertTotalPages}
                        onClick={() => setAlertPage((current) => current + 1)}
                      >
                        Next
                      </button>
                    </div>
                  </div>
                </>
              ) : (
                <div className="flex flex-1 flex-col p-3">
                  <label className="text-xs font-semibold uppercase tracking-wide text-slate dark:text-slate-300">Scratch Notes</label>
                  <textarea
                    className="mt-2 h-full min-h-[180px] w-full flex-1 rounded border border-slate/25 bg-white px-3 py-2 text-sm leading-6 dark:border-cyan-900/40 dark:bg-[#072019]"
                    placeholder="Use this space for quick notes, pivots, and hypotheses..."
                    value={windowLayout.scratch_note}
                    onChange={(event) => updateWindowScratchNote(windowLayout.id, event.target.value)}
                  />
                  <p className="mt-2 text-xs text-slate dark:text-slate-300">Saved in this dashboard window and in saved views.</p>
                </div>
              )}

              {isWideLayout && windowLayout.snap === 'free' && (
                <button
                  type="button"
                  className="absolute bottom-1 right-1 h-4 w-4 cursor-se-resize rounded border border-slate/30 bg-white/85 dark:border-cyan-900/40 dark:bg-[#0b2a23]"
                  aria-label="Resize dashboard window"
                  onMouseDown={(event) => startWindowResize(event, windowLayout.id)}
                />
              )}
            </section>
          )
        })}
      </div>

      {showManageViewsModal && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/55 p-3">
          <div className="max-h-[92vh] w-full max-w-3xl overflow-auto rounded-xl border border-slate/20 bg-white p-4 dark:border-cyan-900/40 dark:bg-[#041612]">
            <div className="flex items-center justify-between gap-3">
              <h3 className="font-display text-xl">Manage Saved Views</h3>
              <button
                type="button"
                className="rounded border border-slate/25 px-2 py-1 text-xs dark:border-cyan-900/40"
                onClick={() => setShowManageViewsModal(false)}
              >
                Close
              </button>
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button
                type="button"
                className="rounded border border-slate/25 px-3 py-1.5 text-xs dark:border-cyan-900/40"
                onClick={exportAllViews}
              >
                Export JSON
              </button>
              <label className="rounded border border-slate/25 px-3 py-1.5 text-xs dark:border-cyan-900/40">
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
                      <p className="text-xs text-slate dark:text-slate-300">{new Date(view.created_at).toLocaleString()}</p>
                      <div className="mt-1 flex flex-wrap gap-1.5 text-[11px]">
                        <span className="rounded border border-slate/25 px-1.5 py-0.5 dark:border-cyan-900/40">
                          RSS {view.window_type_counts.rss}
                        </span>
                        <span className="rounded border border-slate/25 px-1.5 py-0.5 dark:border-cyan-900/40">
                          Alerts {view.window_type_counts.alerts}
                        </span>
                        <span className="rounded border border-slate/25 px-1.5 py-0.5 dark:border-cyan-900/40">
                          Notes {view.window_type_counts.notes}
                        </span>
                      </div>
                    </div>
                  </div>
                  <div className="mt-2 flex items-center gap-2">
                    <button
                      type="button"
                      className="rounded border border-slate/25 px-2 py-1 text-xs dark:border-cyan-900/40"
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
                      className="rounded border border-slate/25 px-2 py-1 text-xs text-red-600 dark:border-cyan-900/40"
                      onClick={() => deleteView.mutate(view.id)}
                      disabled={deleteView.isPending}
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
    </div>
  )
}

function deriveTimeWindow(timeRange: TimeRangeFilter, customSinceDate: string, customUntilDate: string) {
  if (timeRange === 'all') {
    return { sinceIso: '', untilIso: '' }
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

  const now = new Date()
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
  return value === 'all' || value === '24h' || value === '7d' || value === '30d' || value === 'custom'
}

function parseWindowTimeFilterCandidate(value: unknown): WindowTimeFilter | null {
  if (!isRecord(value)) return null
  if (!isTimeRangeFilter(value.time_range)) return null
  return {
    time_range: value.time_range,
    custom_since_date: typeof value.custom_since_date === 'string' ? value.custom_since_date : '',
    custom_until_date: typeof value.custom_until_date === 'string' ? value.custom_until_date : '',
  }
}

function resolveWindowTimeFilter(windowLayout: DashboardWindow, dashboardTimeFilter: WindowTimeFilter): WindowTimeFilter {
  if (windowLayout.type === 'notes') {
    return dashboardTimeFilter
  }
  return windowLayout.time_override ?? dashboardTimeFilter
}

function combineTimeWindows(filters: WindowTimeFilter[]): { sinceIso: string; untilIso: string } {
  if (!filters.length) {
    return { sinceIso: '', untilIso: '' }
  }

  const windows = filters.map((filter) =>
    deriveTimeWindow(filter.time_range, filter.custom_since_date, filter.custom_until_date),
  )

  if (windows.some((entry) => !entry.sinceIso && !entry.untilIso)) {
    return { sinceIso: '', untilIso: '' }
  }

  const sinceDates = windows
    .map((entry) => (entry.sinceIso ? new Date(entry.sinceIso) : null))
    .filter((entry): entry is Date => entry instanceof Date && !Number.isNaN(entry.getTime()))
  const untilDates = windows
    .map((entry) => (entry.untilIso ? new Date(entry.untilIso) : null))
    .filter((entry): entry is Date => entry instanceof Date && !Number.isNaN(entry.getTime()))

  const sinceIso =
    sinceDates.length === windows.length
      ? new Date(Math.min(...sinceDates.map((entry) => entry.getTime()))).toISOString()
      : ''
  const untilIso =
    untilDates.length === windows.length
      ? new Date(Math.max(...untilDates.map((entry) => entry.getTime()))).toISOString()
      : ''

  return { sinceIso, untilIso }
}

function filterEntriesByTimeWindow<T extends { published_at: string | null; first_seen_at: string }>(
  entries: T[],
  filter: WindowTimeFilter,
): T[] {
  const { sinceIso, untilIso } = deriveTimeWindow(filter.time_range, filter.custom_since_date, filter.custom_until_date)
  if (!sinceIso && !untilIso) {
    return entries
  }

  const since = sinceIso ? new Date(sinceIso) : null
  const until = untilIso ? new Date(untilIso) : null

  return entries.filter((entry) => {
    const reference = entry.published_at || entry.first_seen_at
    if (!reference) return false

    const candidate = new Date(reference)
    if (Number.isNaN(candidate.getTime())) return false

    if (since && candidate < since) return false
    if (until && candidate > until) return false
    return true
  })
}

function invalidateLists(queryClient: ReturnType<typeof useQueryClient>, itemId: string) {
  queryClient.invalidateQueries({ queryKey: ['items'] })
  queryClient.invalidateQueries({ queryKey: ['item', itemId] })
}

function formatPublishedAt(value: string | null) {
  if (!value) return 'Unknown'
  const dt = new Date(value)
  if (Number.isNaN(dt.getTime())) return value
  return dt.toLocaleString()
}

function formatClassificationLabel(value: string): string {
  return value
    .split('_')
    .map((part) => (part ? part[0].toUpperCase() + part.slice(1) : part))
    .join(' ')
}

function computeInformationDensity(item: ItemListEntry): { score: number; label: string } {
  const titleWords = item.title.trim().split(/\s+/).filter(Boolean).length
  const summaryLength = (item.summary || '').trim().length
  const visibleTagCount = item.tags.filter((tagName) => !HIDDEN_TAGS.has(tagName)).length

  let score = 0
  score += Math.min(24, titleWords * 2)
  score += Math.min(40, Math.floor(summaryLength / 8))
  score += Math.min(18, visibleTagCount * 6)
  if (item.classification) score += 10
  if (item.status === 'error') score += 6
  if (item.published_at) score += 4

  const normalized = Math.max(1, Math.min(100, score))
  if (normalized >= 70) return { score: normalized, label: 'High' }
  if (normalized >= 40) return { score: normalized, label: 'Medium' }
  return { score: normalized, label: 'Low' }
}

function buildSavedViewPreview(view: SavedView, containerWidth: number, containerHeight: number): SavedViewPreview {
  const parsed = parseDashboardSavedView(view.query_json, containerWidth, containerHeight)
  const counts = {
    rss: 0,
    alerts: 0,
    notes: 0,
  }

  for (const window of parsed.windows) {
    if (window.type === 'rss') counts.rss += 1
    if (window.type === 'alerts') counts.alerts += 1
    if (window.type === 'notes') counts.notes += 1
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
      className="relative shrink-0 overflow-hidden rounded border border-slate/25 bg-gradient-to-br from-slate-100 to-slate-200 dark:border-cyan-900/40 dark:from-[#06221c] dark:to-[#041612]"
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
  if (type === 'alerts') return 'border-violet-500/40 bg-violet-400/30 dark:bg-violet-500/35'
  return 'border-amber-500/40 bg-amber-300/35 dark:bg-amber-500/35'
}

function loadWindowSeenState(): Record<string, string> {
  if (typeof window === 'undefined') {
    return {}
  }

  const raw = window.localStorage.getItem(WINDOW_SEEN_STORAGE_KEY)
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

function loadDashboardWindows(): DashboardWindow[] {
  if (typeof window === 'undefined') {
    return [createWindowLayout('rss', 1, 1380, 760, 'full')]
  }

  const raw = window.localStorage.getItem(WINDOW_STORAGE_KEY)
  if (!raw) {
    const { width, height } = getWindowContainerDimensions(null)
    return [createWindowLayout('rss', 1, width, height, 'full')]
  }

  try {
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) {
      const { width, height } = getWindowContainerDimensions(null)
      return [createWindowLayout('rss', 1, width, height, 'full')]
    }

    const { width, height } = getWindowContainerDimensions(null)
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
        rect: normalizePanelRect(rect, width, height),
        controls_collapsed: entry.controls_collapsed === true,
        scratch_note: typeof entry.scratch_note === 'string' ? entry.scratch_note : '',
        time_override: parseWindowTimeFilterCandidate(entry.time_override),
      })
    }

    if (!windows.length) {
      return [createWindowLayout('rss', 1, width, height, 'full')]
    }

    return normalizeDashboardWindows(windows, width, height)
  } catch {
    const { width, height } = getWindowContainerDimensions(null)
    return [createWindowLayout('rss', 1, width, height, 'full')]
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
  if (type === 'rss') return `RSS Feed ${index}`
  if (type === 'alerts') return `Alerts ${index}`
  return `Notes ${index}`
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

function parseDashboardSavedView(raw: Record<string, unknown>, containerWidth: number, containerHeight: number): DashboardSavedViewState {
  const fallback = createWindowLayout('rss', 1, containerWidth, containerHeight, 'full')

  const legacyFilters = isRecord(raw.filters) ? raw.filters : raw
  const legacyLayout = isRecord(raw.layout) ? raw.layout : {}
  const legacyWindows = isRecord(legacyLayout.windows) ? legacyLayout.windows : {}
  const legacyFeedRect = parsePanelRectCandidate(legacyWindows.feeds) || parsePanelRectCandidate(raw.panel_rect) || fallback.rect

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
    })
  }

  const rssSource = isRecord(raw.rss_filters) ? raw.rss_filters : legacyFilters
  const alertSource = isRecord(raw.alert_filters) ? raw.alert_filters : {}
  const uiSource = isRecord(raw.ui) ? raw.ui : {}

  const selectedFeedIds = Array.isArray(rssSource.selected_feed_ids)
    ? rssSource.selected_feed_ids.filter((entry): entry is string => typeof entry === 'string')
    : []
  const selectedTags = Array.isArray(rssSource.selected_tags)
    ? rssSource.selected_tags.filter((entry): entry is string => typeof entry === 'string' && !HIDDEN_TAGS.has(entry))
    : []

  const selectedAlertIds = Array.isArray(alertSource.selected_alert_ids)
    ? alertSource.selected_alert_ids.filter((entry): entry is string => typeof entry === 'string')
    : []
  const selectedAlertCategories = Array.isArray(alertSource.selected_categories)
    ? alertSource.selected_categories.filter((entry): entry is string => typeof entry === 'string')
    : []

  const readStatus = rssSource.read_status === 'read' || rssSource.read_status === 'unread' ? rssSource.read_status : 'all'
  const starStatus =
    rssSource.star_status === 'starred' || rssSource.star_status === 'unstarred' ? rssSource.star_status : 'all'
  const viewMode = rssSource.view_mode === 'expanded' ? 'expanded' : 'compact'
  const pageSize =
    typeof rssSource.page_size === 'number' && PAGE_SIZE_OPTIONS.includes(rssSource.page_size)
      ? rssSource.page_size
      : 25

  const alertPageSize =
    typeof alertSource.page_size === 'number' && PAGE_SIZE_OPTIONS.includes(alertSource.page_size)
      ? alertSource.page_size
      : 25

  const timeRange =
    rssSource.time_range === '24h' ||
    rssSource.time_range === '7d' ||
    rssSource.time_range === '30d' ||
    rssSource.time_range === 'custom'
      ? rssSource.time_range
      : 'all'

  const alertTimeRange =
    alertSource.time_range === '24h' ||
    alertSource.time_range === '7d' ||
    alertSource.time_range === '30d' ||
    alertSource.time_range === 'custom'
      ? alertSource.time_range
      : 'all'

  const sortValues: TimeSort[] = ['published_at_desc', 'published_at_asc', 'first_seen_desc', 'first_seen_asc']
  const sort =
    typeof rssSource.sort === 'string' && sortValues.includes(rssSource.sort as TimeSort)
      ? (rssSource.sort as TimeSort)
      : 'published_at_desc'

  const alertSort =
    typeof alertSource.sort === 'string' && sortValues.includes(alertSource.sort as TimeSort)
      ? (alertSource.sort as TimeSort)
      : 'published_at_desc'
  const alertViewMode = alertSource.view_mode === 'compact' ? 'compact' : 'expanded'

  return {
    version: DASHBOARD_VIEW_VERSION,
    rss_filters: {
      selected_feed_ids: selectedFeedIds,
      selected_tags: selectedTags,
      q: typeof rssSource.q === 'string' ? rssSource.q : '',
      read_status: readStatus,
      star_status: starStatus,
      view_mode: viewMode,
      page_size: pageSize,
      time_range: timeRange,
      custom_since_date: typeof rssSource.custom_since_date === 'string' ? rssSource.custom_since_date : '',
      custom_until_date: typeof rssSource.custom_until_date === 'string' ? rssSource.custom_until_date : '',
      sort,
    },
    alert_filters: {
      selected_alert_ids: selectedAlertIds,
      selected_categories: selectedAlertCategories,
      q: typeof alertSource.q === 'string' ? alertSource.q : '',
      view_mode: alertViewMode,
      page_size: alertPageSize,
      time_range: alertTimeRange,
      custom_since_date: typeof alertSource.custom_since_date === 'string' ? alertSource.custom_since_date : '',
      custom_until_date: typeof alertSource.custom_until_date === 'string' ? alertSource.custom_until_date : '',
      sort: alertSort,
    },
    windows: normalizeDashboardWindows(parsedWindows, containerWidth, containerHeight),
    ui: {
      show_advanced_filters: typeof uiSource.show_advanced_filters === 'boolean' ? uiSource.show_advanced_filters : false,
    },
  }
}

function buildDashboardSavedViewState(
  rssFilters: DashboardSavedViewQuery,
  alertFilters: DashboardAlertViewQuery,
  windows: DashboardWindow[],
  showAdvancedFilters: boolean,
): DashboardSavedViewState {
  return {
    version: DASHBOARD_VIEW_VERSION,
    rss_filters: {
      ...rssFilters,
      selected_feed_ids: [...rssFilters.selected_feed_ids],
      selected_tags: [...rssFilters.selected_tags],
    },
    alert_filters: {
      ...alertFilters,
      selected_alert_ids: [...alertFilters.selected_alert_ids],
      selected_categories: [...alertFilters.selected_categories],
    },
    windows: windows.map((window) => ({
      id: window.id,
      type: window.type,
      title: window.title,
      snap: window.snap,
      rect: { ...window.rect },
      controls_collapsed: window.controls_collapsed,
      scratch_note: window.scratch_note,
      time_override: window.time_override ? { ...window.time_override } : null,
    })),
    ui: {
      show_advanced_filters: showAdvancedFilters,
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
  return /^\d+[\.\)]\s+/.test(line)
}

function cleanNumbered(line: string): string {
  return line.replace(/^\d+[\.\)]\s+/, '').trim()
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
  if (/^mailto:/i.test(href)) return href
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
  return value === 'rss' || value === 'alerts' || value === 'notes'
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
